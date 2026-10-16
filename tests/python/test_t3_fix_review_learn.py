"""Review T3 phần L (0.156.0 sửa): lượt xem xét reaction và tự rà soát.

    python tests/run.py t3_fix_review_learn -v

- L P2-1: ask_back/revise ở chat riêng không mất vì hạn mức của lượt phán xử: lượt phán xử tiêu ở mức ưu tiên
  reaction_review; cần 2 lượt trống trước khi nhận lý do; phán xử dừng vì hết hạn mức thì trạng thái "Chờ hạn mức" và nguồn
  về lý do chờ (không tiêu thụ).
- L P2-2: tin mới ở chat riêng mang mã phiên nguồn để vào ĐÚNG phiên có tin bị phản hồi (phần main ở test_t3_fix_pulse_main).
- L P2-3: tự rà soát không đếm, không tiêu thụ nguồn còn nằm trong lý do reaction_review chưa xong, hay nhãn sinh từ reaction.
- L P2-4: quay lại một offset_set chỉ bỏ phần dịch của chính nó; phần chủ dạy bằng nhãn trong cửa sổ được giữ.
- L P2-5: lesson_add của tự rà soát cần ít nhất một bằng chứng của CHÍNH ngữ cảnh đích.
- L P3: chủ quay lại thay đổi qua route của P thì ghi `owner`, không phải `policy_revert`.

Kho thật, bộ chiếu T2 thật, hạn mức thật (`agent_budget`), lớp `agent_judge.Judge` THẬT với hàm hỏi model GIẢ (đánh dấu),
engine lượt xem xét GIẢ. Không model thật nào.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import json
import os
import tempfile
from contextlib import closing
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fix-learn-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-fix-learn-w-"))

import agent_budget as AB  # noqa: E402
import agent_judge as AJ  # noqa: E402
import agent_judge_store as AJS  # noqa: E402
import agent_ledger as ALG  # noqa: E402
import agent_review as AR  # noqa: E402
import feedback_projector as FP  # noqa: E402
import resonance_store as RS  # noqa: E402
import sessions  # noqa: E402
import team_store  # noqa: E402

MARK = getattr(AJ, "mark_test_fake", lambda f: f)
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:500]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def run(coro):
    return asyncio.run(coro)


def out(outcome, consumed, message="", reason="lý do"):
    return json.dumps({"outcome": outcome, "message": message, "consumed": consumed, "reason": reason},
                      ensure_ascii=False)


class Engine:
    def __init__(self):
        self.text = None
        self.calls = []

    def engine_for(self, key, ctx, purpose):
        async def run_(req):
            await req["engine_started"]()
            self.calls.append(req)
            return {"text": self.text, "tokens_in": 1, "tokens_out": 1}
        run_.marks_engine = True
        return "fake", "fake-model", run_


_n = [0]


def world(judge=None, ask_reply='{"verdict":"speak","score":0.95,"reason":"đúng lúc"}', resonance=False):
    """`judge=None`: lớp Judge THẬT (kho phán xử thật, hạn mức thật qua agent_budget.wire) với hàm hỏi GIẢ."""
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}"
    d.mkdir()
    gs = RS.GoalStore(d / "resonance.sqlite3")
    ss = sessions.SessionStore(db_path=d / "chat.db")
    tm = team_store.TeamStore(d / "team.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    key = gs.agent_set_enabled(owner, "viet", True)["agent_key"]
    AB.wire(lambda: gs)
    asks = []

    async def ask(prompt, purpose, started):
        started()
        asks.append(purpose)
        return ask_reply

    js = AJS.JudgeStore(d / "agent_judge.sqlite3")
    real = AJ.Judge(store=js, ledger=lambda: ALG.CallLedger(gs), wake=lambda: ALG.WakeReasons(gs), ask=MARK(ask),
                    engine_label=lambda: ("fake-aux", "j"), resonance_on=lambda k: resonance,
                    policy_change=lambda *a: None)
    AJ.wire(real)
    box = {"sent": [], "events": []}
    pj = FP.FeedbackProjector(resonance=lambda: gs, sessions=lambda: ss, teams=lambda: tm, team_view=lambda t, s, m: None)
    dsid = ss.create_session(brain=str(d), channel="agent:viet")
    other = ss.create_session(brain=str(d), channel="agent:viet")   # phiên báo cáo khác phiên nguồn
    eng = Engine()

    def send(k, ctx, text, meta):
        box["sent"].append((k, ctx, text, dict(meta)))
        return {"session_id": meta.get("session_id") or other, "message_id": 1}

    deps = AR.ReviewDeps(store=lambda: gs, reactions=pj, engine_for=eng.engine_for, persona=lambda k: "",
                         try_busy=lambda k, tok: True, release_busy=lambda k, tok: None, send=send,
                         publish=lambda ev: box["events"].append(ev), contexts=lambda k: [f"dm:{k}"])
    if judge is not None:
        deps.judge = judge
    host = AR.ReviewHost(deps)
    return {"gs": gs, "ss": ss, "pj": pj, "key": key, "dsid": dsid, "other": other, "eng": eng, "host": host,
            "W": ALG.WakeReasons(gs), "box": box, "asks": asks, "js": js, "judge": real}


def dm_react(w, text, value="sad", reasons=("too_long",)):
    mid = w["ss"].append_message(w["dsid"], "assistant", text)
    got = w["ss"].set_dm_reaction(w["dsid"], mid, value=value, reasons=list(reasons), comment="", nonce=f"n{mid}-{text}",
                                  agent_key=w["key"])
    w["pj"].project("dm", got["log_id"])
    return mid, f"rx:dm:{got['log_id']}"


def use_scans(w, n):
    led = ALG.CallLedger(w["gs"])
    for i in range(n):
        cid = led.reserve(purpose="scan", reason_ref=f"scan:fill:{w['key']}:{i}", agent_key=w["key"],
                          context_ref=f"dm:{w['key']}", obligation=False)
        led.settle(cid, "ok")


def pending(w):
    return w["W"].reasons(agent_key=w["key"], status="pending", code="reaction_review")


# ═════ L P2-1a: cap 6, đã quét 4: lượt xem xét (5) rồi lượt phán xử tin mới (6) vẫn chạy và gửi ═════
w = world()
use_scans(w, 4)
mid, src = dm_react(w, "Báo cáo dài dòng")
rs = pending(w)
w["eng"].text = out("revise", [src], message="Bản ngắn gọn hơn")
res = run(w["host"].serve_reaction_review(rs[0]))
check("cap 6 đã dùng 4: lượt phán xử tin mới không bị hạn mức chặn, tin revise được gửi",
      len(w["box"]["sent"]) == 1 and w["asks"] == ["agent_judge"], (res, w["box"]["sent"], w["asks"]))
st = w["ss"].dm_reaction_of(w["dsid"], mid)
check("trạng thái 'Đã điều chỉnh' (không phải 'Phán xử chọn im')", st["status"] == "adjusted", st)

# ═════ L P2-2: tin mới mang mã phiên nguồn ═════
check("tin mới ở chat riêng mang session_id của phiên có tin bị phản hồi, reply_to đúng tin",
      w["box"]["sent"] and w["box"]["sent"][0][3].get("session_id") == w["dsid"]
      and w["box"]["sent"][0][3].get("reply_to") == mid, w["box"]["sent"])

# ═════ L P2-1b: chỉ còn 1 lượt: không nhận lý do, phản hồi không bị tiêu thụ ═════
w2 = world()
use_scans(w2, 5)
mid2, src2 = dm_react(w2, "Tin thứ hai")
rs2 = pending(w2)
w2["eng"].text = out("revise", [src2], message="Bản sửa")
res2 = run(w2["host"].serve_reaction_review(rs2[0]))
check("chỉ còn 1 lượt ở chat riêng: lượt xem xét KHÔNG chạy (cần 2: xem xét + phán xử)",
      res2.get("outcome") == "quota" and w2["eng"].calls == [], res2)
check("chỉ còn 1 lượt: lý do giữ pending, nguồn chưa tiêu thụ",
      w2["W"].get(rs2[0]["id"])["status"] == "pending"
      and all(s["consumed_at"] is None for s in w2["W"].sources(rs2[0]["id"])), w2["W"].get(rs2[0]["id"]))
check("chỉ còn 1 lượt: trạng thái 'Chờ hạn mức'", w2["ss"].dm_reaction_of(w2["dsid"], mid2)["status"] == "waiting_quota",
      w2["ss"].dm_reaction_of(w2["dsid"], mid2))
import agent_pulse as AP  # noqa: E402
n_ev = len(AP.events(w2["gs"], w2["key"], kinds=("quota",)))
n_ws = len(w2["box"]["events"])
for _ in range(3):
    run(w2["host"].serve_reaction_review(rs2[0]))
check("hết hạn mức phục vụ lại nhiều nhịp: không ghi thêm dòng sự kiện 'quota' hay gói WS (khử lặp, L P3)",
      len(AP.events(w2["gs"], w2["key"], kinds=("quota",))) == n_ev == 1 and len(w2["box"]["events"]) == n_ws,
      (n_ev, len(AP.events(w2["gs"], w2["key"], kinds=("quota",))), n_ws, len(w2["box"]["events"])))


# ═════ L P2-1c: phán xử dừng vì hết hạn mức: nguồn về lý do chờ, trạng thái 'Chờ hạn mức' ═════
class QuotaJudge:
    def __init__(self):
        self.calls = 0

    async def proactive(self, **kw):
        self.calls += 1
        return {"verdict": "silent", "silence_code": "waiting_quota", "decision_id": 7, "reason": "agent_daily_calls"}

    def profile(self, k):
        return {"offsets": {}}

w3 = world(judge=QuotaJudge())
mid3, src3 = dm_react(w3, "Tin thứ ba")
rs3 = pending(w3)
w3["eng"].text = out("ask_back", [src3], message="Anh muốn ngắn cỡ nào?")
res3 = run(w3["host"].serve_reaction_review(rs3[0]))
st3 = w3["ss"].dm_reaction_of(w3["dsid"], mid3)
check("phán xử báo waiting_quota: trạng thái 'Chờ hạn mức', KHÔNG phải 'Phán xử chọn im'",
      st3["status"] == "waiting_quota", (st3, res3))
again = pending(w3)
check("phán xử báo waiting_quota: nguồn về một lý do chờ mới, chưa tiêu thụ (không mất phản hồi)",
      len(again) == 1 and [s["source_ref"] for s in w3["W"].sources(again[0]["id"], live_only=True)] == [src3],
      (again, res3))
check("không gửi tin nào", w3["box"]["sent"] == [])
res3b = run(w3["host"].serve_reaction_review(again[0]))
check("lùi lại: lý do vừa trả về không bị phục vụ lại ngay ở nhịp kế (backoff)", res3b.get("outcome") == "backoff", res3b)

import time  # noqa: E402

# ═════ L P2-3: tự rà soát không học lại phản hồi của lượt xem xét reaction chưa xong ═════
w4 = world()
k4 = w4["key"]
mA, srcA = dm_react(w4, "Tin A", value="love", reasons=())
mB, srcB = dm_react(w4, "Tin B", value="sad", reasons=("too_long",))
check("chuẩn bị: hai reaction nằm trong một lý do reaction_review đang chờ",
      len(pending(w4)) == 1 and {s["source_ref"] for s in w4["W"].sources(pending(w4)[0]["id"], True)} == {srcA, srcB})
dctx = f"dm:{k4}"
d_hist = w4["js"].add_decision({"agent_key": k4, "context_ref": dctx, "source_event": "pulse:1", "kind": "proactive",
                                "verdict": "silent", "excerpt": "nháp 1"})
d_rx = w4["js"].add_decision({"agent_key": k4, "context_ref": dctx, "source_event": "pulse:2", "kind": "proactive",
                              "verdict": "silent", "excerpt": "nháp 2"})
w4["judge"].label(d_hist, "should_speak", "owner", source="history")
w4["judge"].label(d_rx, "should_speak", "owner", source="reaction")
ev = w4["host"].evidence(w4["gs"], k4, time.time() + 1)
sig_refs = {s["source_ref"] for s in ev["signals"]}
lab_ids = {d["id"] for d in ev["labels"]}
check("tự rà soát: không đếm tín hiệu của reaction còn trong lý do reaction_review chưa xong",
      srcA not in sig_refs and srcB not in sig_refs, sig_refs)
check("tự rà soát: không đếm nhãn sinh từ trục nói/im của reaction; nhãn chủ sửa trong lịch sử vẫn đếm",
      d_rx not in lab_ids and d_hist in lab_ids, lab_ids)

# ═════ L P2-5: lesson_add cần bằng chứng của chính ngữ cảnh đích ═════
now5 = time.time()
r5 = w4["host"].apply_change(w4["gs"], k4, {"kind": "lesson_add", "context_ref": "team:B", "text": "Luôn báo số liệu",
                                             "lesson_kind": "method", "evidence": ["decision:1"], "why": "x"},
                             {"decision:1": "team:A"}, {}, ["team:A", "team:B", dctx], "selfreview:t5", now5)
check("lesson_add cho team:B chỉ có bằng chứng của team:A: bị từ chối (evidence_context)",
      r5["status"] == "rejected" and r5["reason"] == "evidence_context", r5)
r5b = w4["host"].apply_change(w4["gs"], k4, {"kind": "lesson_add", "context_ref": "team:B", "text": "Luôn báo số liệu",
                                              "lesson_kind": "method", "evidence": ["decision:1", "decision:2"],
                                              "why": "x"},
                              {"decision:1": "team:A", "decision:2": "team:B"}, {}, ["team:A", "team:B", dctx],
                              "selfreview:t5b", now5)
check("lesson_add có ít nhất một bằng chứng của team:B: được áp", r5b["status"] == "applied", r5b)

# ═════ L P2-4: quay lại offset_set giữ phần chủ dạy bằng nhãn ═════
w6 = world(resonance=True)
AR.ensure(w6["gs"])
k6 = w6["key"]
c6 = f"dm:{k6}"
now6 = time.time()
r6 = w6["host"].apply_change(w6["gs"], k6, {"kind": "offset_set", "context_ref": c6, "value": 0.2,
                                             "evidence": ["decision:1"], "why": "kín tiếng hơn"},
                             {"decision:1": c6}, {}, [c6], "selfreview:t6", now6)
check("chuẩn bị: tự rà soát đặt độ lệch 0.2", r6["status"] == "applied"
      and abs(w6["js"].get_offset(k6, c6) - 0.2) < 1e-3, (r6, w6["js"].get_offset(k6, c6)))
d6 = w6["js"].add_decision({"agent_key": k6, "context_ref": c6, "source_event": "pulse:6", "kind": "proactive",
                            "verdict": "silent", "excerpt": "nháp 6"})
lab6 = w6["judge"].label(d6, "should_speak", "owner", source="history")
mid6 = w6["js"].get_offset(k6, c6)
check("chuẩn bị: chủ dạy 'Đáng lẽ nên nói' trong cửa sổ quan sát dịch độ lệch xuống", lab6["effect"] == "applied"
      and mid6 < 0.2 - 0.05, (lab6, mid6))
w6["host"].revert_policy({"source": f"self_review:{r6['change_id']}", "revert_by": "owner", "revert_reason": "owner"})
after6 = w6["js"].get_offset(k6, c6)
check("quay lại offset_set: chỉ bỏ phần +0.2 của chính nó, phần chủ dạy được giữ (không về 0.0)",
      abs(after6 - (mid6 - 0.2)) < 2e-3, (mid6, after6))
with closing(w6["gs"]._conn()) as c:
    rr = c.execute("SELECT status, revert_reason FROM agent_review_changes WHERE id=?", (r6["change_id"],)).fetchone()
check("chủ quay lại qua route của P: lý do ghi 'owner' (L P3)", rr["status"] == "reverted"
      and rr["revert_reason"] == "owner", dict(rr))

# ═════ L P3: chuỗi rỗng không phải "Không giới hạn"; thay đổi kẹt ở applying hiện ra ═════
try:
    AB.set_cap(w6["gs"], k6, "", by="owner")
    empty_ok = False
except ValueError:
    empty_ok = True
check("set_cap(''): lỗi nhập (route trả 400), không âm thầm bỏ trần", empty_ok and AB.get_cap(w6["gs"], k6) == 6,
      AB.get_cap(w6["gs"], k6))
stale = w6["host"]._record_change(w6["gs"], "selfreview:crash", k6, c6, "lesson_add", "lesson_add|x|y", ["decision:1"],
                                  "x", "applying", now=now6 - 3600)
w6["host"].refresh_policy_labels(now6)
vp = w6["host"].view_part(w6["gs"], k6)
row_c = next((c for c in vp["changes"] if c["id"] == stale), None)
check("tiến trình chết giữa applying và applied: thay đổi hiện trong mục Bài học với trạng thái interrupted",
      row_c is not None and row_c["status"] == "interrupted" and not row_c["can_revert"], row_c)

# ═════ vòng 2 P2-2: trần phán xử của nhóm không làm lượt xem xét gọi model lặp lại ═════
# Neo 10 giờ sáng hôm nay (giờ máy): bốn lần thử lại cách nhau một giờ vẫn trong cùng ngày, chạy lúc nào cũng vậy.
# Trước đây lấy giờ thật, chạy sau 20 giờ thì lần thử thứ tư sang ngày mới (trần judge_daily làm mới) và test đỏ giả.
CLK = [AB.day_start(time.time()) + 10 * 3600]
TCTX = "team:tcap"


class CapJudge(QuotaJudge):
    async def proactive(self, **kw):
        self.calls += 1
        return {"verdict": "silent", "silence_code": "judge_cap", "decision_id": 8, "reason": ""}


# (a) trần judge_daily của nhóm đã hết TRƯỚC khi nhận lý do: không lời gọi model nào, dù thử lại nhiều lần
w8 = world()
w8["host"].deps.clock = lambda: CLK[0]
w8["host"].deps.judge_payload = lambda ctx: {"judge_daily": 1} if ctx.startswith("team:") else {}
k8 = w8["key"]
led8 = ALG.CallLedger(w8["gs"])
c8 = led8.reserve(purpose="judge_batch", reason_ref="judge:tcap:used", agent_key="", context_ref=TCTX, obligation=False)
led8.settle(c8, "ok")
r8 = w8["W"].add_source(k8, TCTX, "reaction_review", "rx:team:881", now=CLK[0])
w8["eng"].text = out("revise", ["rx:team:881"], message="Bản sửa")
outs8 = []
for i in range(4):
    CLK[0] += 3600
    outs8.append(run(w8["host"].serve_reaction_review(w8["W"].get(r8)))["outcome"])
check("trần phán xử của nhóm đã hết: 4 lần thử lại, KHÔNG lời gọi model nào của lượt xem xét",
      w8["eng"].calls == [] and all(o in ("quota", "backoff") for o in outs8), (len(w8["eng"].calls), outs8))
check("trần phán xử của nhóm đã hết: lý do giữ pending, nguồn chưa tiêu thụ",
      w8["W"].get(r8)["status"] == "pending", w8["W"].get(r8))

# (b) bộ phán xử báo judge_cap SAU lượt xem xét: lùi tới ngày mới, thử lại trong ngày không gọi model lần nữa
w9 = world(judge=CapJudge())
CLK[0] = AB.day_start(time.time()) + 10 * 3600   # 10 giờ sáng
w9["host"].deps.clock = lambda: CLK[0]
k9 = w9["key"]
r9 = w9["W"].add_source(k9, TCTX, "reaction_review", "rx:team:991", now=CLK[0])
w9["eng"].text = out("revise", ["rx:team:991"], message="Bản sửa")
run(w9["host"].serve_reaction_review(w9["W"].get(r9)))
check("chuẩn bị: một lượt xem xét chạy, phán xử báo judge_cap, nguồn về lý do chờ",
      len(w9["eng"].calls) == 1 and len(pending(w9)) == 1, (len(w9["eng"].calls), pending(w9)))
for i in range(5):
    CLK[0] += 3600
    for rr in pending(w9):
        run(w9["host"].serve_reaction_review(rr))
check("judge_cap: thử lại trong cùng ngày (5 giờ) không thêm lời gọi model nào", len(w9["eng"].calls) == 1,
      len(w9["eng"].calls))
CLK[0] = AB.day_start(CLK[0]) + 86400 + 3600 * 2   # 2 giờ sáng hôm sau
for rr in pending(w9):
    run(w9["host"].serve_reaction_review(rr))
check("judge_cap: sang ngày mới thì xem lại đúng một lần", len(w9["eng"].calls) == 2, len(w9["eng"].calls))

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
