"""T3 (0.156.0): chính sách nhịp thích nghi `pulse.v1` (thiết kế 2026-10-15 mục 5.2-5.4, 6.1-6.4; GOAL 12 ca 6, 9, 16, 22).

    python tests/run.py agent_pulse_policy -v

Kho thật (resonance.sqlite3 tạm), đồng hồ GIẢ (mọi hàm nhận `at`), không model. Mọi vectơ số của bảng 5.3 có ở đây với số
chính xác: k của cá nhân hoá, u của từng kịch bản, mốc đổi bậc của dòng thời gian im lặng (ngày 2 B4, ngày 8 B5, ngày 22
B6, ngày 50 B7, ngày 110 ngủ đông), 6 reaction "sai dữ kiện", tin chủ động đã xem bị bỏ qua, im lặng đúng không bị phạt,
gọi trực tiếp lúc ngủ đông, thay/bỏ tín hiệu, lệch đồng hồ, max_tier.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile
from contextlib import closing
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-pulse-policy-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-pulse-policy-w-"))

import agent_pulse as AP  # noqa: E402
import resonance_store as RS  # noqa: E402

fails = []
D = 86400.0
T0 = 2_000_000_000.0   # đồng hồ giả, sau mọi mốc thật (resumed_at thật lúc bật không chen vào)


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def near(a, b, tol):
    return abs(float(a) - float(b)) <= tol


_n = [0]


def world(ctx_kind="dm"):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}"
    d.mkdir()
    st = RS.GoalStore(d / "resonance.sqlite3")
    owner = RS.Principal("owner", "owner", str(d))
    key = st.agent_set_enabled(owner, "viet", True)["agent_key"]
    AP.ensure(st)
    AP.set_state(st, key, "*", "on", at=T0)
    ctx = f"dm:{key}" if ctx_kind == "dm" else "team:tm_a"
    AP.ensure_row(st, key, ctx, at=T0)
    return st, key, ctx


def heart(st, key, ctx, i, at, value="love", reasons=(), reactor="owner"):
    return AP.record_reaction(st, source_ref=f"rx:dm:{i}", agent_key=key, context_ref=ctx, value=value,
                              reasons=list(reasons), reactor=reactor, at=at)


def run_schedule(st, key, ctx, until, start=None, opp=None, feed=None):
    """Đánh giá theo lịch như host: kỳ kế tiếp = kỳ này + khoảng của bậc hiện tại. `feed`: [(at, i, value)] tín hiệu chỉ
    được ghi khi đồng hồ giả đã tới (không ghi trước tín hiệu "tương lai"). Trả [(ngày, bậc trước, bậc sau, lý do, u)]."""
    t = start if start is not None else T0 + AP.TIERS[AP.INIT_TIER]
    log = []
    pending = sorted(feed or [])
    while t <= until:
        while pending and pending[0][0] <= t:
            at, i, value = pending.pop(0)
            heart(st, key, ctx, i, at, value=value)
        r = AP.evaluate(st, key, ctx, at=t, has_opportunity=bool(opp and opp(t)))
        log.append(((t - T0) / D, r["tier_before"], r["tier"], r["reason"], r["u"]))
        iv = AP.interval(r["tier"])
        if iv is None:
            break
        t += iv
    return log


# ═══════════════ hàm thuần: k, tín hiệu, phân giải ═══════════════

check("k: 1 ❤️ (n=1) ≈ 1.00 (0,9962)", near(AP.compute_k(1, 1), 0.99624, 5e-5), AP.compute_k(1, 1))
check("k: 2 ❤️ (n=2) ≈ 0.99 (0,9864)", near(AP.compute_k(2, 2), 0.98636, 5e-5), AP.compute_k(2, 2))
check("k: 5 ❤️ (n=5) ≈ 0.935", near(AP.compute_k(5, 5), 0.93478, 5e-5), AP.compute_k(5, 5))
check("k: 10 ❤️ (n=10) ≈ 0.82", near(AP.compute_k(10, 10), 0.82143, 5e-5), AP.compute_k(10, 10))
check("k dùng lẫn (n=5): ❤️ = 😮 ≈ 0.997", near(AP.compute_k(5, 1), 0.99739, 5e-5), AP.compute_k(5, 1))
check("k dùng lẫn (n=5): 👍 ×3 ≈ 0.966", near(AP.compute_k(5, 3), 0.96609, 5e-5), AP.compute_k(5, 3))
check("k dùng lẫn (n=5): emoji chưa dùng ≈ 1.013", near(AP.compute_k(5, 0), 1.01304, 5e-5), AP.compute_k(5, 0))
check("k: không mẫu thì đúng 1", AP.compute_k(0, 0) == 1.0)
ks = [AP.compute_k(n, c) for n in range(0, 400, 7) for c in range(0, n + 1, max(1, n // 9 or 1))]
check("k luôn trong 0.75-1.25 (người dùng nhất quán không trôi về âm)", min(ks) >= 0.75 and max(ks) <= 1.25,
      (min(ks), max(ks)))
check("k: người chỉ dùng ❤️ rất lâu vẫn ≥ 0.75", AP.compute_k(10000, 10000) == 0.75)
check("k: emoji hiếm tối đa 1.25", AP.compute_k(10000, 0) <= 1.25)

check("tín hiệu 6.1: ❤️ +0.8 w1, 😮 +0.6 w1, 👍 +0.4 w1 (cá nhân hoá)",
      AP.reaction_signal("love") == ("reaction", 0.8, 1.0, True) and AP.reaction_signal("wow")[1:3] == (0.6, 1.0)
      and AP.reaction_signal("like")[1:3] == (0.4, 1.0))
check("tín hiệu 6.1: 😂 +0.2 w0.5; 😢 -0.2 w0.4; 😡 -0.4 w0.4",
      AP.reaction_signal("haha")[1:3] == (0.2, 0.5) and AP.reaction_signal("sad")[1:3] == (-0.2, 0.4)
      and AP.reaction_signal("angry")[1:3] == (-0.4, 0.4))
check("lý do thắng emoji: ❤️ kèm 'sai dữ kiện' là -0.6 w1, không cá nhân hoá",
      AP.reaction_signal("love", ["wrong_facts"]) == ("reaction_reason", -0.6, 1.0, False))
check("lý do: đúng ý +0.8; quá dài -0.3; nhiều lý do lấy trung bình",
      AP.reaction_signal(None, ["on_point"])[1] == 0.8 and AP.reaction_signal(None, ["too_long"])[1] == -0.3
      and near(AP.reaction_signal(None, ["on_point", "too_long"])[1], 0.25, 1e-12))
check("trục nói/im và góp ý chữ không vào ước lượng (không tín hiệu)",
      AP.reaction_signal(None, []) is None and AP.reaction_signal("", ["should_speak"]) is None)
check("|w·v| ≤ 1 cho mọi nguồn của bảng", all(abs(v * w) <= 1 for v, w in list(AP.EMOJI_SIGNAL.values())
                                               + list(AP.HOST_EVENT.values()) + [AP.SEEN_IGNORED]))
check("lệch đồng hồ: tuổi âm kẹp về 0", AP.decay(-5 * D) == 1.0 and near(AP.decay(21 * D), 0.5, 1e-12))
check("thang bậc: B0 15 phút ... B7 1 tháng, trên B7 ngủ đông",
      AP.TIERS == (900, 3600, 14400, 86400, 259200, 604800, 1209600, 2592000) and AP.interval(8) is None
      and AP.state_for_tier(8) == "hibernating" and AP.state_for_tier(2) == "active"
      and AP.state_for_tier(3) == "awake" and AP.state_for_tier(4) == "awake" and AP.state_for_tier(5) == "slowed")

# ═══════════════ vectơ 5.3 (từ B3, Cộng hưởng bật) ═══════════════

# 1 ❤️ và 2 ❤️: giữ B3.
st, key, ctx = world()
heart(st, key, ctx, 1, T0 + 3600)
r = AP.evaluate(st, key, ctx, at=T0 + 3600)
check("1 ❤️: u ≈ 0.05 (0,0531)", near(r["u"], 0.0531, 5e-4), r)
log = run_schedule(st, key, ctx, T0 + 3 * D)
check("1 ❤️: giữ B3 qua dwell (bằng chứng mới < 1.0)", all(x[2] == 3 for x in log[:2]), log)
st, key, ctx = world()
heart(st, key, ctx, 1, T0 + 3600)
heart(st, key, ctx, 2, T0 + 3600)
r = AP.evaluate(st, key, ctx, at=T0 + 3600)
check("2 ❤️: u ≈ 0.13 (0,1315)", near(r["u"], 0.1315, 5e-4), r)
r = AP.evaluate(st, key, ctx, at=T0 + 2 * D)
check("2 ❤️: giữ B3 sau dwell (u < 0.25)", r["tier"] == 3 and r["reason"] == "hold", r)

# 5 ❤️ trong 3 ngày (tuổi trung bình 1,5 ngày lúc đánh giá ngày 3): lên đúng một bậc.
st, key, ctx = world()
for i, dd in enumerate((0.5, 1.0)):
    heart(st, key, ctx, i, T0 + dd * D)
r1 = AP.evaluate(st, key, ctx, at=T0 + 1 * D)
for i, dd in enumerate((1.5, 2.0), start=2):
    heart(st, key, ctx, i, T0 + dd * D)
r2 = AP.evaluate(st, key, ctx, at=T0 + 2 * D)
heart(st, key, ctx, 4, T0 + 2.5 * D)
r3 = AP.evaluate(st, key, ctx, at=T0 + 3 * D)
check("5 ❤️/3 ngày: ngày 1 giữ (dwell), ngày 2 giữ (4 ❤️, u = 0,2494 < 0.25)",
      r1["tier"] == 3 and r1["reason"] == "dwell" and r2["tier"] == 3 and near(r2["u"], 0.2494, 5e-4), (r1, r2))
check("5 ❤️/3 ngày: k ≈ 0.935, u ≈ 0.28 (0,2861) ở ngày 3, lên B2", r3["tier"] == 2 and near(r3["u"], 0.2861, 5e-4)
      and r3["reason"] == "helpful", r3)
log = run_schedule(st, key, ctx, T0 + 6 * D, start=T0 + 3 * D + AP.TIERS[2])
check("5 ❤️/3 ngày: không lên tiếp khi không có bằng chứng mới (không B1)", all(x[2] >= 2 for x in log), log)
ups = [e for e in AP.events(st, key, ctx, kinds=("tier_change",)) if e["after"]["tier"] < e["before"]["tier"]]
check("5 ❤️/3 ngày: đúng một lần lên bậc, có trước/sau, u, new, lý do, phiên bản chính sách",
      len(ups) == 1 and ups[0]["before"]["tier"] == 3 and ups[0]["after"]["tier"] == 2 and ups[0]["reason"] == "helpful"
      and ups[0]["policy_version"] == "pulse.v1" and ups[0]["u"] is not None and ups[0]["new"] >= 1.0, ups)

# 5 ❤️ rải đều 2 tuần: sát ngưỡng, giữ B3. Mốc ghim: ❤️ ở H0 + 0; 3,5; 7; 10,5; 14 ngày; đánh giá mỗi ngày lúc H0 + k + 8h.
st, key, ctx = world()
H0 = T0 + 0.1 * D
feed = [(H0 + dd * D, i, "love") for i, dd in enumerate((0.0, 3.5, 7.0, 10.5, 14.0))]
tiers, us = [], {}
for k in range(0, 21):
    while feed and feed[0][0] <= H0 + k * D + D / 3:
        at, i, v = feed.pop(0)
        heart(st, key, ctx, i, at, value=v)
    r = AP.evaluate(st, key, ctx, at=H0 + k * D + D / 3)
    tiers.append(r["tier"])
    us[k] = r["u"]
check("5 ❤️ rải 2 tuần: u ≈ 0.248 (0,2481) ở ngày 14 + 8 giờ", near(us[14], 0.2481, 3e-4), us[14])
check("5 ❤️ rải 2 tuần: u lớn nhất cả dải vẫn < 0.25", max(us.values()) < 0.25, max(us.values()))
check("5 ❤️ rải 2 tuần: giữ B3 suốt đợt (không bao giờ lên), chỉ chậm lại khi im quá 4 ngày sau ❤️ cuối",
      min(tiers) == 3 and set(tiers[:18]) == {3}, tiers)

# 10 ❤️ trong 1 phút: tối đa một bậc; kỳ sau không lên nữa. Rồi 10 ❤️ rải 2 tuần: lên B1 (= max_tier), không B0.
st, key, ctx = world()
for i in range(10):
    heart(st, key, ctx, i, H0 + i * 6)
r = AP.evaluate(st, key, ctx, at=H0 + 61)
check("10 ❤️/1 phút: k ≈ 0.82, u ≈ 0.39 (0,3912)", near(r["u"], 0.3912, 5e-4), r)
r = AP.evaluate(st, key, ctx, at=T0 + 2 * D)
check("10 ❤️/1 phút: lên đúng một bậc (B2) ở kỳ sau dwell", r["tier"] == 2 and r["reason"] == "helpful", r)
r = AP.evaluate(st, key, ctx, at=T0 + 2 * D + AP.TIERS[2])
check("10 ❤️/1 phút: kỳ sau không lên nữa (không bằng chứng mới)", r["tier"] == 2, r)
log = run_schedule(st, key, ctx, T0 + 40 * D, start=T0 + 2 * D + 2 * AP.TIERS[2],
                   feed=[(T0 + (2.5 + 1.4 * i) * D, 100 + i, "love") for i in range(10)])
check("thêm 10 ❤️ rải 2 tuần: lên B1 (= max_tier)", any(x[2] == 1 for x in log), log[:40])
check("không bao giờ B0 khi max_tier mặc định B1", min(x[2] for x in log) == 1, min(x[2] for x in log))
check("mọi lần lên bậc đều cách nhau ít nhất một dwell (không nhảy liên tục)",
      all(b[0] - a[0] >= 1.0 - 1e-9 for a, b in zip([x for x in log if x[1] != x[2]], [x for x in log if x[1] != x[2]][1:])))
check("im lâu sau đợt cuối thì chậm dần lại (low_demand)", log[-1][2] >= 3, log[-6:])

# Chủ dùng lẫn: 3 👍, 1 ❤️, 1 😮 trong vài ngày (n = 5): k tính lại từ lịch sử hiện tại trong kho.
st, key, ctx = world()
for i, (v, dd) in enumerate((("like", 0.2), ("like", 0.9), ("love", 1.3), ("like", 1.8), ("wow", 2.4))):
    heart(st, key, ctx, i, T0 + dd * D, value=v)
with closing(st._conn()) as c:
    kt = AP._k_table(c, [("owner", "dm")], T0 + 3 * D)
n, counts = kt[("owner", "dm")]
check("dùng lẫn: kho đếm n = 5 (3 👍, 1 ❤️, 1 😮)", n == 5 and counts == {"like": 3, "love": 1, "wow": 1}, kt)
check("dùng lẫn: k(❤️) = k(😮) ≈ 0.997, k(👍) ≈ 0.966, chưa dùng ≈ 1.013",
      near(AP.compute_k(n, counts["love"]), 0.997, 5e-4) and near(AP.compute_k(n, counts["wow"]), 0.997, 5e-4)
      and near(AP.compute_k(n, counts["like"]), 0.966, 5e-4) and near(AP.compute_k(n, counts.get("sad", 0)), 1.013, 5e-4))
heart(st, key, ctx, 50, T0 + 2.5 * D, value="love")  # cùng người, ngữ cảnh nhóm không đổi k của chat riêng
AP.record_reaction(st, source_ref="rx:team:9", agent_key=key, context_ref="team:tm_x", value="angry", at=T0 + 2.6 * D)
with closing(st._conn()) as c:
    kt2 = AP._k_table(c, [("owner", "dm"), ("owner", "team")], T0 + 3 * D)
check("k theo người VÀ loại ngữ cảnh: reaction ở nhóm không vào k của chat riêng",
      kt2[("owner", "dm")][0] == 6 and kt2[("owner", "team")] == (1, {"angry": 1}), kt2)

# k TÍNH LẠI mỗi kỳ: cùng một ❤️ cũ có giá trị khác khi lịch sử dày lên.
st, key, ctx = world()
heart(st, key, ctx, 1, T0 + 100)
u1 = AP.evaluate(st, key, ctx, at=T0 + 200)["u"]
for i in range(9):
    heart(st, key, ctx, 10 + i, T0 + 150 + i)
u10 = AP.evaluate(st, key, ctx, at=T0 + 200)
exp = (10 * 0.8 * AP.compute_k(10, 10) * AP.decay(60)) / (10 * AP.decay(60) + 2)
check("k tính lại mỗi kỳ từ lịch sử hiện tại (không đóng băng lúc ghi)",
      near(u10["estimate"], exp, 2e-3) and near(u1, 0.0531, 1e-3), (u1, u10["estimate"], exp))

# Im hoàn toàn từ B3: ngày 2 B4, ngày 8 B5, ngày 22 B6, ngày 50 B7, ngày 110 ngủ đông.
st, key, ctx = world()
log = run_schedule(st, key, ctx, T0 + 200 * D)
changes = [(round(x[0], 3), x[2]) for x in log if x[1] != x[2]]
check("im lặng: đổi bậc đúng ngày 2 (B4), 8 (B5), 22 (B6), 50 (B7), 110 (ngủ đông)",
      changes == [(2.0, 4), (8.0, 5), (22.0, 6), (50.0, 7), (110.0, 8)], changes)
check("im lặng: mọi lần xuống là low_demand (không ghi là agent sai)",
      all(x[3] == "low_demand" for x in log if x[1] != x[2]), log)


def tier_at(day):
    cur = 3
    for d0, t in changes:
        if d0 <= day:
            cur = t
    return cur


check("im lặng: ngày 30 ở B6, ngày 90 ở B7, ngày 180 đang ngủ đông",
      tier_at(30) == 6 and tier_at(90) == 7 and tier_at(180) == 8)
row = AP.row(st, key, ctx)
check("ngủ đông: không có lịch chủ động, trạng thái hibernating", row["state"] == "hibernating"
      and row["next_due_at"] is None, row)
est_before = row["estimate"]
check("chủ gọi trực tiếp lúc ngủ đông: về B3 ngay", AP.wake(st, key, ctx, "direct_call", at=T0 + 181 * D))
row = AP.row(st, key, ctx)
check("thức lại: B3, awake, có lịch, giữ ước lượng", row["tier"] == 3 and row["state"] == "awake"
      and row["next_due_at"] == T0 + 182 * D and row["estimate"] == est_before, row)

# 3 tin chủ động đã xem bị bỏ qua: u ≈ -0.02, không xuống vì unhelpful; chỉ low_demand khi không có gì khác.
st, key, ctx = world()
for mid in (11, 12, 13):
    AP.record_proactive(st, key, ctx, "s1", mid, at=T0 + 0.1 * D)
AP.record_proactive(st, key, ctx, "s1", 14, at=T0 + 0.1 * D)          # chưa có bằng chứng xem
AP.mark_seen(st, ctx, [11, 12, 13], agent_key=key, at=T0 + 0.2 * D)
early = AP.sweep_seen_ignored(st, lambda p: False, at=T0 + 1.1 * D)
check("tin đã xem chưa đủ 24 giờ: chưa tạo tín hiệu", early == [], early)
got = AP.sweep_seen_ignored(st, lambda p: False, at=T0 + 1.3 * D)
check("qua 24 giờ không tương tác: 3 tín hiệu seen_ignored (-0.15, w 0.6), tin chưa xem không có",
      sorted(got) == [f"seen:{ctx}:11", f"seen:{ctx}:12", f"seen:{ctx}:13"], got)
sigs = AP.signals(st, key, ctx, status="live")
check("seen_ignored mang đúng giá trị và độ tin", all(s["value"] == -0.15 and s["w"] == 0.6 for s in sigs
                                                      if s["kind"] == "seen_ignored"), sigs)
r = AP.evaluate(st, key, ctx, at=T0 + 1.3 * D)
check("3 tin bị bỏ qua: u ≈ -0.02 (-0,0221)", near(r["u"], -0.0221, 5e-4), r)
r = AP.evaluate(st, key, ctx, at=T0 + 2 * D)
check("3 tin bị bỏ qua: không xuống vì unhelpful; low_demand vì không có gì khác",
      r["tier"] == 4 and r["reason"] == "low_demand", r)
st2, key2, ctx2 = world()
for mid in (11, 12, 13):
    AP.record_proactive(st2, key2, ctx2, "s1", mid, at=T0 + 0.1 * D)
AP.mark_seen(st2, ctx2, [11, 12, 13], agent_key=key2, at=T0 + 0.2 * D)
AP.sweep_seen_ignored(st2, lambda p: False, at=T0 + 1.3 * D)
r = AP.evaluate(st2, key2, ctx2, at=T0 + 2 * D, has_opportunity=True)
check("3 tin bị bỏ qua nhưng có cơ hội: giữ (không unhelpful, không low_demand)", r["tier"] == 3, r)
st3, key3, ctx3 = world()
AP.record_proactive(st3, key3, ctx3, "s1", 21, at=T0 + 0.1 * D)
AP.mark_seen(st3, ctx3, [21], agent_key=key3, at=T0 + 0.2 * D)
check("tin đã xem nhưng chủ có tương tác: không tín hiệu", AP.sweep_seen_ignored(st3, lambda p: True,
                                                                                at=T0 + 2 * D) == [])

# 5 reaction "sai dữ kiện": giữ; reaction thứ 6: xuống một bậc (unhelpful) sau dwell.
for n_bad, want_tier, want_u in ((5, 3, -0.2378), (6, 4, -0.2697)):
    st, key, ctx = world()
    for i in range(n_bad):
        heart(st, key, ctx, i, T0 + 2 * D - 3600, value="sad", reasons=["wrong_facts"])
    r = AP.evaluate(st, key, ctx, at=T0 + 2 * D)
    check(f"{n_bad} reaction 'sai dữ kiện': u ≈ {want_u}, bậc B{want_tier}", near(r["u"], want_u, 5e-4)
          and r["tier"] == want_tier and r["reason"] == ("hold" if n_bad == 5 else "unhelpful"), r)

# Im lặng đúng (không tín hiệu, có cơ hội nhưng phán xử chọn im): không phạt, không low_demand khi có cơ hội.
st, key, ctx = world()
r2 = AP.evaluate(st, key, ctx, at=T0 + 2 * D, has_opportunity=True)
r3 = AP.evaluate(st, key, ctx, at=T0 + 3 * D, has_opportunity=False)
r4 = AP.evaluate(st, key, ctx, at=T0 + 4 * D, has_opportunity=False)
check("im lặng đúng: kỳ có cơ hội giữ B3; ước lượng 0 (không phạt)", r2["tier"] == 3 and r2["estimate"] == 0.0, r2)
check("cơ hội đã tiêu thụ không giữ mãi: kỳ sau còn trong hai lần khoảng thì giữ, quá thì low_demand",
      r3["tier"] == 3 and r4["tier"] == 4 and r4["reason"] == "low_demand", (r3, r4))
check("im lặng đúng không tạo tín hiệu âm nào", AP.signals(st, key, ctx) == [])

# Thay/bỏ reaction, xoá nguồn.
st, key, ctx = world()
heart(st, key, ctx, 1, T0 + 100, value="love")
check("ghi lại cùng nguồn: idempotent (False, không thêm dòng)", heart(st, key, ctx, 1, T0 + 100) is False
      and len(AP.signals(st, key, ctx)) == 1)
AP.supersede_signal(st, "rx:dm:1", "rx:dm:2")
heart(st, key, ctx, 2, T0 + 200, value="sad")
live = AP.signals(st, key, ctx, status="live")
old = [s for s in AP.signals(st, key, ctx) if s["source_ref"] == "rx:dm:1"][0]
check("thay reaction: dòng cũ superseded_by dòng mới, chỉ một dòng sống (không cộng dồn)",
      [s["source_ref"] for s in live] == ["rx:dm:2"] and old["status"] == "superseded"
      and old["superseded_by"] == "rx:dm:2", (live, old))
AP.void_signal(st, "rx:dm:2")
r = AP.evaluate(st, key, ctx, at=T0 + 300)
check("xoá nguồn: void, ước lượng về 0", AP.signals(st, key, ctx, status="live") == [] and r["estimate"] == 0.0, r)
check("reaction của agent không bao giờ thành tín hiệu",
      heart(st, key, ctx, 3, T0 + 400, reactor="agent:ag_x") is False and AP.signals(st, key, ctx, status="live") == [])
try:
    AP.record_signal(st, source_ref="x:1", agent_key=key, context_ref=ctx, kind="host_event", value=1.0, w=1.5)
    check("|w·v| > 1 bị từ chối", False)
except ValueError:
    check("|w·v| > 1 bị từ chối", True)

# Share: một nguồn chia cho nhiều agent, mỗi agent dùng w·share ở tử số và n_eff.
st, key, ctx = world()
AP.record_signal(st, source_ref="rx:team:5", agent_key=key, context_ref=ctx, kind="reaction_reason", value=0.8, w=1.0,
                 share=0.5, at=T0 + 100, personalize=False)
r = AP.evaluate(st, key, ctx, at=T0 + 100)
check("share 0.5: n_eff = 0.5, estimate = 0.4/2.5", near(r["n_eff"], 0.5, 1e-9) and near(r["estimate"], 0.16, 1e-9), r)

# Lệch đồng hồ: tín hiệu "tương lai" tuổi 0; next_due quá khứ xa chỉ chạy một lần rồi tính lại.
st, key, ctx = world()
heart(st, key, ctx, 1, T0 + 10 * D)
r = AP.evaluate(st, key, ctx, at=T0 + 1 * D)
check("lệch đồng hồ: tín hiệu ghi 'tương lai' tính tuổi 0 (decay 1)", near(r["n_eff"], 1.0, 1e-12), r)
far = T0 + 400 * D
AP.evaluate(st, key, ctx, at=far)
row = AP.row(st, key, ctx)
check("next_due_at quá khứ xa: sau một lần chạy, lịch tính lại từ bây giờ (không chạy bù)",
      row["last_eval_at"] == far, row)

# Hysteresis (đổi chiều chỉ sau ít nhất một kỳ giữ) và max_tier (hàm thuần).
base = {"tier": 3, "max_tier": 1, "last_change_at": T0, "resumed_at": T0, "last_change_dir": +1, "holds": 0}
sc_down = {"u": -0.3, "new": 2.0, "engagement_new": 2, "last_engagement_at": T0 + D}
check("hysteresis: vừa lên bậc thì chưa xuống ngay (cần một kỳ giữ)",
      AP.decide(base, sc_down, T0 + 3 * D)["reason"] == "hysteresis")
check("hysteresis: sau một kỳ giữ thì được đổi chiều",
      AP.decide(dict(base, holds=1), sc_down, T0 + 3 * D) == {"tier": 4, "dir": -1, "reason": "unhelpful"})
sc_up = {"u": 0.4, "new": 3.0, "engagement_new": 3, "last_engagement_at": T0 + D}
check("max_tier B1: ở B1 không lên B0", AP.decide(dict(base, tier=1, last_change_dir=0), sc_up, T0 + 3 * D)
      == {"tier": 1, "dir": 0, "reason": "max_tier"})
check("chủ cấp B0: lên được B0", AP.decide(dict(base, tier=1, max_tier=0, last_change_dir=0), sc_up, T0 + 3 * D)
      ["tier"] == 0)
check("từ B7 'unhelpful' không đưa vào ngủ đông", AP.decide(dict(base, tier=7, last_change_dir=0),
                                                          dict(sc_down, last_engagement_at=T0 + 90 * D - 3600),
                                                          T0 + 90 * D)["reason"] == "hold")
st, key, ctx = world()
for who in ("leader", "agent:ag_x", "full_authority"):
    try:
        AP.set_max_tier(st, key, 0, by=who)
        check(f"max_tier: '{who}' không nâng được", False)
    except PermissionError:
        check(f"max_tier: '{who}' không nâng được", True)
AP.set_max_tier(st, key, 0, by="owner", at=T0 + 10)
check("max_tier: chủ cấp B0 cho mọi ngữ cảnh", all(r["max_tier"] == 0 for r in AP.rows(st, key)))
AP.set_max_tier(st, key, 4, by="owner", at=T0 + 20)
check("max_tier chậm hơn bậc hiện tại: bậc chậm lại tới trần", AP.row(st, key, ctx)["tier"] == 4)

# Thức lại min(bậc, B3); tạm dừng/tắt không thức; bật lại về B3 giữ ước lượng, ghi resumed_at mọi ngữ cảnh.
st, key, ctx = world()
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=2, state='active' WHERE agent_key=? AND context_ref=?", (key, ctx))
AP.wake(st, key, ctx, "reaction", at=T0 + 50)
check("thức lại khi đang nhanh hơn B3: giữ bậc", AP.row(st, key, ctx)["tier"] == 2)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=6, state='slowed' WHERE agent_key=? AND context_ref=?", (key, ctx))
AP.wake(st, key, ctx, "new_job", at=T0 + 60)
check("thức lại từ B6: về B3", AP.row(st, key, ctx)["tier"] == 3)
AP.set_state(st, key, ctx, "paused", at=T0 + 70)
check("tạm dừng: thức lại không đổi gì", AP.wake(st, key, ctx, "reaction", at=T0 + 80) is False
      and AP.row(st, key, ctx)["state"] == "paused")
AP.set_state(st, key, ctx, "resume", at=T0 + 90)
check("tiếp: về trạng thái theo bậc, có lịch", AP.row(st, key, ctx)["state"] == "awake"
      and AP.row(st, key, ctx)["next_due_at"] == T0 + 90 + D)
AP.set_state(st, key, ctx, "waiting_owner", at=T0 + 100)
check("chờ chủ: reaction không gỡ, chủ nhắn thì gỡ",
      AP.wake(st, key, ctx, "reaction", at=T0 + 110) is False and AP.wake(st, key, ctx, "owner_message", at=T0 + 120)
      and AP.row(st, key, ctx)["state"] == "awake")
heart(st, key, ctx, 1, T0 + 130)
AP.evaluate(st, key, ctx, at=T0 + 140)
est = AP.row(st, key, ctx)["estimate"]
AP.set_state(st, key, "*", "off", at=T0 + 150)
check("tắt Cộng hưởng: mọi ngữ cảnh off, không lịch", all(r["state"] == "off" and r["next_due_at"] is None
                                                          for r in AP.rows(st, key)))
check("tắt: thức lại không đổi gì", AP.wake(st, key, ctx, "direct_call", at=T0 + 160) is False)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=6 WHERE agent_key=? AND context_ref=?", (key, ctx))
AP.set_state(st, key, "*", "on", at=T0 + 170)
row = AP.row(st, key, ctx)
check("bật lại: về B3, giữ ước lượng cũ, resumed_at = lúc bật ở mọi ngữ cảnh",
      row["tier"] == 3 and row["state"] == "awake" and row["estimate"] == est
      and all(r["resumed_at"] == T0 + 170 for r in AP.rows(st, key)), row)
r = AP.evaluate(st, key, ctx, at=T0 + 180)
check("bật lại: phản hồi trước mốc không còn là bằng chứng mới", r["new"] == 0.0, r)

# Ngữ cảnh mới khởi tạo bằng 0,5 x tổng hợp (không phải bằng chứng mới).
st, key, ctx = world()
for i in range(6):
    heart(st, key, ctx, i, T0 + 100 + i)
agg = AP.view(st, key, at=T0 + 200)["aggregate"]
AP.ensure_row(st, key, "team:tm_new", at=T0 + 200)
init = [s for s in AP.signals(st, key, "team:tm_new") if s["kind"] == "context_init"]
r = AP.evaluate(st, key, "team:tm_new", at=T0 + 200)
check("ngữ cảnh mới: tín hiệu khởi tạo mang ước lượng tổng hợp, độ tin = min(1, 0.5 x n_eff tổng hợp)",
      len(init) == 1 and near(init[0]["value"], agg["estimate"], 1e-9) and near(init[0]["w"], 1.0, 1e-9), (init, agg))
check("ngữ cảnh mới: ước lượng dương nhưng không có bằng chứng mới (không tự lên bậc)",
      r["estimate"] > 0 and r["new"] == 0.0 and r["tier"] == 3, r)
ctxs = {x["context_ref"] for x in AP.view(st, key, at=T0 + 200)["contexts"]}
check("view: mỗi ngữ cảnh một dòng, tổng hợp '*' tách riêng", ctxs == {ctx, "team:tm_new"}, ctxs)

# Quay lại một lần đổi bậc, đặt lại ngữ cảnh.
st, key, ctx = world()
log = run_schedule(st, key, ctx, T0 + 2 * D)
ev = AP.events(st, key, ctx, kinds=("tier_change",))[0]
got = AP.revert(st, key, ev["id"], at=T0 + 2 * D + 10)
check("quay lại đổi bậc: về bậc trước, ghi sự kiện revert", got and got["tier"] == 3
      and AP.row(st, key, ctx)["tier"] == 3, got)
check("quay lại lần hai: không làm gì", AP.revert(st, key, ev["id"], at=T0 + 2 * D + 20) is None)
AP.reset(st, key, ctx, at=T0 + 2 * D + 30)
row = AP.row(st, key, ctx)
check("đặt lại ngữ cảnh: B3, resumed_at = lúc đặt lại", row["tier"] == 3 and row["resumed_at"] == T0 + 2 * D + 30, row)

# Thay đổi chính sách: cửa sổ quan sát, tự quay lại khi ước lượng giảm quá 0.3 (confidence ≥ 0.5).
st, key, ctx = world()
for i in range(6):
    heart(st, key, ctx, i, T0 + 100 + i)
called = []
AP.register_policy_reverter("offset_set", lambda ch: called.append(ch["id"]))
cid = AP.apply_policy_change(st, key, ctx, {"kind": "offset_set", "before": {"offset": 0}, "after": {"offset": 0.1},
                                            "source": "self_review"}, at=T0 + 200)
check("thay đổi chính sách: chưa đủ cửa sổ thì không tuyên bố gì", AP.observe_policy_changes(st, at=T0 + 300) == [])
for i in range(6):
    AP.supersede_signal(st, f"rx:dm:{i}", f"rx:dm:{100 + i}")
    heart(st, key, ctx, 100 + i, T0 + 400 + i, value="angry", reasons=["wrong_facts"])
dec = AP.observe_policy_changes(st, at=T0 + 500)
check("ước lượng giảm quá 0.3 (≥ 5 tín hiệu mới): tự quay lại, gọi hàm quay lại của phần sở hữu",
      dec == [{"change_id": cid, "decision": "reverted", "reason": "estimate_down"}] and called == [cid], dec)
cid2 = AP.apply_policy_change(st, key, ctx, {"kind": "offset_set", "before": {}, "after": {}}, at=T0 + 600)
dec = AP.observe_policy_changes(st, at=T0 + 600 + AP.POLICY_WINDOW_S)
check("hết 14 ngày không xấu đi: giữ (kept)", dec == [{"change_id": cid2, "decision": "kept", "reason": "window_ok"}],
      dec)
cid3 = AP.apply_policy_change(st, key, ctx, {"kind": "offset_set", "before": {}, "after": {}, "base_error_rate": 0.1},
                              at=T0 + 700)
AP.record_policy_labels(st, cid3, 6, 2)
dec = AP.observe_policy_changes(st, at=T0 + 800)
check("tỷ lệ lỗi phán xử tăng quá 0.15 (≥ 6 nhãn): tự quay lại",
      dec == [{"change_id": cid3, "decision": "reverted", "reason": "judge_error_up"}], dec)

# Di trú: kho có từ T2 (dòng agent_pulse cũ) mở bằng mã T3: B3, resumed_at = lúc nâng cấp, agent tắt ở off.
d = TEMP / "upgrade"
d.mkdir()
st = RS.GoalStore(d / "resonance.sqlite3")
owner = RS.Principal("owner", "owner", str(d))
on_key = st.agent_set_enabled(owner, "on", True)["agent_key"]
off_key = st.agent_register(owner, "off")["agent_key"]
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=6, resumed_at=1 WHERE agent_key=?", (on_key,))
    c.execute("INSERT OR IGNORE INTO agent_pulse(agent_key, context_ref, state, updated_at) VALUES(?,?,?,?)",
              (off_key, "*", "off", 1))
with st._Tx(st) as c:   # kho "của T2": chưa có mốc nâng cấp T3
    c.execute("DELETE FROM pulse_meta WHERE name=?", (AP.UPGRADE_MARK,))
import time as _time  # noqa: E402
before = _time.time()
AP.ensure(st, force=True)
r_on, r_off = AP.row(st, on_key, "*"), AP.row(st, off_key, "*")
check("nâng cấp: agent bật về B3, resumed_at = lúc nâng cấp", r_on["tier"] == 3 and r_on["resumed_at"] >= before
      and r_on["state"] == "awake", r_on)
check("nâng cấp: agent tắt ở off", r_off["state"] == "off", r_off)
with closing(st._conn()) as c:
    check("nâng cấp: không backfill tín hiệu từ reaction cũ", c.execute("SELECT COUNT(*) FROM pulse_signals")
          .fetchone()[0] == 0)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=5 WHERE agent_key=?", (on_key,))
RS.GoalStore(d / "resonance.sqlite3")
check("nâng cấp chỉ một lần (mở lại không đặt lại bậc)", AP.row(st, on_key, "*")["tier"] == 5)

print(f"\n{len(fails)} FAIL" if fails else "\nALL OK")
raise SystemExit(1 if fails else 0)
