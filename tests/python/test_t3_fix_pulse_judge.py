"""Review T3 phần J, P1 (0.156.0 sửa): lượt quét `say` của nhịp đi tới bộ phán xử THẬT.

    python tests/run.py t3_fix_pulse_judge -v

Trước bản sửa, host nhịp truyền `engine=lambda: deps.engine_for(...)` (không đối số, trả bộ ba) cho `judge_proactive`, sai
chữ ký `async (prompt, purpose, started) -> str`: `_run_engine` ném TypeError, mọi `say` thành `silent/policy_error` và lượt
vẫn bị tính. Test này nối `PulseHost` (bộ chuyển mặc định, không tiêm phán xử giả) với lớp `agent_judge.Judge` THẬT, kho phán
xử thật, sổ lượt thật; chỉ hàm hỏi model là GIẢ (đánh dấu `mark_test_fake`). Không có model thật nào.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import json
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fix-pj-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-fix-pj-w-"))

import agent_judge as AJ  # noqa: E402
import agent_judge_store as AJS  # noqa: E402
import agent_ledger as AL  # noqa: E402
import agent_pulse as AP  # noqa: E402
import agent_pulse_host as H  # noqa: E402
import resonance_store as RS  # noqa: E402

# Chạy được cả trên mã TRƯỚC bản sửa (chưa có mark_test_fake) để chứng minh test đỏ vì đúng lỗi chữ ký engine.
MARK = getattr(AJ, "mark_test_fake", lambda f: f)
fails = []
D = 86400.0
T0 = 2_000_000_000.0


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:500]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class ScanEngine:
    """Engine GIẢ của lượt quét: luôn trả hành động `say`."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def engine_for(self, key, ctx, purpose):
        async def run_(req):
            await req["engine_started"]()
            self.calls += 1
            return {"text": self.text, "tokens_in": 1, "tokens_out": 1}
        return "fake-scan", "fake-model", run_


class JudgeAsk:
    """Hàm hỏi model GIẢ của bộ phán xử, đúng chữ ký `async (prompt, purpose, started) -> str`."""

    def __init__(self, reply='{"verdict":"speak","score":0.95,"reason":"đúng lúc"}', boom_before_start=None):
        self.reply = reply
        self.calls = []
        self.boom = boom_before_start

    async def __call__(self, prompt, purpose, started):
        if self.boom is not None:
            raise self.boom          # lỗi TRƯỚC mốc engine: model chưa được gọi
        started()
        self.calls.append(purpose)
        return self.reply


_n = [0]


def world(ctx_of, ask, judge_payload=lambda ctx: {}, text='{"action": "say", "source_ref": "goal:g1", "text": "Anh ơi"}'):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}"
    d.mkdir()
    st = RS.GoalStore(d / "resonance.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    key = st.agent_set_enabled(owner, "a0", True)["agent_key"]
    AP.set_state(st, key, "*", "on", at=T0)
    ctx = ctx_of(key)
    clock = Clock(T0)
    js = AJS.JudgeStore(d / "agent_judge.sqlite3")
    judge = AJ.Judge(store=js, ledger=lambda: AL.CallLedger(st), wake=lambda: AL.WakeReasons(st),
                     ask=MARK(ask), engine_label=lambda: ("fake-aux", "fake-judge"),
                     try_spend=lambda k, p: (True, ""), clock=clock, timeout_s=3.0)
    AJ.wire(judge)
    sent = []
    eng = ScanEngine(text)
    deps = H.PulseHostDeps(
        store=lambda: st, agents=lambda: [{"agent_key": key, "brain_id": str(d)}],
        contexts=lambda k: [f"dm:{k}", ctx] if ctx != f"dm:{k}" else [ctx], busy=lambda k: False,
        try_busy=lambda k, tok: True, release_busy=lambda k, tok: None,
        opportunities=lambda k, c, iv: [{"source_ref": "goal:g1", "version": "1", "kind": "goal_waiting",
                                         "summary": "Mục tiêu đang chờ"}] if c == ctx else [],
        persona=lambda k: "Bạn là trợ lý.", engine_for=eng.engine_for,
        send=lambda k, c, t, meta: sent.append((k, c, t, meta)) or {"session_id": "s1", "message_id": 501},
        clock=clock, spawn=asyncio.ensure_future)
    deps.judge_payload = judge_payload
    host = H.PulseHost(deps, budget=lambda k, p, at=None: (True, ""))   # judge=None: bộ chuyển mặc định
    AP.ensure_row(st, key, ctx, at=T0)
    with st._Tx(st) as c:
        c.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=? AND context_ref=?", (T0 + 10, key, ctx))
    clock.t = T0 + D + 1
    return st, js, key, ctx, host, sent, eng


async def tick(host):
    await host.tick()
    await host.wait_idle()


def judge_rows(st):
    return [r for r in AL.CallLedger(st).rows() if r["purpose"] == "judge_batch"]


def last_say(st, key, ctx):
    ev = [e for e in AP.events(st, key, ctx, kinds=("scan",), limit=5)]
    return ((ev[0].get("payload") or {}).get("result") or {}) if ev else {}


# ═════ 1. chat riêng: say -> Judge thật -> nói -> gửi ═════
ask = JudgeAsk()
st, js, key, ctx, host, sent, eng = world(lambda k: f"dm:{k}", ask)
asyncio.run(tick(host))
check("chuẩn bị: lượt quét đã chạy và chọn say", eng.calls == 1, eng.calls)
check("bộ phán xử THẬT được gọi đúng một lần qua đường hỏi của chính nó (purpose agent_judge)",
      ask.calls == ["agent_judge"], ask.calls)
dec = js.decisions(key, ctx, 5)
check("quyết định proactive 'speak' được ghi trong kho phán xử", dec and dec[0]["verdict"] == "speak"
      and dec[0]["kind"] == "proactive" and (dec[0].get("features") or {}).get("origin") == "pulse", dec)
check("tin được gửi đúng một lần kèm decision_id", len(sent) == 1 and sent[0][2] == "Anh ơi"
      and sent[0][3].get("decision_id") == dec[0]["id"], sent)
rows = judge_rows(st)
check("một dòng judge_batch, đã qua mốc engine, chốt ok", len(rows) == 1 and rows[0]["status"] == "ok"
      and rows[0]["engine"] == "fake-aux", rows)
res = last_say(st, key, ctx)
check("kết quả lượt quét: say/speak có message_id", res.get("action") == "say" and res.get("verdict") == "speak"
      and res.get("message_id") == 501, res)

# ═════ 2. nhóm: judge_daily của nhóm đi vào payload ═════
ask2 = JudgeAsk()
seen_payload = []


def payload_zero(c):
    seen_payload.append(c)
    return {"judge_daily": 0} if c.startswith("team:") else {}

st2, js2, key2, ctx2, host2, sent2, _e2 = world(lambda k: "team:tA", ask2, judge_payload=payload_zero)
asyncio.run(tick(host2))
d2 = js2.decisions(key2, ctx2, 5)
check("nhóm có judge_daily=0: phán xử dừng ở trần của NHÓM (judge_cap), không gọi model, không gửi",
      d2 and d2[0]["silence_code"] == "judge_cap" and ask2.calls == [] and sent2 == [], (d2, ask2.calls, sent2))
check("host hỏi payload phán xử cho đúng ngữ cảnh nhóm", "team:tA" in seen_payload, seen_payload)

ask3 = JudgeAsk()
st3, js3, key3, ctx3, host3, sent3, _e3 = world(lambda k: "team:tB", ask3,
                                                judge_payload=lambda c: {"judge_daily": None})
asyncio.run(tick(host3))
check("nhóm Không giới hạn (None): phán xử chạy và gửi", ask3.calls == ["agent_judge"] and len(sent3) == 1,
      (ask3.calls, sent3))

# ═════ 3. lỗi trước mốc engine: im, lượt được hoàn ═════
ask4 = JudgeAsk(boom_before_start=TypeError("sai chữ ký"))
st4, js4, key4, ctx4, host4, sent4, _e4 = world(lambda k: f"dm:{k}", ask4)
asyncio.run(tick(host4))
d4 = js4.decisions(key4, ctx4, 5)
check("lỗi ném trước started(): im với engine_unavailable (không phải policy_error), không gửi",
      d4 and d4[0]["verdict"] == "silent" and d4[0]["silence_code"] == "engine_unavailable" and sent4 == [], (d4, sent4))
rows4 = judge_rows(st4)
check("lỗi ném trước started(): dòng judge_batch được hoàn (không tính trần ngày)",
      len(rows4) == 1 and rows4[0]["status"] == "refunded", rows4)

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
