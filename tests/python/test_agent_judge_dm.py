"""T3 phần J (0.156.0): bộ phán xử ở chat riêng và tin chủ động (thiết kế 7.3, 7.4, 9; GOAL 12 ca 20).

    python tests/run.py agent_judge_dm -v

Kho SQLite tạm thật, engine phán xử GIẢ.
- Câu hỏi trực tiếp của chủ ở chat riêng KHÔNG BAO GIỜ qua phán xử: đường chat riêng của host chỉ gỡ chờ, không gọi model.
- Tin chủ động: speak / wait / silent theo điểm và ngưỡng; lỗi engine, hết giờ, JSON sai, engine chưa sẵn sàng: không gửi,
  lý do ghi lại, dòng sổ lượt chốt đúng (hoàn khi chưa tới model). Không mất gì: người gọi giữ bản nháp và lý do của mình.
- Lượt phán xử ở chat riêng hỏi `agent_daily_calls` (phần L, qua bộ chuyển `try_spend`) trước khi giữ chỗ; hết hạn mức thì
  `waiting_quota`, không gọi engine.
- Chờ: tin mới của chủ gỡ chờ, tạo lý do `wait_unblocked`, xét lại đúng một lần, `speak` thì gọi người gửi đã đăng ký;
  hết 30 phút thì im có lý do hay một câu hỏi lại (khi có người gửi).
"""
import asyncio
import json
import os

os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

from _judge_world import TEMP, check, finish  # noqa: E402
import agent_judge as AJ  # noqa: E402
import agent_judge_store as AJS  # noqa: E402
import agent_ledger as AL  # noqa: E402
import resonance_store as RS  # noqa: E402


class Eng:
    def __init__(self):
        self.prompts = []
        self.reply = json.dumps({"verdict": "silent", "score": 0.1, "reason": "thừa"})
        self.exc = None
        self.delay = 0.0
        self.unavailable = False

    async def ask(self, prompt, purpose, started):
        if self.unavailable:
            raise AJ.EngineUnavailable("x")
        started()
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.reply


_n = [0]


def make(spend=None, enabled=True):
    _n[0] += 1
    d = TEMP / f"dm-{_n[0]}"
    d.mkdir()
    gs = RS.GoalStore(d / "resonance.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    ag = gs.agent_register(owner, "viet")
    if enabled:
        ag = gs.agent_set_enabled(owner, "viet", True)
    eng = Eng()
    now = [1_800_000_000.0]
    spent = []

    def try_spend(key, purpose):
        spent.append((key, purpose))
        return spend(key, purpose) if spend else (True, "")
    js = AJS.JudgeStore(d / "agent_judge.sqlite3")
    led = AL.CallLedger(gs)
    wake = AL.WakeReasons(gs)
    j = AJ.Judge(store=js, ledger=lambda: led, wake=lambda: wake, ask=AJ.mark_test_fake(eng.ask), clock=lambda: now[0],
                 resonance_on=lambda k: True, try_spend=try_spend, timeout_s=1.0,
                 history=lambda ctx: [{"who": "Chủ", "text": f"tin của {ctx}"}],
                 agent_meta=lambda k: {"name": "Việt", "role_text": "Trợ lý bán hàng"})
    return j, eng, ag["agent_key"], now, spent, led, wake, js


def run(c):
    return asyncio.run(c)


def proactive(j, key, draft="Hôm nay doanh thu giảm 20%, anh xem chưa?", src="scan:1"):
    return run(j.judge_proactive(agent_key=key, context_ref=f"dm:{key}", draft=draft, source_event=src, origin="scan"))


# ═════ speak / wait / silent ═════
j, eng, key, now, spent, led, wake, js = make()
eng.reply = json.dumps({"verdict": "speak", "score": 0.9, "reason": "dữ kiện mới"})
d = proactive(j, key)
check("speak: điểm >= ngưỡng thì nói", d["verdict"] == "speak" and d["score"] == 0.9 and d["threshold"] == 0.6, d)
check("khuôn Decision theo hợp đồng", {"decision_id", "verdict", "score", "threshold", "reason", "wait_until",
                                       "ask_clarify"} <= set(d))
check("prompt chỉ có lịch sử của đúng ngữ cảnh dm:<agent>", f"tin của dm:{key}" in eng.prompts[0]
      and "team:" not in eng.prompts[0])
row = [r for r in led.rows() if r["purpose"] == "judge_batch"]
check("chat riêng: một dòng sổ lượt judge_batch có agent_key, obligation 0, ok",
      len(row) == 1 and row[0]["agent_key"] == key and row[0]["context_ref"] == f"dm:{key}" and row[0]["obligation"] == 0
      and row[0]["status"] == "ok", row)
check("chat riêng: hỏi agent_daily_calls (try_spend) trước khi giữ chỗ", spent == [(key, "proactive_judge")], spent)
eng.reply = json.dumps({"verdict": "speak", "score": 0.5, "reason": "ít giá trị"})
d = proactive(j, key, src="scan:2")
check("speak dưới ngưỡng: silent/below_threshold", d["verdict"] == "silent" and d["silence_code"] == "below_threshold", d)
eng.reply = json.dumps({"verdict": "silent", "score": 0.2, "reason": "thừa"})
d = proactive(j, key, src="scan:3")
check("silent: không gửi", d["verdict"] == "silent" and d["silence_code"] == "model_silent", d)

# ═════ lỗi: không gửi, có lý do ═════
for label, setup, code, status in (
        ("timeout", lambda e: setattr(e, "delay", 3.0), "timeout", "error"),
        ("bad-json", lambda e: setattr(e, "reply", "nên gửi nhé"), "bad_json", "ok"),
        ("engine-error", lambda e: setattr(e, "exc", RuntimeError("x")), "policy_error", "error"),
        ("unavailable", lambda e: setattr(e, "unavailable", True), "engine_unavailable", "refunded")):
    j, eng, key, now, spent, led, wake, js = make()
    setup(eng)
    d = proactive(j, key)
    check(f"{label}: silent/{code}, không gửi", d["verdict"] == "silent" and d["silence_code"] == code, d)
    rows = [r for r in led.rows() if r["purpose"] == "judge_batch"]
    check(f"{label}: dòng sổ lượt {status}", rows and rows[0]["status"] == status, rows)
    check(f"{label}: quyết định im vẫn nằm trong lịch sử (không mất dấu)", j.decisions(key)[0]["decision_id"] == d["decision_id"])

j, eng, key, now, spent, led, wake, js = make(spend=lambda k, p: (False, "hết lượt hôm nay"))
d = proactive(j, key)
check("hết agent_daily_calls: silent/waiting_quota, không gọi engine, không dòng sổ lượt",
      d["silence_code"] == "waiting_quota" and eng.prompts == [] and led.rows() == [], d)

# ═════ chờ, gỡ chờ, xét lại một lần ═════
j, eng, key, now, spent, led, wake, js = make()
sent = []
j.register_sender(lambda dec, payload, mode: sent.append((dec["verdict"], payload.get("draft"), mode)))
eng.reply = json.dumps({"verdict": "wait", "score": 0.7, "reason": "chủ đang bận", "wait_for": ["owner_message"]})
d = proactive(j, key, draft="Bản nháp A")
w = js.waits_of(key)
check("wait: ghi lần chờ (tối đa 30 phút, điều kiện tin mới của chủ)", d["verdict"] == "wait" and len(w) == 1
      and w[0]["until_kinds"] == ["owner_message"] and abs(w[0]["until_ts"] - now[0] - 1800) < 1, w)
now[0] += 60
check("sự kiện khác loại không gỡ chờ", j.release_waits(f"dm:{key}", {"kind": "speaker_done"}) == [])
items = j.release_waits(f"dm:{key}", {"kind": "owner_message"})
check("tin mới của chủ gỡ chờ và tạo lý do wait_unblocked", len(items) == 1 and items[0]["reason_id"]
      and wake.get(items[0]["reason_id"])["code"] == "wait_unblocked", items)
check("gỡ lần hai không có gì (CAS một lần)", j.release_waits(f"dm:{key}", {"kind": "owner_message"}) == [])
eng.reply = json.dumps({"verdict": "speak", "score": 0.8, "reason": "giờ hợp lý"})
got = run(j.process_released(items))
check("xét lại: speak thì gọi người gửi đã đăng ký với đúng bản nháp", sent == [("speak", "Bản nháp A", "send")], sent)
check("xét lại dùng đúng dòng sổ của lý do (wr:<id>), lý do served",
      wake.get(items[0]["reason_id"])["status"] == "served"
      and any(r["reason_ref"] == f"wr:{items[0]['reason_id']}" and r["status"] == "ok" for r in led.rows()), led.rows())
check("lần chờ chuyển rejudged", js.get_wait(w[0]["id"])["status"] == "rejudged")
check("xét lại hỏi agent_daily_calls với loại wait_unblocked", (key, "wait_unblocked") in spent, spent)

j, eng, key, now, spent, led, wake, js = make()
eng.reply = json.dumps({"verdict": "wait", "score": 0.7, "reason": "đợi", "wait_for": ["owner_message"]})
proactive(j, key, src="scan:w2")
now[0] += 5
items = j.release_waits(f"dm:{key}", {"kind": "owner_message"})
got = run(j.process_released(items))
check("xét lại lại chọn wait: im/rejudge_limit (một lần xét lại mỗi tin gốc)",
      got and got[0]["silence_code"] == "rejudge_limit", got)

j, eng, key, now, spent, led, wake, js = make()
sent = []
j.register_sender(lambda dec, payload, mode: sent.append((mode, payload.get("ask_clarify"))))
eng.reply = json.dumps({"verdict": "wait", "score": 0.6, "reason": "thiếu số liệu", "wait_for": ["owner_message"],
                        "ask_clarify": "Anh muốn so với tuần trước hay tháng trước?"})
proactive(j, key, src="scan:w3")
now[0] += 1801
items = j.tick()
check("quá 30 phút: lần chờ hết hạn ở vòng tick", len(items) == 1 and items[0]["action"] == "expired", items)
got = run(j.process_released(items))
check("hết hạn có ask_clarify và có người gửi: một câu hỏi lại", sent == [("ask_clarify",
                                                                           "Anh muốn so với tuần trước hay tháng trước?")]
      and got[0]["kind"] == "clarify", (sent, got))

j, eng, key, now, spent, led, wake, js = make()
eng.reply = json.dumps({"verdict": "wait", "score": 0.6, "reason": "đợi"})
proactive(j, key, src="scan:w4")
now[0] += 1801
got = run(j.process_released(j.tick()))
check("hết hạn không câu hỏi lại: im/wait_expired có lý do", got and got[0]["silence_code"] == "wait_expired", got)

j, eng, key, now, spent, led, wake, js = make()
eng.reply = json.dumps({"verdict": "wait", "score": 0.7, "reason": "đợi", "wait_for": ["owner_message"]})
proactive(j, key, src="scan:w5")
now[0] += 5
items = j.release_waits(f"dm:{key}", {"kind": "owner_message"})
eng.reply = json.dumps({"verdict": "speak", "score": 0.9, "reason": "ok"})
got = run(j.process_released(items))
d2 = j.decisions(key)
check("không ai đăng ký người gửi: speak sau xét lại được ghi lại thành im/no_sender (không giả là đã gửi)",
      any(x["silence_code"] == "no_sender" for x in d2), d2)

# ═════ đường chat riêng của host: câu hỏi trực tiếp không qua phán xử ═════
import re  # noqa: E402
from pathlib import Path  # noqa: E402

main_src = (Path(__file__).resolve().parents[2] / "server" / "main.py").read_text(encoding="utf-8")
hook = main_src[main_src.index("def _judge_dm_owner_message"):]
hook = hook[:hook.index("\n\n\n")]
check("hook chat riêng của host chỉ GỠ CHỜ (release_waits), không gọi judge_proactive hay model",
      "release_waits" in hook and "judge_proactive" not in hook and "ask(" not in hook)
ws_part = main_src[main_src.index("_judge_dm_owner_message(brain, _persona[1], conv_sid)") - 400:]
ws_part = ws_part[:2000]
check("lượt chat riêng của chủ vẫn dựng prompt và trả lời như cũ (không có cổng phán xử chen vào)",
      "judge_proactive" not in ws_part and re.search(r"_legacy_system_prompt", ws_part))

finish()
