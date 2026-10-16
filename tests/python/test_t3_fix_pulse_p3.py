"""Review T3 phần P, các mục P3 rẻ và an toàn (0.156.0 sửa).

    python tests/run.py t3_fix_pulse_p3 -v

1. Thức lại, dòng mới, đặt lại, bật lại không nhanh hơn `max_tier` chủ đặt (B4-B7).
2. Lease 15 phút lớn hơn hạn một lượt quét, và hạn lượt quét dưới mốc đối soát sổ lượt.
3. `ledger_busy` nhả lịch (không kẹt tới khi lease hết); nhãn "Chờ hạn mức" gỡ khi hạn mức đã đủ.
4. Model trỏ nguồn bịa: `none` (bad_source), không gắn vào cơ hội đầu.
5. `record_contrib` không hồi sinh dòng đã `void`.
6. Route nhịp chỉ nhận nhóm agent đang tham gia (hay đã có dòng).
7. Lý do lớp 3 phục vụ theo ưu tiên tiêu hạn mức (`sort_by_priority`).
8. "Huỷ"/"sửa yêu cầu" sau kết quả có bản tổng hợp phân bổ theo 6.5.
Kho thật, đồng hồ giả, engine GIẢ. Không model thật nào.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fix-pp3-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-fix-pp3-w-"))

import agent_ledger as AL  # noqa: E402
import agent_pulse as AP  # noqa: E402
import agent_pulse_host as H  # noqa: E402
import feedback_projector as FP  # noqa: E402
import resonance_store as RS  # noqa: E402
import sessions  # noqa: E402
import task_store  # noqa: E402
import team_runner as TR  # noqa: E402
import team_store  # noqa: E402

fails = []
D = 86400.0
T0 = 2_000_000_000.0


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


_n = [0]


def store():
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}"
    d.mkdir()
    st = RS.GoalStore(d / "resonance.sqlite3")
    key = st.agent_set_enabled(RS.Principal("owner", "owner", str(d)), "a0", True)["agent_key"]
    AP.set_state(st, key, "*", "on", at=T0)
    return st, key, d


# ═════ 1. trần max_tier ═════
st, k, _d = store()
AP.ensure_row(st, k, f"dm:{k}", at=T0)
AP.set_max_tier(st, k, 5, at=T0)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET tier=6, state='slowed' WHERE agent_key=? AND context_ref=?", (k, f"dm:{k}"))
AP.wake(st, k, f"dm:{k}", "owner_message", at=T0 + 10)
check("thức lại khi trần B5: về B5, không về B3", AP.row(st, k, f"dm:{k}")["tier"] == 5, AP.row(st, k, f"dm:{k}"))
AP.ensure_row(st, k, "team:new", at=T0 + 20)
check("dòng mới khi trần B5: bắt đầu ở B5", AP.row(st, k, "team:new")["tier"] == 5, AP.row(st, k, "team:new"))
AP.reset(st, k, "team:new", at=T0 + 30)
check("đặt lại khi trần B5: về B5", AP.row(st, k, "team:new")["tier"] == 5)
AP.set_state(st, k, "*", "off", at=T0 + 40)
AP.set_state(st, k, "*", "on", at=T0 + 50)
check("bật lại khi trần B5: mọi ngữ cảnh về B5", AP.row(st, k, f"dm:{k}")["tier"] == 5
      and AP.row(st, k, "team:new")["tier"] == 5)

# ═════ 2. lease và hạn lượt quét ═════
check("lease 15 phút; lượt quét kết thúc trước lease và trước mốc đối soát sổ lượt",
      AP.CLAIM_S >= 900 and H.SCAN_TIMEOUT_S < AP.CLAIM_S and H.SCAN_TIMEOUT_S < AL.STALE_RESERVED_S,
      (AP.CLAIM_S, H.SCAN_TIMEOUT_S, AL.STALE_RESERVED_S))


# ═════ 3 + 4 + 7: host ═════
def host_for(st, key, text, opps):
    calls = []

    def engine_for(k_, ctx, purpose):
        async def run_(req):
            await req["engine_started"]()
            calls.append(req)
            return {"text": text}
        return "fake", "fake-model", run_
    proposed = []
    deps = H.PulseHostDeps(store=lambda: st, agents=lambda: [{"agent_key": key, "brain_id": ""}],
                           contexts=lambda k_: [f"dm:{k_}"], try_busy=lambda k_, tok: True,
                           release_busy=lambda k_, tok: None, opportunities=lambda k_, c_, iv: list(opps),
                           persona=lambda k_: "", engine_for=engine_for,
                           propose=lambda k_, c_, item, src: proposed.append(src) or "msg:1",
                           clock=lambda: T0 + D, spawn=asyncio.ensure_future)
    return H.PulseHost(deps, budget=lambda k_, p, at=None: (True, "")), calls, proposed


st3, k3, _ = store()
ctx3 = f"dm:{k3}"
AP.ensure_row(st3, k3, ctx3, at=T0)
with st3._Tx(st3) as c:
    c.execute("UPDATE agent_pulse SET next_due_at=?, quota_wait=1 WHERE agent_key=? AND context_ref=?", (T0 + 5, k3, ctx3))
h3, calls3, _p3 = host_for(st3, k3, '{"action": "none"}', [{"source_ref": "task:t1", "version": "1", "kind": "x",
                                                             "summary": "y"}])
claims, _r = h3._code_pass(T0 + D)
due_ref = h3._due_ref(claims[0])
AL.CallLedger(st3).reserve(purpose="scan", reason_ref=due_ref, agent_key=k3, context_ref=ctx3, obligation=False)
out3 = asyncio.run(h3.wake_step(claims[0]))
row3 = AP.row(st3, k3, ctx3)
check("ledger_busy: nhả lịch ngay (không giữ claim tới khi lease hết)", out3.get("outcome") == "ledger_busy"
      and not row3.get("claim_token") and row3.get("claim_until") is None and row3["next_due_at"] is not None,
      (out3, row3))
check("hạn mức đã đủ: nhãn 'Chờ hạn mức' gỡ (không đứng mãi tới lượt quét thành công)", row3["quota_wait"] == 0, row3)

st4, k4, _ = store()
ctx4 = f"dm:{k4}"
AP.ensure_row(st4, k4, ctx4, at=T0)
with st4._Tx(st4) as c:
    c.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=? AND context_ref=?", (T0 + 5, k4, ctx4))
h4, calls4, proposed4 = host_for(st4, k4, '{"action": "propose", "source_ref": "task:BIA", "title": "Việc", "brief": "b"}',
                                 [{"source_ref": "task:t1", "version": "1", "kind": "x", "summary": "y"}])
cl4, _r = h4._code_pass(T0 + D)
out4 = asyncio.run(h4.wake_step(cl4[0]))
check("nguồn bịa: không đề xuất, kết quả none/bad_source", proposed4 == []
      and (out4.get("result") or {}).get("reason") == "bad_source", (out4, proposed4))
check("nguồn bịa: cơ hội thật task:t1 không bị khoá bởi đề xuất sai nguồn",
      not AP.proposal_blocked(st4, k4, ctx4, "task:t1", "1"))

st7, k7, _ = store()
ctx7 = f"dm:{k7}"
AP.ensure_row(st7, k7, ctx7, at=T0)
W7 = AL.WakeReasons(st7)
r_wait = W7.add_source(k7, ctx7, "wait_unblocked", "wait:1", now=T0)
r_rx = W7.add_source(k7, ctx7, "reaction_review", "rx:dm:1", now=T0 + 1)
H.register_reason_handler("wait_unblocked", lambda _h, r: None)
H.register_reason_handler("reaction_review", lambda _h, r: None)
h7, _c7, _p7 = host_for(st7, k7, '{"action": "none"}', [])
_cl, rs7 = h7._code_pass(T0 + D)
check("lý do lớp 3 theo ưu tiên: reaction_review trước wait_unblocked dù đến sau",
      [r["id"] for r in rs7] == [r_rx, r_wait], [r["code"] for r in rs7])

# ═════ 5. record_contrib không hồi sinh dòng void ═════
st5, k5, _ = store()
k5b = st5.agent_set_enabled(RS.Principal("owner", "owner", str(TEMP)), "a1", True)["agent_key"]
AP.record_contrib(st5, source_ref="rx:team:9", result_ref="msg:1", synth=k5, inputs=[k5b], context_ref="team:x",
                  value="love", at=T0)
AP.void_signal(st5, "rx:team:9")
got5 = AP.record_contrib(st5, source_ref="rx:team:9", result_ref="msg:2", synth=k5, inputs=[k5b], context_ref="team:x",
                         value="love", at=T0 + 1)
live5 = [s for s in AP.signals(st5, k5) + AP.signals(st5, k5b) if s["source_ref"] == "rx:team:9" and s["status"] == "live"]
check("nguồn đã void: phân bổ lại không hồi sinh dòng nào", got5 == {} and live5 == [], (got5, live5))

# ═════ 6. route chỉ nhận nhóm agent đang tham gia ═════
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

st6, k6, d6 = store()
AP.ensure_row(st6, k6, "team:mine", at=T0)
app = FastAPI()
H.register(app, H.PulseRouteDeps(brain_key=lambda b: b, agent_exists=lambda root, slug: True,
                                 agent_key=lambda root, slug: k6, store=lambda: st6, seen_resolve=lambda *a: [],
                                 contexts=lambda key: [f"dm:{key}", "team:mine"]))
cl6 = TestClient(app)
r_bad = cl6.post("/agents/a0/pulse/wake", json={"context_ref": "team:unrelated"})
check("route: nhóm agent không tham gia bị từ chối (400), không tạo dòng có lịch", r_bad.status_code == 400
      and AP.row(st6, k6, "team:unrelated") is None, (r_bad.status_code, r_bad.text[:200]))
r_ok = cl6.post("/agents/a0/pulse/reset", json={"context_ref": "team:mine"})
check("route: nhóm đang tham gia vẫn làm được", r_ok.status_code == 200, r_ok.text[:200])

# ═════ 8. huỷ/sửa yêu cầu có bản tổng hợp: phân bổ 6.5 ═════
gs = RS.GoalStore(TEMP / "team-resonance.sqlite3")
own = RS.Principal("owner", "owner", str(TEMP))
K = {s: gs.agent_set_enabled(own, s, True)["agent_key"] for s in ("lan", "minh", "hoa")}
ss = sessions.SessionStore(db_path=TEMP / "chat.db")
ts = task_store.TaskStore(TEMP / "kanban.sqlite3")
tm = team_store.TeamStore(TEMP / "team.sqlite3")
box = {}
pj = FP.FeedbackProjector(resonance=lambda: gs, sessions=lambda: ss, teams=lambda: tm,
                          team_view=lambda t, s, m: box["r"].team_message_view(t, s, m))
r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=lambda *a: None, publish=lambda e: None,
                  agent_info=lambda root, key: {"slug": key, "name": key, "system": ""}, projector=pj)
box["r"] = r
tid = team_store.new_team_id()
tsid = ss.create_session(brain=str(TEMP), channel=f"team:{tid}")
tm.create_team(str(TEMP), "Đội", "", tsid, team_id=tid)
for s_, k_ in K.items():
    tm.add_member(tid, k_, s_)
tm.set_leader(tid, K["lan"])
task = r.submit(tid, "Báo cáo", as_job=True, client_msg_id="j1")["task_id"]
rev = ts.get_task(task)["team_rev"]


def step(kind, key, inputs, text):
    s_ = tm.add_step(task, tid, kind, key, rev, "", inputs)
    mid, _ = ss.append_team_message(tsid, "assistant", text, author_kind="agent", author_ref=key, task_id=task,
                                    step_id=s_["step_id"])
    tm.finish_step(s_["step_id"], "done", mid, True)
    return mid


m1 = step("work", K["minh"], [], "Phần Minh")
m2 = step("work", K["hoa"], [], "Phần Hoà")
step("synthesize", K["lan"], [m1, m2], "Tổng hợp")
shares = r.result_shares(ts.get_task(task))
check("phần chia khi có bản tổng hợp: 0.4 / 0.4 cho người làm, 0.2 cho người tổng hợp (6.5)",
      abs(shares.get(K["minh"], 0) - 0.4) < 1e-9 and abs(shares.get(K["hoa"], 0) - 0.4) < 1e-9
      and abs(shares.get(K["lan"], 0) - 0.2) < 1e-9, shares)
r.cancel_job(task, owner_cancel=True)
cc = {s["agent_key"]: s["share"] for k_ in K.values() for s in AP.signals(gs, k_) if s["source_ref"] == f"cancel:{task}"}
check("chủ huỷ sau bản tổng hợp: tín hiệu -0,4 chia theo 6.5, không chia đều", abs(cc.get(K["lan"], 0) - 0.2) < 1e-9
      and abs(cc.get(K["minh"], 0) - 0.4) < 1e-9, cc)

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
