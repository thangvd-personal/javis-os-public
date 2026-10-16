"""T3 (0.156.0): host của nhịp thích nghi (thiết kế 2026-10-15 mục 3.2-3.4, 5.1, 5.5, 9.1; GOAL 12 ca 7, 15, 23, 24).

    python tests/run.py agent_pulse_host -v

Kho thật (resonance.sqlite3, team.sqlite3, kanban, phiên chat tạm), đồng hồ GIẢ, engine GIẢ (đếm lời gọi). Không model thật:
- nhận lịch bằng CAS, không lượt đôi qua khởi động lại; bận thì dời 60 giây, không tốn lần thử;
- giữ chỗ sổ lượt TRƯỚC engine, thử khoá bận TRƯỚC giữ chỗ; hết hạn mức thì "Chờ hạn mức", không gọi model;
- tạm dừng/tắt/ngủ đông chỉ dừng phần chủ động: lời gọi trực tiếp và việc nhóm vẫn chạy (bộ chạy nhóm thật);
- hoạt động ở nhóm A, ngủ ở nhóm B; nội dung của A không vào lượt quét của B;
- quét: enqueue khi có quyền, không thì propose; cơ hội tiêu thụ một lần; quét giới hạn mỗi 3 lần thức, tối đa một/ngày;
- `say` luôn qua bộ phán xử; thiếu bộ phán xử thì `silent` (`judge_unavailable`), không gửi;
- điều phối lý do lớp 3 cho phần đăng ký; nhắc chờ chủ một lần sau 24 giờ.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import tempfile
from contextlib import closing
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-pulse-host-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-pulse-host-w-"))

import agent_ledger as AL  # noqa: E402
import agent_pulse as AP  # noqa: E402
import agent_pulse_host as H  # noqa: E402
import resonance_store as RS  # noqa: E402

fails = []
D = 86400.0
T0 = 2_000_000_000.0


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Engine:
    """Engine GIẢ cho lượt quét: ghi lại thứ tự (khoá bận, sổ lượt) lúc được gọi, trả chữ đặt sẵn."""

    def __init__(self, store, text='{"action": "none"}'):
        self.store = store
        self.text = text
        self.calls = []
        self.order = []
        self.fail = None

    def engine_for(self, key, ctx, purpose):
        async def run_(req):
            with closing(self.store._conn()) as c:
                live = [dict(r) for r in c.execute("SELECT status, obligation, purpose FROM agent_calls WHERE "
                                                   "reason_ref=?", (req["turn_id"],)).fetchall()]
            self.order.append(("engine", live))
            await req["engine_started"]()
            self.calls.append(req)
            if self.fail:
                raise self.fail
            return {"text": self.text(req) if callable(self.text) else self.text, "tokens_in": 3, "tokens_out": 4}
        return "fake", "fake-model", run_


_n = [0]


def world(n_agents=1, judge=None, budget=None, text='{"action": "none"}'):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}"
    d.mkdir()
    st = RS.GoalStore(d / "resonance.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    keys = []
    for i in range(n_agents):
        k = st.agent_set_enabled(owner, f"a{i}", True)["agent_key"]
        AP.set_state(st, k, "*", "on", at=T0)
        keys.append(k)
    clock = Clock(T0)
    eng = Engine(st, text)
    box = {"busy": set(), "opps": {}, "can_enqueue": False, "enqueued": [], "proposed": [], "sent": [], "notified": [],
           "try_busy": [], "contexts": {}}

    def try_busy(key, tok):
        box["try_busy"].append(key)
        eng.order.append(("try_busy", key))
        return key not in box["busy"]

    deps = H.PulseHostDeps(
        store=lambda: st, agents=lambda: [{"agent_key": k, "brain_id": str(d)} for k in keys],
        contexts=lambda key: box["contexts"].get(key, [f"dm:{key}"]), busy=lambda key: key in box["busy"],
        try_busy=try_busy, release_busy=lambda key, tok: None,
        opportunities=lambda key, ctx, iv: list(box["opps"].get((key, ctx), [])), persona=lambda key: "Bạn là trợ lý.",
        engine_for=eng.engine_for, can_enqueue=lambda key, ctx: box["can_enqueue"],
        enqueue=lambda key, ctx, item, src: box["enqueued"].append((key, ctx, item, src)) or "t_1",
        propose=lambda key, ctx, item, src: box["proposed"].append((key, ctx, item, src)) or "msg:9",
        send=lambda key, ctx, text, meta: box["sent"].append((key, ctx, text, meta)) or {"session_id": "s1",
                                                                                       "message_id": 77},
        notify=lambda key, ctx, text: box["notified"].append((key, ctx, text)),
        interacted=lambda p: False, clock=clock, spawn=asyncio.ensure_future)
    host = H.PulseHost(deps, judge=judge, budget=budget or (lambda key, purpose, at=None: (True, "")))
    return st, keys, clock, eng, box, host


async def tick_all(host):
    await host.tick()
    await host.wait_idle()


def due_now(st, key, ctx, t):
    with st._Tx(st) as c:
        c.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=? AND context_ref=?", (t, key, ctx))


def opp(src, ver="1", kind="stale_job", summary="Việc cũ chưa cập nhật"):
    return {"source_ref": src, "version": ver, "kind": kind, "summary": summary}


# ═════ CAS: hai host cùng kho, một lịch chỉ một lượt; khởi động lại không chạy lần hai ═════
st, (k,), clock, eng, box, h1 = world()
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("task:t1")]
h2 = H.PulseHost(h1.deps, budget=h1.budget)
clock.t = T0 + D + 1
due_now(st, k, ctx, T0 + D)
c1, _ = h1._code_pass(clock.t)
c2, _ = h2._code_pass(clock.t)
check("CAS: hai host cùng kho, cùng lịch: chỉ một host nhận", len(c1) == 1 and c2 == [], (c1, c2))
# Host 1 "chết" ngay sau khi lượt đã tới model: dòng sổ đang chạy, lịch còn giữ.
ref = h1._due_ref(c1[0])
cid = AL.CallLedger(st).reserve(purpose="scan", reason_ref=ref, agent_key=k, context_ref=ctx, now=clock.t)
AL.CallLedger(st).mark_engine(cid, "fake", "m")
clock.t += AP.CLAIM_S + 5
c3, _ = h2._code_pass(clock.t)
check("khởi động lại: lịch hết hạn giữ thì host khác nhận lại đúng lịch đó", len(c3) == 1
      and h2._due_ref(c3[0]) == ref, c3)
out = run(h2.wake_step(c3[0]))
check("khởi động lại: lượt của lịch đã tới model thì không gọi engine lần hai", out["outcome"] == "already_ran"
      and eng.calls == [], out)
row = AP.row(st, k, ctx)
check("khởi động lại: lịch tính lại từ bây giờ, nhả quyền giữ", row["next_due_at"] > clock.t and not row["claim_token"],
      row)

# ═════ thứ tự: khoá bận trước, giữ chỗ sổ lượt trước engine (obligation = 0, purpose scan) ═════
st, (k,), clock, eng, box, host = world()
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("task:t1")]
clock.t = T0 + D + 1
run(tick_all(host))
check("lượt quét gọi engine đúng một lần", len(eng.calls) == 1, eng.calls)
check("thử khoá bận TRƯỚC khi engine chạy", eng.order[0] == ("try_busy", k), eng.order)
live = eng.order[1][1]
check("dòng sổ lượt đã giữ chỗ TRƯỚC engine: reserved, obligation 0, purpose scan",
      live and live[0]["status"] == "reserved" and live[0]["obligation"] == 0 and live[0]["purpose"] == "scan", live)
rows = AL.CallLedger(st).rows(reason_ref=host._due_ref({"agent_key": k, "context_ref": ctx, "due_at": T0 + D}))
check("sổ lượt chốt ok kèm token", len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["tokens_out"] == 4, rows)
check("lời nhắc quét có danh sách cơ hội và luật trả JSON", "task:t1" in eng.calls[0]["prompt"]
      and '"action"' in eng.calls[0]["system"], eng.calls[0])

# ═════ bận: dời 60 giây, không tốn lần thử, không giữ chỗ ═════
st, (k,), clock, eng, box, host = world()
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("task:t1")]
box["busy"].add(k)
clock.t = T0 + D + 1
run(tick_all(host))
row = AP.row(st, k, ctx)
check("effective_due: agent đang có lượt nhóm/A2 thì không nhận lịch, dời đúng 60 giây",
      row["next_due_at"] == clock.t + 60 and eng.calls == [] and not row["claim_token"], row)
with closing(st._conn()) as c:
    check("bận: không có dòng sổ lượt nào", c.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0] == 0)
# Bận ngay lúc thử khoá (sau khi đã nhận lịch): không tốn lần thử, cơ hội chưa tiêu thụ.
box["busy"].clear()
clock.t += 61
cl, _ = host._code_pass(clock.t)
box["busy"].add(k)
out = run(host.wake_step(cl[0]))
with closing(st._conn()) as c:
    n_calls = c.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0]
    n_cons = c.execute("SELECT COUNT(*) FROM pulse_opportunities WHERE consumed_at IS NOT NULL").fetchone()[0]
check("bận lúc thử khoá: dời 60 giây, không giữ chỗ, cơ hội chưa tiêu thụ", out["outcome"] == "busy" and n_calls == 0
      and n_cons == 0 and AP.row(st, k, ctx)["next_due_at"] == clock.t + 60, out)

# ═════ hạn mức: hết agent_daily_calls hay trần chung thì Chờ hạn mức, không gọi model ═════
st, (k,), clock, eng, box, host = world(budget=lambda key, purpose, at=None: (False, "agent_daily_calls"))
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("task:t1")]
clock.t = T0 + D + 1
run(tick_all(host))
row = AP.row(st, k, ctx)
check("hết hạn mức ngày: Chờ hạn mức, không gọi engine, không giữ chỗ", row["quota_wait"] == 1 and eng.calls == []
      and AL.CallLedger(st).rows() == [], row)
os.environ["JAVIS_RESONANCE_CALL_CEILING"] = "1"
st, (k,), clock, eng, box, host = world()
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("task:t1")]
clock.t = T0 + D + 1
run(tick_all(host))
check("trần chung: lượt tự phát chỉ tới floor(trần x 0.8) = 0, Chờ hạn mức, không gọi engine",
      AP.row(st, k, ctx)["quota_wait"] == 1 and eng.calls == [], AP.events(st, k, ctx, kinds=("quota",)))
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

# ═════ quét: enqueue khi có quyền, propose khi không; cơ hội tiêu thụ một lần ═════
st, (k,), clock, eng, box, host = world(text='{"action": "enqueue", "source_ref": "task:t1", "title": "Cập nhật việc", '
                                             '"brief": "Hỏi tiến độ"}')
ctx = "team:tA"
box["contexts"][k] = [ctx]
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("task:t1")]
clock.t = T0 + D + 1
run(tick_all(host))
check("không có quyền: enqueue đổi thành propose (thẻ đề xuất có nguồn)", box["enqueued"] == []
      and len(box["proposed"]) == 1 and box["proposed"][0][3] == "task:t1", box["proposed"])
ev = AP.events(st, k, ctx, kinds=("scan",))[0]
check("sự kiện quét ghi hành động và nguồn", ev["payload"]["result"]["action"] == "propose"
      and ev["payload"]["result"]["downgraded"] == "no_permission" and ev["payload"]["opportunities"] == ["task:t1"], ev)
clock.t += D + 1
due_now(st, k, ctx, clock.t - 1)
run(tick_all(host))
check("cơ hội đã tiêu thụ không mở lượt quét lần hai (cùng phiên bản)", len(eng.calls) == 1, len(eng.calls))
props = AP.proposals(st, k)
check("đề xuất của lượt quét vào sổ đề xuất: nguồn, phiên bản, chờ chủ", len(props) == 1 and props[0]["source_ref"]
      == "task:t1" and props[0]["version"] == "1" and props[0]["status"] == "pending", props)
with st._Tx(st) as c:   # mô phỏng cơ hội hiện lại cùng phiên bản (vd. sổ cơ hội mất): đề xuất vẫn không mở lại
    c.execute("DELETE FROM pulse_opportunities")
clock.t += D + 1
due_now(st, k, ctx, clock.t - 1)
run(tick_all(host))
res = AP.events(st, k, ctx, kinds=("scan",))[0]["payload"]["result"]
check("không mở lại đề xuất khi không có dữ kiện mới (cùng nguồn, cùng phiên bản)", res.get("reason") == "already_proposed"
      and len(box["proposed"]) == 1, res)
box["opps"][(k, ctx)] = [opp("task:t1", ver="2")]
box["can_enqueue"] = True
clock.t += D + 1
due_now(st, k, ctx, clock.t - 1)
run(tick_all(host))
check("nguồn có phiên bản mới: quét lại; có quyền thì enqueue qua hàng đợi chung, có nguồn",
      len(eng.calls) == 3 and len(box["enqueued"]) == 1 and box["enqueued"][0][3] == "task:t1", box["enqueued"])
st, (k,), clock, eng, box, host = world(text='{"action": "none"}')
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("goal:g1", kind="goal_waiting")]
clock.t = T0 + D + 1
run(tick_all(host))
with closing(st._conn()) as c:
    cons = c.execute("SELECT consumed_at FROM pulse_opportunities").fetchone()["consumed_at"]
check("lượt quét trả none vẫn tiêu thụ cơ hội", cons is not None)

# ═════ quét giới hạn: mỗi 3 lần thức ở active/awake, tối đa một/ngày; không nguồn thì không tạo việc ═════
st, (k,), clock, eng, box, host = world(text='{"action": "propose", "title": "Tự nghĩ việc", "brief": "x"}')
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
with st._Tx(st) as c:   # có tương tác gần đây để nhịp không chậm lại vì ít nhu cầu trong lúc đếm
    pass
outs = []
for i in range(1, 4):
    AP.record_reaction(st, source_ref=f"rx:dm:{i}", agent_key=k, context_ref=ctx, value="like", at=T0 + i * D - 10)
    clock.t = T0 + i * D + 1
    due_now(st, k, ctx, clock.t - 1)
    cl, _ = host._code_pass(clock.t)
    outs.append(run(host.wake_step(cl[0]))["outcome"])
check("quét giới hạn: lần thức 1, 2 không gọi model; lần 3 quét", outs == ["no_action", "no_action", "scanned"], outs)
check("quét giới hạn không có nguồn: không tạo việc (none, no_source)", box["proposed"] == []
      and AP.events(st, k, ctx, kinds=("scan",))[0]["payload"]["result"]["reason"] == "no_source")
AP.record_reaction(st, source_ref="rx:dm:9", agent_key=k, context_ref=ctx, value="like", at=clock.t + 10)
for j in range(3):
    clock.t += 3600
    due_now(st, k, ctx, clock.t - 1)
    with st._Tx(st) as c:
        c.execute("UPDATE agent_pulse SET scan_counter=5 WHERE agent_key=?", (k,))
    cl, _ = host._code_pass(clock.t)
    run(host.wake_step(cl[0]))
check("quét giới hạn tối đa một lần mỗi ngày mỗi ngữ cảnh", len(eng.calls) == 1, len(eng.calls))

# ═════ say: luôn qua bộ phán xử; thiếu bộ phán xử thì silent, không gửi ═════
import sys as _sys  # noqa: E402


def say_world():
    st_, (k_,), clock_, eng_, box_, host_ = world(
        text='{"action": "say", "source_ref": "goal:g1", "text": "Anh xem giúp mục tiêu"}')
    ctx_ = f"dm:{k_}"
    AP.ensure_row(st_, k_, ctx_, at=T0)
    box_["opps"][(k_, ctx_)] = [opp("goal:g1", kind="goal_waiting")]
    clock_.t = T0 + D + 1
    run(tick_all(host_))
    return AP.events(st_, k_, ctx_, kinds=("scan",))[0]["payload"]["result"], box_


# Thiếu hẳn module phán xử (bản P đứng một mình): giả lập bằng cách chặn import.
_saved_judge = _sys.modules.get("agent_judge")
_sys.modules["agent_judge"] = None
res, box = say_world()
if _saved_judge is not None:
    _sys.modules["agent_judge"] = _saved_judge
else:
    _sys.modules.pop("agent_judge", None)
check("chưa có agent_judge: say thành silent (judge_unavailable), không gửi gì",
      res == {"action": "say", "verdict": "silent", "reason": "judge_unavailable", "decision_id": None}
      and box["sent"] == [], res)
# Có module (sau khi ghép phần J) nhưng chưa được main.py nối: lỗi phán xử cũng là silent có lý do, không gửi gì.
res, box = say_world()
check("có agent_judge mà chưa nối (lỗi phán xử): say thành silent có lý do, không gửi gì",
      res.get("verdict") == "silent" and str(res.get("reason", "")).startswith(("judge_error", "judge_unavailable"))
      and box["sent"] == [], res)
judged = []


async def judge_speak(**kw):
    judged.append(kw)
    return {"decision_id": "d1", "verdict": "speak", "score": 0.9, "threshold": 0.5, "reason": "ok"}

st, (k,), clock, eng, box, host = world(judge=judge_speak,
                                        text='{"action": "say", "source_ref": "goal:g1", "text": "Anh xem giúp"}')
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("goal:g1", kind="goal_waiting")]
clock.t = T0 + D + 1
run(tick_all(host))
check("bộ phán xử chọn nói: gửi đúng một tin, nhận nháp, ngữ cảnh, nguồn; KHÔNG truyền engine (bộ phán xử dùng đường hỏi "
      "của chính nó, review T3 J P1)",
      len(box["sent"]) == 1 and judged and judged[0]["draft"] == "Anh xem giúp" and judged[0]["context_ref"] == ctx
      and judged[0]["source_event"].startswith("pulse:") and "engine" not in judged[0]
      and judged[0].get("origin") == "pulse" and (judged[0].get("payload") or {}).get("source_ref") == "goal:g1",
      (box["sent"], judged))
with closing(st._conn()) as c:
    pro = [dict(r) for r in c.execute("SELECT * FROM pulse_proactive").fetchall()]
check("tin chủ động được ghi để đo 'đã xem nhưng bỏ qua'", len(pro) == 1 and pro[0]["message_id"] == 77, pro)


async def judge_wait(**kw):
    return {"decision_id": "d2", "verdict": "wait", "reason": "người khác đang nói"}

st, (k,), clock, eng, box, host = world(judge=judge_wait, text='{"action": "say", "text": "x"}')
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
box["opps"][(k, ctx)] = [opp("goal:g1")]
clock.t = T0 + D + 1
run(tick_all(host))
check("bộ phán xử chọn chờ: không gửi", box["sent"] == [])


async def judge_boom(**kw):
    raise RuntimeError("judge down")

st, (k,), clock, eng, box, host = world(judge=lambda **kw: H._judge_proactive(**kw), text='{"action": "say", "text": "x"}')
check("bộ chuyển phán xử: thiếu module thì silent", run(H._judge_proactive(agent_key="a", context_ref="dm:a", draft="x",
                                                                          source_event="s", engine=None))["verdict"]
      == "silent")

# ═════ tạm dừng, tắt, ngủ đông chỉ dừng phần chủ động ═════
st, (k,), clock, eng, box, host = world()
for ctx, state in (("team:tP", "paused"), ("team:tH", "hibernating"), (f"dm:{k}", "awake")):
    AP.ensure_row(st, k, ctx, at=T0)
    box["opps"][(k, ctx)] = [opp(f"task:{ctx}")]
AP.set_state(st, k, "team:tP", "paused", at=T0)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=8, state='hibernating', next_due_at=NULL WHERE context_ref='team:tH'")
    c.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=?", (T0 + 10, k))
clock.t = T0 + D + 1
cl, _ = host._code_pass(clock.t)
check("tạm dừng và ngủ đông: không nhận lịch chủ động; ngữ cảnh khác vẫn chạy",
      [c["context_ref"] for c in cl] == [f"dm:{k}"], cl)
AP.set_state(st, k, "*", "off", at=clock.t)
clock.t += D
due_now(st, k, f"dm:{k}", clock.t - 1)
cl, _ = host._code_pass(clock.t)
check("tắt Cộng hưởng: không ngữ cảnh nào nhận lịch", cl == [])
check("tắt Cộng hưởng: lời gọi trực tiếp không tự bật lại nhịp", AP.wake(st, k, "team:tH", "direct_call", at=clock.t)
      is False and AP.row(st, k, "team:tH")["state"] == "off")

# Nghĩa vụ không bị lớp 3 làm chậm: bộ chạy nhóm thật, engine giả, agent đang tạm dừng / ngủ đông / tắt nhịp.
import sessions  # noqa: E402
import task_store  # noqa: E402
import team_runner as TR  # noqa: E402
import team_store  # noqa: E402


class TeamFake:
    def __init__(self):
        self.calls = []

    def engine_for(self, team, key, role):
        async def run_(req):
            await req["engine_started"]()
            self.calls.append(role)
            return {"text": "Xong phần việc."}
        return "fake", "fake", run_


for state in ("paused", "hibernating", "off"):
    d = TEMP / f"oblig-{state}"
    d.mkdir()
    gs = RS.GoalStore(d / "resonance.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    ag = gs.agent_set_enabled(owner, "viet", True)
    key = ag["agent_key"]
    ss = sessions.SessionStore(db_path=d / "chat.db")
    ts = task_store.TaskStore(d / "kanban.sqlite3")
    tm = team_store.TeamStore(d / "team.sqlite3")
    tf = TeamFake()
    r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=tf.engine_for, publish=lambda e: None,
                      agent_info=lambda root, kk: {"slug": "viet", "name": "Viết", "system": ""})
    tid = team_store.new_team_id()
    tsid = ss.create_session(brain=str(d), channel=f"team:{tid}")
    tm.create_team(str(d), "Đội", "", tsid, team_id=tid)
    tm.add_member(tid, key, "viet")
    tm.set_leader(tid, key)
    ctx = f"team:{tid}"
    AP.ensure_row(gs, key, ctx)
    if state == "paused":
        AP.set_state(gs, key, ctx, "paused")
    elif state == "hibernating":
        with gs._Tx(gs) as c:
            c.execute("UPDATE agent_pulse SET tier=8, state='hibernating', next_due_at=NULL WHERE context_ref=?", (ctx,))
    else:
        AP.set_state(gs, key, "*", "off")
    sub = r.submit(tid, "Viết ơi, xem giúp", mentions=[key], client_msg_id="m1")
    asyncio.run(r.run_replies(sub))
    job = r.submit(tid, "Soạn kế hoạch", as_job=True, client_msg_id="j1")["task_id"]

    async def go():
        await r.dispatch_once(wait=True)
    asyncio.run(go())
    check(f"nhịp '{state}': lời gọi trực tiếp vẫn được trả lời và việc nhóm vẫn chạy (nghĩa vụ không chậm)",
          tf.calls[:1] == ["leader_reply"] and len(tf.calls) >= 2 and ts.get_task(job)["status"] == "done",
          (tf.calls, ts.get_task(job)["status"]))
    check(f"nhịp '{state}': trạng thái nhịp giữ nguyên sau nghĩa vụ", AP.row(gs, key, ctx)["state"]
          == {"paused": "paused", "hibernating": "hibernating", "off": "off"}[state])

# ═════ hoạt động ở nhóm A, ngủ ở nhóm B; không rò nội dung ═════
st, (k,), clock, eng, box, host = world(text='{"action": "none"}')
A, B = "team:tA", "team:tB"
box["contexts"][k] = [A, B]
host._code_pass(T0)
check("ngữ cảnh được tạo theo phạm vi đã giao (mỗi nhóm một dòng)", {r["context_ref"] for r in AP.rows(st, k)}
      >= {A, B})
i = 0
clock.t = T0
end = T0 + 30 * D
while clock.t < end:
    clock.t += 3600
    if clock.t < T0 + 30 * D and int((clock.t - T0) / 3600) % 8 == 0:
        i += 1
        AP.record_reaction(st, source_ref=f"rx:team:{i}", agent_key=k, context_ref=A, value="love", at=clock.t - 60)
        box["opps"][(k, A)] = [opp(f"task:a{i}", summary="BÍ MẬT CỦA NHÓM A")]
    cl, _ = host._code_pass(clock.t)
    for c_ in cl:
        run(host.wake_step(c_))
ta, tb = AP.row(st, k, A)["tier"], AP.row(st, k, B)["tier"]
check("cùng agent sau 30 ngày: hoạt động ở nhóm A (nhanh hơn B3), ngủ/chậm ở nhóm B (chậm hơn B3)", ta <= 2 and tb >= 5, (ta, tb))
check("lượt quét của B không chứa nội dung của A", all("BÍ MẬT" not in q["prompt"] for q in eng.calls
                                                         if q["context_ref"] == B))
check("tín hiệu của A không vào ngữ cảnh B", all(s["context_ref"] == A for s in AP.signals(st, k)
                                                if s["kind"] != "context_init"))

# ═════ điều phối lý do lớp 3 cho phần đã đăng ký; tạm dừng thì không ═════
st, (k, k2), clock, eng, box, host = world(n_agents=2)
served = []


async def fake_review(h, reason):
    try_b, rel_b = h.busy_pair(reason["agent_key"])
    got = AL.WakeReasons(st).claim(reason["id"], try_busy=try_b, release_busy=rel_b, purpose="reaction_review")
    served.append((reason["code"], got["outcome"]))
    if got["outcome"] == "claimed":
        AL.WakeReasons(st).settle(reason["id"], "ok", consumed=got["sources"])
        rel_b()

H.register_reason_handler("reaction_review", fake_review)
W = AL.WakeReasons(st)
W.add_source(k, f"dm:{k}", "reaction_review", "rx:dm:1")
W.add_source(k2, f"dm:{k2}", "reaction_review", "rx:dm:2")
W.add_source(k, f"dm:{k}", "related_review", "rel:1")
AP.ensure_row(st, k2, f"dm:{k2}", at=T0)
AP.set_state(st, k2, f"dm:{k2}", "paused", at=T0)
clock.t = T0 + 10
run(tick_all(host))
check("lý do có phần đăng ký được điều phối và phục vụ (claim qua khoá bận của host)",
      served == [("reaction_review", "claimed")], served)
check("lý do của agent tạm dừng không được điều phối; loại chưa ai đăng ký nằm pending",
      W.reasons(agent_key=k2)[0]["status"] == "pending" and W.reasons(agent_key=k, code="related_review")[0]["status"]
      == "pending")
del H._REASON_HANDLERS["reaction_review"]

# ═════ chờ chủ: nhắc một lần sau 24 giờ rồi giữ nguyên ═════
st, (k,), clock, eng, box, host = world()
ctx = f"dm:{k}"
AP.ensure_row(st, k, ctx, at=T0)
AP.set_state(st, k, ctx, "waiting_owner", at=T0)
clock.t = T0 + D - 10
run(tick_all(host))
clock.t = T0 + D + 10
run(tick_all(host))
clock.t = T0 + 3 * D
run(tick_all(host))
check("chờ chủ: nhắc đúng một lần sau 24 giờ, trạng thái giữ nguyên", len(box["notified"]) == 1
      and AP.row(st, k, ctx)["state"] == "waiting_owner", box["notified"])

# ═════ khung view: sổ đăng ký mục của J/L ═════
H.register_view_part("judge", lambda store, key: {"decisions": [], "agent": key})
H.register_view_part("broken", lambda store, key: 1 / 0)
v = H.view(st, k)
check("view: mục của phần đăng ký có mặt; mục lỗi không làm hỏng trang",
      v["parts"]["judge"] == {"decisions": [], "agent": k} and "error" in v["parts"]["broken"]
      and v["contexts"][0]["context_ref"] == ctx, v["parts"])
del H._VIEW_PARTS["judge"], H._VIEW_PARTS["broken"]

# ═════ đường thật qua main.py: engine thật bị chặn (fail fast), route nhịp, POST /seen ═════
BR = (TEMP / "brain-main").resolve()
(BR / "agents").mkdir(parents=True)
(BR / "agents" / "viet.md").write_text("---\nname: Viết\nrole: Viết bài\n---\nBạn là Viết.\n", encoding="utf-8",
                                       newline="\n")
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

built = []


def boom(*a, **kw):
    built.append(a)
    raise AssertionError("test không được dựng engine thật")


main._pulse_engine_for = boom
main._team_engine_for = boom
main._resonance_engine = boom
main._TEAM_API.deps.spawn = lambda coro: coro.close()
client = TestClient(main.app, base_url="http://127.0.0.1:8080")
B = str(BR)
r0 = client.get("/agents/viet/pulse", params={"brain": B})
check("GET pulse: trợ lý chưa có mã thì khung rỗng, không tạo kho", r0.status_code == 200 and r0.json()["agent_key"]
      is None, r0.text)
check("GET pulse: trợ lý không có thì 404", client.get("/agents/khong-co/pulse", params={"brain": B}).status_code == 404)
rt = client.post("/resonance/agents/toggle", params={"brain": B}, json={"slug": "viet", "enabled": True})
key = rt.json()["agent"]["agent_key"]
mst = main._resonance_store()
mctx = f"dm:{key}"
main._pulse_opportunities = lambda k_, c_, iv: ([opp("goal:gx", kind="goal_waiting")] if c_ == mctx else [])
main._PULSE_HOST[0] = None
AP.ensure_row(mst, key, mctx)
due_now(mst, key, mctx, 1.0)


async def main_tick():
    await main._pulse_tick()
    await main._pulse_host().wait_idle()

run(main_tick())
calls_rows = AL.CallLedger(mst).rows()
check("main.py: lượt quét tới chỗ dựng engine thì engine thật bị chặn ngay (fail fast), không lời gọi model nào",
      len(built) == 1 and all(r_["status"] == "refunded" for r_ in calls_rows) and len(calls_rows) == 1, calls_rows)
check("main.py: lỗi dựng engine ghi scan_error, lịch tính lại", AP.events(mst, key, mctx, kinds=("scan_error",))
      and AP.row(mst, key, mctx)["next_due_at"] > 1.0)
v = client.get("/agents/viet/pulse", params={"brain": B}).json()
check("GET pulse: trạng thái và bậc theo từng ngữ cảnh, Cộng hưởng bật", v["ok"] and v["resonance"] is True
      and any(c_["context_ref"] == mctx and c_["tier"] == 3 for c_ in v["contexts"]) and "parts" in v, v)
rp = client.post("/agents/viet/pulse/pause", params={"brain": B}, json={"context_ref": mctx})
check("POST pause: 200 sau khi kho đã ghi, khung mới trả kèm", rp.status_code == 200
      and next(c_ for c_ in rp.json()["view"]["contexts"] if c_["context_ref"] == mctx)["state"] == "paused", rp.text)
check("POST wake khi tạm dừng: 409 (không có gì để đổi), không báo thành công giả",
      client.post("/agents/viet/pulse/wake", params={"brain": B}, json={"context_ref": mctx}).status_code == 409)
rr = client.post("/agents/viet/pulse/resume", params={"brain": B}, json={"context_ref": mctx})
check("POST resume: về trạng thái theo bậc", rr.status_code == 200 and AP.row(mst, key, mctx)["state"] == "awake")
check("POST reset thiếu ngữ cảnh: 400", client.post("/agents/viet/pulse/reset", params={"brain": B},
                                                     json={}).status_code == 400)
check("POST reset: 200", client.post("/agents/viet/pulse/reset", params={"brain": B},
                                      json={"context_ref": mctx}).status_code == 200)
check("POST max_tier B0 từ route của chủ: 200", client.post("/agents/viet/pulse/max_tier", params={"brain": B},
                                                             json={"tier": 0}).status_code == 200
      and AP.row(mst, key, mctx)["max_tier"] == 0)
ss = main.get_store()
sid = ss.create_session(brain=B, channel="agent:viet")
mid_a = ss.append_message(sid, "assistant", "Tin của Viết")
mid_u = ss.append_message(sid, "user", "Tin của chủ")
rs = client.post("/seen", json={"context_ref": f"session:{sid}", "message_ids": [mid_a, mid_u]})
check("POST /seen: chỉ tin của agent được ghi, lần đầu", rs.status_code == 200 and rs.json()["recorded"] == 1
      and AP.seen_at(mst, mctx, mid_a) is not None and AP.seen_at(mst, mctx, mid_u) is None, rs.text)
rs2 = client.post("/seen", json={"context_ref": f"session:{sid}", "message_ids": [mid_a]})
check("POST /seen lần hai: không ghi lại", rs2.json()["recorded"] == 0)
check("POST /seen thân sai: 400", client.post("/seen", json={"context_ref": "", "message_ids": []}).status_code == 400)
check("POST /seen ngữ cảnh chat riêng của agent khác: không ghi",
      client.post("/seen", json={"context_ref": "dm:ag_khac", "message_ids": [mid_a], "session_id": sid})
      .json()["recorded"] == 0)
pdm = AP.record_proposal(mst, agent_key=key, context_ref=mctx, source_ref="goal:gx", version="1", title="Hỏi lại mục tiêu",
                         brief="Mục tiêu chờ bạn đã hai ngày", session_id=sid)
ra = client.post(f"/agents/viet/pulse/proposals/{pdm}/accept", params={"brain": B})
tasks_now = [t_ for t_ in main.tasks_feature.store.list_tasks(B) if f"pp{pdm}" in str(t_.get("idempotency_key") or "")]
check("POST proposals/<id>/accept (chat riêng): 200 sau khi việc đã xếp qua hàng đợi chung, việc gắn trợ lý, báo về chat riêng",
      ra.status_code == 200 and ra.json()["result"]["task_id"] and len(tasks_now) == 1
      and tasks_now[0]["chat_id"] == f"web:{sid}" and AP.proposal(mst, pdm)["status"] == "accepted", (ra.text, tasks_now))
check("chấp nhận đề xuất: +0.6 cho trợ lý", [s_["value"] for s_ in AP.signals(mst, key)
                                             if s_["source_ref"] == f"accept:pp:{pdm}"] == [0.6])
check("quyết lại đề xuất đã quyết: 409, không xếp thêm việc",
      client.post(f"/agents/viet/pulse/proposals/{pdm}/reject", params={"brain": B}).status_code == 409)
pdr = AP.record_proposal(mst, agent_key=key, context_ref=mctx, source_ref="goal:gy", version="1", title="Đề xuất khác")
rr2 = client.post(f"/agents/viet/pulse/proposals/{pdr}/reject", params={"brain": B})
check("POST proposals/<id>/reject: 200, -0.4, khung mới có trạng thái Đã bác",
      rr2.status_code == 200 and [s_["value"] for s_ in AP.signals(mst, key) if s_["source_ref"] == f"reject:pp:{pdr}"]
      == [-0.4] and any(p_["id"] == pdr and p_["status"] == "rejected" for p_ in rr2.json()["view"]["proposals"]),
      rr2.text)
check("không engine thật nào được dựng trong cả phần main.py", len(built) == 1, built)

print(f"\n{len(fails)} FAIL" if fails else "\nALL OK")
raise SystemExit(1 if fails else 0)
