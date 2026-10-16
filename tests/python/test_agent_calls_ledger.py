"""T2 (0.154.0): sổ lượt chung `agent_calls`, trần máy chủ cho lượt nhóm, vòng đời lý do thức theo agent.

    python tests/run.py agent_calls -v

Thiết kế: docs/superpowers/specs/2026-10-15-team-adaptive-design.md mục 3.1-3.4, 9.1. Kho thật trên SQLite tạm, bộ chạy nhóm
thật với engine GIẢ (không model nào được gọi). Các ca:
- chỉ mục duy nhất từng phần theo `reason_ref`; thử lại sau refunded/interrupted/error (attempt + 1);
- phép đếm trần một nơi, không đếm hai lần (counted = 0, refunded); dự trữ 20% (tự phát chặn ở 80%, nghĩa vụ tới 100%);
- trần N sát biên; đồng thời bằng luồng và hai kết nối kho riêng không vượt trần;
- lượt nhóm giữ chỗ TRƯỚC engine; trần đầy thì hoàn lượt nhóm, kết cục `cap = server_ceiling`, không gọi engine;
  bước việc bị chặn `server_ceiling`; lỗi kho chung lúc chốt không làm hỏng lượt;
- đối soát phản chiếu thiếu và dư; khởi động lại giữa giữ chỗ và chốt;
- vòng đời lý do: gom, CAS, bận không tốn lần thử, nguồn đến lúc đang chạy, nguồn chưa tiêu thụ, lỗi khi đã có lý do chờ
  mới hơn, quá 3 lần, gỡ nguồn, lý do hết nguồn.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-agent-calls-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-agent-calls-w-"))

import agent_ledger as AL  # noqa: E402
import resonance_store as RS  # noqa: E402
import sessions  # noqa: E402
import task_store  # noqa: E402
import team_runner as TR  # noqa: E402
import team_store  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def ceiling(n):
    if n is None:
        os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
    else:
        os.environ["JAVIS_RESONANCE_CALL_CEILING"] = str(n)


_n = [0]


def fresh(label):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}-{label}"
    d.mkdir()
    return d, RS.GoalStore(d / "resonance.sqlite3")


def run(coro):
    return asyncio.run(coro)


# ═════ 1. chỉ mục duy nhất từng phần, thử lại ═════
ceiling(None)
d, gs = fresh("unique")
L = AL.CallLedger(gs)
c1 = L.reserve(purpose="reaction_review", reason_ref="wr:1", agent_key="ag_a")
try:
    L.reserve(purpose="reaction_review", reason_ref="wr:1", agent_key="ag_a")
    dup = False
except AL.ReasonBusy:
    dup = True
check("một lý do chỉ một lượt đang chạy: lượt thứ hai bị chặn", dup)
L.mark_engine(c1, "fake", "m")
L.settle(c1, "ok")
try:
    L.reserve(purpose="reaction_review", reason_ref="wr:1")
    dup_ok = False
except AL.ReasonBusy:
    dup_ok = True
check("lượt đã ok vẫn giữ khoá của lý do", dup_ok)
for st in ("refunded", "interrupted", "error"):
    ref = f"wr:{st}"
    a = L.reserve(purpose="review", reason_ref=ref)
    if st == "refunded":
        L.refund(a)
    else:
        L.mark_engine(a)
        L.settle(a, st)
    b = L.reserve(purpose="review", reason_ref=ref)
    check(f"thử lại sau {st}: được, attempt + 1", b and L.get(b)["attempt"] == 2 and L.get(a)["attempt"] == 1,
          L.rows(reason_ref=ref))
a = L.reserve(purpose="review", reason_ref="wr:x")
L.mark_engine(a)
check("đã qua mốc engine thì không hoàn được", L.refund(a) is False and L.get(a)["status"] == "running")

# ═════ 2. phép đếm một nơi, không đếm hai lần ═════
d, gs = fresh("count")
L = AL.CallLedger(gs)
with gs._Tx(gs) as c:
    c.execute("INSERT INTO goals(id,brain_id,owner,revision,status,idempotency_key,output_root,budget_calls,calls_used,"
              "created_at,updated_at) VALUES('g1','b','o',1,'active','k','x',10,3,0,0)")
    c.execute("INSERT INTO call_ledger(brain_id,kind,ref,status,created_at,updated_at) VALUES('b','plan','',"
              "'used',0,0)")
    c.execute("INSERT INTO call_ledger(brain_id,kind,ref,status,created_at,updated_at) VALUES('b','plan','',"
              "'released',0,0)")
L.reserve(purpose="goal", reason_ref="goal_call:1", counted=False, mirror_ref="goal_call:1")   # phản chiếu goal
r1 = L.reserve(purpose="reply", reason_ref="team_turn:t1", obligation=True)
r2 = L.reserve(purpose="reply", reason_ref="team_turn:t2", obligation=True)
L.refund(r2)
r3 = L.reserve(purpose="scan", reason_ref="scan:1")
L.mark_engine(r3)
L.settle(r3, "error")
check("đếm = goals.calls_used + call_ledger used + agent_calls counted chưa hoàn (3 + 1 + 2)", L.used() == 6, L.used())

# ═════ 3. dự trữ 20% và trần N sát biên ═════
d, gs = fresh("reserve")
L = AL.CallLedger(gs)
ceiling(10)
got = [L.reserve(purpose="proactive", reason_ref=f"p:{i}") for i in range(10)]
check("tự phát chỉ tới floor(10 * 0.8) = 8", sum(1 for x in got if x) == 8 and got[8] is None, got)
ob = [L.reserve(purpose="reply", reason_ref=f"o:{i}", obligation=True) for i in range(5)]
check("nghĩa vụ dùng được 20% cuối, tới đúng trần 10", sum(1 for x in ob if x) == 2 and ob[2] is None, ob)
check("đầy trần: tự phát vẫn bị chặn", L.reserve(purpose="scan", reason_ref="s:late") is None)
check("ceiling_limit(7) cho tự phát = 5", RS.ceiling_limit(7, False) == 5 and RS.ceiling_limit(7, True) == 7)
d, gs = fresh("boundary")
L = AL.CallLedger(gs)
ceiling(3)
seq = [L.reserve(purpose="reply", reason_ref=f"b:{i}", obligation=True) for i in range(4)]
check("trần N=3: lượt thứ 3 được, lượt thứ 4 bị chặn", all(seq[:3]) and seq[3] is None, seq)
L.refund(seq[0])
check("hoàn một lượt (chưa gọi model) trả lại đúng một chỗ",
      L.reserve(purpose="reply", reason_ref="b:again", obligation=True) is not None
      and L.reserve(purpose="reply", reason_ref="b:again2", obligation=True) is None)
ceiling(0)
check("trần 0 chặn hết", L.reserve(purpose="reply", reason_ref="z", obligation=True) is None)
ceiling(None)

# ═════ 4. đồng thời: luồng và hai kết nối kho riêng ═════
d, gs = fresh("concurrent")
gs2 = RS.GoalStore(d / "resonance.sqlite3")        # kho thứ hai, kết nối riêng, cùng file
ceiling(25)
barrier = threading.Barrier(40, timeout=30)
ok_ids, errs = [], []
lock = threading.Lock()


def worker(i):
    store = gs if i % 2 else gs2
    led = AL.CallLedger(store)
    try:
        barrier.wait()
        x = led.reserve(purpose="reply", reason_ref=f"cc:{i}", obligation=True)
        with lock:
            if x:
                ok_ids.append(x)
    except Exception as ex:  # noqa: BLE001
        with lock:
            errs.append(repr(ex))


ths = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
for t in ths:
    t.start()
for t in ths:
    t.join(60)
check("40 lượt đồng thời (luồng, hai kết nối): đúng 25 lượt được, không vượt trần",
      len(ok_ids) == 25 and AL.CallLedger(gs).used() == 25 and not errs, (len(ok_ids), errs[:2]))
ceiling(None)


# ═════ 5. lượt nhóm qua bộ chạy thật, engine giả ═════
class Fake:
    def __init__(self, gs):
        self.calls, self.seen_rows, self.gs = [], [], gs

    def engine_for(self, team, key, role):
        async def run_(req):
            self.calls.append(req)
            self.seen_rows.append(AL.CallLedger(self.gs).rows(mirror_ref=f"team_turn:{req['turn_id']}"))
            return {"text": f"{key} trả lời", "tokens_in": 3, "tokens_out": 4}
        return "fake", "fake-model", run_


def world(label, ceiling_obj=None):
    d, gs = fresh(label)
    ss = sessions.SessionStore(db_path=d / "chat.db")
    ts = task_store.TaskStore(d / "kanban.sqlite3")
    tm = team_store.TeamStore(d / "team.sqlite3")
    fake = Fake(gs)
    pub = []
    ceil = ceiling_obj(gs) if ceiling_obj else AL.TeamCeiling(gs)
    r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=fake.engine_for, publish=pub.append,
                      agent_info=lambda root, key: {"slug": key, "name": key.title(), "system": ""}, ceiling=ceil)
    tid = team_store.new_team_id()
    sid = ss.create_session(brain=str(d), channel=f"team:{tid}")
    tm.create_team(str(d), "Đội", "", sid, team_id=tid)
    for k in ("lan", "minh"):
        tm.add_member(tid, k, k)
    tm.set_leader(tid, "lan")
    return gs, ss, ts, tm, fake, r, tm.get(tid)


gs, ss, ts, tm, fake, r, T = world("turn-ok")
ceiling(5)
run(r.run_replies(r.submit(T["team_id"], "Chào", client_msg_id="m1")))
rows = AL.CallLedger(gs).rows()
check("lượt nhóm: engine được gọi một lần", len(fake.calls) == 1)
check("lượt nhóm giữ chỗ dòng sổ TRƯỚC engine (lúc engine chạy đã có dòng running, counted = 1)",
      fake.seen_rows and len(fake.seen_rows[0]) == 1 and fake.seen_rows[0][0]["status"] == "running"
      and fake.seen_rows[0][0]["counted"] == 1 and fake.seen_rows[0][0]["obligation"] == 1, fake.seen_rows)
turn = tm.turns(T["team_id"])[0]
check("dòng sổ chốt ok theo lượt nhóm, kèm token", len(rows) == 1 and rows[0]["status"] == "ok"
      and rows[0]["mirror_ref"] == f"team_turn:{turn['turn_id']}" and rows[0]["tokens_out"] == 4, rows)

# Trần đầy: lượt nhóm hoàn, cap = server_ceiling, engine không được gọi.
gs, ss, ts, tm, fake, r, T = world("turn-cap")
ceiling(2)
L = AL.CallLedger(gs)
L.reserve(purpose="reply", reason_ref="x1", obligation=True)
L.reserve(purpose="reply", reason_ref="x2", obligation=True)
sub = r.submit(T["team_id"], "Chào", client_msg_id="m1")
run(r.run_replies(sub))
rep = tm.replies_of(T["team_id"], sub["message_id"])
check("trần đầy: không gọi engine", fake.calls == [])
check("trần đầy: lượt nhóm vừa giữ được hoàn", [t["status"] for t in tm.turns(T["team_id"])] == ["refunded"],
      tm.turns(T["team_id"]))
check("trần đầy: kết cục lỗi lý do server_ceiling, chi tiết cap", rep and rep[0]["status"] == "error"
      and rep[0]["reason"] == "server_ceiling" and rep[0]["detail"] == "server_ceiling", rep)
check("trần đầy: không thêm dòng sổ nào", L.used() == 2 and len(L.rows()) == 2)
out = run(r._turn(T, "lan", "leader_reply", "x"))
check("_turn trả {status: cap, cap: server_ceiling}", out == {"status": "cap", "cap": "server_ceiling"}, out)

# Bước việc bị chặn server_ceiling.
gs, ss, ts, tm, fake, r, T = world("step-cap")
ceiling(0)
job = r.submit(T["team_id"], "Việc", as_job=True, client_msg_id="j1")
run(r.dispatch_once(wait=True))
task = ts.get_task(job["task_id"])
check("bước việc gặp trần chung: việc chặn kind server_ceiling, không gọi engine",
      task["status"] == "team_blocked" and task["block_kind"] == "server_ceiling" and fake.calls == [], task)
check("bước về hàng chờ (Tiếp tục chạy lại được sau khi nâng trần)",
      any(s["status"] == "queued" for s in tm.steps(job["task_id"])), tm.steps(job["task_id"]))
ceiling(None)


# Lỗi kho chung lúc chốt không làm hỏng lượt nhóm.
class BadSettle(AL.TeamCeiling):
    def settle(self, *a, **k):
        raise RuntimeError("kho chung hỏng")

    def mark(self, *a, **k):
        raise RuntimeError("kho chung hỏng")


gs, ss, ts, tm, fake, r, T = world("bad-settle", BadSettle)
sub = r.submit(T["team_id"], "Chào", client_msg_id="m1")
run(r.run_replies(sub))
rep = tm.replies_of(T["team_id"], sub["message_id"])
check("lỗi kho chung lúc mốc/chốt: lượt vẫn xong, có tin trả lời", rep and rep[0]["status"] == "done"
      and [t["status"] for t in tm.turns(T["team_id"])] == ["ok"], rep)
out = r.ceiling.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 60)
row = AL.CallLedger(gs).rows()[0]
check("đối soát sau đó sửa dòng sổ theo lượt nhóm (ok)", row["status"] == "ok", (out, row))


# Kho chung lỗi lúc GIỮ CHỖ khi máy chủ có trần: hoàn lượt nhóm, không gọi engine.
class BadReserve(AL.TeamCeiling):
    def reserve(self, *a, **k):
        raise RuntimeError("kho chung khoá")


gs, ss, ts, tm, fake, r, T = world("bad-reserve", BadReserve)
ceiling(10)
sub = r.submit(T["team_id"], "Chào", client_msg_id="m1")
run(r.run_replies(sub))
check("kho chung lỗi lúc giữ chỗ (có trần): không gọi engine, lượt nhóm hoàn",
      fake.calls == [] and [t["status"] for t in tm.turns(T["team_id"])] == ["refunded"])
ceiling(None)
gs, ss, ts, tm, fake, r, T = world("bad-reserve-nocap", BadReserve)
run(r.run_replies(r.submit(T["team_id"], "Chào", client_msg_id="m1")))
r.ceiling.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 60)
check("kho chung lỗi lúc giữ chỗ (không đặt trần): lượt chạy, đối soát dựng lại dòng counted",
      len(fake.calls) == 1 and [x["status"] for x in AL.CallLedger(gs).rows()] == ["ok"])

# ═════ 6. đối soát phản chiếu thiếu và dư ═════
gs, ss, ts, tm, fake, r, T = world("reconcile")
TC = r.ceiling
L = AL.CallLedger(gs)
t_ref = tm.reserve_turn(T["team_id"], None, None, "lan", "leader_reply")
TC.reserve({"turn_id": t_ref, "team_id": T["team_id"], "agent_key": "lan", "role": "leader_reply"})
tm.refund_turn(t_ref)                                   # hoàn bên nhóm mà dòng sổ còn reserved (dư)
t_miss = tm.reserve_turn(T["team_id"], None, None, "minh", "member_reply")
tm.mark_engine(t_miss, "fake", "m")                     # tới mốc engine mà thiếu dòng sổ
t_done = tm.reserve_turn(T["team_id"], None, None, "lan", "leader_reply")
TC.reserve({"turn_id": t_done, "team_id": T["team_id"], "agent_key": "lan", "role": "leader_reply"})
tm.mark_engine(t_done, "fake", "m")
tm.settle_turn(t_done, "error")
t_run = tm.reserve_turn(T["team_id"], None, None, "minh", "member_reply")
TC.reserve({"turn_id": t_run, "team_id": T["team_id"], "agent_key": "minh", "role": "member_reply"})
tm.mark_engine(t_run, "fake", "m")
stale = L.reserve(purpose="scan", reason_ref="scan:old", now=time.time() - 3600)
stale_e = L.reserve(purpose="proactive", reason_ref="pro:old", now=time.time() - 3600)
L.mark_engine(stale_e)
fresh_row = L.reserve(purpose="scan", reason_ref="scan:new")
rep1 = TC.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 60)


def st_of(tid):
    rows = L.rows(mirror_ref=f"team_turn:{tid}")
    return [x["status"] for x in rows]


check("dư: lượt nhóm đã hoàn thì dòng sổ refunded", st_of(t_ref) == ["refunded"], st_of(t_ref))
check("thiếu: lượt nhóm đã tới mốc engine thì dựng lại dòng counted", st_of(t_miss) == ["running"]
      and L.rows(mirror_ref=f"team_turn:{t_miss}")[0]["counted"] == 1, st_of(t_miss))
check("lượt nhóm đã chốt thì dòng sổ chốt theo (error, vẫn tính)", st_of(t_done) == ["error"], st_of(t_done))
check("lượt nhóm đang chạy thì dòng sổ running", st_of(t_run) == ["running"], st_of(t_run))
check("dòng tự phát quá 10 phút: chưa mốc engine thì refunded, có mốc thì interrupted",
      L.get(stale)["status"] == "refunded" and L.get(stale_e)["status"] == "interrupted", rep1)
check("dòng tự phát mới (chưa quá 10 phút) giữ nguyên", L.get(fresh_row)["status"] == "reserved")
rep2 = TC.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 60)
check("đối soát idempotent: lần hai không đổi gì", rep2["refunded"] == 0 and rep2["rebuilt"] == 0
      and rep2["settled"] == 0 and rep2["marked"] == 0, rep2)
used_before = L.used()
tm.settle_turn(t_miss, "ok")
TC.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 60)
check("dòng dựng lại chốt theo lượt nhóm, không đếm hai lần", st_of(t_miss) == ["ok"] and L.used() == used_before)

# ═════ 6b. không hoàn lượt nhóm theo tuổi; cửa sổ ân hạn 120 giây (thiết kế 9.1 sửa sau review T1 vòng 2) ═════
gs, ss, ts, tm, fake, r, T = world("no-age")
TC = r.ceiling
L = AL.CallLedger(gs)
wait = tm.reserve_turn(T["team_id"], None, None, "lan", "leader_reply")
TC.reserve({"turn_id": wait, "team_id": T["team_id"], "agent_key": "lan", "role": "leader_reply"},
           now=time.time() - 3000)             # lượt đang chờ khoá bận đã 50 phút, chưa có mốc engine
for startup in (False, True):
    TC.reconcile(tm, startup=startup, now=time.time() + 4000)
check("lượt nhóm đang chờ khoá bận lâu: dòng sổ KHÔNG bị hoàn theo tuổi (thường và lúc khởi động)",
      [x["status"] for x in L.rows(mirror_ref=f"team_turn:{wait}")] == ["reserved"])
step_run = tm.reserve_turn(T["team_id"], None, None, "minh", "work")
TC.reserve({"turn_id": step_run, "team_id": T["team_id"], "agent_key": "minh", "role": "work"},
           now=time.time() - 3500)
tm.mark_engine(step_run, "fake", "m")
TC.mark(step_run, "fake", "m")
TC.reconcile(tm, now=time.time() + 4000)
check("bước đang chạy rất lâu (tới STEP_TIMEOUT_S): dòng sổ giữ running",
      [x["status"] for x in L.rows(mirror_ref=f"team_turn:{step_run}")] == ["running"])
young = tm.reserve_turn(T["team_id"], None, None, "lan", "leader_reply")
TC.reserve({"turn_id": young, "team_id": T["team_id"], "agent_key": "lan", "role": "leader_reply"})
tm.refund_turn(young)
TC.reconcile(tm)
check("cửa sổ ân hạn: dòng sổ mới hơn 120 giây không bị đụng", [x["status"] for x in
                                                                L.rows(mirror_ref=f"team_turn:{young}")] == ["reserved"])
TC.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 5)
check("qua cửa sổ ân hạn: theo lượt nhóm đã hoàn thì refunded",
      [x["status"] for x in L.rows(mirror_ref=f"team_turn:{young}")] == ["refunded"])
half = tm.reserve_turn(T["team_id"], None, None, "minh", "member_reply")   # giữa hai bước giữ chỗ
tm.mark_engine(half, "fake", "m")
TC.reconcile(tm)
check("cửa sổ ân hạn: lượt nhóm mới chưa có dòng sổ không bị dựng lại vội", L.rows(mirror_ref=f"team_turn:{half}") == [])
TC.reconcile(tm, now=time.time() + AL.RECONCILE_GRACE_S + 5)
check("qua cửa sổ: lượt nhóm đã tới mốc engine mà thiếu dòng sổ thì dựng lại",
      [x["status"] for x in L.rows(mirror_ref=f"team_turn:{half}")] == ["running"])
lost = r._reserve_both(T, None, None, "lan", "leader_reply")
r._refund_if_unmarked(lost)
check("đường hoàn trước engine (lease_lost lúc chờ khoá, busy_long, lỗi dựng engine): hoàn cả hai kho",
      tm.turn(lost)["status"] == "refunded"
      and [x["status"] for x in L.rows(mirror_ref=f"team_turn:{lost}")] == ["refunded"])

# ═════ 7. khởi động lại giữa giữ chỗ và chốt ═════
gs, ss, ts, tm, fake, r, T = world("restart")
TC = r.ceiling
a = tm.reserve_turn(T["team_id"], None, None, "lan", "leader_reply")
TC.reserve({"turn_id": a, "team_id": T["team_id"], "agent_key": "lan", "role": "leader_reply"})
b = tm.reserve_turn(T["team_id"], None, None, "minh", "member_reply")
TC.reserve({"turn_id": b, "team_id": T["team_id"], "agent_key": "minh", "role": "member_reply"})
tm.mark_engine(b, "fake", "m")
TC.mark(b, "fake", "m")
# "Tiến trình chết": mở lại kho, bộ chạy mới phục hồi.
gs2 = RS.GoalStore(gs.path)
tm2 = team_store.TeamStore(tm.path)
r2 = TR.TeamRunner(teams=tm2, tasks=ts, sessions=ss, engine_for=fake.engine_for, publish=lambda e: None,
                   agent_info=lambda root, key: {"slug": key, "name": key, "system": ""}, ceiling=AL.TeamCeiling(gs2))
r2.recover()
L2 = AL.CallLedger(gs2)
check("restart: lượt chưa tới mốc engine hoàn cả hai kho", tm2.turn(a)["status"] == "refunded"
      and [x["status"] for x in L2.rows(mirror_ref=f"team_turn:{a}")] == ["refunded"])
check("restart: lượt đã qua mốc engine interrupted ở cả hai kho (vẫn tính)", tm2.turn(b)["status"] == "interrupted"
      and [x["status"] for x in L2.rows(mirror_ref=f"team_turn:{b}")] == ["interrupted"] and L2.used() == 1)

# ═════ 8. vòng đời lý do ═════
d, gs = fresh("reasons")
W = AL.WakeReasons(gs)
L = AL.CallLedger(gs)
rid = W.add_source("ag_a", "dm:ag_a", "reaction_review", "rx:dm:1", "g1")
for i in range(2, 6):
    W.add_source("ag_a", "dm:ag_a", "reaction_review", f"rx:dm:{i}", f"g{i}")
check("N nguồn gom vào MỘT lý do chờ", len(W.reasons(status="pending")) == 1 and len(W.sources(rid)) == 5)
check("add_source idempotent theo source_ref", W.add_source("ag_a", "dm:ag_a", "reaction_review", "rx:dm:1") == rid
      and len(W.sources(rid)) == 5)
other = W.add_source("ag_a", "team:t1", "reaction_review", "rx:team:1")
check("ngữ cảnh khác có lý do riêng", other != rid)
busy = W.claim(rid, try_busy=lambda: False)
check("bận: không nhận, lý do giữ pending, attempt không tăng", busy["outcome"] == "busy"
      and W.get(rid)["status"] == "pending" and W.get(rid)["attempt"] == 0 and L.rows() == [], busy)
released = []
cl = W.claim(rid, try_busy=lambda: True, release_busy=lambda: released.append(1))
check("CAS nhận: claimed, attempt 1, giữ chỗ agent_calls (reason_ref wr:<id>)", cl["outcome"] == "claimed"
      and W.get(rid)["status"] == "claimed" and W.get(rid)["attempt"] == 1
      and L.get(cl["call_id"])["reason_ref"] == f"wr:{rid}" and len(cl["sources"]) == 5, cl)
check("nhận lần hai (đã claimed): gone", W.claim(rid)["outcome"] == "gone")
new_pending = W.add_source("ag_a", "dm:ag_a", "reaction_review", "rx:dm:6", "g6")
check("nguồn đến lúc lượt đang chạy vào lý do pending MỚI", new_pending != rid
      and W.get(new_pending)["status"] == "pending" and W.get(rid)["status"] == "claimed")
res = W.settle(rid, "ok", consumed=["rx:dm:1", "rx:dm:2", "rx:dm:3"])
moved = {s["source_ref"] for s in W.sources(new_pending, live_only=True)}
check("served: nguồn chưa tiêu thụ chuyển sang lý do chờ cùng loại", res["status"] == "served"
      and res["moved_to"] == new_pending and moved == {"rx:dm:4", "rx:dm:5", "rx:dm:6"}, (res, moved))
check("nguồn đã tiêu thụ có consumed_at", all(s["consumed_at"] for s in W.sources(rid)))
check("dòng sổ lượt chốt ok", L.get(cl["call_id"])["status"] == "ok")
# lỗi khi đã có lý do chờ mới hơn
cl2 = W.claim(new_pending, try_busy=lambda: True)
W.add_source("ag_a", "dm:ag_a", "reaction_review", "rx:dm:7", "g7")
newer = W.reasons(agent_key="ag_a", context_ref="dm:ag_a", status="pending")[0]["id"]
with gs._Tx(gs) as c:
    c.execute("UPDATE agent_wake_reasons SET attempt=0 WHERE id=?", (newer,))
L.mark_engine(cl2["call_id"])
res2 = W.settle(new_pending, "error")
check("lỗi khi đã có lý do chờ mới hơn: chuyển nguồn sang lý do chờ, giữ attempt lớn hơn, lý do chạy superseded",
      res2 == {"status": "superseded", "moved_to": newer} and W.get(new_pending)["status"] == "superseded"
      and W.get(newer)["attempt"] == 1
      and {s["source_ref"] for s in W.sources(newer, live_only=True)} == {"rx:dm:4", "rx:dm:5", "rx:dm:6", "rx:dm:7"},
      (res2, W.get(newer)))
check("lượt lỗi sau mốc engine vẫn tính", L.get(cl2["call_id"])["status"] == "error")
# quá 3 lần thì failed
for i in range(5):
    x = W.claim(newer, try_busy=lambda: True)
    if x["outcome"] != "claimed":
        break
    L.mark_engine(x["call_id"])
    out = W.settle(newer, "interrupted")
check("lỗi đủ 3 lần thì failed (hiển thị, chủ bấm thử lại)", W.get(newer)["status"] == "failed"
      and W.get(newer)["attempt"] == AL.MAX_ATTEMPTS, W.get(newer))
back = W.retry_failed(newer)
check("chủ thử lại: về pending, attempt 0", back == newer and W.get(newer)["status"] == "pending"
      and W.get(newer)["attempt"] == 0)
# hoàn (chưa gọi model) không tốn lần thử
x = W.claim(newer, try_busy=lambda: True)
W.settle(newer, "refunded")
check("lượt hoàn không tốn lần thử", W.get(newer)["status"] == "pending" and W.get(newer)["attempt"] == 0
      and L.get(x["call_id"])["status"] == "refunded")
# trần đầy: không nhận, không tốn lần thử
ceiling(0)
rel = []
q = W.claim(newer, try_busy=lambda: True, release_busy=lambda: rel.append(1))
check("trần đầy: không nhận (quota), attempt giữ nguyên, nhả khoá bận", q["outcome"] == "quota"
      and W.get(newer)["status"] == "pending" and W.get(newer)["attempt"] == 0 and rel == [1], q)
ceiling(None)
# gỡ nguồn
srcs = [s["source_ref"] for s in W.sources(newer, live_only=True)]
W.remove_source(srcs[0])
check("gỡ một nguồn khi lý do còn pending: removed_at, lý do vẫn chờ",
      W.source(srcs[0])["removed_at"] is not None and W.get(newer)["status"] == "pending")
for sref in srcs[1:]:
    W.remove_source(sref)
check("lý do không còn nguồn nào thì superseded", W.get(newer)["status"] == "superseded")
g = W.add_source("ag_b", "dm:ag_b", "reaction_review", "rx:dm:100", "grp")
W.add_source("ag_b", "dm:ag_b", "reaction_review", "rx:dm:101", "grp")
removed = W.remove_group("grp", keep=["rx:dm:101"])
check("gỡ theo cặp (tin, người phản hồi) trừ nguồn mới nhất", removed == ["rx:dm:100"]
      and [s["source_ref"] for s in W.sources(g, live_only=True)] == ["rx:dm:101"])
cl3 = W.claim(g, try_busy=lambda: True)
check("gỡ nguồn của lý do đang chạy: đánh removed_at, lý do vẫn claimed (review T2 P2-1)",
      W.remove_source("rx:dm:101") is True and W.source("rx:dm:101")["removed_at"] is not None
      and W.get(g)["status"] == "claimed")
check("lý do tự phát dùng sổ chung với obligation = 0", L.get(cl3["call_id"])["obligation"] == 0)

# ═════ review T2 P2-1: đổi/bỏ reaction trong lúc lượt xem xét đang chạy ═════
d, gs = fresh("claimed-change")
W = AL.WakeReasons(gs)
r1 = W.add_source("ag_c", "dm:ag_c", "reaction_review", "rx:dm:1", "dm:s:9:owner")
W.claim(r1, try_busy=lambda: True)
W.add_source("ag_c", "dm:ag_c", "reaction_review", "rx:dm:2", "dm:s:9:owner")     # chủ đổi reaction
W.remove_group("dm:s:9:owner", keep=["rx:dm:2"])
out = W.settle(r1, "ok", consumed=[])
nxt = W.reasons(agent_key="ag_c", status="pending")
check("đổi reaction lúc đang xem xét: lý do chờ kế tiếp chỉ giữ nguồn MỚI", len(nxt) == 1
      and [x["source_ref"] for x in W.sources(nxt[0]["id"], live_only=True)] == ["rx:dm:2"], (out, nxt))
r3 = W.add_source("ag_d", "dm:ag_d", "reaction_review", "rx:dm:3", "dm:s:10:owner")
W.claim(r3, try_busy=lambda: True)
W.remove_group("dm:s:10:owner")                                                    # chủ bỏ hết
out = W.settle(r3, "ok", consumed=[])
check("bỏ reaction lúc đang xem xét: không mang gì sang lượt sau", out == {"status": "served", "moved_to": None}
      and W.reasons(agent_key="ag_d", status="pending") == [], out)

# ═════ review T2 P3: lý do rỗng chốt superseded thật; khoá bận không rò khi kho lỗi ═════
d, gs = fresh("claim-p3")
W = AL.WakeReasons(gs)
e = W.add_source("ag_e", "dm:ag_e", "reaction_review", "rx:dm:50")
with gs._Tx(gs) as c:
    c.execute("UPDATE agent_wake_sources SET removed_at=1 WHERE source_ref='rx:dm:50'")
rel = []
res_e = W.claim(e, try_busy=lambda: True, release_busy=lambda: rel.append(1))
check("nhận lý do không còn nguồn: superseded được GHI (không bị rollback), nhả khoá bận",
      res_e["outcome"] == "gone" and W.get(e)["status"] == "superseded" and rel == [1], (res_e, W.get(e)))
f = W.add_source("ag_f", "dm:ag_f", "reaction_review", "rx:dm:60")


class Boom(AL.CallLedger):
    def reserve(self, **k):
        raise RuntimeError("kho khoá")


Wb = AL.WakeReasons(gs, ledger=Boom(gs))
rel = []
try:
    Wb.claim(f, try_busy=lambda: True, release_busy=lambda: rel.append(1))
    boom = False
except RuntimeError:
    boom = True
check("lỗi kho bất ngờ lúc nhận: lỗi nổi lên, khoá bận vẫn được nhả, lý do giữ pending",
      boom and rel == [1] and W.get(f)["status"] == "pending" and W.get(f)["attempt"] == 0)

# ═════ review T2 P3: lỗi kho bất ngờ lúc nhận lý do vẫn nhả khoá bận ═════
d, gs = fresh("claim-leak")
W = AL.WakeReasons(gs)
rid_l = W.add_source("ag_l", "dm:ag_l", "reaction_review", "rx:dm:900")
_orig_reserve = W.ledger.reserve


def _reserve_boom(*a, **k):
    raise RuntimeError("disk I/O error")


W.ledger.reserve = _reserve_boom
rel = []
try:
    W.claim(rid_l, try_busy=lambda: True, release_busy=lambda: rel.append(1))
    leak_out = "no-raise"
except RuntimeError:
    leak_out = "raised"
W.ledger.reserve = _orig_reserve
check("review T2 P3: kho lỗi bất ngờ khi nhận lý do: khoá bận vẫn được nhả, lý do giữ pending",
      rel == [1] and W.get(rid_l)["status"] == "pending" and W.get(rid_l)["attempt"] == 0, (leak_out, rel, W.get(rid_l)))

# ═════ review T2 P2-3: đường bật người review của A5 ghi resumed_at ═════
d, gs = fresh("reviewer-enable")
own = RS.Principal("owner", "owner", "brainX")
mk = gs.agent_register(own, "maker")
rv = gs.agent_register(own, "reviewer")
with gs._Tx(gs) as c:
    c.execute("INSERT INTO goals(id,brain_id,owner,revision,status,idempotency_key,output_root,budget_calls,calls_used,"
              "created_at,updated_at) VALUES('gr','brainX','o',1,'active','kr','x',10,0,0,0)")
    c.execute("INSERT INTO goal_team(goal_id,maker_agent_key,reviewer_agent_key,reviewer_version_at_attach,"
              "pair_config_version,max_rounds,status,created_at) VALUES('gr',?,?,1,1,2,'active',0)",
              (mk["agent_key"], rv["agent_key"]))
check("người review chưa bật: chưa có mốc bật lại", gs.resumed_at(rv["agent_key"]) is None)
gs.team_reviewer_enable(own, "gr", rv["agent_key"])
check("bật người review (A5): ghi resumed_at, nhịp thôi 'off'", (gs.resumed_at(rv["agent_key"]) or 0) > 0
      and all(x["state"] != "off" for x in gs.pulse_rows(rv["agent_key"])), gs.pulse_rows(rv["agent_key"]))

# ═════ review T2 P1: nâng cấp không tính lịch sử lượt nhóm vào trần ═════
ceiling(20)
d = TEMP / "upgrade-history"
d.mkdir()
tm_u = team_store.TeamStore(d / "team.sqlite3")
old = time.time() - 30 * 86400
with closing(tm_u._conn()) as c:
    for i in range(30):
        c.execute("INSERT INTO team_turns(turn_id,team_id,agent_key,role,engine,model,status,engine_at,created_at,"
                  "settled_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (f"old{i}", "tm_x", "lan", "leader_reply", "e", "m", "ok",
                                                              old + i, old + i, old + i + 1))
    c.commit()
gs_u = RS.GoalStore(d / "resonance.sqlite3")          # mã mới mở kho lần đầu: ghi mốc t2_migrated_at
TC_u = AL.TeamCeiling(gs_u)
rep_u = TC_u.reconcile(tm_u, startup=True, now=time.time() + 300)
check("nâng cấp: 30 lượt nhóm đã chốt từ trước KHÔNG bị dựng lại, đã dùng vẫn 0",
      rep_u["rebuilt"] == 0 and AL.CallLedger(gs_u).used() == 0, rep_u)
check("lượt nhóm kế tiếp vẫn được giữ chỗ", TC_u.reserve({"turn_id": "new1", "team_id": "tm_x", "agent_key": "lan",
                                                         "role": "leader_reply"}))
with closing(gs_u._conn()) as c:
    check("đường kiểm trần của goal A2 vẫn cho chạy", RS._under_ceiling(c, 1))
rep_u2 = TC_u.reconcile(tm_u, now=time.time() + 3600)
check("đối soát theo nhịp sau đó cũng không dựng lại lịch sử", rep_u2["rebuilt"] == 0
      and not any(x["mirror_ref"].startswith("team_turn:old") for x in AL.CallLedger(gs_u).rows()), rep_u2)
d2 = TEMP / "upgrade-open"
d2.mkdir()
tm_o = team_store.TeamStore(d2 / "team.sqlite3")
with closing(tm_o._conn()) as c:
    c.execute("INSERT INTO team_turns(turn_id,team_id,agent_key,role,engine,model,status,engine_at,created_at) "
              "VALUES('open1','tm_y','lan','work','e','m','running',?,?)", (old, old))
    c.execute("INSERT INTO team_turns(turn_id,team_id,agent_key,role,status,engine_at,created_at,settled_at) "
              "VALUES('done1','tm_y','lan','work','ok',?,?,?)", (old, old, old + 5))
    c.commit()
gs_o = RS.GoalStore(d2 / "resonance.sqlite3")
tm_o.settle_turn("open1", "interrupted")              # phục hồi lúc khởi động chốt lượt còn mở lúc nâng
rep_o = AL.TeamCeiling(gs_o).reconcile(tm_o, startup=True, now=time.time() + 300)
check("lượt còn mở lúc nâng (đã tới mốc engine) được dựng lại và tính; lượt đã chốt trước đó thì không",
      rep_o["rebuilt"] == 1 and [x["mirror_ref"] for x in AL.CallLedger(gs_o).rows()] == ["team_turn:open1"]
      and AL.CallLedger(gs_o).rows()[0]["status"] == "interrupted", (rep_o, AL.CallLedger(gs_o).rows()))
gs_o2 = RS.GoalStore(d2 / "resonance.sqlite3")
with closing(gs_o2._conn()) as c:
    m1 = c.execute("SELECT value FROM ceiling_state WHERE name='t2_migrated_at'").fetchone()[0]
with closing(gs_o._conn()) as c:
    m0 = c.execute("SELECT value FROM ceiling_state WHERE name='t2_migrated_at'").fetchone()[0]
check("mốc t2_migrated_at ghi một lần, mở lại kho không ghi đè", m0 == m1)
ceiling(None)

# Đối soát có trần số dòng mỗi lần, quét tiếp từ mốc lần trước (không nạp cả bảng mỗi 60 giây).
gs, ss, ts, tm, fake, r, T = world("batch")
ids = []
for i in range(5):
    tid_b = tm.reserve_turn(T["team_id"], None, None, "lan", "leader_reply")
    tm.mark_engine(tid_b, "e", "m")
    tm.settle_turn(tid_b, "ok")
    ids.append(tid_b)
later = time.time() + AL.RECONCILE_GRACE_S + 60
p1 = r.ceiling.reconcile(tm, now=later, batch=2)
p2 = r.ceiling.reconcile(tm, now=later + 1, batch=2)
p3 = r.ceiling.reconcile(tm, now=later + 2, batch=2)
built = {x["mirror_ref"] for x in AL.CallLedger(gs).rows()}
check("đối soát theo lô: mỗi lần tối đa batch dòng, các lần sau quét tiếp, cuối cùng đủ cả 5, không trùng",
      p1["truncated"] and built == {f"team_turn:{x}" for x in ids} and len(AL.CallLedger(gs).rows()) == 5,
      (p1, p2, p3, built))

print(f"\n{'FAIL' if fails else 'PASS'} - test_agent_calls_ledger: {len(fails)} lỗi")
raise SystemExit(1 if fails else 0)
