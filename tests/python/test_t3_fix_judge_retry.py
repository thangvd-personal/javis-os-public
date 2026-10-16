"""Review T3 phần J, P2-4 (0.156.0 sửa): lượt tự lên tiếng bị lỗi giữ danh tính "tự lên tiếng" cho nút Thử lại.

    python tests/run.py t3_fix_judge_retry -v

Trước bản sửa, `demote()` đặt `volunteer=False` nên Thử lại: (a) với tin của chủ thành lời gọi trực tiếp `member_reply`, đi
vòng qua bộ phán xử; (b) với tin của agent (xem xét liên quan, chờ) ném "không tìm thấy tin của chủ nhóm"; (c) thử lại thành
công vẫn để quyết định `silent/turn_blocked`; (d) câu hỏi lại `clarify` mất nội dung. Kho thật, bộ chạy nhóm thật, engine
lượt nhóm và engine phán xử GIẢ.
"""
import asyncio
import os

os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

from _judge_world import World, batch, check, finish, keys_in  # noqa: E402
import agent_judge as AJ  # noqa: E402

QUESTION = "Cả nhóm ơi, ai biết cách xử lý doanh thu tháng này không?"


def failing_engine(w, fail):
    """Engine lượt nhóm: ném lỗi khi `fail["on"]` (lượt không đăng được tin), không thì như engine giả của thế giới."""
    orig = w.eng.engine_for

    def engine_for(team, key, role):
        prov, model, run = orig(team, key, role)

        async def run2(req):
            if fail["on"]:
                w.eng.calls.append({"agent": key, "role": role, "prompt": req["prompt"], "failed": True})
                raise RuntimeError("engine down")
            return await run(req)
        return prov, model, run2
    w.runner.engine_for = engine_for


def retry_run(w, mid, key):
    box = {}

    async def go():
        w.runner._bind_loop()
        box["sub"] = sub = await asyncio.to_thread(w.runner.retry_reply, w.team_id, mid, key)
        if sub:
            await w.runner.run_replies(sub)
        await w.runner.wait_background()
    asyncio.run(go())
    return box["sub"]


# ═════ 1. tin của chủ: thử lại vẫn là lượt tự lên tiếng, thành công thì quyết định về lại speak ═════
w = World("retry-owner", enabled=("lan", "minh", "hoa"), roles={"minh": "doanh thu", "hoa": "kho"})
w.jeng.decide = lambda p: batch(**{k: (("speak", 0.95) if k == w.k("minh") else ("silent", 0.1)) for k in keys_in(p)})
fail = {"on": True}
failing_engine(w, fail)
sub = w.send(QUESTION)
mid = int(sub["message_id"])
dm = [d for d in w.decisions("minh") if d["source_event"] == f"msg:{mid}" and d["kind"] == "message"]
check("chuẩn bị: minh được chọn tự lên tiếng nhưng lượt lỗi nên bị hạ (silent/turn_blocked)",
      dm and dm[0]["verdict"] == "silent" and dm[0]["silence_code"] == "turn_blocked", dm)
fail["on"] = False
n_before = len(w.eng.calls)
rs = retry_run(w, mid, w.k("minh"))
roles = [c["role"] for c in w.eng.calls[n_before:] if c["agent"] == w.k("minh")]
check("Thử lại tin của chủ: giữ vai tự lên tiếng (volunteers), không thành lời gọi trực tiếp",
      rs and rs.get("volunteers") == [w.k("minh")], rs)
check("Thử lại tin của chủ: lượt chạy là member_volunteer, KHÔNG phải member_reply", roles == ["member_volunteer"], roles)
d_after = w.js.get_decision(dm[0]["id"])
check("Thử lại thành công: quyết định về lại speak, tự lên tiếng, gắn tin",
      d_after["verdict"] == "speak" and d_after["volunteer"] and d_after["message_ref"].startswith("msg:")
      and not d_after["silence_code"], d_after)

# ═════ 2. tin của agent (xem xét liên quan): Thử lại làm được ═════
w2 = World("retry-agent", enabled=("lan", "minh", "hoa"))
fail2 = {"on": True}
failing_engine(w2, fail2)
amid, _ = w2.ss.append_team_message(w2.team["session_id"], "assistant", "Minh: doanh thu giảm 10%", author_kind="agent",
                                    author_ref=w2.k("minh"))
did = w2.js.add_decision({"agent_key": w2.k("hoa"), "context_ref": w2.ctx, "source_event": f"related:{amid}",
                          "root_ref": "msg:1", "kind": "related", "verdict": "speak", "address": "none", "score": 0.9,
                          "threshold": 0.6, "reason": "liên quan kho", "volunteer": True,
                          "features": {"reply_to": int(amid), "relation": "job"}})


async def run_vol(w_, mid_, decs, kind):
    w_.runner._bind_loop()
    await w_.runner._run_volunteers(w_.team_id, mid_, decs, kind=kind, via_key=w_.k("minh"))
    await w_.runner.wait_background()

asyncio.run(run_vol(w2, int(amid), [AJ.public_decision(w2.js.get_decision(did))], "related"))
check("chuẩn bị: lượt xem xét liên quan của hoa lỗi, quyết định bị hạ",
      w2.js.get_decision(did)["verdict"] == "silent", w2.js.get_decision(did))
fail2["on"] = False
try:
    rs2 = retry_run(w2, int(amid), w2.k("hoa"))
    err2 = None
except Exception as ex:  # noqa: BLE001
    rs2, err2 = None, ex
check("Thử lại trên tin của agent: không ném 'không tìm thấy tin của chủ nhóm', giữ vai tự lên tiếng",
      err2 is None and rs2 and rs2.get("volunteers") == [w2.k("hoa")], (rs2, err2))
check("Thử lại trên tin của agent thành công: quyết định về lại speak", w2.js.get_decision(did)["verdict"] == "speak",
      w2.js.get_decision(did))

# ═════ 3. câu hỏi lại (clarify): Thử lại giữ nội dung câu hỏi ═════
w3 = World("retry-clarify", enabled=("lan", "minh", "hoa"))
fail3 = {"on": True}
failing_engine(w3, fail3)
omid, _ = w3.ss.append_team_message(w3.team["session_id"], "user", "Chuẩn bị báo cáo quý nhé", author_kind="owner",
                                    author_ref="owner")
cid = w3.js.add_decision({"agent_key": w3.k("minh"), "context_ref": w3.ctx, "source_event": f"msg:{omid}",
                          "root_ref": f"msg:{omid}", "kind": "clarify", "verdict": "speak", "address": "none",
                          "ask_clarify": "Báo cáo quý mấy, năm nào?", "volunteer": True,
                          "features": {"reply_to": int(omid), "expired_wait": 1}})
asyncio.run(run_vol(w3, int(omid), [AJ.public_decision(w3.js.get_decision(cid))], "clarify"))
fail3["on"] = False
n3 = len(w3.eng.calls)
rs3 = retry_run(w3, int(omid), w3.k("minh"))
new3 = [c for c in w3.eng.calls[n3:] if c["agent"] == w3.k("minh")]
check("Thử lại câu hỏi lại: lượt member_volunteer giữ đúng nội dung clarify",
      rs3 and [c["role"] for c in new3] == ["member_volunteer"] and "Báo cáo quý mấy, năm nào?" in new3[0]["prompt"],
      [(c["role"], c["prompt"][-300:]) for c in new3])

finish()
