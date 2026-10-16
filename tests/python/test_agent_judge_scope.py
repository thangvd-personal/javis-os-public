"""T3 phần J (0.156.0): ngữ cảnh của bộ phán xử tách bạch (thiết kế 2 "không đưa nội dung ngữ cảnh này vào prompt ngữ cảnh
khác", 7.1 "ca thô chỉ lấy cùng ngữ cảnh"; GOAL 8.1, 12 ca 1).

    python tests/run.py agent_judge_scope -v

Cùng một agent ở hai nhóm A, B và chat riêng: ca, độ lệch, quyết định, lịch sử của nhóm A không bao giờ vào prompt hay ngưỡng
của nhóm B hay chat riêng, và ngược lại.
"""
import asyncio
import json
import os

os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

from _judge_world import World, batch, check, finish, keys_in  # noqa: E402
import agent_judge as AJ  # noqa: E402
import team_store  # noqa: E402

ROLES = {"lan": "Điều phối", "minh": "Quảng cáo", "hoa": "Kho hàng"}
w = World("scope", roles=ROLES, enabled=("minh",))
TA, CA = w.team_id, w.ctx
tid_b = team_store.new_team_id()
sid_b = w.ss.create_session(brain=str(w.dir), channel=f"team:{tid_b}")
w.tm.create_team(str(w.dir), "Đội B", "", sid_b, team_id=tid_b)
for s in ("lan", "minh", "hoa"):
    w.tm.add_member(tid_b, w.k(s), s)
w.tm.set_leader(tid_b, w.k("lan"))
CB = f"team:{tid_b}"


def in_team(tid):
    w.team_id, w.ctx = tid, f"team:{tid}"


w.now[0] += 5
w.jeng.decide = lambda p: batch(**{k: (("speak", 0.9) if k == w.k("minh") else ("silent", 0.1)) for k in keys_in(p)})
in_team(TA)
w.send("BIMATNHOMA: mọi người ơi ngân sách ads quý này chốt bao nhiêu?")
vol = [d for d in w.decisions("minh", "message") if d["volunteer"]][0]
w.judge.label(vol["id"], "should_silent", "owner", at=w.now[0] + 1)
w.jeng.prompts.clear()
in_team(tid_b)
w.send("Mọi người ơi đơn hàng hôm nay ra sao?")
pb = w.jeng.prompts[-1]
check("prompt lô nhóm B không chứa ca hay lịch sử của nhóm A", "BIMATNHOMA" not in pb, pb[:400])
check("prompt lô nhóm B không nhắc tên nhóm A", "Đội vận hành" not in pb)
db = w.js.decisions(w.k("minh"), CB, 50)
check("ngưỡng của Minh ở nhóm B không bị nhãn ở nhóm A làm đổi (0.60)", db and db[0]["threshold"] == 0.6, db)
check("ngưỡng của Minh ở nhóm A đã đổi (0.72)", w.judge.view(w.k("minh"))["contexts"] and any(
    c["context_ref"] == CA and c["threshold"] == 0.72 for c in w.judge.view(w.k("minh"))["contexts"]))
check("ca của nhóm A chỉ đọc được trong nhóm A", w.js.cases(w.k("minh"), CB) == [] and len(w.js.cases(w.k("minh"), CA)) == 1)
check("quyết định theo ngữ cảnh: lịch sử nhóm B không có quyết định của nhóm A",
      all(d["context_ref"] == CB for d in w.judge.decisions(w.k("minh"), CB)))

# Chat riêng: tin chủ động không thấy gì của nhóm.
seen = {}


async def ask(prompt, purpose, started):
    started()
    seen["p"] = prompt
    return json.dumps({"verdict": "silent", "score": 0.1, "reason": "x"})

w.judge.history = lambda ctx: w.runner._judge_history(w.tm.get(ctx.split(":", 1)[1])) if ctx.startswith("team:") else \
    [{"who": "Chủ", "text": "chuyện riêng"}]
asyncio.run(w.judge.judge_proactive(agent_key=w.k("minh"), context_ref=f"dm:{w.k('minh')}", draft="Nháp riêng",
                                    source_event="scan:dm1", engine=AJ.mark_test_fake(ask)))
check("prompt chat riêng không chứa ca/lịch sử của nhóm nào", "BIMATNHOMA" not in seen["p"]
      and "đơn hàng hôm nay" not in seen["p"] and "chuyện riêng" in seen["p"], seen["p"][:300])
check("ngưỡng chat riêng không bị nhãn nhóm làm đổi",
      w.js.decisions(w.k("minh"), f"dm:{w.k('minh')}", 5)[0]["threshold"] == 0.6)
w.judge.label(w.js.decisions(w.k("minh"), f"dm:{w.k('minh')}", 5)[0]["id"], "should_speak", "owner", at=w.now[0] + 2)
check("nhãn ở chat riêng không đổi ngưỡng ở nhóm", w.js.get_offset(w.k("minh"), CB, w.now[0] + 2) == 0.0
      and abs(w.js.get_offset(w.k("minh"), CA, w.now[0] + 2) - 0.12) < 1e-6)

# Lịch sử nhóm trong prompt chỉ là của đúng phiên nhóm đó.
in_team(TA)
hist_a = w.runner._judge_history(w.tm.get(TA))
hist_b = w.runner._judge_history(w.tm.get(tid_b))
check("lịch sử phán xử theo nhóm: A có tin A, B không có tin A",
      any("BIMATNHOMA" in h["text"] for h in hist_a) and not any("BIMATNHOMA" in h["text"] for h in hist_b))
v = w.judge.view(w.k("minh"), [{"context_ref": CA, "label": "Đội vận hành"}, {"context_ref": CB, "label": "Đội B"}])
check("khung nhìn phán xử tách quyết định theo từng ngữ cảnh",
      all(d["context_ref"] == c["context_ref"] for c in v["contexts"] for d in c["decisions"]))

finish()
