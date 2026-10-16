"""T2 (0.154.0): bộ chiếu phản hồi (outbox của reaction), thiết kế 2026-10-15 mục 3.5.

    python tests/run.py feedback_projector -v

Kho thật trên SQLite tạm (conversations.db, team.sqlite3, resonance.sqlite3). Không có engine nào: bộ chiếu chỉ là code.
Các ca: idempotent theo source_ref; thứ tự theo mã nhật ký và trạng thái ròng (dòng cũ superseded); bỏ hết thì gỡ nguồn;
lỗi bộ chiếu rồi chiếu lại ở nhịp và lúc khởi động; lỗi kho không làm mất reaction, không tác động kép; trạng thái hiển
thị ghi ngược theo lý do; reaction của agent không tạo lý do.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fb-proj-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-fb-proj-w-"))

import agent_ledger as AL  # noqa: E402
import feedback_projector as FP  # noqa: E402
import resonance_store as RS  # noqa: E402
import sessions  # noqa: E402
import team_store  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


_n = [0]


def world(label):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}-{label}"
    d.mkdir()
    gs = RS.GoalStore(d / "resonance.sqlite3")
    ss = sessions.SessionStore(db_path=d / "chat.db")
    tm = team_store.TeamStore(d / "team.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    agent = gs.agent_set_enabled(owner, "viet", True)
    sid = ss.create_session(brain=str(d), channel="agent:viet")
    box = {"gs": gs, "fail": False}

    def res():
        if box["fail"]:
            raise RuntimeError("kho resonance đang khoá")
        return box["gs"]

    pj = FP.FeedbackProjector(resonance=res, sessions=lambda: ss, teams=lambda: tm)
    return d, gs, ss, tm, agent, sid, pj, box


def react(ss, sid, mid, agent, nonce, value="like", **kw):
    return ss.set_dm_reaction(sid, mid, value=value, nonce=nonce, agent_key=agent["agent_key"], **kw)


# ═════ idempotent ═════
d, gs, ss, tm, ag, sid, pj, box = world("idem")
W = AL.WakeReasons(gs)
mid = ss.append_message(sid, "assistant", "Bài viết số một")
got = react(ss, sid, mid, ag, "n1", "love")
ev1 = pj.project("dm", got["log_id"])
ev2 = pj.project("dm", got["log_id"])
rs = W.reasons(agent_key=ag["agent_key"])
check("chiếu: một lý do reaction_review ở ngữ cảnh dm:<agent_key>", len(rs) == 1
      and rs[0]["context_ref"] == f"dm:{ag['agent_key']}" and rs[0]["code"] == "reaction_review", rs)
check("chiếu lại cùng dòng: không thêm nguồn, không thêm lý do", len(W.sources(rs[0]["id"])) == 1 and ev2 == [])
row = ss.dm_reaction_log_row(got["log_id"])
check("dòng nhật ký done kèm bằng chứng tác động", row["effect_state"] == "done"
      and row["effect_refs"].get("route") == "l3" and row["effect_refs"].get("source_ref") == f"rx:dm:{got['log_id']}",
      row)
check("trạng thái reaction: Chờ xem xét", ss.dm_reaction_of(sid, mid)["status"] == "pending_review")
check("gói WS chat_reaction mang trạng thái mới", ev1 and ev1[0]["type"] == "chat_reaction"
      and ev1[0]["reaction"]["status"] == "pending_review", ev1)

# ═════ thứ tự và trạng thái ròng ═════
d, gs, ss, tm, ag, sid, pj, box = world("order")
W = AL.WakeReasons(gs)
mid = ss.append_message(sid, "assistant", "Bài hai")
logs = [react(ss, sid, mid, ag, f"o{i}", v)["log_id"] for i, v in enumerate(("like", "sad", "angry"))]
pj.project_pending()
states = [ss.dm_reaction_log_row(x)["effect_state"] for x in logs]
check("ba lần đổi chưa chiếu: chỉ áp trạng thái ròng của dòng mới nhất, dòng cũ superseded",
      states == ["superseded", "superseded", "done"], states)
rs = W.reasons(agent_key=ag["agent_key"])
check("một lý do, đúng một nguồn (dòng mới nhất)", len(rs) == 1 and [s["source_ref"] for s in W.sources(rs[0]["id"])]
      == [f"rx:dm:{logs[-1]}"], [W.sources(r["id"]) for r in rs])
# đổi sau khi đã chiếu: nguồn cũ gỡ khỏi lý do còn chờ
l4 = react(ss, sid, mid, ag, "o4", "wow")["log_id"]
pj.project("dm", l4)
live = [s["source_ref"] for s in W.sources(rs[0]["id"], live_only=True)]
check("đổi reaction: nguồn cũ gỡ khỏi lý do còn chờ, nguồn mới vào cùng lý do", live == [f"rx:dm:{l4}"]
      and W.source(f"rx:dm:{logs[-1]}")["removed_at"] is not None, live)
l5 = ss.set_dm_reaction(sid, mid, value=None, nonce="o5", agent_key=ag["agent_key"])["log_id"]
pj.project("dm", l5)
check("bỏ hết: nguồn gỡ, lý do không còn nguồn thì superseded, reaction null",
      W.get(rs[0]["id"])["status"] == "superseded" and ss.dm_reaction_of(sid, mid) is None
      and ss.dm_reaction_log_row(l5)["effect_refs"]["route"] == "cleared")

# ═════ lỗi bộ chiếu, chiếu lại ở nhịp và lúc khởi động ═════
d, gs, ss, tm, ag, sid, pj, box = world("retry")
W = AL.WakeReasons(gs)
mid = ss.append_message(sid, "assistant", "Bài ba")
box["fail"] = True
got = react(ss, sid, mid, ag, "r1", "haha")
out = pj.project("dm", got["log_id"])
check("lỗi kho resonance: project không ném, reaction vẫn lưu, dòng giữ pending",
      out == [] and ss.dm_reaction_of(sid, mid)["value"] == "haha"
      and ss.dm_reaction_log_row(got["log_id"])["effect_state"] == "pending")
out = pj.project_pending()
check("nhịp lúc kho còn lỗi: vẫn pending, không ném", ss.dm_reaction_log_row(got["log_id"])["effect_state"] == "pending")
box["fail"] = False
pj.project_pending()
check("nhịp sau khi kho hết lỗi: chiếu xong, đúng một lý do", ss.dm_reaction_log_row(got["log_id"])["effect_state"]
      == "done" and len(W.reasons(agent_key=ag["agent_key"])) == 1)
# khởi động lại: bộ chiếu mới trên kho mở lại
mid2 = ss.append_message(sid, "assistant", "Bài bốn")
box["fail"] = True
g2 = react(ss, sid, mid2, ag, "r2", "sad")
pj.project("dm", g2["log_id"])
ss2 = sessions.SessionStore(db_path=ss.db_path)
gs2 = RS.GoalStore(gs.path)
pj2 = FP.FeedbackProjector(resonance=lambda: gs2, sessions=lambda: ss2, teams=lambda: None)
pj2.project_pending()
W2 = AL.WakeReasons(gs2)
check("khởi động lại: dòng còn pending được chiếu, gom vào lý do đang chờ (không tác động kép)",
      ss2.dm_reaction_log_row(g2["log_id"])["effect_state"] == "done"
      and len(W2.reasons(agent_key=ag["agent_key"], status="pending")) == 1
      and len(W2.sources(W2.reasons(agent_key=ag["agent_key"], status="pending")[0]["id"])) == 2)
pj2.project_pending()
check("chiếu lại lần nữa: không đổi gì", len(W2.sources(W2.reasons(agent_key=ag["agent_key"])[0]["id"])) == 2)


# lỗi giữa chừng (sau khi đã gom nguồn, trước khi đánh done): chiếu lại không gom lần hai
class Flaky(FP.FeedbackProjector):
    boom = True

    def _mark(self, kind, ids, state, refs=None):
        if state == "done" and self.boom:
            self.boom = False
            raise RuntimeError("chết giữa hai kho")
        return super()._mark(kind, ids, state, refs)


d, gs, ss, tm, ag, sid, pj, box = world("half")
fl = Flaky(resonance=lambda: gs, sessions=lambda: ss, teams=lambda: tm)
mid = ss.append_message(sid, "assistant", "Bài năm")
got = react(ss, sid, mid, ag, "h1", "love")
fl.project("dm", got["log_id"])
check("chết giữa hai kho: nguồn đã gom nhưng dòng còn pending", ss.dm_reaction_log_row(got["log_id"])["effect_state"]
      == "pending" and AL.WakeReasons(gs).source(f"rx:dm:{got['log_id']}") is not None)
fl.project_pending()
W = AL.WakeReasons(gs)
check("chiếu lại: done, vẫn đúng một nguồn một lý do (idempotent theo source_ref)",
      ss.dm_reaction_log_row(got["log_id"])["effect_state"] == "done"
      and len(W.reasons(agent_key=ag["agent_key"])) == 1 and len(W.sources(W.reasons()[0]["id"])) == 1)

# ═════ trạng thái ghi ngược theo lý do ═════
rid = W.reasons(agent_key=ag["agent_key"])[0]["id"]
cl = W.claim(rid, try_busy=lambda: True)
ev = fl.refresh("dm", [got["log_id"]])
check("lý do đang chạy: reaction 'Đang xem xét', có gói WS", ss.dm_reaction_of(sid, mid)["status"] == "reviewing"
      and ev and ev[0]["reaction"]["status"] == "reviewing", ev)
W.settle(rid, "ok", consumed=[f"rx:dm:{got['log_id']}"])
fl.project_pending()
check("lý do phục vụ xong và đã tiêu thụ nguồn: 'Không cần đổi'", ss.dm_reaction_of(sid, mid)["status"] == "no_change")
ss.set_dm_reaction_status(sid, mid, "owner", got["log_id"], "adjusted", "Đã sửa cách trình bày")
fl.project_pending()
check("trạng thái do lượt xem xét thật ghi (adjusted) không bị bộ chiếu ghi đè",
      ss.dm_reaction_of(sid, mid)["status"] == "adjusted")

# ═════ nhóm: reaction của agent không tạo lý do; trang chính không agent ═════
d, gs, ss, tm, ag, sid, pj, box = world("team")
tid = team_store.new_team_id()
tsid = ss.create_session(brain=str(d), channel=f"team:{tid}")
tm.create_team(str(d), "Đội", "", tsid, team_id=tid)
m1, _ = ss.append_team_message(tsid, "assistant", "Tin của Viết", author_kind="agent", author_ref=ag["agent_key"])
x = tm.set_reaction_ex(team_id=tid, session_id=tsid, message_id=m1, agent_key=ag["agent_key"], task_id=None,
                       content_sha="s", value="like", nonce="ta1", reactor="agent:ag_khac", source_kind="member")
pj.project("team", x["log_id"])
W = AL.WakeReasons(gs)
check("reaction của agent (member): không tạo lý do, trạng thái 'Đã nhận'", W.reasons() == []
      and tm.reaction_of(tsid, m1, "agent:ag_khac")["status"] == "received"
      and tm.reaction_log_row(x["log_id"])["effect_refs"]["route"] == "agent_reaction")
y = tm.set_reaction_ex(team_id=tid, session_id=tsid, message_id=m1, agent_key=ag["agent_key"], task_id=None,
                       content_sha="s", value="love", nonce="to1")
pj.project("team", y["log_id"])
rs = W.reasons()
check("reaction của chủ trong nhóm: lý do ở ngữ cảnh team:<id>", len(rs) == 1 and rs[0]["context_ref"] == f"team:{tid}"
      and tm.reaction_of(tsid, m1)["status"] == "pending_review", rs)
main_sid = ss.create_session(brain=str(d))
mm = ss.append_message(main_sid, "assistant", "Câu của Javis")
z = ss.set_dm_reaction(main_sid, mm, value="like", nonce="mn1")
pj.project("dm", z["log_id"])
check("trang Trò chuyện chính (không agent): 'Đã nhận', không lý do", ss.dm_reaction_of(main_sid, mm)["status"]
      == "received" and len(W.reasons()) == 1)
check("dòng nhật ký cũ của nhóm (trước T2) coi như đã chiếu: mặc định effect_state done",
      "DEFAULT 'done'" in str(team_store._T2_REACTION_COLS))

# ═════ review T2 P3: hai lần chiếu chạy đua trên cùng cặp (route và nhịp, hay hai lần đổi nhanh) ═════
import threading  # noqa: E402
import time as _time  # noqa: E402
d, gs, ss, tm, ag, sid, pj, box = world("race")
W = AL.WakeReasons(gs)
mid = ss.append_message(sid, "assistant", "Tin bị đổi nhanh hai lần")
g1 = react(ss, sid, mid, ag, "race-1", "love")
_orig_pair = pj._pair
gate, done_new = threading.Event(), threading.Event()
box_new = {}


def slow_pair(kind, row):
    out = _orig_pair(kind, row)
    if int(row["id"]) == int(g1["log_id"]) and not gate.is_set():
        gate.set()
        done_new.wait(0.6)   # lần chiếu dòng cũ đã đọc cặp; trong khe này dòng mới được ghi và chiếu
    return out


def project_new():
    gate.wait(5)
    box_new["g2"] = react(ss, sid, mid, ag, "race-2", "angry")
    pj.project("dm", box_new["g2"]["log_id"])
    done_new.set()


pj._pair = slow_pair
t_old = threading.Thread(target=lambda: pj.project("dm", g1["log_id"]))
t_new = threading.Thread(target=project_new)
t_old.start()
t_new.start()
t_old.join(10)
t_new.join(10)
g2 = box_new["g2"]
pj._pair = _orig_pair
live = [x["source_ref"] for r_ in W.reasons(agent_key=ag["agent_key"]) for x in W.sources(r_["id"])
        if x.get("removed_at") is None]
check("review T2 P3: chiếu chạy đua: chỉ còn nguồn của trạng thái mới nhất, nguồn cũ không sống lại",
      live == [f"rx:dm:{g2['log_id']}"], live)

# Review T2 vòng 2: dọn khoá cặp không bao giờ bỏ khoá đang được giữ hay đang được chờ.
held = FP._pair_lock("cap:giu")
held.__enter__()
for k in range(5200):
    with FP._pair_lock(f"cap:{k}"):
        pass
check("review T2 vòng 2: khoá cặp đang giữ không bị dọn khi bảng khoá đầy", "cap:giu" in FP._LOCKS
      and FP._LOCKS["cap:giu"][1] == 1 and len(FP._LOCKS) <= 5001 + 2500, len(FP._LOCKS))
held.__exit__(None, None, None)

print(f"\n{'FAIL' if fails else 'PASS'} - test_feedback_projector: {len(fails)} lỗi")
raise SystemExit(1 if fails else 0)
