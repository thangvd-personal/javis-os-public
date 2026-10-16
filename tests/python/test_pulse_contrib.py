"""T3 (0.156.0): ghi nhận đóng góp `pulse_contrib` (thiết kế 2026-10-15 mục 6.5, 6.1; GOAL 12 ca 8, 28).

    python tests/run.py pulse_contrib -v

Hàm thuần `attribute` (chia đều theo tập đầu vào host, trần 0.6, phần vượt trần cho người khác trước, 0.2 cho tác giả
tổng hợp, tập rỗng thì tác giả 1.0, gộp phần, tổng luôn 1) và đường thật: bộ chạy nhóm + bộ chiếu phản hồi + kho nhịp, bước
tổng hợp có `input_refs` do host ghi. Không tự thưởng, không đếm trùng, tác giả tổng hợp không có thêm tín hiệu trực tiếp,
thay bản kết quả thì phân bổ cũ `superseded`, tổng hợp nhiều tầng không nhân điểm. Sự kiện host: chấp nhận đề xuất, chốt
quyết định, sửa yêu cầu sau kết quả, huỷ.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import random
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-contrib-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-contrib-w-"))

import agent_pulse as AP  # noqa: E402
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


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


def same(got, want):
    return set(got) == set(want) and all(near(got[k], want[k]) for k in want)


# ═════ hàm thuần ═════
check("tập rỗng (trả lời trực tiếp): tác giả nhận 1.0", AP.attribute("L", []) == {"L": 1.0})
check("hai người đóng góp: 0.4 / 0.4, tổng hợp 0.2", same(AP.attribute("L", ["A", "B"]), {"A": 0.4, "B": 0.4, "L": 0.2}))
check("một người đóng góp: trần 0.6, phần vượt về tác giả tổng hợp khi không còn ai dưới trần",
      same(AP.attribute("L", ["A"]), {"A": 0.6, "L": 0.4}))
check("chia đều theo TẬP ĐẦU VÀO (mỗi bước một phần): A 3 bước, B 1 bước", same(AP.attribute("L", ["A", "A", "A", "B"]),
                                                                           {"A": 0.6, "B": 0.2, "L": 0.2}))
check("vượt trần chia cho người khác còn dưới trần trước", same(AP.attribute("L", ["A", "A", "A", "A", "B"]),
                                                               {"A": 0.6, "B": 0.2, "L": 0.2}))
check("vượt trần chia tiếp nhiều vòng", same(AP.attribute("L", ["A"] * 8 + ["B", "C"]),
                                             {"A": 0.6, "B": 0.1, "C": 0.1, "L": 0.2}))
check("tác giả tổng hợp cũng làm bước: gộp phần, không đếm hai lần", same(AP.attribute("L", ["L", "A"]),
                                                                          {"L": 0.6, "A": 0.4}))
check("tổng hợp chính đầu vào của mình: 1.0", same(AP.attribute("L", ["L"]), {"L": 1.0}))
check("trưởng nhóm không mặc nhiên nhận hết: 4 người làm, tổng hợp chỉ 0.2",
      near(AP.attribute("L", ["A", "B", "C", "D"])["L"], 0.2))
rnd = random.Random(7)
ok_sum = ok_cap = True
for _ in range(2000):
    synth = rnd.choice("LABC")
    ins = [rnd.choice("ABCDEFL") for _ in range(rnd.randint(0, 9))]
    al = AP.attribute(synth, ins)
    ok_sum &= near(sum(al.values()), 1.0, 1e-9)
    ok_cap &= all(v <= 0.6 + 1e-9 for k, v in al.items() if k != synth)
check("tổng share của một nguồn luôn bằng 1 (2000 ca ngẫu nhiên)", ok_sum)
check("người đóng góp (không phải tác giả tổng hợp) không quá 0.6", ok_cap)

# ═════ đường thật: bộ chạy nhóm, bộ chiếu, kho nhịp ═════
d = TEMP / "team"
d.mkdir()
gs = RS.GoalStore(d / "resonance.sqlite3")
owner = RS.Principal("owner", "owner", str(d))
K = {s: gs.agent_set_enabled(owner, s, True)["agent_key"] for s in ("lan", "minh", "hoa")}
ss = sessions.SessionStore(db_path=d / "chat.db")
ts = task_store.TaskStore(d / "kanban.sqlite3")
tm = team_store.TeamStore(d / "team.sqlite3")
box = {}
pj = FP.FeedbackProjector(resonance=lambda: gs, sessions=lambda: ss, teams=lambda: tm,
                          team_view=lambda t, s, m: box["r"].team_message_view(t, s, m))
r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=lambda *a: None, publish=lambda e: None,
                  agent_info=lambda root, key: {"slug": key, "name": key, "system": ""}, projector=pj)
box["r"] = r
tid = team_store.new_team_id()
tsid = ss.create_session(brain=str(d), channel=f"team:{tid}")
tm.create_team(str(d), "Đội", "", tsid, team_id=tid)
for s, k in K.items():
    tm.add_member(tid, k, s)
tm.set_leader(tid, K["lan"])
ctx = f"team:{tid}"
task = r.submit(tid, "Viết báo cáo", as_job=True, client_msg_id="j1")["task_id"]
rev = ts.get_task(task)["team_rev"]


def step(kind, key, inputs, text):
    st = tm.add_step(task, tid, kind, key, rev, "", inputs)
    mid, _ = ss.append_team_message(tsid, "assistant", text, author_kind="agent", author_ref=key, task_id=task,
                                    step_id=st["step_id"])
    tm.finish_step(st["step_id"], "done", mid, True)
    return mid


m_minh = step("work", K["minh"], [], "Phần của Minh")
m_hoa = step("work", K["hoa"], [], "Phần của Hoà")
m_syn = step("synthesize", K["lan"], [m_minh, m_hoa], "Bản tổng hợp")
st = r.react(tid, m_syn, "love", nonce="n1")
log1 = st["log_id"]
src1 = f"rx:team:{log1}"
rows = {s["agent_key"]: s for s in sum((AP.signals(gs, k) for k in K.values()), []) if s["source_ref"] == src1}
check("❤️ trên bản tổng hợp: một nguồn chia cho Minh 0.4, Hoà 0.4, Lan (tổng hợp) 0.2",
      set(rows) == set(K.values()) and near(rows[K["minh"]]["share"], 0.4) and near(rows[K["hoa"]]["share"], 0.4)
      and near(rows[K["lan"]]["share"], 0.2), rows)
check("tổng share của nguồn = 1", near(sum(x["share"] for x in rows.values()), 1.0))
check("tác giả tổng hợp không có thêm tín hiệu trực tiếp riêng cho cùng nguồn (đúng một dòng)",
      len([s for s in AP.signals(gs, K["lan"]) if s["source_ref"] == src1]) == 1)
contrib = AP.contrib(gs, src1)
check("pulse_contrib ghi chuỗi do host: vai input/synth, tập đầu vào, bản kết quả",
      {(c["agent_key"], c["role"]) for c in contrib} == {(K["minh"], "input"), (K["hoa"], "input"), (K["lan"], "synth")}
      and all(c["result_ref"].startswith("step:") for c in contrib), contrib)
pj.project("team", log1)
check("chiếu lại: không đếm trùng", len([s for s in AP.signals(gs, K["minh"]) if s["source_ref"] == src1]) == 1
      and len(AP.contrib(gs, src1)) == 3)
r.react(tid, m_syn, "sad", nonce="n2")
old = [s for k in K.values() for s in AP.signals(gs, k) if s["source_ref"] == src1]
check("đổi reaction: tín hiệu và phân bổ cũ superseded (không cộng dồn)",
      all(s["status"] == "superseded" for s in old) and AP.contrib(gs, src1) == [], old)
live = [s for k in K.values() for s in AP.signals(gs, k, status="live") if s["kind"] != "context_init"]
check("chỉ còn phân bổ của reaction mới, tổng 1", len(live) == 3 and near(sum(s["share"] for s in live), 1.0)
      and all(s["emoji"] == "sad" for s in live), live)

# Agent không tự thưởng: reaction của trưởng nhóm trên bản tổng hợp không tạo tín hiệu.
x = tm.set_reaction_ex(team_id=tid, session_id=tsid, message_id=m_minh, agent_key=K["minh"], task_id=task,
                       content_sha="s", value="love", nonce="ag1", reactor=f"agent:{K['lan']}", source_kind="leader")
pj.project("team", x["log_id"])
check("reaction của agent (trưởng nhóm) không tạo tín hiệu nào", all(s["source_ref"] != f"rx:team:{x['log_id']}"
                                                                    for k in K.values() for s in AP.signals(gs, k)))
check("record_contrib từ chối người phản hồi là agent", AP.record_contrib(gs, source_ref="rx:team:999", result_ref="r",
                                                                         synth=K["lan"], inputs=[K["minh"]],
                                                                         context_ref=ctx, value="love",
                                                                         reactor=f"agent:{K['minh']}") == {})

# Tổng hợp nhiều tầng không nhân điểm: tổng hợp B lấy tổng hợp A làm đầu vào; chỉ một tầng được phân bổ.
m_syn2 = step("synthesize", K["lan"], [m_syn, m_hoa], "Tổng hợp của tổng hợp")
st2 = r.react(tid, m_syn2, "love", nonce="n3")
src2 = f"rx:team:{st2['log_id']}"
al = {c["agent_key"]: c["share"] for c in AP.contrib(gs, src2)}
check("tổng hợp nhiều tầng: tầng trước tính như MỘT đầu vào của tác giả nó, không lan tiếp tới Minh",
      K["minh"] not in al and near(al[K["lan"]], 0.6) and near(al[K["hoa"]], 0.4) and near(sum(al.values()), 1.0), al)

# Thay bản kết quả: phân bổ cũ superseded, tính lại theo bản mới.
got = AP.record_contrib(gs, source_ref="rx:team:5000", result_ref="step:v1", synth=K["lan"], inputs=[K["minh"]],
                        context_ref=ctx, value="love")
again = AP.record_contrib(gs, source_ref="rx:team:5000", result_ref="step:v1", synth=K["lan"], inputs=[K["minh"]],
                          context_ref=ctx, value="love")
new = AP.record_contrib(gs, source_ref="rx:team:5000", result_ref="step:v2", synth=K["lan"], inputs=[K["hoa"]],
                        context_ref=ctx, value="love")
allc = AP.contrib(gs, "rx:team:5000", live_only=False)
sig = {s["agent_key"]: s for k in K.values() for s in AP.signals(gs, k) if s["source_ref"] == "rx:team:5000"}
check("cùng bản kết quả: gọi lại không đổi gì", got == again and len(allc) == 4, allc)
check("thay bản kết quả: phân bổ v1 superseded, v2 sống", {c["result_ref"] for c in allc if c["status"] == "live"}
      == {"step:v2"} and {c["result_ref"] for c in allc if c["status"] == "superseded"} == {"step:v1"}, allc)
check("thay bản kết quả: Minh hết phần (superseded), Hoà nhận phần mới, tổng phần sống = 1",
      sig[K["minh"]]["status"] == "superseded" and sig[K["hoa"]]["status"] == "live"
      and near(sum(s["share"] for s in sig.values() if s["status"] == "live"), 1.0), sig)

# ═════ sự kiện host: chấp nhận đề xuất, chốt quyết định, sửa sau kết quả, huỷ ═════
mp, _ = ss.append_team_message(tsid, "assistant", "Đề xuất", author_kind="agent", author_ref=K["lan"],
                               meta={"proposal": {"title": "Bản tin", "brief": "x"}})
r.accept_proposal(tid, mp)
r.accept_proposal(tid, mp)
acc = [s for s in AP.signals(gs, K["lan"]) if s["source_ref"] == f"accept:{tid}:{mp}"]
check("chấp nhận đề xuất: +0.6 w0.8 cho agent đề xuất, bấm lại không thêm", len(acc) == 1 and acc[0]["value"] == 0.6
      and acc[0]["w"] == 0.8, acc)
check("thẻ đã nhận thành việc: trạng thái ghi lên tin (mọi tab thấy), không bác được nữa",
      (ss.team_message(tsid, mp)["meta"] or {}).get("proposal_status") == "accepted")
try:
    r.reject_proposal(tid, mp)
    check("bác thẻ đã thành việc: từ chối", False)
except team_store.TeamError:
    check("bác thẻ đã thành việc: từ chối", True)
mq, _ = ss.append_team_message(tsid, "assistant", "Đề xuất 2", author_kind="agent", author_ref=K["lan"],
                               meta={"proposal": {"title": "Khảo sát", "brief": "x"}})
check("bác đề xuất (nút Bỏ qua): lần đầu đổi trạng thái, bấm lại không ghi lần hai",
      r.reject_proposal(tid, mq) is True and r.reject_proposal(tid, mq) is False
      and (ss.team_message(tsid, mq)["meta"] or {}).get("proposal_status") == "rejected")
rj = [s for s in AP.signals(gs, K["lan"]) if s["source_ref"] == f"reject:{tid}:{mq}"]
check("bác đề xuất: -0.4 w0.8 cho agent đề xuất, đúng một dòng", len(rj) == 1 and rj[0]["value"] == -0.4
      and rj[0]["w"] == 0.8, rj)
try:
    r.accept_proposal(tid, mq)
    check("thẻ đã bác: không tạo việc được", False)
except team_store.TeamError:
    check("thẻ đã bác: không tạo việc được", True)
# Đề xuất do lượt quét tạo trong nhóm: chủ quyết ở thẻ thì sổ đề xuất của nhịp cập nhật, không ghi tín hiệu lần hai.
mr, _ = ss.append_team_message(tsid, "assistant", "Đề xuất 3", author_kind="agent", author_ref=K["lan"],
                               meta={"proposal": {"title": "Cập nhật", "brief": "x"}})
pid = AP.record_proposal(gs, agent_key=K["lan"], context_ref=ctx, source_ref="task:t9", version="5", title="Cập nhật",
                         session_id=tsid, message_id=mr)
r.reject_proposal(tid, mr)
check("thẻ nhóm do lượt quét tạo: sổ đề xuất thành rejected, chỉ một tín hiệu (của thẻ)",
      AP.proposal(gs, pid)["status"] == "rejected" and not [s for s in AP.signals(gs, K["lan"])
                                                              if s["source_ref"] == f"reject:pp:{pid}"])
check("không mở lại đề xuất khi không có dữ kiện mới: cùng nguồn cùng phiên bản bị chặn, phiên bản mới thì được",
      AP.proposal_blocked(gs, K["lan"], ctx, "task:t9", "5") and not AP.proposal_blocked(gs, K["lan"], ctx, "task:t9", "6"))
pd = AP.record_proposal(gs, agent_key=K["minh"], context_ref=f"dm:{K['minh']}", source_ref="goal:g", version="1",
                        title="Hỏi lại mục tiêu")
row = AP.decide_proposal(gs, pd, "accepted", task_id="t_x")
check("đề xuất chat riêng được nhận: +0.6 w0.8, nguồn accept:pp:<id>, lần hai không quyết được",
      row and row["status"] == "accepted" and AP.decide_proposal(gs, pd, "rejected") is None
      and [s["value"] for s in AP.signals(gs, K["minh"]) if s["source_ref"] == f"accept:pp:{pd}"] == [0.6])
authors = r.result_authors(ts.get_task(task))
check("người nộp kết quả của bản hiện tại: bước làm và tổng hợp đã áp", set(authors) == set(K.values()), authors)
r.pulse_task_event("revise", tid, authors, f"revise:{task}:2")
rv = [s for k in K.values() for s in AP.signals(gs, k) if s["source_ref"] == f"revise:{task}:2"]
check("sửa yêu cầu sau kết quả: -0.4 w0.8 chia cho người nộp kết quả, tổng 1",
      len(rv) == 3 and all(s["value"] == -0.4 and s["w"] == 0.8 for s in rv)
      and near(sum(s["share"] for s in rv), 1.0), rv)
shares_c = r.result_shares(ts.get_task(task))
r.cancel_job(task, owner_cancel=True)   # route chủ huỷ việc (team_api)
cc = [s for k in K.values() for s in AP.signals(gs, k) if s["source_ref"] == f"cancel:{task}"]
# Có bản tổng hợp: phân bổ như phản hồi trên bản tổng hợp (6.5, review T3 P P3), không chia đều.
check("huỷ sau khi có kết quả: -0.4 cho người nộp kết quả, chia theo 6.5", cc and all(s["value"] == -0.4 for s in cc)
      and {s["agent_key"]: round(s["share"], 6) for s in cc} == {k: round(v, 6) for k, v in shares_c.items()}
      and near(sum(s["share"] for s in cc), 1.0), (cc, shares_c))
task2 = r.submit(tid, "Việc chưa ai làm", as_job=True, client_msg_id="j2")["task_id"]
r.cancel_job(task2, owner_cancel=True)
check("huỷ khi chưa có kết quả: không ai bị tính", all(s["source_ref"] != f"cancel:{task2}" for k in K.values()
                                                       for s in AP.signals(gs, k)))
gs.agent_set_enabled(owner, "hoa", False)
r.pulse_task_event("decision", tid, [K["hoa"]], f"decision:{tid}:1")
check("agent tắt Cộng hưởng: sự kiện host không thành tín hiệu", all(s["source_ref"] != f"decision:{tid}:1"
                                                                     for s in AP.signals(gs, K["hoa"])))
r.pulse_task_event("decision", tid, [K["minh"]], f"decision:{tid}:2")
pj.void_task_event(f"decision:{tid}:2")
check("bỏ chốt quyết định: tín hiệu void", [s["status"] for s in AP.signals(gs, K["minh"])
                                            if s["source_ref"] == f"decision:{tid}:2"] == ["void"])

# ═════ kiểm khách quan: chỉ kết luận CUỐI của mỗi (mục tiêu, revision) ═════
import inspect  # noqa: E402
import time as _t  # noqa: E402
import resonance as R  # noqa: E402

go = TEMP / "goal"
go.mkdir()
G = RS.GoalStore(go / "resonance.sqlite3")
gown = RS.Principal("owner", "owner", str(go))
gk = G.agent_set_enabled(gown, "viet", True)["agent_key"]
gk2 = G.agent_set_enabled(gown, "lan", True)["agent_key"]


def mk_goal(gid, key):
    now = _t.time()
    with G._Tx(G) as c:
        c.execute("INSERT INTO goals(id,brain_id,owner,revision,status,idempotency_key,output_root,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (gid, str(go), "owner", 1, "active", gid, str(go), now, now))
        c.execute("INSERT INTO goal_agents(goal_id,brain_id,agent_key,created_at) VALUES(?,?,?,?)", (gid, str(go), key, now))


mk_goal("g1", gk)
for v in ("not_met", "not_met", "unknown"):
    G.add_assessment(gown, {"goal_id": "g1", "revision": 1, "verdict": v})
check("đánh giá trung gian trong vòng làm việc không tạo tín hiệu nào",
      [s for s in AP.signals(G, gk) if s["source_ref"].startswith("obj:")] == [])
check("kết thúc succeeded (kiểm khách quan cuối đạt): một tín hiệu +0.5 w0.8 cho agent của mục tiêu, nguồn obj:<goal>:<rev>",
      G.finish(gown, "g1", 1, "succeeded") is True)
obj = [s for s in AP.signals(G, gk) if s["source_ref"] == "obj:g1:1"]
check("tín hiệu kiểm khách quan đúng giá trị, ngữ cảnh chat riêng của agent", len(obj) == 1 and obj[0]["value"] == 0.5
      and obj[0]["w"] == 0.8 and obj[0]["context_ref"] == f"dm:{gk}" and obj[0]["status"] == "live", obj)
check("kết thúc lại (không còn active): không thêm tín hiệu", G.finish(gown, "g1", 1, "succeeded") is False
      and len([s for s in AP.signals(G, gk) if s["source_ref"] == "obj:g1:1"]) == 1)
mk_goal("g2", gk)
check("revision bị gác vì hết hạn mức với kiểm cuối không đạt: -0.5", G.pulse_objective("g2", 1, False) == "recorded"
      and [s["value"] for s in AP.signals(G, gk) if s["source_ref"] == "obj:g2:1"] == [-0.5])
check("gọi lại cùng kết luận: không đổi", G.pulse_objective("g2", 1, False) == "unchanged")
check("chủ nới hạn mức rồi chính revision đó đạt: sửa ĐÚNG tín hiệu đó thành +0.5, vẫn một dòng",
      G.finish(gown, "g2", 1, "succeeded") and [s["value"] for s in AP.signals(G, gk) if s["source_ref"] == "obj:g2:1"]
      == [0.5])
mk_goal("g3", gk2)
G.agent_set_enabled(gown, "lan", False)
G.finish(gown, "g3", 1, "succeeded")
check("agent tắt Cộng hưởng: kết luận cuối không thành tín hiệu", [s for s in AP.signals(G, gk2)
                                                                  if s["source_ref"].startswith("obj:")] == [])
src = inspect.getsource(R._settle)
i = src.index('"blocked", "budget"')
check("đường hết hạn mức trong _settle chỉ ghi kiểm khách quan khi kết luận là not_met (không ghi cho unknown)",
      'if a.verdict == "not_met"' in src[i:i + 900] and "pulse_objective" in src[i:i + 900])
check("finish chỉ là nơi kết luận cuối đạt/không đạt; add_assessment không gọi kho nhịp",
      "pulse_objective" not in inspect.getsource(RS.GoalStore.add_assessment))

print(f"\n{len(fails)} FAIL" if fails else "\nALL OK")
raise SystemExit(1 if fails else 0)
