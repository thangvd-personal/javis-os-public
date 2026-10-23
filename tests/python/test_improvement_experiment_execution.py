"""V2 baseline uses real Runner/workspace/encryption/usage with synthetic packets only."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import hashlib
import json
import subprocess
import threading
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

import test_improvement_experiment_lineage as fixtures
import improvement_store as IM
import improvement_evaluation as EVAL
import usage_store as US
import usage_index as UI
from agent_ledger import CallLedger
from improvement_trial_runner import TrialRunner
from improvement_trials import TrialStore
from improvement_experiment_admission import ExperimentAdmission
from improvement_claude import ClaudeTrialAdapter

_REAL_RUN, _REAL_POPEN = subprocess.run, subprocess.Popen


def _acl_only(original, command, *args, **kwargs):
    if Path(command[0]).name.lower() not in {"whoami", "whoami.exe", "icacls", "icacls.exe"}:
        raise AssertionError("Only disposable workspace ACL metadata commands are permitted")
    return original(command, *args, **kwargs)


class ExperimentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.owner, self.assets, self.host = self.h.owner, self.h.assets, self.h.h.host
        self.source = self.h.h.f
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        state = self.source.root / "usage-state"
        state.mkdir()
        for obj, name, value in ((US, "_PATH", state / "usage.json"), (US, "_EVENTS_PATH", state / "usage-events.jsonl"),
                (US, "_TELEMETRY_ERRORS", {}), (UI, "_EVENTS_PATH", state / "usage-events.jsonl"),
                (UI, "DB_PATH", state / "usage_index.db"), (UI, "_claude_dir", lambda: str(state / "empty-native")),
                (UI, "_codex_dir", lambda: str(state / "empty-codex"))):
            self.stack.enter_context(patch.object(obj, name, value))
        self.stack.enter_context(patch("subprocess.run", side_effect=lambda *a, **kw: _acl_only(_REAL_RUN, *a, **kw)))
        self.stack.enter_context(patch("subprocess.Popen", side_effect=lambda *a, **kw: _acl_only(_REAL_POPEN, *a, **kw)))
        self.trials = TrialStore(self.assets)
        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": True, "capability_hash": "d" * 64}):
            admitted = ExperimentAdmission(self.h.h.plans()).admit(self.owner, "association-experiment", self.h.comparison["id"],
                self.h.request["lineage"]["cell_position"], expected_generation=self.h.frozen["generation"], event_key="e2e-admit-baseline-v2")
        self.h.request = admitted.request
        self.trial = self.trials.get(self.owner, admitted.trial_id)
        self.executed, self.closed = 0, False

    def factory(self, pin, *, workspace_root, before_engine_start):
        test = self

        class SyntheticAdapter:
            def verify(self):
                return {"supported": True, "capability_hash": "d" * 64}

            async def execute(self, native_request, workspace):
                test.executed += 1
                identity = {key: workspace[key] for key in ("trial_id", "attempt", "input_hash")}

                def packet(sequence, kind, **fields):
                    return {"version": 1, "sequence": sequence, "type": kind, **identity, **fields}

                try:
                    test.workspace_path = Path(workspace["path"])
                    manifest = test.trials.workspace.validate(workspace)
                    test.assertEqual(manifest["assets"][0]["content"].encode(), test.source.skill.read_bytes())
                    test.assertEqual(manifest["bundle_hash"], test.h.value["base_bundle_hash"])
                    yield packet(1, "worker_ready", capability_hash="d" * 64)
                    await before_engine_start()
                    current = await asyncio.to_thread(test.trials.get, test.owner, native_request["trial_id"])
                    test.assertEqual(current["request"]["version"], 2)
                    test.assertEqual(current["request"]["lineage"], test.h.request["lineage"])
                    call = CallLedger(test.assets.store).get(current["attempts"][0]["call_id"])
                    test.assertIsNotNone(call["engine_at"])
                    yield packet(2, "engine_start_requested")
                    artifact = '{"value":"synthetic baseline"}\r\n'
                    events = [
                        {"type": "native_system", "subtype": "init", "data": {"session_id": "synthetic-b-execution"}},
                        {"type": "observation", "model": pin["model"], "message_id": "synthetic-b-request",
                         "session_id": "synthetic-b-execution", "usage": {"input_tokens": 3, "output_tokens": 2,
                            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
                        {"type": "artifact", "content": artifact, "hash": hashlib.sha256(artifact.encode()).hexdigest()},
                        {"type": "installation_receipt", "receipt": {"label": "synthetic-incomplete"},
                         "hash": IM._hash({"label": "synthetic-incomplete"})},
                        {"type": "terminal", "subtype": "success", "is_error": False, "artifact_status": "observed"}]
                    for sequence, event in enumerate(events, 3):
                        yield packet(sequence, "engine_event", event=event)
                    yield packet(8, "worker_complete", fixture_events=[], tool_denials=[])
                finally:
                    test.closed = True

        return SyntheticAdapter()

    async def test_actual_v2_baseline_end_to_end_preserves_lineage_usage_and_unknowns(self):
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader, live_guard=self.host.live_guard,
                             adapter_factory=self.factory)
        result = await runner.run(self.owner, self.trial["trial_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(self.closed)
        self.assertEqual(self.executed, 1)
        report = result["result"]
        self.assertEqual(report["version"], 2)
        self.assertEqual(report["request_hash"], IM._hash(self.h.request))
        self.assertEqual(report["experiment_lineage"], self.h.request["lineage"])
        self.assertNotIn("execution_records", {check["id"] for check in report["mandatory"]})
        self.assertIn(report["verdict"], ("unknown", "fail"))
        self.assertEqual((report["usage"]["tokens_in"], report["usage"]["tokens_out"]), (3, 2))
        self.assertIsNone(report["usage"]["requests"])
        self.assertEqual(report["usage"]["coverage"]["status"], "partial")
        replay = await runner.run(self.owner, self.trial["trial_id"])
        self.assertEqual(replay["result"], report)
        self.assertEqual(self.executed, 1)
        self.h.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.h.frozen["generation"], event_key="stop-after-technical-v2-result")
        self.assertEqual(self.trials.read_result(self.owner, self.trial["trial_id"], 1), report)
        from improvement_private_review import PrivateReview
        private = PrivateReview(self.assets, pack_state_dir=self.source.root / "packs", fixture_observer=self.host.fixture_observer)
        self.assertTrue(private.artifact(self.owner, self.trial["trial_id"], 1))

    async def test_stop_during_actual_checker_work_blocks_late_result_and_settles_usage(self):
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader, live_guard=self.host.live_guard,
                             adapter_factory=self.factory)
        entered, release = asyncio.Event(), threading.Event()
        loop, evaluate = asyncio.get_running_loop(), EVAL.evaluate_trial

        def blocked_checker(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError("Synthetic checker ownership was not released")
            return evaluate(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=blocked_checker):
            task = asyncio.create_task(runner.run(self.owner, self.trial["trial_id"]))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                await asyncio.to_thread(self.h.h.h.records().begin_stop, self.owner, "association-experiment",
                    kind="cancelled", reason="owner_requested", expected_generation=self.h.frozen["generation"],
                    event_key="stop-during-v2-checker")
            finally:
                release.set()
            result = await asyncio.wait_for(task, 10)
        self.assertEqual(result["status"], "unknown")  # Existing post-query abort semantics preserve uncertainty.
        self.assertIsNone(result["result"])
        self.assertEqual(self.executed, 1)
        self.assertTrue(self.closed)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_results WHERE trial_id=?",
                                               (self.trial["trial_id"],)).fetchone()[0], 0)
        call = CallLedger(self.assets.store).get(result["attempts"][0]["call_id"])
        self.assertEqual(call["status"], "interrupted")
        self.assertFalse(self.workspace_path.exists())


if __name__ == "__main__":
    unittest.main()
