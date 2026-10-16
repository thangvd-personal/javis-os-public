"""Đo hiệu năng nhóm chat thích nghi (T4, 0.170.0; thiết kế 2026-10-15 mục 11, GOAL mục 10) bằng ENGINE GIẢ.

    python tools/team_adaptive_bench.py                       # mặc định: 12 nhóm, 1.500 tin mỗi nhóm
    python tools/team_adaptive_bench.py --teams 12 --messages 1500 --reactions 1500 --runs 30 \\
        --out exports/reviews/team-adaptive/t4-bench-2026-10-15.json

Dựng state TẠM (thư mục mới, không đụng dữ liệu thật), brain tạm có nhiều trợ lý, rồi chạy host thật `main.app` trong
tiến trình (httpx ASGITransport, không mạng, không mở cổng). KHÔNG BAO GIỜ gọi model: mọi lối dựng engine thật (engine
nhóm, Claude, Codex, engine Cộng hưởng, engine phán xử, engine quét) bị thay bằng hàm ném lỗi ngay; lượt nhóm, lô phán xử
và lượt quét chạy bằng engine giả có bộ đếm.

Đo:
- độ trễ API p50/p95 phía server: trang nhóm, trang tin, thả reaction nhóm, thả reaction chat riêng, khung nhịp của trợ lý;
- độ trễ event loop: một bộ hẹn giờ 10 ms chạy suốt pha đồng thời (nhiều request, lô phán xử, nhịp pulse), ghi độ trễ
  lớn nhất và p95 (cùng cách với tests/python/test_resonance_perf.py);
- số lượt model giả (lượt nhóm, lô phán xử, lượt quét);
- số câu SQL đọc/ghi mỗi thao tác (đếm bằng trace callback của sqlite3 trên MỌI kết nối mở sau khi bật đếm);
- chi phí vẽ của trình duyệt: Playwright cho Python không có trong .venv, nên đo hàm vẽ thuần của dashboard dưới node
  (tools/team_adaptive_ui_bench.js: danh sách nhóm, trang tin, hai tab nhận gói reaction). Đó KHÔNG phải số đo trình duyệt
  thật (không có layout, paint); giới hạn này ghi trong kết quả.

Số đo trên máy local KHÔNG phải số đo VPS. Kịch bản VPS: tools/team_adaptive_vps_measure.py (chưa chạy).

Module này cũng cấp `install_guards(main)` và `FakeModels` cho test T4 (chốt chặn engine thật dùng chung).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ───────────── đếm câu SQL ─────────────


class SqlTally:
    """Đếm câu SQL đọc/ghi trên mọi kết nối sqlite3 mở SAU khi `install()` (trace callback). Chỉ đếm khi `on`."""

    READ = ("SELECT", "WITH")
    WRITE = ("INSERT", "UPDATE", "DELETE", "REPLACE")

    def __init__(self):
        self.reads = 0
        self.writes = 0
        self.on = False
        self._lock = threading.Lock()

    def cb(self, stmt: str) -> None:
        if not self.on:
            return
        head = stmt.lstrip()[:10].upper()
        with self._lock:
            if head.startswith(self.READ):
                self.reads += 1
            elif head.startswith(self.WRITE):
                self.writes += 1

    def install(self) -> None:
        tally = self
        orig = sqlite3.connect

        class _Conn(sqlite3.Connection):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.set_trace_callback(tally.cb)

        def connect(*a, **k):
            if "factory" not in k:
                k["factory"] = _Conn
            return orig(*a, **k)

        sqlite3.connect = connect

    def reset(self) -> None:
        with self._lock:
            self.reads = self.writes = 0

    def snap(self) -> tuple:
        with self._lock:
            return self.reads, self.writes


# ───────────── chốt chặn engine thật và engine giả ─────────────


class RealEngineUsed(AssertionError):
    pass


REAL_ATTEMPTS: list = []   # mỗi lần có đường nào thử dựng engine thật (đã bị chặn) ghi một dòng; báo cáo đếm thật


REQ_PHASE_MIN_S = 1.5   # pha chỉ có request chạy tối thiểu chừng này giây (đủ khoảng 150 mẫu của bộ hẹn giờ 10 ms)

def _no_real_engine(*a, **k):
    REAL_ATTEMPTS.append(time.time())
    raise RealEngineUsed("a REAL engine was built or called; T4 harnesses only run fake engines")


class FakeModels:
    """Engine giả có bộ đếm cho ba loại lượt: lượt nhóm, lô phán xử, lượt quét của nhịp."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.team_calls = 0
        self.judge_calls = 0
        self.scan_calls = 0
        self.review_calls = 0
        self.judge_verdict = "silent"
        self.team_text = None
        self.team_error = None
        self._lock = threading.Lock()

    def _bump(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def team_engine_for(self, team, key, role):
        async def run(req):
            started = req.get("engine_started")
            if callable(started):
                r = started()
                if asyncio.iscoroutine(r):
                    await r
            self._bump("team_calls")
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.team_error is not None:
                raise self.team_error
            return {"text": self.team_text or f"{key} {role}: trả lời giả", "tokens_in": 1, "tokens_out": 1}
        return "fake", "fake-model", run

    async def judge_ask(self, prompt, purpose="", started=None):
        import re
        if callable(started):
            started()
        self._bump("judge_calls")
        if self.delay:
            await asyncio.sleep(self.delay)
        keys = re.findall(r"^- \[([^\]]+)\]", prompt, re.M)
        return json.dumps({"decisions": [{"agent_key": k, "verdict": self.judge_verdict, "score": 0.2,
                                          "reason": "giả"} for k in keys]})

    def pulse_engine_for(self, key, ctx, purpose):
        async def run(req):
            started = req.get("engine_started")
            if callable(started):
                r = started()
                if asyncio.iscoroutine(r):
                    await r
            self._bump("scan_calls")
            if self.delay:
                await asyncio.sleep(self.delay)
            return {"text": '{"action": "none", "reason": "giả"}', "tokens_in": 1, "tokens_out": 1}
        return "fake", "fake-model", run

    def review_engine_for(self, key, ctx, purpose):
        """Lượt học (xem xét reaction, tự rà soát của phần L): trả JSON rỗng hợp lệ, không đổi gì."""
        async def run(req):
            started = req.get("engine_started")
            if callable(started):
                r = started()
                if asyncio.iscoroutine(r):
                    await r
            self._bump("review_calls")
            if self.delay:
                await asyncio.sleep(self.delay)
            return {"text": '{"consumed": [], "changes": [], "reason": "giả"}', "tokens_in": 1, "tokens_out": 1}
        return "fake", "fake-model", run

    def total(self) -> int:
        return self.team_calls + self.judge_calls + self.scan_calls + self.review_calls


def install_guards(main, fakes: FakeModels | None = None) -> FakeModels:
    """Mọi lối dựng engine thật trong `main` ném lỗi ngay; lượt nhóm, phán xử, quét chạy bằng engine giả `fakes`."""
    fakes = fakes or FakeModels()
    # Chốt trung tâm (tests/run.py, tests/python/_paths.py đặt cùng hai biến): Judge từ chối mọi hàm hỏi model chưa đánh
    # dấu giả, main dừng engine phán xử và engine lượt học trước khi dựng. Bench chạy ngoài tests/run.py nên tự đặt.
    os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"] = "1"
    os.environ["JAVIS_NO_REAL_REVIEW_ENGINE"] = "1"
    os.environ["JAVIS_NO_REAL_TEAM_ENGINE"] = "1"
    import agent_judge  # noqa: WPS433
    judge_fake = agent_judge.mark_test_fake(fakes.judge_ask)
    main.team_engine.build = _no_real_engine
    main.claude_engine = _no_real_engine
    main.CodexCLI = _no_real_engine
    main._resonance_engine = _no_real_engine
    main._reply_policy_sandbox_engine = _no_real_engine
    main._TEAM_API.deps.engine_for = fakes.team_engine_for
    main._team_engine_for = fakes.team_engine_for
    main._agent_judge_ask = judge_fake
    main._pulse_engine_for = fakes.pulse_engine_for
    if hasattr(main, "_learn_engine_for"):
        main._learn_engine_for = fakes.review_engine_for
    judge = main._agent_judge()
    if judge is not None:
        judge.ask = judge_fake
    return fakes


def guards_hold(main) -> list:
    """Danh sách lối dựng engine thật CHƯA bị chặn (rỗng là an toàn)."""
    bad = []
    for name in ("claude_engine", "CodexCLI", "_resonance_engine", "_reply_policy_sandbox_engine"):
        if getattr(main, name) is not _no_real_engine:
            bad.append(name)
    if main.team_engine.build is not _no_real_engine:
        bad.append("team_engine.build")
    for env in ("JAVIS_NO_REAL_JUDGE_ENGINE", "JAVIS_NO_REAL_REVIEW_ENGINE", "JAVIS_NO_REAL_TEAM_ENGINE"):
        if os.environ.get(env) != "1":
            bad.append(env)
    judge = main._agent_judge()
    if judge is not None and not getattr(judge.ask, "javis_test_fake", False):
        bad.append("judge.ask (chưa đánh dấu giả)")
    return bad


# ───────────── dựng thế giới ─────────────


def make_brain(base: Path, n_agents: int) -> tuple:
    brain = base / "brain"
    (brain / "agents").mkdir(parents=True)
    slugs = [f"tro-ly-{i:02d}" for i in range(n_agents)]
    for i, slug in enumerate(slugs):
        (brain / "agents" / f"{slug}.md").write_text(
            f"---\nname: Trợ lý {i:02d}\nrole: Thành viên số {i}\n---\nBạn là trợ lý số {i}.\n",
            encoding="utf-8", newline="\n")
    return brain, slugs


def pct(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def summarize_ms(values) -> dict:
    return {"p50_ms": round(statistics.median(values), 2) if values else 0.0,
            "p95_ms": round(pct(values, 0.95), 2), "max_ms": round(max(values), 2) if values else 0.0,
            "runs": len(values)}


class StallWatch:
    """Chẩn đoán: luồng canh xem event loop có bị giữ quá `limit` giây không; nếu có, chụp ngăn xếp của luồng loop và
    đếm hàm sâu nhất trong code Javis (server/). Chỉ bật bằng --stall-watch."""

    def __init__(self, limit: float = 0.05):
        self.limit = limit
        self.beat = time.perf_counter()
        self.loop_tid = None
        self.hits: dict = {}
        self.armed = False
        self._stop = threading.Event()

    async def heartbeat(self, stop: asyncio.Event) -> None:
        self.loop_tid = threading.get_ident()
        while not stop.is_set():
            self.beat = time.perf_counter()
            await asyncio.sleep(0.005)

    def run(self) -> None:
        import traceback
        while not self._stop.is_set():
            time.sleep(self.limit / 2)
            if not self.armed or self.loop_tid is None or time.perf_counter() - self.beat < self.limit:
                continue
            frame = sys._current_frames().get(self.loop_tid)
            if frame is None:
                continue
            stack = traceback.extract_stack(frame)
            mine = [f for f in stack if "server" in f.filename.replace("\\", "/").split("/")[-2:-1]]
            key = (" < ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(mine[-4:])) if mine else
                   f"{Path(stack[-1].filename).name}:{stack[-1].lineno} {stack[-1].name}")
            self.hits[key] = self.hits.get(key, 0) + 1
            # Một mẫu mỗi lần bị giữ: chờ nhịp tim đổi rồi mới canh tiếp (chụp liên tục sẽ tranh GIL với chính loop).
            stalled_at = self.beat
            while not self._stop.is_set() and self.beat == stalled_at:
                time.sleep(self.limit)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self) -> dict:
        self._stop.set()
        return dict(sorted(self.hits.items(), key=lambda kv: -kv[1])[:15])


async def lag_probe(stop: asyncio.Event, out: list, period: float = 0.01) -> None:
    while not stop.is_set():
        t = time.perf_counter()
        await asyncio.sleep(period)
        out.append(max(0.0, time.perf_counter() - t - period) * 1000.0)


def run_ui_bench(args) -> dict:
    js = ROOT / "tools" / "team_adaptive_ui_bench.js"
    node = "node"
    try:
        p = subprocess.run([node, str(js), "--teams", str(args.teams), "--messages", str(min(200, args.messages)),
                            "--runs", str(max(3, args.runs // 3))], capture_output=True, text=True, encoding="utf-8",
                           timeout=600)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return {"available": False, "error": f"{type(ex).__name__}: {ex}"}
    if p.returncode != 0:
        return {"available": False, "error": p.stderr[-800:]}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"available": False, "error": p.stdout[-800:]}


def main_bench(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--teams", type=int, default=12)
    ap.add_argument("--messages", type=int, default=1500, help="số tin mỗi nhóm")
    ap.add_argument("--reactions", type=int, default=1500, help="tổng reaction của chủ seed sẵn")
    ap.add_argument("--agents", type=int, default=8)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--judge-messages", type=int, default=24, help="tin không @ gửi đồng thời (mở lô phán xử)")
    ap.add_argument("--pulse-ticks", type=int, default=5)
    ap.add_argument("--fake-delay", type=float, default=0.005, help="thời gian giả của một lượt model (giây)")
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--stall-watch", action="store_true",
                    help="chụp ngăn xếp luồng event loop mỗi lần bị giữ quá 50 ms (chẩn đoán)")
    ap.add_argument("--asyncio-debug", action="store_true",
                    help="in callback giữ event loop lâu hơn 40 ms (chẩn đoán nguồn lag)")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    base = Path(tempfile.mkdtemp(prefix="javis-t4-bench-"))
    os.environ["JAVIS_STATE_DIR"] = str(base / "state")
    os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
    os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"] = "1"   # chốt trung tâm, như tests/run.py
    os.environ["JAVIS_NO_REAL_REVIEW_ENGINE"] = "1"
    os.environ["JAVIS_NO_REAL_TEAM_ENGINE"] = "1"
    os.environ["JAVIS_PULSE_TICK_PAUSED"] = "1"       # nhịp nền của scheduler không chạy; bench gọi tick trực tiếp
    os.environ["JAVIS_RESONANCE_TICK_PAUSED"] = "1"
    (base / "state").mkdir()
    brain, slugs = make_brain(base, a.agents)
    tally = SqlTally()
    tally.install()
    sys.path.insert(0, str(ROOT / "server"))
    import main as M  # noqa: E402
    import agent_pulse as AP  # noqa: E402
    import httpx  # noqa: E402
    import resonance_store as RS  # noqa: E402

    fakes = install_guards(M, FakeModels(delay=a.fake_delay))
    assert not guards_hold(M), guards_hold(M)
    sent = []

    async def fake_publish(ev):
        sent.append(ev.get("type"))

    M._CHAT_RUNTIME.publish = fake_publish
    q = {"brain": str(brain)}
    report: dict = {}
    watch = StallWatch() if a.stall_watch else None

    async def go():
        tr = httpx.ASGITransport(app=M.app)
        watch_stop = asyncio.Event()
        if watch is not None:
            watch.start()
            asyncio.create_task(watch.heartbeat(watch_stop))
        async with httpx.AsyncClient(transport=tr, base_url="http://127.0.0.1:8080", timeout=120) as c:
            t0 = time.perf_counter()
            teams = []
            for i in range(a.teams):
                mem = [slugs[(i + k) % len(slugs)] for k in range(min(4, len(slugs)))]
                r = await c.post("/teams", params=q, json={"name": f"Nhóm {i:02d}", "members": mem,
                                                           "leader_slug": mem[0]})
                assert r.status_code == 200, r.text[:300]
                t = r.json()["team"]
                await c.post(f"/teams/{t['team_id']}/caps", params=q, json={"caps": {"daily_turns": 100000,
                                                                                       "judge_daily": 100000}})
                teams.append(t)
            rs = M._resonance_store()
            owner = RS.Principal("owner", "owner", M._brain_key(str(brain)))
            awake, asleep = [], []
            for i, slug in enumerate(slugs):
                rs.agent_register(owner, slug)
                if i % 2 == 0:
                    rs.agent_set_enabled(owner, slug, True)
            store = M.get_store()
            tm = M._TEAM_API.teams()
            agent_msgs = []
            for t in teams:
                keys = [m["agent_key"] for m in tm.members(t["team_id"])]
                for k in range(a.messages):
                    if k % 2 == 0:
                        store.append_team_message(t["session_id"], "user", f"Tin {k}: doanh thu, kế hoạch số {k}",
                                                  author_kind="owner", client_msg_id=f"seed-{t['team_id']}-{k}")
                    else:
                        key = keys[k % len(keys)]
                        mid, _ = store.append_team_message(t["session_id"], "assistant", f"Trả lời {k} của {key}",
                                                           author_kind="agent", author_ref=key)
                        agent_msgs.append((t, mid, key))
            # Reaction của chủ seed sẵn (thẳng vào kho, như đã thả từ trước).
            vals = ("like", "love", "haha", "wow", "sad", "angry")
            for n, (t, mid, key) in enumerate(agent_msgs[:a.reactions]):
                tm.set_reaction_ex(team_id=t["team_id"], session_id=t["session_id"], message_id=mid, agent_key=key,
                                   task_id=None, content_sha="seed", value=vals[n % 6], reasons=[], comment="",
                                   nonce=f"seed-r-{n}")
            # Outbox: reaction seed thẳng vào kho còn `pending` như sau một lần kho lỗi kéo dài. Đo thời gian bộ chiếu
            # xả hết hàng tồn (mỗi lần tối đa 200 dòng), rồi chi phí một lần chiếu ở trạng thái ổn định (chỉ còn đối soát
            # trạng thái các reaction đang chờ xem xét). Phần L chưa ghép thì các reaction "Chờ xem xét" không bao giờ
            # được phục vụ, nên lần đối soát này lặp lại ở MỌI nhịp 30 giây.
            proj = M._feedback_projector()
            drain_rounds, t_d = 0, time.perf_counter()
            while drain_rounds < 200:
                drain_rounds += 1
                await asyncio.to_thread(proj.project_pending)
                with sqlite3.connect(Path(os.environ["JAVIS_STATE_DIR"]) / "team.sqlite3") as cx:
                    left = cx.execute("SELECT COUNT(*) FROM team_reaction_log WHERE effect_state='pending'").fetchone()[0]
                if not left:
                    break
            drain_s = time.perf_counter() - t_d
            steady = []
            for _ in range(3):
                s_ = time.perf_counter()
                await asyncio.to_thread(proj.project_pending)
                steady.append((time.perf_counter() - s_) * 1000.0)
            with sqlite3.connect(Path(os.environ["JAVIS_STATE_DIR"]) / "team.sqlite3") as cx:
                open_n = cx.execute("SELECT COUNT(*) FROM team_reactions WHERE status IN ('pending_review','reviewing')"
                                    ).fetchone()[0]
            report["outbox"] = {"seeded_pending": min(a.reactions, len(agent_msgs)), "drain_rounds": drain_rounds,
                                "drain_seconds": round(drain_s, 2), "open_reactions_after": open_n,
                                "steady_project_pending_ms": [round(x, 1) for x in steady],
                                "note": "chi phí một lần chiếu mỗi nhịp khi còn reaction 'Chờ xem xét' (tối đa 200 dòng "
                                        "đối soát mỗi nhịp)"}
            # Agent ngủ/thức: nửa số ngữ cảnh nhóm ngủ đông, nửa đến hạn ngay (lượt quét giả khi tick).
            ps = M._pulse_store()
            AP.ensure(ps)
            now = time.time()
            for t in teams:
                for m in tm.members(t["team_id"]):
                    a_row = rs.agent_any(m["agent_key"]) or {}
                    if not a_row.get("enabled"):
                        continue
                    ctx = f"team:{t['team_id']}"
                    AP.ensure_row(ps, m["agent_key"], ctx, at=now)
                    if (len(awake) + len(asleep)) % 2 == 0:
                        with ps._Tx(ps) as cx:
                            cx.execute("UPDATE agent_pulse SET state='hibernating', tier=?, next_due_at=NULL WHERE "
                                       "agent_key=? AND context_ref=?", (AP.HIBERNATE, m["agent_key"], ctx))
                        asleep.append((m["agent_key"], ctx))
                    else:
                        with ps._Tx(ps) as cx:
                            cx.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=? AND context_ref=?",
                                       (now - 1, m["agent_key"], ctx))
                        awake.append((m["agent_key"], ctx))
            # Chat riêng: một phiên mỗi trợ lý, vài câu trả lời.
            dms = []
            for slug in slugs:
                sid = store.create_session(brain=str(brain), channel=f"agent:{slug}")
                for k in range(20):
                    store.append_message(sid, "user", f"Hỏi {k}")
                    dms.append((sid, store.append_message(sid, "assistant", f"Đáp {k} của {slug}"), slug))
            seed_s = time.perf_counter() - t0

            mid_team = teams[len(teams) // 2]
            enabled_slug = slugs[0]
            nonce = [0]

            def next_nonce():
                nonce[0] += 1
                return f"bench-{nonce[0]}"

            team_msgs = [x for x in agent_msgs if x[0]["team_id"] == mid_team["team_id"]]
            ops = {
                "GET /teams/{id} (trang nhóm)": lambda i: c.get(f"/teams/{mid_team['team_id']}", params=q),
                "GET /teams/{id}/messages (trang tin)": lambda i: c.get(f"/teams/{mid_team['team_id']}/messages",
                                                                        params=q),
                "POST reaction nhóm": lambda i: c.post(
                    f"/teams/{mid_team['team_id']}/messages/{team_msgs[i % len(team_msgs)][1]}/reaction", params=q,
                    json={"value": vals[i % 6], "nonce": next_nonce()}),
                "POST reaction chat riêng": lambda i: c.post(
                    f"/sessions/{dms[i % len(dms)][0]}/messages/{dms[i % len(dms)][1]}/reaction", params=q,
                    json={"value": vals[(i + 1) % 6], "nonce": next_nonce()}),
                "GET /agents/{slug}/pulse (khung nhịp)": lambda i: c.get(f"/agents/{enabled_slug}/pulse", params=q),
            }
            api_ms, io_ops = {}, {}
            if watch is not None:
                watch.armed = True
            for name, fn in ops.items():
                r = await fn(0)   # làm nóng
                assert r.status_code == 200, (name, r.status_code, r.text[:300])
                ms, rd, wr = [], [], []
                for i in range(1, a.runs + 1):
                    tally.reset()
                    tally.on = True
                    s = time.perf_counter()
                    r = await fn(i)
                    ms.append((time.perf_counter() - s) * 1000.0)
                    tally.on = False
                    reads, writes = tally.snap()
                    rd.append(reads)
                    wr.append(writes)
                    assert r.status_code == 200, (name, r.status_code, r.text[:300])
                api_ms[name] = summarize_ms(ms)
                io_ops[name] = {"sql_reads_median": statistics.median(rd), "sql_reads_max": max(rd),
                                "sql_writes_median": statistics.median(wr), "sql_writes_max": max(wr)}
            await M._TEAM_API.runner().wait_background()

            # ── Pha đồng thời: request hỗn hợp + lô phán xử + nhịp pulse, đo độ trễ event loop ──
            mixed_ms = []

            async def worker(w, base=1000):
                names = list(ops)
                for i in range(a.runs):
                    fn = ops[names[(w + i) % len(names)]]
                    s = time.perf_counter()
                    r = await fn(base + w * 1000 + i)
                    mixed_ms.append((time.perf_counter() - s) * 1000.0)
                    assert r.status_code == 200, r.text[:300]

            async def judge_wave(tag="jw"):
                outs = []
                for i in range(a.judge_messages):
                    t = teams[i % len(teams)]
                    outs.append(c.post(f"/teams/{t['team_id']}/messages", params=q,
                                       json={"text": f"Cả nhóm ơi, ai xem giúp kế hoạch doanh thu số {i} được không?",
                                             "client_msg_id": f"{tag}-{i}"}))
                rr = await asyncio.gather(*outs)
                assert all(x.status_code == 200 for x in rr), [x.text[:200] for x in rr if x.status_code != 200]

            ticks = []

            tick_ms = []

            async def pulse_wave():
                host = M._pulse_host()
                for _ in range(a.pulse_ticks):
                    s_ = time.perf_counter()
                    ticks.append(await host.tick())
                    tick_ms.append((time.perf_counter() - s_) * 1000.0)
                    await asyncio.sleep(0.02)
                s_ = time.perf_counter()
                await host.wait_idle(60)
                tick_ms.append(-(time.perf_counter() - s_) * 1000.0)   # số âm: thời gian chờ các lần thức xong

            # Từng nguồn tải riêng (để biết nguồn nào giữ event loop), rồi tất cả cùng lúc.
            isolated = {}

            async def requests_wave():
                # Lặp đợt request tới khi đủ ít nhất REQ_PHASE_MIN_S giây: máy nhanh (CI) xong một đợt trong nửa giây chỉ
                # có vài chục mẫu của bộ hẹn giờ 10 ms, không đủ để nói về p95. Số mẫu tăng, ngưỡng giữ nguyên.
                s0 = time.perf_counter()
                while True:
                    await asyncio.gather(*(worker(w, 500000) for w in range(a.concurrency)))
                    if time.perf_counter() - s0 >= REQ_PHASE_MIN_S:
                        return

            for label, make in (("requests_only", requests_wave),
                                ("judge_wave_only", lambda: judge_wave("iso")),
                                ("pulse_ticks_only", pulse_wave)):
                st_, lg_ = asyncio.Event(), []
                pr_ = asyncio.create_task(lag_probe(st_, lg_))
                s_ = time.perf_counter()
                await make()
                await M._TEAM_API.runner().wait_background()
                el_ = time.perf_counter() - s_
                st_.set()
                await pr_
                isolated[label] = {"seconds": round(el_, 2), "tick_ms": [round(x, 1) for x in tick_ms]
                                   if label == "pulse_ticks_only" else None, "lag_p95_ms": round(pct(lg_, 0.95), 2),
                                   "lag_max_ms": round(max(lg_) if lg_ else 0.0, 2), "samples": len(lg_)}
            mixed_ms.clear()
            ticks.clear()
            tick_ms.clear()
            calls0 = (fakes.team_calls, fakes.judge_calls, fakes.scan_calls)
            stop, lags = asyncio.Event(), []
            probe = asyncio.create_task(lag_probe(stop, lags))
            t1 = time.perf_counter()
            await asyncio.gather(*(worker(w) for w in range(a.concurrency)), judge_wave(), pulse_wave())
            await M._TEAM_API.runner().wait_background()
            conc_s = time.perf_counter() - t1
            stop.set()
            await probe
            calls1 = (fakes.team_calls, fakes.judge_calls, fakes.scan_calls)

            pool = M._TEAM_API.runner()
            report.update({
                "seed_seconds": round(seed_s, 2),
                "api_sequential": api_ms,
                "store_io_per_op": io_ops,
                "isolated_load_lag": isolated,
                "concurrent_phase": {
                    "seconds": round(conc_s, 2), "workers": a.concurrency, "requests": len(mixed_ms),
                    "request_latency": summarize_ms(mixed_ms),
                    "event_loop_lag": {"probe_period_ms": 10, "samples": len(lags),
                                       "p50_ms": round(pct(lags, 0.5), 2), "p95_ms": round(pct(lags, 0.95), 2),
                                       "max_ms": round(max(lags) if lags else 0.0, 2)},
                    "fake_model_calls": {"team_turns": calls1[0] - calls0[0], "judge_batches": calls1[1] - calls0[1],
                                         "pulse_scans": calls1[2] - calls0[2]},
                    "judge_messages": a.judge_messages, "pulse_ticks": a.pulse_ticks,
                    "pulse_claims": sum(int((x or {}).get("claims") or 0) for x in ticks),
                    "pulse_tick_ms": [round(x, 1) for x in tick_ms],
                    "pulse_note": ("lần thức chạy code trước; không có cơ hội mới thì không gọi model "
                                   "(thiết kế 5.5), nên pulse_scans có thể bằng 0 dù có lần thức"),
                },
                "fake_model_calls_total": {"team_turns": fakes.team_calls, "judge_batches": fakes.judge_calls,
                                           "pulse_scans": fakes.scan_calls, "review_turns": fakes.review_calls},
                "pool": {"max_parallel_teams": pool.max_parallel_teams, "held_after": len(pool._pool)},
                "dataset": {"teams": a.teams, "messages_per_team": a.messages, "messages_total": a.teams * a.messages,
                            "owner_reactions_seeded": min(a.reactions, len(agent_msgs)), "agents": a.agents,
                            "agents_resonance_on": len([s for i, s in enumerate(slugs) if i % 2 == 0]),
                            "team_contexts_asleep": len(asleep), "team_contexts_due": len(awake),
                            "dm_sessions": len(slugs)},
                "ws_events_published": len(sent),
            })

    if a.asyncio_debug:
        import logging
        logging.basicConfig(level=logging.WARNING)

        async def debug_go():
            asyncio.get_running_loop().slow_callback_duration = 0.04
            await go()
        asyncio.run(debug_go(), debug=True)
    else:
        asyncio.run(go())
    still = guards_hold(M)
    if watch is not None:
        report["stall_watch"] = watch.stop()
    report = {
        "kind": "t4_adaptive_bench_local_fake_engine",
        "note": ("Engine GIẢ (không gọi model), host thật main.app trong tiến trình qua httpx ASGITransport. Thời gian "
                 "phía server trên máy local; KHÔNG phải số đo VPS, KHÔNG phải trình duyệt thật."),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": {"platform": platform.platform(), "python": platform.python_version(),
                    "processor": platform.processor(), "cpu_count": os.cpu_count()},
        "conditions": {"fake_delay_s": a.fake_delay, "runs": a.runs, "concurrency": a.concurrency,
                       "pulse_tick_scheduler": "paused (bench gọi tick trực tiếp)",
                       "call_ceiling_env": "không đặt"},
        **report,
        "real_engine_guard": {"unguarded": still, "real_engine_build_attempts": len(REAL_ATTEMPTS),
                              "note": "đếm bằng chốt: mỗi lần một đường thử dựng engine thật thì chốt ghi lại rồi ném lỗi"},
    }
    if not a.no_ui:
        report["ui_render"] = run_ui_bench(a)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8", newline="\n")
    return report


if __name__ == "__main__":
    main_bench()
    raise SystemExit(0)
