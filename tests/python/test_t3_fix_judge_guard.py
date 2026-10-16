"""Review T3 phần J, P2-2 (0.156.0 sửa): chốt "không engine phán xử thật trong test" không có đường vòng.

    python tests/run.py t3_fix_judge_guard -v

Trước bản sửa, JAVIS_NO_REAL_JUDGE_ENGINE chỉ được kiểm trong `main._agent_judge_ask`; engine do người gọi tiêm vào
(`judge_proactive(engine=...)`, `ctx["ask"]`) và `Judge(ask=...)` đi thẳng tới model. Nay `Judge._run_engine` và
`_run_engine_claimed` từ chối MỌI hàm hỏi chưa đánh dấu `agent_judge.mark_test_fake`, lượt được hoàn, không gọi gì.
Engine ở đây đều là hàm GIẢ đếm lời gọi: nếu chốt hỏng, test đỏ chứ không có model thật nào được gọi.
"""
import asyncio
import json
import os

os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

from _judge_world import ROOT, World, batch, check, finish, keys_in  # noqa: E402
import agent_judge as AJ  # noqa: E402

check("_paths đặt chốt JAVIS_NO_REAL_JUDGE_ENGINE=1", os.environ.get("JAVIS_NO_REAL_JUDGE_ENGINE") == "1")


class Counting:
    """Hàm hỏi model GIẢ chưa đánh dấu: chỉ đếm số lần bị gọi (chốt đúng thì luôn 0)."""

    def __init__(self, reply):
        self.calls = 0
        self.reply = reply

    async def __call__(self, prompt, purpose, started):
        self.calls += 1
        started()
        return self.reply(prompt) if callable(self.reply) else self.reply


SPEAK_ONE = json.dumps({"verdict": "speak", "score": 0.95, "reason": "x"})

# ═════ 1. Judge(ask=...) chưa đánh dấu: chat riêng không gọi, lượt hoàn ═════
w = World("guard-dm", enabled=("lan",))
key = w.k("lan")
raw = Counting(SPEAK_ONE)
w.judge.ask = raw
d = asyncio.run(w.judge.judge_proactive(agent_key=key, context_ref=f"dm:{key}", draft="Nháp", source_event="g:1"))
check("Judge(ask) chưa đánh dấu: không bao giờ được gọi", raw.calls == 0, raw.calls)
check("Judge(ask) chưa đánh dấu: im với engine_unavailable", d["verdict"] == "silent"
      and d["silence_code"] == "engine_unavailable", d)
rows = w.judge_calls()
check("Judge(ask) chưa đánh dấu: dòng sổ được hoàn (không tính trần)", rows and all(r["status"] == "refunded" for r in rows),
      rows)

# ═════ 2. engine= do người gọi tiêm vào, chưa đánh dấu ═════
w.judge.ask = AJ.mark_test_fake(Counting(SPEAK_ONE))
inj = Counting(SPEAK_ONE)
d2 = asyncio.run(w.judge.judge_proactive(agent_key=key, context_ref=f"dm:{key}", draft="Nháp 2", source_event="g:2",
                                         engine=inj))
check("judge_proactive(engine=...) chưa đánh dấu: không gọi, im engine_unavailable",
      inj.calls == 0 and d2["silence_code"] == "engine_unavailable", (inj.calls, d2))

# ═════ 3. ctx["ask"] của lô nhóm, chưa đánh dấu ═════
w3 = World("guard-team", enabled=("lan", "minh", "hoa"), roles={"minh": "doanh thu", "hoa": "doanh thu"})
team_raw = Counting(lambda p: batch(**{k: ("speak", 0.9) for k in keys_in(p)}))
ctx = {"team_id": w3.team_id, "team_name": "Đội", "context_ref": w3.ctx, "kind": "message", "source_event": "msg:991",
       "root_ref": "msg:991", "message": {"id": 991, "text": "Cả nhóm ơi doanh thu tháng này sao?", "author_kind": "owner",
                                          "mentions": []},
       "members": [{"agent_key": w3.k("minh"), "name": "Minh", "role_text": "doanh thu", "order": 1, "open_jobs": [],
                    "recent_texts": []}],
       "caps": {"judge_daily": None, "k": 1}, "history": [{"who": "Chủ", "text": "x"}], "ask": team_raw}
out = asyncio.run(w3.judge.judge_team_message(ctx))
check("ctx['ask'] chưa đánh dấu: lô nhóm không gọi engine", team_raw.calls == 0, team_raw.calls)
check("ctx['ask'] chưa đánh dấu: ứng viên im engine_unavailable, không ai tự lên tiếng",
      out and all(x["verdict"] == "silent" and x["silence_code"] == "engine_unavailable" for x in out), out)

# ═════ 4. xét lại ở chat riêng qua lý do đã nhận (_run_engine_claimed) ═════
w4 = World("guard-claimed", enabled=("lan",))
k4 = w4.k("lan")
answers = ['{"verdict":"wait","score":0.5,"reason":"đợi","wait_for":["owner_message"]}']
w4.jeng.decide = lambda p: answers.pop(0)
d4 = asyncio.run(w4.judge.judge_proactive(agent_key=k4, context_ref=f"dm:{k4}", draft="Nháp chờ", source_event="g:4"))
check("chuẩn bị: lần đầu chờ (engine giả đã đánh dấu chạy được)", d4["verdict"] == "wait", d4)
claimed_raw = Counting(SPEAK_ONE)
w4.judge.ask = claimed_raw
w4.now[0] += 5
items = w4.judge.release_waits(f"dm:{k4}", {"kind": "owner_message"})
got = asyncio.run(w4.judge.process_released(items))
check("xét lại qua lý do đã nhận: hàm hỏi chưa đánh dấu không được gọi", claimed_raw.calls == 0, claimed_raw.calls)
check("xét lại qua lý do đã nhận: kết cục im, không gửi", all(x["verdict"] == "silent" for x in got), got)

# ═════ 5. engine giả đã đánh dấu vẫn chạy ═════
ok_fake = Counting(SPEAK_ONE)
w.judge.ask = AJ.mark_test_fake(ok_fake)
d5 = asyncio.run(w.judge.judge_proactive(agent_key=key, context_ref=f"dm:{key}", draft="Nháp 5", source_event="g:5"))
check("engine giả đã đánh dấu: được gọi đúng một lần và quyết định nói", ok_fake.calls == 1 and d5["verdict"] == "speak",
      (ok_fake.calls, d5))
check("real_engine_blocked: hàm thường bị chặn, hàm đã đánh dấu không",
      AJ.real_engine_blocked(Counting("")) and not AJ.real_engine_blocked(AJ.mark_test_fake(Counting(""))))

# ═════ 6. chốt đặt tập trung và nhất quán ═════
run_src = (ROOT / "tests" / "run.py").read_text(encoding="utf-8")
check("tests/run.py đặt JAVIS_NO_REAL_JUDGE_ENGINE=1 cho MỌI tiến trình test",
      'JAVIS_NO_REAL_JUDGE_ENGINE="1"' in run_src and 'JAVIS_NO_REAL_REVIEW_ENGINE="1"' in run_src)
main_src = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
ask_src = main_src[main_src.index("async def _agent_judge_ask"):main_src.index("def _agent_judge_engine_label")]
check("main._agent_judge_ask so `== \"1\"` (cùng phép so với agent_judge và _learn_engine_for)",
      'os.environ.get("JAVIS_NO_REAL_JUDGE_ENGINE") == "1"' in ask_src)
p8 = (ROOT / "tests" / "python" / "test_adaptive_context_phase8.py").read_text(encoding="utf-8")
check("test_adaptive_context_phase8 đặt chốt trước khi nạp máy chủ",
      'os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"] = "1"' in p8
      and p8.index('os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"]') < p8.index("import config"))
pilot = (ROOT / "tests" / "python" / "_a5_pilot_server.py").read_text(encoding="utf-8")
check("pilot A5 dry đặt chốt trước khi nạp main",
      'os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"] = "1"' in pilot
      and pilot.index('os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"]') < pilot.index("import main"))

finish()
