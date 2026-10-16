"""T3 hiệu năng (sau bench T4): học, phán xử, nhịp không được giữ event loop (GOAL mục 10, quy ước R5 của T1).

    python tests/run.py t3_event_loop -v

Cách đo như R5 (tests/python/test_team_t1_review.py) và test_resonance_perf.py: kho thật nhưng LÀM CHẬM (time.sleep ở
mỗi lần mở kết nối SQLite của kho phán xử và kho resonance, và ở các hàm kho chính), một bộ hẹn giờ 5 ms chạy suốt; nếu
còn I/O nào chạy trên luồng event loop thì khoảng trống lớn nhất vượt 100 ms. Engine GIẢ, không model thật nào.

1. Lô phán xử nhóm (tin của chủ -> lô -> lượt tự lên tiếng), tin chủ động ở chat riêng, chờ rồi gỡ chờ và xét lại: loop
   không bị giữ quá 100 ms, kết quả vẫn đúng (quyết định, dòng sổ `judge_batch` chốt `ok`, người gửi được gọi).
2. Huỷ lô ngay sau khi đã giữ chỗ (đang ở bước chuẩn bị): dòng sổ được hoàn, engine không được gọi.
3. Nhịp pulse (chiếu outbox, bước code, lần thức, lượt quét, đề xuất) với mọi hàm của host chậm: loop không bị giữ.
4. Bộ chiếu: hai nhịp liền nhau không chiếu lại dòng đã `done`, không ghi lại trạng thái không đổi, không đọc từng dòng;
   lý do đổi trạng thái thì chỉ dòng đó được ghi.
5. Qua main.py (review T3 P P2-3): route reaction chat riêng `POST /sessions/{id}/messages/{mid}/reaction` và lần thức
   nhịp chat riêng cuối lượt chat (`_pulse_wake_dm`) với kho chậm: không hàm kho nào chạy trên luồng event loop.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import json
import os
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-t3-loop-"))
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)

from _judge_world import World, batch, check, finish  # noqa: E402
import agent_ledger as AL  # noqa: E402
import agent_pulse as AP  # noqa: E402
import agent_pulse_host as H  # noqa: E402
import resonance_store as RS  # noqa: E402

SLOW = 0.25        # hàm kho chính
SLOW_CONN = 0.11   # mỗi lần mở kết nối SQLite: một lần mở trên luồng loop là đủ vượt ngưỡng 100 ms
LIMIT_MS = 100.0
LOOP_THREAD = {"id": None}
ON_LOOP: list = []


def slow(fn, s=SLOW, name=""):
    def wrap(*a, **k):
        if threading.get_ident() == LOOP_THREAD["id"]:
            ON_LOOP.append(name or getattr(fn, "__name__", "?"))
        time.sleep(s)
        return fn(*a, **k)
    return wrap


async def max_gap(coro_factory):
    LOOP_THREAD["id"] = threading.get_ident()
    beats = []
    stop = asyncio.Event()

    async def beat():
        while not stop.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(0.005)
            beats.append(time.perf_counter() - t0)
    p = asyncio.create_task(beat())
    await asyncio.sleep(0.02)
    try:
        out = await coro_factory()
    finally:
        stop.set()
        await p
    return round(max(beats) * 1000, 1), out


def slow_stores(w, extra=()):
    """Patch: mở kết nối chậm cho kho phán xử và kho resonance (sổ lượt, lý do, Cộng hưởng), cộng các hàm kho chính."""
    ps = [patch.object(w.js, "_conn", side_effect=slow(w.js._conn, SLOW_CONN, "judge._conn")),
          patch.object(w.gs, "_conn", side_effect=slow(w.gs._conn, SLOW_CONN, "resonance._conn")),
          patch.object(w.js, "add_decision", side_effect=slow(w.js.add_decision, name="add_decision")),
          patch.object(w.js, "cases", side_effect=slow(w.js.cases, name="cases")),
          patch.object(w.gs, "agent_any", side_effect=slow(w.gs.agent_any, name="agent_any")),
          patch.object(w.ledger, "reserve", side_effect=slow(w.ledger.reserve, name="ledger.reserve")),
          patch.object(w.ledger, "mark_engine", side_effect=slow(w.ledger.mark_engine, name="ledger.mark_engine")),
          patch.object(w.ledger, "settle", side_effect=slow(w.ledger.settle, name="ledger.settle")),
          patch.object(w.ledger, "refund", side_effect=slow(w.ledger.refund, name="ledger.refund"))]
    return ps + list(extra)


class Many:
    def __init__(self, ps):
        self.ps = ps

    def __enter__(self):
        for p in self.ps:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.ps):
            p.__exit__(*exc)
        return False


QUESTION = "Cả nhóm ơi, ai biết cách xử lý doanh thu tháng này không?"

# ═════ 1a. lô phán xử nhóm với kho chậm ═════
w = World("loop-team", enabled=("lan", "minh", "hoa"), roles={"minh": "doanh thu", "hoa": "doanh thu"})
w.jeng.decide = lambda prompt: batch(**{w.k("minh"): ("speak", 0.95), w.k("hoa"): ("silent", 0.1)})
w.runner._bind_loop()
sub = w.runner.submit(w.team_id, QUESTION, client_msg_id="loop-1")
ON_LOOP.clear()


async def team_flow():
    await w.runner.run_message(sub)
    await w.runner.wait_background()

with Many(slow_stores(w)):
    gap, _ = asyncio.run(max_gap(team_flow))
check("lô phán xử nhóm với kho chậm: event loop không bị giữ quá 100 ms", gap < LIMIT_MS, gap)
check("lô phán xử nhóm: không hàm kho nào chạy trên luồng event loop", not ON_LOOP, ON_LOOP[:8])
mine = [d for d in w.decisions() if d["source_event"] == f"msg:{sub['message_id']}" and d["kind"] == "message"]
vol = [d for d in mine if d["verdict"] == "speak" and d["volunteer"]]
check("lô phán xử nhóm vẫn xong đúng: minh được chọn tự lên tiếng, hoa im có lý do",
      [w.slug_of[d["agent_key"]] for d in vol] == ["minh"]
      and any(w.slug_of[d["agent_key"]] == "hoa" and d["verdict"] == "silent" for d in mine), mine)
calls = w.judge_calls()
check("lô phán xử nhóm: đúng một dòng judge_batch, đã qua mốc engine và chốt ok",
      len(calls) == 1 and calls[0]["status"] == "ok" and calls[0].get("engine"), calls)
check("lượt tự lên tiếng của minh đã chạy (engine lượt nhóm giả)",
      [c["agent"] for c in w.eng.roles("member_volunteer")] == [w.k("minh")], w.eng.calls)

# ═════ 1b. tin chủ động ở chat riêng, chờ, gỡ chờ, xét lại, gửi ═════
w2 = World("loop-dm", enabled=("lan",))
key = w2.k("lan")
answers = ['{"verdict":"wait","score":0.5,"reason":"đợi chủ","wait_for":["owner_message"]}',
           '{"verdict":"speak","score":0.95,"reason":"đúng lúc"}']
w2.jeng.decide = lambda prompt: answers.pop(0)
sent = []


def sender(decision, payload, mode):
    if threading.get_ident() == LOOP_THREAD["id"]:
        ON_LOOP.append("sender")
    time.sleep(SLOW)
    sent.append((decision["verdict"], payload.get("draft"), mode))

w2.judge.register_sender(sender)
w2.judge.try_spend = slow(lambda k, p: (True, ""), name="try_spend")
ON_LOOP.clear()


async def dm_flow():
    d = await w2.judge.judge_proactive(agent_key=key, context_ref=f"dm:{key}", draft="Em nhắc lịch họp chiều nay",
                                       source_event="pulse:test:1", origin="pulse")
    w2.now[0] += 5
    items = await asyncio.to_thread(w2.judge.release_waits, f"dm:{key}", {"kind": "owner_message"})
    got = await w2.judge.process_released(items)
    return d, items, got

with Many(slow_stores(w2)):
    gap, (d1, items, got) = asyncio.run(max_gap(dm_flow))
check("tin chủ động + gỡ chờ + xét lại ở chat riêng với kho chậm: event loop không bị giữ quá 100 ms",
      gap < LIMIT_MS, gap)
check("chat riêng: không hàm kho hay người gửi nào chạy trên luồng event loop", not ON_LOOP, ON_LOOP[:8])
check("chat riêng: lần đầu chờ, gỡ bằng tin của chủ, xét lại thành nói và người gửi được gọi một lần",
      d1["verdict"] == "wait" and len(items) == 1 and [x["verdict"] for x in got] == ["speak"]
      and sent == [("speak", "Em nhắc lịch họp chiều nay", "send")], (d1, items, got, sent))
wst = w2.js.waits_of(key)
check("chat riêng: lần chờ đã khép 'rejudged'", [x["status"] for x in wst] == ["rejudged"], wst)
calls2 = [r for r in w2.ledger.rows() if r["purpose"] == "judge_batch"]
check("chat riêng: hai dòng judge_batch (lần đầu và lần xét lại), đều chốt ok",
      len(calls2) == 2 and all(r["status"] == "ok" for r in calls2), calls2)

# ═════ 2. huỷ ngay sau khi giữ chỗ: dòng sổ được hoàn, không gọi engine ═════
w3 = World("loop-cancel", enabled=("lan", "minh", "hoa"), roles={"minh": "doanh thu", "hoa": "doanh thu"})
w3.runner._bind_loop()
sub3 = w3.runner.submit(w3.team_id, QUESTION, client_msg_id="loop-3")
ctx3 = w3.runner._judge_message_ctx(w3.team_id, int(sub3["message_id"]), QUESTION, [])
reserved = threading.Event()
orig_reserve = w3.ledger.reserve


def reserve_then_flag(*a, **k):
    out = orig_reserve(*a, **k)
    reserved.set()
    return out


async def cancel_flow():
    t = asyncio.ensure_future(w3.judge.judge_team_message(ctx3))
    while not reserved.is_set():
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)   # đang ở bước đọc ca (chậm) sau khi đã giữ chỗ
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        return "cancelled"
    return "finished"

with patch.object(w3.ledger, "reserve", side_effect=reserve_then_flag), \
        patch.object(w3.js, "cases", side_effect=slow(w3.js.cases, 0.4)):
    gap, res = asyncio.run(max_gap(cancel_flow))
rows3 = [r for r in w3.ledger.rows() if r["purpose"] == "judge_batch"]
check("huỷ lô sau khi giữ chỗ: lô bị huỷ, dòng judge_batch được hoàn, engine không được gọi",
      res == "cancelled" and len(rows3) == 1 and rows3[0]["status"] == "refunded" and not w3.jeng.prompts,
      (res, rows3, len(w3.jeng.prompts)))
check("huỷ lô: event loop vẫn không bị giữ", gap < LIMIT_MS, gap)

# ═════ 3. nhịp pulse với mọi hàm của host chậm ═════
TEMP = Path(tempfile.mkdtemp(prefix="javis-t3-loop-pulse-"))
st = RS.GoalStore(TEMP / "resonance.sqlite3")
owner = RS.Principal("owner", "owner", str(TEMP))
pkey = st.agent_set_enabled(owner, "a0", True)["agent_key"]
T0 = time.time()
AP.set_state(st, pkey, "*", "on", at=T0)
pctx = f"dm:{pkey}"
AP.ensure_row(st, pkey, pctx, at=T0)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=? AND context_ref=?", (T0 - 1, pkey, pctx))
box = {"proposed": [], "engine": 0, "projected": 0, "snapshots": 0}


def engine_for(key, ctx, purpose):
    async def run_(req):
        await req["engine_started"]()
        box["engine"] += 1
        return {"text": json.dumps({"action": "propose", "source_ref": "task:t1", "title": "Cập nhật việc cũ",
                                    "brief": "Việc cũ chưa cập nhật"}), "tokens_in": 1, "tokens_out": 1}
    return "fake", "fake-model", run_


def project_pending():
    box["projected"] += 1


def busy_snapshot():
    box["snapshots"] += 1
    return lambda key: False

deps = H.PulseHostDeps(
    store=slow(lambda: st, SLOW_CONN, "store"),
    agents=slow(lambda: [{"agent_key": pkey, "brain_id": str(TEMP)}], name="agents"),
    contexts=slow(lambda key: [f"dm:{key}"], name="contexts"),
    busy=slow(lambda key: False, name="busy"), busy_snapshot=slow(busy_snapshot, name="busy_snapshot"),
    try_busy=slow(lambda key, tok: True, name="try_busy"), release_busy=slow(lambda key, tok: None, name="release_busy"),
    opportunities=slow(lambda key, ctx, iv: [{"source_ref": "task:t1", "version": "1", "kind": "stale_job",
                                              "summary": "Việc cũ"}], name="opportunities"),
    persona=slow(lambda key: "Bạn là trợ lý.", name="persona"), engine_for=slow(engine_for, name="engine_for"),
    can_enqueue=slow(lambda key, ctx: False, name="can_enqueue"),
    propose=slow(lambda key, ctx, item, src: box["proposed"].append((key, ctx, item, src)) or "msg:9", name="propose"),
    interacted=slow(lambda p: False, name="interacted"), project_pending=slow(project_pending, name="project_pending"),
    clock=lambda: T0 + 1, spawn=asyncio.ensure_future)
host = H.PulseHost(deps, budget=slow(lambda key, purpose, at=None: (True, ""), name="budget"))
ON_LOOP.clear()


async def pulse_flow():
    out = await host.tick()
    await host.wait_idle(60)
    return out

with patch.object(st, "_conn", side_effect=slow(st._conn, SLOW_CONN, "resonance._conn")):
    gap, out = asyncio.run(max_gap(pulse_flow))
check("nhịp pulse với kho và hàm host chậm: event loop không bị giữ quá 100 ms", gap < LIMIT_MS, gap)
check("nhịp pulse: không hàm kho hay hàm host nào chạy trên luồng event loop", not ON_LOOP, ON_LOOP[:8])
with closing(st._conn()) as c:
    scans = [dict(r) for r in c.execute("SELECT status FROM agent_calls WHERE purpose='scan'").fetchall()]
check("nhịp pulse vẫn chạy đúng: chiếu outbox một lần, một lần thức, một lượt quét chốt ok, một đề xuất",
      box["projected"] == 1 and out.get("claims") == 1 and box["engine"] == 1 and scans == [{"status": "ok"}]
      and len(box["proposed"]) == 1, (box, out, scans))
check("nhịp pulse: trạng thái bận đọc MỘT lần cho cả nhịp (busy_snapshot), không gọi busy từng agent",
      box["snapshots"] == 1, box)

# ═════ 4. bộ chiếu: không chiếu lại dòng đã done, chỉ ghi trạng thái đã đổi ═════
w4 = World("loop-projector", enabled=("lan", "minh", "hoa"))
tsid = w4.team["session_id"]
log_ids = []
for i, slug in enumerate(("lan", "minh", "hoa") * 2):
    mid, _ = w4.ss.append_team_message(tsid, "assistant", f"Trả lời {i}", author_kind="agent", author_ref=w4.k(slug))
    got = w4.tm.set_reaction_ex(team_id=w4.team_id, session_id=tsid, message_id=mid, agent_key=w4.k(slug),
                                task_id=None, content_sha="x", value="like", reasons=[], comment="", nonce=f"n{i}")
    log_ids.append(got["log_id"])
first = w4.pj.project_pending()
with closing(w4.tm._conn()) as c:
    states = [r[0] for r in c.execute("SELECT effect_state FROM team_reaction_log").fetchall()]
    statuses = [r[0] for r in c.execute("SELECT status FROM team_reactions").fetchall()]
check("bộ chiếu: lần đầu chiếu hết sáu dòng (done), reaction chờ xem xét",
      states == ["done"] * 6 and statuses == ["pending_review"] * 6, (states, statuses))

counts = {}


def count(obj, name):
    fn = getattr(obj, name)

    def wrap(*a, **k):
        counts[name] = counts.get(name, 0) + 1
        return fn(*a, **k)
    return patch.object(obj, name, side_effect=wrap)

watch = [count(w4.pj, "_apply"), count(w4.pj, "project"), count(w4.tm, "set_reaction_status"),
         count(w4.tm, "reaction_log_row"), count(w4.tm, "reaction_of"), count(w4.tm, "open_reaction_rows"),
         count(w4.tm, "reaction_log_rows"), count(w4.tm, "mark_reaction_effects")]
with Many(watch):
    ev1 = w4.pj.project_pending()
    ev2 = w4.pj.project_pending()
check("bộ chiếu: hai nhịp liền nhau không chiếu lại dòng done (không _apply, project, mark)",
      not counts.get("_apply") and not counts.get("project") and not counts.get("mark_reaction_effects"), counts)
check("bộ chiếu: không ghi lại trạng thái không đổi, không gói WS thừa",
      not counts.get("set_reaction_status") and ev1 == [] and ev2 == [], (counts, ev1, ev2))
check("bộ chiếu: đối soát đọc theo lô (một câu reaction, một câu nhật ký mỗi nhịp), không đọc từng dòng",
      counts.get("open_reaction_rows") == 2 and counts.get("reaction_log_rows") == 2
      and not counts.get("reaction_log_row") and not counts.get("reaction_of"), counts)

# Lý do của một reaction được nhận (đang xem xét): chỉ đúng reaction đó được ghi lại.
row = w4.tm.reaction_log_row(log_ids[0])
rid = int(row["effect_refs"]["reason_id"])
cl = AL.WakeReasons(w4.gs).claim(rid, purpose="reaction_review", obligation=False)
counts.clear()
with Many([count(w4.tm, "set_reaction_status")]):
    ev3 = w4.pj.project_pending()
with closing(w4.tm._conn()) as c:
    by = {int(r[0]): r[1] for r in c.execute("SELECT log_id, status FROM team_reactions").fetchall()}
same_reason = [lid for lid in log_ids
               if int((w4.tm.reaction_log_row(lid)["effect_refs"] or {}).get("reason_id") or 0) == rid]
check("bộ chiếu: lý do đổi trạng thái thì chỉ các reaction của lý do đó được ghi 'reviewing'",
      cl["outcome"] == "claimed" and counts.get("set_reaction_status") == len(same_reason)
      and all(by[lid] == ("reviewing" if lid in same_reason else "pending_review") for lid in log_ids)
      and len(ev3) == len(same_reason), (cl["outcome"], counts, by, same_reason, len(ev3)))

# ═════ 5. qua main.py: route reaction chat riêng và lần thức nhịp cuối lượt chat (review T3 P P2-3) ═════
import httpx  # noqa: E402
import main  # noqa: E402


def _no_real_engine(*a, **k):
    raise AssertionError("test không được dựng engine thật")

main.team_engine.build = _no_real_engine
main._team_engine_for = _no_real_engine
main._pulse_engine_for = _no_real_engine
mss = main.get_store()
dsid = mss.create_session(brain="brain", channel="web")
dmid = mss.append_message(dsid, "assistant", "Câu trả lời để thả cảm xúc")
names = ("get_session", "message_row", "dm_nonce_owner", "set_dm_reaction", "dm_reaction_of",
         "report_receipt_by_message")


async def warm_up():
    # Request đầu tiên qua main.app dựng bảng route lười của FastAPI (~270 ms trên luồng loop, không phải mã T3; review T3
    # vòng 2 P2-3): gửi một request hỏng (400, không ghi gì) TRƯỚC khi đo, ngưỡng giữ nguyên.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1:8080") as cl_:
        return await cl_.post(f"/sessions/{dsid}/messages/{dmid}/reaction", json={"value": "khong-co"})

warm = asyncio.run(warm_up())
check("làm nóng route trước khi đo: request hỏng bị từ chối, không ghi reaction", warm.status_code == 400
      and mss.dm_reaction_of(dsid, dmid) is None, (warm.status_code, warm.text[:200]))
ON_LOOP.clear()


async def reaction_flow():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1:8080") as cl_:
        return await cl_.post(f"/sessions/{dsid}/messages/{dmid}/reaction",
                              json={"value": "like", "reasons": [], "nonce": "loop-react-1"})

with Many([patch.object(mss, n, side_effect=slow(getattr(mss, n), name=n)) for n in names]):
    gap, resp = asyncio.run(max_gap(reaction_flow))
check("route reaction chat riêng: trả 200 và ghi reaction", resp.status_code == 200 and resp.json().get("ok"),
      resp.text[:300])
check("route reaction chat riêng với kho chậm: event loop không bị giữ quá 100 ms", gap < LIMIT_MS, gap)
check("route reaction chat riêng: không hàm kho nào chạy trên luồng event loop", not ON_LOOP, ON_LOOP[:8])

ON_LOOP.clear()


async def wake_flow():
    main._pulse_wake_dm("brain", "khong-co")   # gọi đồng bộ từ cuối lượt chat, như trong websocket_endpoint
    await asyncio.sleep(0.4)
    return True

with patch.object(main, "_pulse_store", side_effect=slow(main._pulse_store, name="pulse_store")):
    gap, _w = asyncio.run(max_gap(wake_flow))
check("thức nhịp chat riêng cuối lượt chat: kho chạy ở luồng phụ, event loop không bị giữ", gap < LIMIT_MS and
      not ON_LOOP, (gap, ON_LOOP[:4]))

finish()
