"""T3 phần L (0.156.0): hạn mức lượt tự phát mỗi trợ lý mỗi ngày `agent_daily_calls` (thiết kế 2026-10-15 mục 9).

    python tests/run.py agent_budget -v

Kho thật tạm, sổ lượt chung thật. Phần route đi qua main.py với engine thật bị chặn (fail fast), không model:
- mặc định 6; chỉ chủ đổi (kể cả Không giới hạn); trưởng nhóm/học không nâng được;
- thứ tự ưu tiên và 2 lượt giữ cho reaction_review/related_review (quét, chủ động, tự rà soát không dùng phần giữ);
- đếm từ sổ lượt chung: lượt hoàn không tính, phán xử theo lô của nhóm không tính, phán xử tin chủ động ở chat riêng tính;
- qua ngày đếm lại; lý do đã chờ hạn mức chỉ được một lượt mỗi ngày (không bù hàng loạt);
- dòng `agent_daily_calls` trong bảng giới hạn T1 (nhóm owner, có Không giới hạn); route `POST /agents/{slug}/budget`.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile
import time
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-budget-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-budget-w-"))

import agent_budget as AB  # noqa: E402
import agent_ledger as ALG  # noqa: E402
import resonance_store as RS  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


gs = RS.GoalStore(TEMP / "resonance.sqlite3")
L = ALG.CallLedger(gs)
K = "ag_budget"
lt = time.localtime()
NOON = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))
_r = [0]


def spend(purpose, at=NOON, ctx=f"dm:{K}", key=K, status="ok", ref=None):
    _r[0] += 1
    cid = L.reserve(purpose=purpose, reason_ref=ref or f"t:{_r[0]}", agent_key=key, context_ref=ctx, obligation=False,
                    now=at)
    if status == "refunded":
        L.refund(cid, now=at)
    else:
        L.mark_engine(cid, now=at)
        L.settle(cid, status, now=at)
    return cid


def ok(purpose, at=NOON, **kw):
    return AB.try_spend(K, purpose, at, store=gs, **kw)


check("mặc định 6 lượt mỗi ngày", AB.get_cap(gs, K) == 6 and AB.usage(gs, K, NOON)["left"] == 6)
for i in range(4):
    check(f"quét lượt {i + 1}/4: được", ok("scan")[0])
    spend("scan")
check("quét không dùng 2 lượt giữ: lượt thứ 5 bị chặn", ok("scan") == (False, "agent_daily_calls"))
check("chủ động / tự rà soát / gỡ chờ / phán xử tin chủ động cũng không dùng phần giữ",
      not ok("proactive")[0] and not ok("review")[0] and not ok("wait_unblocked")[0] and not ok("proactive_judge")[0])
check("xem xét reaction dùng được phần giữ", ok("reaction_review")[0])
spend("reaction_review")
check("xem xét liên quan dùng được phần giữ", ok("related_review")[0])
spend("reaction_review")
check("hết 6: mọi loại bị chặn", not ok("reaction_review")[0] and not ok("scan")[0])
u = AB.usage(gs, K, NOON)
check("usage: đã dùng 6, phần giữ đã dùng 2, theo mục đích", u["used"] == 6 and u["reserved_used"] == 2
      and u["by_purpose"] == {"scan": 4, "reaction_review": 2}, u)
check("qua ngày: đếm lại từ đầu", ok("scan", at=NOON + 86400)[0])

# Đếm đúng từ sổ lượt chung.
K2 = "ag_two"


def ok2(purpose, at=NOON, **kw):
    return AB.try_spend(K2, purpose, at, store=gs, **kw)


spend("scan", key=K2, status="refunded")
spend("judge_batch", key="", ctx="team:x")          # lô phán xử của nhóm: tính vào judge_daily, không ở đây
spend("judge_batch", key=K2, ctx="team:x")          # gỡ chờ trong nhóm qua lô của nhóm: không ở đây
spend("judge_batch", key=K2, ctx=f"dm:{K2}")        # phán xử tin chủ động ở chat riêng: tính
spend("reply", key=K2, ctx="team:x")                # lượt nghĩa vụ: không tính
W = ALG.WakeReasons(gs)
rid = W.add_source(K2, "team:x", "related_review", "msg:team:1")
spend("judge_batch", key=K2, ctx=f"dm:{K2}", ref=f"wr:{rid}")
u2 = AB.usage(gs, K2, NOON)
check("lượt hoàn, lô của nhóm, lượt nghĩa vụ không tính; phán xử chat riêng tính; lý do related_review vào phần giữ",
      u2["used"] == 2 and u2["by_purpose"] == {"proactive_judge": 1, "related_review": 1} and u2["reserved_used"] == 1,
      u2)

# Chủ đổi trần; người khác không đổi được.
check("trưởng nhóm / học không đổi được trần", raises(lambda: AB.set_cap(gs, K, 20, by="leader"), PermissionError)
      and raises(lambda: AB.set_cap(gs, K, 20, by="review"), PermissionError) and AB.get_cap(gs, K) == 6)
check("giá trị hỏng: từ chối", raises(lambda: AB.set_cap(gs, K, -1), ValueError)
      and raises(lambda: AB.set_cap(gs, K, "abc"), ValueError) and raises(lambda: AB.set_cap(gs, K, 999), ValueError))
AB.set_cap(gs, K, "unlimited")
check("Không giới hạn: get_cap None, mọi loại được", AB.get_cap(gs, K) is None and ok("scan")[0]
      and AB.usage(gs, K, NOON)["left"] is None)
AB.set_cap(gs, K, 8)
check("chủ nâng lên 8: quét dùng tới 8 - phần giữ còn lại (đã dùng đủ 2 giữ nên tới 8)", ok("scan")[0])
AB.set_cap(gs, K, 1)
check("trần nhỏ hơn phần giữ: quét không bao giờ chạy, xem xét vẫn theo trần",
      not ok("scan", at=NOON + 3 * 86400)[0] and ok("reaction_review", at=NOON + 3 * 86400)[0])
AB.set_cap(gs, K, 6)

# Qua ngày mỗi lý do đã chờ hạn mức tối đa một lượt (không bù hàng loạt).
K3 = "ag_three"
AB.set_cap(gs, K3, 0)
check("hết hạn mức ghi lý do đã chờ", AB.try_spend(K3, "reaction_review", NOON, store=gs, reason_ref="wr:77")
      == (False, "agent_daily_calls"))
AB.set_cap(gs, K3, 6)
day2 = NOON + 86400
check("ngày sau: lý do đã chờ được một lượt", AB.try_spend(K3, "reaction_review", day2, store=gs, reason_ref="wr:77")[0])
L.reserve(purpose="reaction_review", reason_ref="wr:77", agent_key=K3, context_ref=f"dm:{K3}", now=day2)
rows = L.rows(reason_ref="wr:77")
L.mark_engine(rows[-1]["id"], now=day2)
L.settle(rows[-1]["id"], "error", now=day2)
check("ngày sau: lượt thứ hai cho cùng lý do bị chặn (backlog_once)",
      AB.try_spend(K3, "reaction_review", day2 + 60, store=gs, reason_ref="wr:77") == (False, "backlog_once"))
check("lý do khác không bị ảnh hưởng", AB.try_spend(K3, "reaction_review", day2 + 60, store=gs, reason_ref="wr:78")[0])
check("ngày sau nữa: lại được một lượt", AB.try_spend(K3, "reaction_review", day2 + 86400, store=gs,
                                                    reason_ref="wr:77")[0])

# Thứ tự ưu tiên.
srt = AB.sort_by_priority([{"id": 3, "code": "proactive_due"}, {"id": 2, "code": "wait_unblocked"},
                           {"id": 5, "code": "reaction_review"}, {"id": 1, "code": "related_review"},
                           {"id": 4, "code": "reaction_review"}])
check("ưu tiên reaction_review > related_review > wait_unblocked > chủ động", [x["id"] for x in srt] == [4, 5, 1, 2, 3])

# Chưa nối kho: cho qua (không có lượt tự phát nào chạy được khi chưa có kho).
AB.wire(None)
check("chưa nối kho: cho qua", AB.try_spend("x", "scan") == (True, ""))
AB.wire(lambda: gs)
check("đã nối kho: dùng kho mặc định", AB.try_spend(K, "scan", NOON) == (False, "agent_daily_calls"))

# ═════ bảng giới hạn T1 và route của chủ qua main.py (engine thật bị chặn) ═════
import sessions  # noqa: E402
import task_store  # noqa: E402
import team_runner as TR  # noqa: E402
import team_store  # noqa: E402

ss = sessions.SessionStore(db_path=TEMP / "chat.db")
tm = team_store.TeamStore(TEMP / "team.sqlite3")
r = TR.TeamRunner(teams=tm, tasks=task_store.TaskStore(TEMP / "k.sqlite3"), sessions=ss,
                  engine_for=lambda *a: (_ for _ in ()).throw(AssertionError("không engine")),
                  agent_info=lambda root, k: None)
tid = team_store.new_team_id()
tm.create_team(str(TEMP), "Đội", "", ss.create_session(brain=str(TEMP), channel=f"team:{tid}"), team_id=tid)
row = next((x for x in r.limits_table(tid) if x["key"] == "agent_daily_calls"), None)
check("bảng giới hạn T1: dòng agent_daily_calls nhóm owner, mặc định 6, có Không giới hạn",
      row == {"key": "agent_daily_calls", "category": "owner", "value": 6, "unit": "count", "unlimited_ok": True,
              "reserved": 2}, row)

BR = (TEMP / "brain-main").resolve()
(BR / "agents").mkdir(parents=True)
(BR / "agents" / "viet.md").write_text("---\nname: Viết\nrole: Viết bài\n---\nBạn là Viết.\n", encoding="utf-8")
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

built = []


def boom(*a, **kw):
    built.append(a)
    raise AssertionError("test không được dựng engine thật")


main._pulse_engine_for = boom
main._team_engine_for = boom
main._resonance_engine = boom
client = TestClient(main.app, base_url="http://127.0.0.1:8080")
B = str(BR)
check("route hạn mức: trợ lý chưa có mã thì 409", client.post("/agents/viet/budget", params={"brain": B},
                                                             json={"daily_calls": 3}).status_code == 409)
key = client.post("/resonance/agents/toggle", params={"brain": B}, json={"slug": "viet", "enabled": True}).json()[
    "agent"]["agent_key"]
rb = client.post("/agents/viet/budget", params={"brain": B}, json={"daily_calls": 3})
check("route hạn mức: 200 sau khi ghi, trả mục lessons kèm hạn mức mới", rb.status_code == 200
      and rb.json()["view"]["budget"]["cap"] == 3 and AB.get_cap(main._resonance_store(), key) == 3, rb.text)
ru = client.post("/agents/viet/budget", params={"brain": B}, json={"daily_calls": "unlimited"})
check("route hạn mức: Không giới hạn", ru.status_code == 200 and AB.get_cap(main._resonance_store(), key) is None)
check("route hạn mức: giá trị hỏng 400, thiếu 400",
      client.post("/agents/viet/budget", params={"brain": B}, json={"daily_calls": -5}).status_code == 400
      and client.post("/agents/viet/budget", params={"brain": B}, json={}).status_code == 400)
check("route hạn mức: trợ lý không có 404",
      client.post("/agents/khong/budget", params={"brain": B}, json={"daily_calls": 1}).status_code == 404)
v = client.get("/agents/viet/pulse", params={"brain": B}).json()
check("khung nhịp có mục lessons kèm hạn mức hôm nay", v["parts"]["lessons"]["budget"]["cap"] is None, v.get("parts"))
import agent_lessons as ALS  # noqa: E402

mst = main._resonance_store()
lid = ALS.add(mst, agent_key=key, scope=f"dm:{key}", kind="method", text="Hỏi lại khi mơ hồ.", source="self")
foreign = ALS.add(mst, agent_key="ag_khac", scope="team:q", kind="method", text="Của trợ lý khác.", source="self")
rv = client.post(f"/agents/viet/lessons/{lid}/revoke", params={"brain": B})
check("route thu hồi: 200 sau khi ghi, bài học revoked, khung mới không còn bài học đó", rv.status_code == 200
      and ALS.get(mst, lid)["status"] == "revoked" and not rv.json()["view"]["scopes"], rv.text)
check("route thu hồi lần hai: 409 (không báo thành công giả)",
      client.post(f"/agents/viet/lessons/{lid}/revoke", params={"brain": B}).status_code == 409)
check("route thu hồi bài học của trợ lý khác: 409, không đụng",
      client.post(f"/agents/viet/lessons/{foreign}/revoke", params={"brain": B}).status_code == 409
      and ALS.get(mst, foreign)["status"] == "active")
lid2 = ALS.add(mst, agent_key=key, scope=f"dm:{key}", kind="method", text="Gửi bảng tóm tắt.", source="self")
rp = client.post(f"/agents/viet/lessons/{lid2}/promote", params={"brain": B})
check("route chủ nâng bài học lên mọi nơi: 200", rp.status_code == 200
      and ALS.get(mst, rp.json()["result"]["lesson_id"])["scope"] == "*", rp.text)
# Chốt an toàn: host học mặc định của main.py không bao giờ dựng engine thật trong test (JAVIS_NO_REAL_JUDGE_ENGINE).
import asyncio  # noqa: E402
import agent_review as ARV  # noqa: E402

check("_paths.py đặt chốt JAVIS_NO_REAL_JUDGE_ENGINE cho mọi test", os.environ.get("JAVIS_NO_REAL_JUDGE_ENGINE") == "1")
dsid = ss2 = main.get_store()
dsid = ss2.create_session(brain=B, channel="agent:viet")
dmid = ss2.append_message(dsid, "assistant", "Tin của Viết")
got = ss2.set_dm_reaction(dsid, dmid, value="sad", nonce="guard1", agent_key=key)
main._feedback_projector().project("dm", got["log_id"])
rr = ALG.WakeReasons(mst).reasons(agent_key=key, status="pending", code="reaction_review")
res = asyncio.run(ARV.default().serve_reaction_review(rr[0])) if rr else {}
rows_g = ALG.CallLedger(mst).rows(reason_ref=f"wr:{rr[0]['id']}") if rr else []
check("lượt xem xét qua main.py: chốt chặn trước khi dựng engine, lượt hoàn, lý do giữ pending, không model",
      res.get("outcome") == "engine_error" and rows_g and rows_g[0]["status"] == "refunded"
      and ALG.WakeReasons(mst).get(rr[0]["id"])["status"] == "pending" and built == [], [res, rows_g])
check("không engine thật nào được dựng", built == [])

print(f"\n{'ĐỎ' if fails else 'XANH'}: {len(fails)} lỗi")
raise SystemExit(1 if fails else 0)
