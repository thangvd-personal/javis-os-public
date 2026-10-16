"""T2 (0.154.0): phản hồi tạo lý do thức đúng luật Cộng hưởng (GOAL 12 ca 5, 25; thiết kế 2026-10-15 mục 2, 3.3, 4.3).

    python tests/run.py feedback_wake -v

Kho thật trên SQLite tạm, bộ chạy nhóm thật với engine GIẢ (đếm lượt model: phải luôn bằng 0 vì reaction không gọi model).
- Cộng hưởng tắt: `stored_only`, không lý do nào (chat riêng và nhóm).
- Cộng hưởng bật: N lần bấm gom thành MỘT lý do, 0 lượt model; ba lần đổi trên một tin vẫn một lý do một nguồn.
- Bật lại không phát lại phản hồi cũ (kể cả dòng còn chờ chiếu từ lúc tắt); phản hồi mới sau mốc thì có lý do.
- Tin gắn goal: chỉ lý do `feedback` của goal ở A2, không lý do lớp 3 (không ba lượt cho một sự kiện).
- Emoji không đổi trạng thái việc hay goal, không đổi ưu tiên, không cấp gì (cả chat riêng).
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import tempfile
import time
from contextlib import closing
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fb-wake-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-fb-wake-w-"))

import agent_ledger as AL  # noqa: E402
import feedback_projector as FP  # noqa: E402
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


class Fake:
    def __init__(self):
        self.calls = []

    def engine_for(self, team, key, role):
        async def run_(req):
            self.calls.append(req)
            return {"text": "ok"}
        return "fake", "fake", run_


_n = [0]


def world(label, enabled):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}-{label}"
    d.mkdir()
    gs = RS.GoalStore(d / "resonance.sqlite3")
    ss = sessions.SessionStore(db_path=d / "chat.db")
    ts = task_store.TaskStore(d / "kanban.sqlite3")
    tm = team_store.TeamStore(d / "team.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    ag = gs.agent_register(owner, "viet")
    if enabled:
        ag = gs.agent_set_enabled(owner, "viet", True)
    fake = Fake()
    runner_box = {}
    pj = FP.FeedbackProjector(resonance=lambda: gs, sessions=lambda: ss, teams=lambda: tm,
                              team_view=lambda t, s, m: runner_box["r"].team_message_view(t, s, m))
    r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=fake.engine_for, publish=lambda e: None,
                      agent_info=lambda root, key: {"slug": key, "name": key, "system": ""}, projector=pj)
    runner_box["r"] = r
    tid = team_store.new_team_id()
    tsid = ss.create_session(brain=str(d), channel=f"team:{tid}")
    tm.create_team(str(d), "Đội", "", tsid, team_id=tid)
    tm.add_member(tid, ag["agent_key"], "viet")
    tm.set_leader(tid, ag["agent_key"])
    dsid = ss.create_session(brain=str(d), channel="agent:viet")
    return d, gs, ss, ts, tm, owner, ag, fake, pj, r, tm.get(tid), dsid


def dm(ss, pj, sid, mid, ag, nonce, value="like", goal_id=""):
    got = ss.set_dm_reaction(sid, mid, value=value, nonce=nonce, agent_key=ag["agent_key"], goal_id=goal_id)
    pj.project("dm", got["log_id"])
    return got


def team_msg(ss, T, author, text):
    return ss.append_team_message(T["session_id"], "assistant", text, author_kind="agent", author_ref=author)[0]


# ═════ Cộng hưởng tắt ═════
d, gs, ss, ts, tm, owner, ag, fake, pj, r, T, dsid = world("off", enabled=False)
W = AL.WakeReasons(gs)
mids = [ss.append_message(dsid, "assistant", f"Câu {i}") for i in range(3)]
for i, mid in enumerate(mids):
    dm(ss, pj, dsid, mid, ag, f"off{i}", "love")
tmid = team_msg(ss, T, ag["agent_key"], "Tin nhóm")
r.react(T["team_id"], tmid, "angry", reasons=["wrong_facts"], nonce="toff")
check("Cộng hưởng tắt (chat riêng): 'Đã nhận, chỉ lưu'",
      all(ss.dm_reaction_of(dsid, m)["status"] == "stored_only" for m in mids))
check("Cộng hưởng tắt (nhóm): stored_only", tm.reaction_of(T["session_id"], tmid)["status"] == "stored_only")
check("Cộng hưởng tắt: KHÔNG lý do thức nào, không lượt model", W.reasons() == [] and fake.calls == []
      and AL.CallLedger(gs).rows() == [])

# Một dòng còn chờ chiếu từ lúc tắt (bộ chiếu chưa chạy), rồi bật lại.
late_mid = ss.append_message(dsid, "assistant", "Câu chờ chiếu")
pending = ss.set_dm_reaction(dsid, late_mid, value="sad", nonce="off-pending", agent_key=ag["agent_key"])
time.sleep(0.01)
gs.agent_set_enabled(owner, "viet", True)
check("bật lại: ghi resumed_at vào agent_pulse", (gs.resumed_at(ag["agent_key"]) or 0) > 0)
pj.project_pending()
check("bật lại KHÔNG phát lại: phản hồi trước mốc (kể cả dòng còn chờ chiếu) không tạo lý do",
      W.reasons() == [] and ss.dm_reaction_of(dsid, late_mid)["status"] == "stored_only"
      and ss.dm_reaction_log_row(pending["log_id"])["effect_state"] == "done")
late_why = ss.dm_reaction_of(dsid, late_mid)["status_reason"]
check("smoke T2: phản hồi có trước lúc bật lại ghi đúng lý do (không nói Cộng hưởng đang tắt)",
      ("trước lúc bật lại" in late_why or "before Resonance" in late_why) and "đang tắt" not in late_why, late_why)
new_mid = ss.append_message(dsid, "assistant", "Câu sau khi bật")
dm(ss, pj, dsid, new_mid, ag, "on-new", "love")
rs = W.reasons()
check("phản hồi mới sau mốc bật lại: đúng một lý do một nguồn", len(rs) == 1 and len(W.sources(rs[0]["id"])) == 1, rs)
gs.agent_set_enabled(owner, "viet", False)
check("tắt Cộng hưởng: nhịp về off", any(p["state"] == "off" for p in gs.pulse_rows(ag["agent_key"])))

# ═════ Cộng hưởng bật: gom, không gọi model ═════
d, gs, ss, ts, tm, owner, ag, fake, pj, r, T, dsid = world("on", enabled=True)
W = AL.WakeReasons(gs)
mids = [ss.append_message(dsid, "assistant", f"Đoạn {i}") for i in range(6)]
for i, mid in enumerate(mids):
    dm(ss, pj, dsid, mid, ag, f"on{i}", ("like", "love", "haha", "wow", "sad", "angry")[i])
rs = W.reasons(agent_key=ag["agent_key"])
check("6 lần bấm trên 6 tin: MỘT lý do chờ gom 6 nguồn", len(rs) == 1 and rs[0]["status"] == "pending"
      and len(W.sources(rs[0]["id"])) == 6, rs)
check("không lượt model nào, không dòng sổ lượt nào", fake.calls == [] and AL.CallLedger(gs).rows() == [])
check("mọi reaction: 'Chờ xem xét'", all(ss.dm_reaction_of(dsid, m)["status"] == "pending_review" for m in mids))
one = ss.append_message(dsid, "assistant", "Một tin đổi nhiều lần")
for i, v in enumerate(("like", "sad", "angry")):
    dm(ss, pj, dsid, one, ag, f"chg{i}", v)
check("ba lần đổi trên MỘT tin: vẫn một lý do, đúng một nguồn sống cho tin đó",
      len(W.reasons(status="pending")) == 1
      and sum(1 for s in W.sources(rs[0]["id"], live_only=True) if s["group_ref"].endswith(f":{one}:owner")) == 1)
# Nhóm: lý do riêng theo ngữ cảnh team:<id>
tm1 = team_msg(ss, T, ag["agent_key"], "Tin nhóm A")
tm2 = team_msg(ss, T, ag["agent_key"], "Tin nhóm B")
r.react(T["team_id"], tm1, "love", nonce="t1")
r.react(T["team_id"], tm2, "like", axis="should_silent", nonce="t2")
trs = W.reasons(context_ref=f"team:{T['team_id']}")
check("nhóm: một lý do gom hai tin ở ngữ cảnh team:<id>, tách khỏi chat riêng", len(trs) == 1
      and len(W.sources(trs[0]["id"])) == 2 and len(W.reasons(status="pending")) == 2, trs)
check("trục nói/im lưu riêng khỏi lý do chất lượng", tm.reaction_of(T["session_id"], tm2)["axis"] == "should_silent"
      and tm.reaction_of(T["session_id"], tm2)["reasons"] == [])
check("nhóm: không lượt model, không lượt nhóm nào", fake.calls == [] and tm.turns(T["team_id"]) == [])
# Reaction của agent trên tin của đồng đội: không lý do, không vào ước lượng
other = gs.agent_register(owner, "minh")
tm.add_member(T["team_id"], other["agent_key"], "minh")
mm = team_msg(ss, T, other["agent_key"], "Tin của Minh")
before = len(W.reasons())
r._agent_react(T["team_id"], ag["agent_key"], {"op": "react", "message_id": mm, "value": "love"}, [], "turnX#rx0")
check("reaction của trưởng nhóm agent: không tạo lý do thức nào", len(W.reasons()) == before
      and tm.reaction_of(T["session_id"], mm, f"agent:{ag['agent_key']}")["source_kind"] == "leader")

# ═════ tin gắn goal: chỉ A2 ═════
d, gs, ss, ts, tm, owner, ag, fake, pj, r, T, dsid = world("goal", enabled=True)
W = AL.WakeReasons(gs)
with gs._Tx(gs) as c:
    c.execute("INSERT INTO goals(id,brain_id,owner,revision,status,idempotency_key,output_root,budget_calls,calls_used,"
              "created_at,updated_at) VALUES('g1',?,'o',1,'active','k','x',10,0,0,0)", (str(d),))
    c.execute("INSERT INTO goal_agents(goal_id,brain_id,agent_key,created_at) VALUES('g1',?,?,0)",
              (str(d), ag["agent_key"]))
gm = ss.append_message(dsid, "assistant", "Báo cáo mục tiêu", report={"key": "outbox:1", "goal_id": "g1"})
got = dm(ss, pj, dsid, gm, ag, "g-1", "love", goal_id="g1")
with closing(gs._conn()) as c:
    a2 = [dict(x) for x in c.execute("SELECT * FROM wake_reasons WHERE goal_id='g1' AND code='feedback'").fetchall()]
check("tin gắn goal: một lý do feedback của goal ở A2 (source_ref rx:dm:<id>)", len(a2) == 1
      and a2[0]["source_ref"] == f"rx:dm:{got['log_id']}" and a2[0]["state"] == "pending", a2)
check("tin gắn goal: KHÔNG lý do lớp 3 (một lý do, không ba)", W.reasons() == [])
check("tin gắn goal: reaction 'Chờ xem xét'", ss.dm_reaction_of(dsid, gm)["status"] == "pending_review")
got2 = dm(ss, pj, dsid, gm, ag, "g-2", "sad", goal_id="g1")
with closing(gs._conn()) as c:
    a2 = {x["source_ref"]: x["state"] for x in c.execute("SELECT * FROM wake_reasons WHERE goal_id='g1'").fetchall()}
check("đổi reaction trên tin gắn goal: nguồn cũ superseded, nguồn mới chờ",
      a2.get(f"rx:dm:{got['log_id']}") == "superseded" and a2.get(f"rx:dm:{got2['log_id']}") == "pending", a2)
with closing(gs._conn()) as c:
    g = dict(c.execute("SELECT status, paused, calls_used, run_state FROM goals WHERE id='g1'").fetchone())
check("emoji không đổi trạng thái goal, không tính lượt, không tạm dừng/chốt", g == {"status": "active", "paused": 0,
                                                                                      "calls_used": 0,
                                                                                      "run_state": "ready"}, g)

# Review T2 vòng 2 (cùng lỗi P2-1 ở A2): nguồn đang thuộc một lượt goal chưa chốt bị đổi; lượt đó bị huỷ thì nguồn cũ không
# trở về chờ cạnh nguồn mới. Lượt goal "đang chạy" được giả lập đúng như A2 ghi: `served` kèm `settled_by = <mã lượt>`.
with gs._Tx(gs) as c:
    c.execute("UPDATE wake_reasons SET state='served', settled_by='act_run' WHERE goal_id='g1' AND source_ref=?",
              (f"rx:dm:{got2['log_id']}",))
got3 = dm(ss, pj, dsid, gm, ag, "g-3", "wow", goal_id="g1")
with gs._Tx(gs) as c:   # đúng câu lệnh trả lý do về chờ của đường huỷ lượt goal (_refund_not_run)
    c.execute("UPDATE wake_reasons SET state='pending', settled_at=NULL, settled_by='' WHERE goal_id=? AND "
              "state='served' AND settled_by=?", ("g1", "act_run"))
with closing(gs._conn()) as c:
    pend = sorted(x["source_ref"] for x in c.execute("SELECT * FROM wake_reasons WHERE goal_id='g1' AND state='pending'"))
check("review T2 vòng 2: đổi reaction khi lượt goal đang chạy rồi lượt đó huỷ: chỉ nguồn mới chờ, nguồn cũ không sống lại",
      pend == [f"rx:dm:{got3['log_id']}"], pend)

# ═════ emoji không đổi việc, ưu tiên, quyền (nhóm) ═════
d, gs, ss, ts, tm, owner, ag, fake, pj, r, T, dsid = world("effects", enabled=True)
job = r.submit(T["team_id"], "Việc", as_job=True, client_msg_id="j")["task_id"]
m2 = team_msg(ss, T, ag["agent_key"], "Kết quả")
snap = (ts.get_task(job)["status"], ts.get_task(job).get("priority"), ts.team_queue(T["team_id"])["version"],
        tm.active_authority(T["team_id"]), gs.agent_any(ag["agent_key"])["config_version"])
for i, v in enumerate(("like", "love", "haha", "wow", "sad", "angry", "none")):
    r.react(T["team_id"], m2, v, reasons=["on_point"], comment="tốt", nonce=f"e{i}")
after = (ts.get_task(job)["status"], ts.get_task(job).get("priority"), ts.team_queue(T["team_id"])["version"],
         tm.active_authority(T["team_id"]), gs.agent_any(ag["agent_key"])["config_version"])
check("emoji không đổi trạng thái việc, ưu tiên, hàng đợi, quyền, cấu hình agent", snap == after, (snap, after))
check("emoji không mở lượt, không gọi model", fake.calls == [] and tm.turns(T["team_id"]) == [])

print(f"\n{'FAIL' if fails else 'PASS'} - test_feedback_wake: {len(fails)} lỗi")
raise SystemExit(1 if fails else 0)
