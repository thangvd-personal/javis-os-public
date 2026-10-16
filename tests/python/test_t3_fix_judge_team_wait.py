"""Review T3 vòng 2, P2-1: lần chờ của TIN CHỦ ĐỘNG trong nhóm được xét lại đúng một lần khi gỡ chờ, không bị huỷ.

    python tests/run.py t3_fix_judge_team_wait -v

Trước bản sửa, mọi lần chờ ngoài chat riêng đi bộ chạy nhóm; tin chủ động (lượt quét `say`, `ask_back`/`revise`) không có
`message_id` nên `_judge_wait_ctx` huỷ nó: bản nháp mất, quyết định đứng `wait` mãi không kết cục (trái 7.4 và GOAL 8.1). Nay
gỡ chờ thì xét lại bản nháp qua `judge_proactive` (trần `judge_daily` của nhóm, không hỏi hạn mức ngày của agent) và gửi qua
người gửi đã đăng ký; lần chờ của lô nhóm không còn tin gốc thì có quyết định `silent/wait_cancelled` nhìn thấy được.
Kho thật, bộ chạy nhóm thật, engine GIẢ. Không model thật nào.
"""
import asyncio
import os

os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

from _judge_world import World, check, finish  # noqa: E402

WAIT = '{"verdict":"wait","score":0.5,"reason":"đợi chủ","wait_for":["owner_message"]}'
SPEAK = '{"verdict":"speak","score":0.95,"reason":"đúng lúc"}'

w = World("team-proactive-wait", enabled=("lan", "minh"))
k = w.k("minh")
answers = [WAIT, SPEAK, WAIT, SPEAK]
w.jeng.decide = lambda p: answers.pop(0)
sent, spent = [], []
w.judge.register_sender(lambda d, payload, mode: sent.append((d["verdict"], payload.get("draft"), mode, d["context_ref"])))
w.judge.try_spend = lambda key, purpose: spent.append(purpose) or (True, "")

# ═════ 1. gỡ chờ không qua lý do (vòng 5 giây, process_released) ═════
d0 = asyncio.run(w.judge.judge_proactive(agent_key=k, context_ref=w.ctx, draft="Nhắc cả nhóm hạn chót chiều nay",
                                         source_event="pulse:tw:1", origin="pulse",
                                         payload={"judge_daily": None, "source_ref": "task:t1"}))
check("chuẩn bị: tin chủ động trong nhóm được phán xử chờ", d0["verdict"] == "wait", d0)
w.now[0] += 5
items = w.judge.release_waits(w.ctx, {"kind": "owner_message", "at": w.now[0]})
wid = items[0]["wait"]["id"]
for it in items:
    it["reason_id"] = None
got = asyncio.run(w.judge.process_released(items))
check("gỡ chờ trong nhóm: bản nháp được xét lại đúng một lần và nói", [x["verdict"] for x in got] == ["speak"]
      and got[0]["kind"] == "wait", got)
check("gỡ chờ trong nhóm: người gửi nhận đúng bản nháp, ngữ cảnh nhóm", sent == [("speak", "Nhắc cả nhóm hạn chót chiều nay",
                                                                              "send", w.ctx)], sent)
check("gỡ chờ trong nhóm: lần chờ khép 'rejudged' (không 'cancelled')", w.js.get_wait(wid)["status"] == "rejudged",
      w.js.get_wait(wid))
check("trong nhóm không hỏi hạn mức ngày của agent (trần judge_daily của nhóm)", "wait_unblocked" not in spent, spent)

# ═════ 2. gỡ chờ qua lý do wait_unblocked (vòng pulse gọi serve_reason) ═════
sent.clear()
d1 = asyncio.run(w.judge.judge_proactive(agent_key=k, context_ref=w.ctx, draft="Bản sửa ngắn hơn",
                                         source_event="wr:tw:2", origin="pulse", payload={"judge_daily": None}))
check("chuẩn bị: lần chờ thứ hai", d1["verdict"] == "wait", d1)
w.now[0] += 5
items2 = w.judge.release_waits(w.ctx, {"kind": "owner_message", "at": w.now[0]})
rid = items2[0]["reason_id"]
got2 = asyncio.run(w.judge.serve_reason(int(rid)))
check("serve_reason trong nhóm: xét lại và gửi (không chuyển bộ chạy nhóm để bị huỷ)",
      got2 and got2["verdict"] == "speak" and sent and sent[0][1] == "Bản sửa ngắn hơn", (got2, sent))
check("serve_reason trong nhóm: lý do khép", w.wake.get(int(rid))["status"] == "served", w.wake.get(int(rid)))
again = asyncio.run(w.judge.serve_reason(int(rid)))
check("gọi lại lần hai: không xét lại lần nữa (đúng một lần)", again is None and len(sent) == 1, (again, sent))

# ═════ 3. lần chờ của lô nhóm không còn tin gốc: quyết định silent/wait_cancelled ═════
did = w.js.add_decision({"agent_key": k, "context_ref": w.ctx, "source_event": "msg:9999", "root_ref": "msg:9999",
                         "kind": "message", "verdict": "wait", "excerpt": "câu hỏi cũ"}, now=w.now[0])
w.js.add_wait(decision_id=did, agent_key=k, context_ref=w.ctx, root_ref="msg:9999", source_event="msg:9999",
              until_kinds=["owner_message"], until_ts=w.now[0] + 1800, job_refs=[],
              payload={"message_id": 9999, "kind": "message"}, now=w.now[0])
w.now[0] += 5
items3 = w.judge.release_waits(w.ctx, {"kind": "owner_message", "at": w.now[0]})


async def team_waits():
    w.runner._bind_loop()
    return await w.runner._judge_team_waits(items3)

asyncio.run(team_waits())
canc = [d for d in w.decisions("minh") if d.get("silence_code") == "wait_cancelled"]
check("lần chờ của lô nhóm mất tin gốc: có quyết định silent/wait_cancelled nhìn thấy được",
      len(canc) == 1 and canc[0]["verdict"] == "silent", canc)

finish()
