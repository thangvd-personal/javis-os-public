"""Real solver lifetime/storage/usage for v3, with synthetic worker packets only."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import hashlib
import subprocess
import threading
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

import test_improvement_preparation as fixtures
from test_improvement_experiment_execution import _acl_only, _REAL_RUN, _REAL_POPEN
import improvement_store as IM
import improvement_evaluation as EVAL
import usage_store as US
import usage_index as UI
from agent_ledger import CallLedger
from improvement_trial_runner import TrialRunner
from improvement_trial_tasks import TrialTasks
from improvement_trial_views import read_trial


class PreparationExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self, repeat_policy='feasibility'):
        self.f = fixtures.PreparationTests()
        self.f.setUp(repeat_policy=repeat_policy)
        self.addCleanup(self.f.doCleanups)
        self.owner, self.assets, self.host = self.f.owner, self.f.assets, self.f.host
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        state = self.f.f.f.root / "preparation-usage"
        state.mkdir()
        for obj, name, value in ((US, "_PATH", state / "usage.json"), (US, "_EVENTS_PATH", state / "events.jsonl"),
                (US, "_TELEMETRY_ERRORS", {}), (UI, "_EVENTS_PATH", state / "events.jsonl"),
                (UI, "DB_PATH", state / "index.db"), (UI, "_claude_dir", lambda: str(state / "empty-native")),
                (UI, "_codex_dir", lambda: str(state / "empty-codex"))):
            self.stack.enter_context(patch.object(obj, name, value))
        self.stack.enter_context(patch("subprocess.run", side_effect=lambda *a, **kw: _acl_only(_REAL_RUN, *a, **kw)))
        self.stack.enter_context(patch("subprocess.Popen", side_effect=lambda *a, **kw: _acl_only(_REAL_POPEN, *a, **kw)))
        self.f.freeze()
        self.admitted = self.f.admit()
        self.trials = self.f.service().trials
        self.closed = False
        self.starts = 0

    def factory(self, pin, *, workspace_root, before_engine_start):
        test = self

        class SyntheticAdapter:
            def verify(self):
                return {"supported": True, "capability_hash": "c" * 64}

            async def execute(self, native_request, workspace):
                identity = {key: workspace[key] for key in ("trial_id", "attempt", "input_hash")}

                def packet(sequence, kind, **fields):
                    return {"version": 1, "sequence": sequence, "type": kind, **identity, **fields}

                try:
                    test.workspace_path = Path(workspace["path"])
                    manifest = test.trials.workspace.validate(workspace)
                    test.assertEqual(manifest["assets"][0]["content"].encode(), test.f.f.f.skill.read_bytes())
                    yield packet(1, "worker_ready", capability_hash="c" * 64)
                    if getattr(test, "pause_before_start", False):
                        test.start_entered.set()
                        await test.start_release.wait()
                    await before_engine_start()
                    test.starts += 1
                    current = test.trials.get(test.owner, test.admitted.trial_id)
                    call = CallLedger(test.assets.store).get(current["attempts"][0]["call_id"])
                    test.assertIsNotNone(call["engine_at"])
                    yield packet(2, "engine_start_requested")
                    artifact = '{"value":"synthetic preparation baseline"}\r\n'
                    for sequence, event in enumerate([
                        {"type": "native_system", "subtype": "init", "data": {"session_id": "synthetic-preparation"}},
                        {"type": "observation", "model": pin["model"], "message_id": "synthetic-preparation-request",
                         "session_id": "synthetic-preparation", "usage": {"input_tokens": 3, "output_tokens": 2,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
                        {"type": "artifact", "content": artifact, "hash": hashlib.sha256(artifact.encode()).hexdigest()},
                        {"type": "installation_receipt", "receipt": {"label": "synthetic-incomplete"},
                         "hash": IM._hash({"label": "synthetic-incomplete"})},
                        {"type": "terminal", "subtype": "success", "is_error": False, "artifact_status": "observed"}], 3):
                        yield packet(sequence, "engine_event", event=event)
                    yield packet(8, "worker_complete", fixture_events=[], tool_denials=[])
                finally:
                    test.closed = True

        return SyntheticAdapter()

    async def test_real_preparation_result_keeps_exact_lineage_unknowns_and_cold_history(self):
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader, live_guard=self.host.live_guard,
                             adapter_factory=self.factory)
        result = await runner.run(self.owner, self.admitted.trial_id)
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(self.closed)
        report = result["result"]
        self.assertEqual(report["version"], 3)
        self.assertEqual(report["request_hash"], IM._hash(self.admitted.request))
        self.assertEqual(report["preparation_lineage"], self.admitted.request["preparation"])
        self.assertNotIn("experiment_lineage", report)
        self.assertNotIn("execution_records", {row["id"] for row in report["mandatory"]})
        self.assertIn(report["verdict"], ("unknown", "fail"))
        self.assertEqual((report["usage"]["tokens_in"], report["usage"]["tokens_out"]), (3, 2))
        self.assertIsNone(report["usage"]["requests"])
        self.assertEqual(report["usage"]["coverage"]["status"], "partial")
        self.assertEqual((await runner.run(self.owner, self.admitted.trial_id))["result"], report)
        self.assertEqual(self.starts, 1)
        service = self.f.service()
        self.assertTrue(callable(getattr(service, "complete", None)), "Actual preparation completion is absent")
        held = self.trials.workspace.acquire_brain_execution(self.owner.brain_id)
        self.assertIsNotNone(held)
        try:
            with self.assertRaises(IM.ConflictError):
                await asyncio.to_thread(service.complete, self.owner, "association-experiment", expected_generation=2,
                                        event_key="owner-complete-baseline-preparation")
        finally:
            held.close()
        sealed = await asyncio.to_thread(service.complete, self.owner, "association-experiment", expected_generation=2,
                                         event_key="owner-complete-baseline-preparation")
        self.assertEqual((sealed["phase"], sealed["generation"]), ("proposing", 3))
        self.assertEqual(len(sealed["result_refs"]), 1)
        self.assertEqual(sealed["result_refs"][0]["trial_id"], self.admitted.trial_id)
        self.assertEqual(sealed["result_refs"][0]["result_hash"], IM._hash(report))
        self.f.plans.records.begin_stop(self.owner, "association-experiment", kind="cancelled", reason="owner_requested",
                                        expected_generation=3, event_key="stop-after-preparation-result")
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Completion replay cannot decrypt")):
            self.assertEqual(service.complete(self.owner, "association-experiment", expected_generation=2,
                                             event_key="owner-complete-baseline-preparation"), sealed)
        self.assertEqual(self.trials.read_result(self.owner, self.admitted.trial_id, 1), report)
        projection = read_trial(self.assets, self.owner, self.admitted.trial_id)
        self.assertEqual(projection["preparation_lineage"], self.admitted.request["preparation"])
        from improvement_private_review import PrivateReview
        private = PrivateReview(self.assets, pack_state_dir=self.f.f.f.root / "packs", fixture_observer=self.host.fixture_observer)
        self.assertTrue(private.artifact(self.owner, self.admitted.trial_id, 1))

    async def test_stop_before_engine_marker_refunds_actual_preparation_without_query(self):
        self.pause_before_start = True
        self.start_entered, self.start_release = asyncio.Event(), asyncio.Event()
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader, live_guard=self.host.live_guard,
                             adapter_factory=self.factory)
        task = asyncio.create_task(runner.run(self.owner, self.admitted.trial_id))
        try:
            await asyncio.wait_for(self.start_entered.wait(), 10)
            await asyncio.to_thread(self.f.plans.records.begin_stop, self.owner, "association-experiment",
                kind="cancelled", reason="owner_requested", expected_generation=2, event_key="stop-before-preparation-marker")
        finally:
            self.start_release.set()
        result = await asyncio.wait_for(task, 10)
        self.assertEqual(result["status"], "not_run", result)
        self.assertEqual(self.starts, 0)
        self.assertTrue(self.closed)
        call = CallLedger(self.assets.store).get(result["attempts"][0]["call_id"])
        self.assertEqual(call["status"], "refunded")
        self.assertIsNone(call["engine_at"])
        self.assertFalse(self.workspace_path.exists())

    async def test_stop_during_real_checker_prevents_late_preparation_result(self):
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader, live_guard=self.host.live_guard,
                             adapter_factory=self.factory)
        entered, release = asyncio.Event(), threading.Event()
        loop, evaluate = asyncio.get_running_loop(), EVAL.evaluate_trial

        def checker(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError("Synthetic checker not released")
            return evaluate(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=checker):
            task = asyncio.create_task(runner.run(self.owner, self.admitted.trial_id))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                await asyncio.to_thread(self.f.plans.records.begin_stop, self.owner, "association-experiment",
                    kind="cancelled", reason="owner_requested", expected_generation=2, event_key="stop-during-preparation-checker")
            finally:
                release.set()
            result = await asyncio.wait_for(task, 10)
        self.assertEqual(result["status"], "unknown", result)
        self.assertIsNone(result["result"])
        self.assertEqual(self.starts, 1)
        self.assertTrue(self.closed)
        call = CallLedger(self.assets.store).get(result["attempts"][0]["call_id"])
        self.assertEqual(call["status"], "interrupted")
        self.assertFalse(self.workspace_path.exists())
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_trial_results WHERE trial_id=?",
                                               (self.admitted.trial_id,)).fetchone()[0], 0)

    async def test_existing_dispatcher_owns_the_exact_preparation_admission_without_second_call(self):
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader, live_guard=self.host.live_guard,
                             adapter_factory=self.factory)
        tasks = TrialTasks(self.trials, runner)
        try:
            await tasks.start(self.owner, self.admitted.request, event_key=self.admitted.reserve_event_key)
            owned = tasks._tasks.get((self.owner.brain_id, self.admitted.trial_id))
            if owned is not None:
                await asyncio.wait_for(asyncio.shield(owned[1]), 10)
            result = await runner._response(self.owner, self.admitted.trial_id)
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["result"]["version"], 3)
            self.assertEqual(self.starts, 1)
            self.assertTrue(self.closed)
            with closing(self.assets.store._conn()) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM agent_calls").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM improvement_trials").fetchone()[0], 1)
        finally:
            await tasks.close()


if __name__ == "__main__":
    unittest.main()
