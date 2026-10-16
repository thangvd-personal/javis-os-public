"""Dựng một "thế giới" thật cho test bộ phán xử theo agent (T3 phần J): kho SQLite tạm thật (resonance, phiên, Kanban,
nhóm, phán xử), bộ chạy nhóm thật, engine lượt nhóm GIẢ và engine phán xử GIẢ. Không có đường nào tới model thật: engine
giả ném lỗi nếu bị gọi với vai lạ, và `real_engine_guard` chặn mọi lối dựng engine thật nếu test có nạp main.

Không phải file test (tên bắt đầu bằng `_`), `tests/run.py` không chạy trực tiếp.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import json
import re
import tempfile
import time
from pathlib import Path

import agent_judge as AJ  # noqa: E402
import agent_judge_store as AJS  # noqa: E402
import agent_ledger as AL  # noqa: E402
import feedback_projector as FP  # noqa: E402
import resonance_store as RS  # noqa: E402
import sessions  # noqa: E402
import task_store  # noqa: E402
import team_runner as TR  # noqa: E402
import team_store  # noqa: E402

TEMP = Path(tempfile.mkdtemp(prefix="javis-judge-world-"))
_n = [0]

fails: list = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:500]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def finish():
    if fails:
        print(f"\n{len(fails)} FAIL")
        raise SystemExit(1)
    print("\nOK")
    raise SystemExit(0)


class FakeTeamEngine:
    """Engine lượt nhóm giả: ghi mọi lần gọi (vai, agent), trả chữ theo kịch bản."""

    def __init__(self):
        self.calls = []
        self.script = {}

    def engine_for(self, team, key, role):
        async def run(req):
            self.calls.append({"agent": key, "role": role, "prompt": req["prompt"]})
            q = self.script.get((key, role)) or []
            return {"text": q.pop(0) if q else f"{key} {role} trả lời", "tokens_in": 1, "tokens_out": 1}
        return "fake", "fake-model", run

    def roles(self, role):
        return [c for c in self.calls if c["role"] == role]


class FakeJudgeEngine:
    """Engine phán xử giả. `decide(prompt) -> str` (JSON thô) hay ném lỗi. Mặc định: mọi người `silent`."""

    def __init__(self):
        self.prompts = []
        self.decide = None
        self.delay = 0.0
        self.raise_exc = None
        self.unavailable = False

    async def ask(self, prompt, purpose, started):
        if self.unavailable:
            raise AJ.EngineUnavailable("fake engine unavailable")
        started()
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.decide is None:
            keys = re.findall(r"^- \[([^\]]+)\]", prompt, re.M)
            return json.dumps({"decisions": [{"agent_key": k, "verdict": "silent", "score": 0.1, "reason": "không"}
                                             for k in keys]})
        return self.decide(prompt)


def batch(**by_key):
    """JSON lô: by_key = {agent_key: (verdict, score[, wait_for, ask_clarify])}."""
    items = []
    for k, v in by_key.items():
        d = {"agent_key": k, "verdict": v[0], "score": v[1], "reason": f"lý do {v[0]}"}
        if len(v) > 2:
            d["wait_for"] = v[2]
        if len(v) > 3:
            d["ask_clarify"] = v[3]
        items.append(d)
    return json.dumps({"decisions": items}, ensure_ascii=False)


def keys_in(prompt):
    return re.findall(r"^- \[([^\]]+)\]", prompt, re.M)


class World:
    def __init__(self, label, members=("lan", "minh", "hoa"), roles=None, enabled=(), leader="lan",
                 clock=None):
        _n[0] += 1
        d = TEMP / f"{_n[0]:02d}-{label}"
        d.mkdir()
        self.dir = d
        self.gs = RS.GoalStore(d / "resonance.sqlite3")
        self.ss = sessions.SessionStore(db_path=d / "chat.db")
        self.ts = task_store.TaskStore(d / "kanban.sqlite3")
        self.tm = team_store.TeamStore(d / "team.sqlite3")
        self.js = AJS.JudgeStore(d / "agent_judge.sqlite3")
        self.owner = RS.Principal("owner", "owner", str(d))
        self.roles = dict(roles or {})
        self.keys = {}
        for slug in members:
            ag = self.gs.agent_register(self.owner, slug)
            if slug in enabled:
                ag = self.gs.agent_set_enabled(self.owner, slug, True)
            self.keys[slug] = ag["agent_key"]
        self.slug_of = {v: k for k, v in self.keys.items()}
        self.eng = FakeTeamEngine()
        self.jeng = FakeJudgeEngine()
        self.now = [time.time()]
        self.clock = clock or (lambda: self.now[0])
        self.ledger = AL.CallLedger(self.gs)
        self.wake = AL.WakeReasons(self.gs)
        self.judge = AJ.Judge(
            store=self.js, ledger=lambda: self.ledger, wake=lambda: self.wake, ask=AJ.mark_test_fake(self.jeng.ask),
            engine_label=lambda: ("fake-aux", "fake-judge-model"),
            resonance_on=lambda key: bool((self.gs.agent_any(key) or {}).get("enabled")),
            resumed_at=lambda key, ctx: self.gs.resumed_at(key, ctx), clock=self.clock, timeout_s=2.0)
        box = {}
        self.pj = FP.FeedbackProjector(resonance=lambda: self.gs, sessions=lambda: self.ss, teams=lambda: self.tm,
                                       team_view=lambda t, s, m: box["r"].team_message_view(t, s, m),
                                       judge_label=self.judge.label)
        self.runner = TR.TeamRunner(teams=self.tm, tasks=self.ts, sessions=self.ss, engine_for=self.eng.engine_for,
                                    publish=lambda e: None, agent_info=self.agent_info, projector=self.pj,
                                    ceiling=AL.TeamCeiling(self.gs), judge=self.judge)
        box["r"] = self.runner
        tid = team_store.new_team_id()
        tsid = self.ss.create_session(brain=str(d), channel=f"team:{tid}")
        self.tm.create_team(str(d), "Đội vận hành", "", tsid, team_id=tid)
        for slug in members:
            self.tm.add_member(tid, self.keys[slug], slug)
        if leader:
            self.tm.set_leader(tid, self.keys[leader])
        self.team_id = tid
        self.ctx = f"team:{tid}"
        self._cm = 0

    def agent_info(self, root, key):
        slug = self.slug_of.get(key)
        if slug is None:
            return None
        return {"slug": slug, "name": slug.capitalize(), "system": "", "role": self.roles.get(slug, "")}

    @property
    def team(self):
        return self.tm.get(self.team_id)

    def k(self, slug):
        return self.keys[slug]

    def send(self, text, mentions=()):
        """Chủ gửi tin qua đúng đường của route (submit rồi run_message), chờ mọi việc nền."""
        self._cm += 1
        box = {}

        async def go():
            # Như route: submit chạy ở luồng phụ khi bộ chạy đã biết event loop (gỡ chờ xếp việc nền về đúng loop).
            self.runner._bind_loop()
            box["sub"] = sub = await asyncio.to_thread(
                self.runner.submit, self.team_id, text, mentions=[self.keys[m] for m in mentions],
                client_msg_id=f"c{self._cm}-{time.time()}")
            if sub["replies"] or sub.get("judge"):
                await self.runner.run_message(sub)
            await self.runner.wait_background()
        asyncio.run(go())
        return box["sub"]

    def do(self, fn, *args, **kw):
        """Gọi một thao tác đồng bộ của bộ chạy ở luồng phụ (như route), rồi chờ mọi việc nền nó sinh ra."""
        box = {}

        async def go():
            self.runner._bind_loop()
            box["v"] = await asyncio.to_thread(fn, *args, **kw)
            await asyncio.sleep(0)
            await self.runner.wait_background()
        asyncio.run(go())
        return box.get("v")

    def drain(self):
        async def go():
            await self.runner.wait_background()
        asyncio.run(go())

    def decisions(self, slug=None, kind=None):
        rows = self.js.decisions(self.keys[slug] if slug else None, self.ctx, 500)
        return [r for r in rows if kind is None or r["kind"] == kind]

    def judge_calls(self):
        return [r for r in self.ledger.rows() if r["purpose"] == "judge_batch"]

    def messages(self):
        return self.ss.team_messages_after(self.team["session_id"], 0, 200)
