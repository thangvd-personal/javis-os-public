"""Concrete v2 Runner/dispatcher/stop integration with synthetic native packets."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import threading
import unittest
from contextlib import aclosing, closing
from unittest.mock import patch

import improvement_evaluation as EVAL
import improvement_store as IM
from improvement_experiment_service import ExperimentService
from improvement_trial_runner import TrialRunner
from improvement_trial_service import OwnerTrialService
import test_improvement_experiment_execution as fixtures


class ExperimentLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.ExperimentExecutionTests()
        self.f.setUp()

        def cleanup_fixture():
            self.f.stack.close()
            self.assertTrue(self.f.h.doCleanups())

        self.addCleanup(cleanup_fixture)
        self.owner, self.assets = self.f.owner, self.f.assets
        self.trials = OwnerTrialService(self.f.host)
        self.addAsyncCleanup(self.trials.close)
        self.service = ExperimentService(self.f.h.h.plans(), self.trials)
        self.addAsyncCleanup(self.service.close)
        self.entered = asyncio.Event()
        self.pause_packet = "engine_start_requested"
        self.trials.tasks.runner = TrialRunner(self.trials.trials, context_loader=self.f.host.context_loader,
            live_guard=self.f.host.live_guard, adapter_factory=self.factory)

    def factory(self, pin, *, workspace_root, before_engine_start):
        inner = self.f.factory(pin, workspace_root=workspace_root, before_engine_start=before_engine_start)
        test = self

        class Adapter:
            def verify(self):
                return inner.verify()

            async def execute(self, request, workspace):
                async with aclosing(inner.execute(request, workspace)) as packets:
                    async for packet in packets:
                        yield packet
                        if packet["type"] == test.pause_packet:
                            test.entered.set()
                            await asyncio.Future()

        return Adapter()

    async def start_owned(self):
        key = "experiment-reserve-" + IM._hash([self.owner.brain_id, "e2e-admit-baseline-v2"])
        row = await self.trials.tasks.start(self.owner, self.f.h.request, event_key=key)
        return row["trial_id"]

    async def stop(self):
        return await self.service.cancel(self.owner, "association-experiment",
            expected_generation=self.f.h.frozen["generation"], event_key="stop-actual-v2-lifetime")

    def call(self, identifier):
        with closing(self.assets.store._conn()) as connection:
            return dict(connection.execute("SELECT c.* FROM agent_calls c JOIN improvement_trial_attempts a ON a.call_id=c.id "
                "WHERE a.trial_id=?", (identifier,)).fetchone())

    async def test_stop_during_launch_refunds_before_engine_and_drains_actual_worker(self):
        self.pause_packet = "worker_ready"
        identifier = await self.start_owned()
        await asyncio.wait_for(self.entered.wait(), 10)
        result = await self.stop()
        self.assertEqual(result["current"]["phase"], "cancelled")
        self.assertTrue(self.f.closed)
        self.assertIsNone(self.call(identifier)["engine_at"])
        self.assertEqual(self.call(identifier)["status"], "refunded")
        self.assertEqual(self.trials.tasks._tasks, {})

    async def test_terminal_database_row_does_not_release_experiment_before_live_worker_drains(self):
        identifier = await self.start_owned()
        await asyncio.wait_for(self.entered.wait(), 10)
        current = await asyncio.to_thread(self.trials.trials.get, self.owner, identifier)
        await asyncio.to_thread(self.trials.trials.cancel, self.owner, identifier,
            expected_generation=current["generation"], event_key="db-terminal-live-v2-worker")
        self.assertFalse(self.f.closed)
        self.assertIsNotNone(self.call(identifier)["engine_at"])
        result = await self.stop()
        self.assertEqual(result["current"]["phase"], "cancelled")
        self.assertTrue(self.f.closed)
        self.assertEqual(self.call(identifier)["status"], "interrupted")
        self.assertFalse(self.f.workspace_path.exists())
        self.assertEqual(self.trials.tasks._tasks, {})

    async def test_stop_waits_for_actual_checker_and_rejects_late_result_publication(self):
        self.pause_packet = None
        entered, release = threading.Event(), threading.Event()
        original = EVAL.evaluate_trial

        def checker(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Technical checker was not released")
            return original(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=checker):
            identifier = await self.start_owned()
            stopped = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                stopped = asyncio.create_task(self.stop())
                for _ in range(200):
                    current = await asyncio.to_thread(self.service.records.get, self.owner, "association-experiment")
                    if current["phase"] == "stopping":
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(current["phase"], "stopping")
                self.assertFalse(stopped.done())
                release.set()
                self.assertEqual((await stopped)["current"]["phase"], "cancelled")
            finally:
                release.set()
                if stopped is not None:
                    await asyncio.gather(stopped, return_exceptions=True)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_results WHERE trial_id=?", (identifier,)).fetchone()[0], 0)
        self.assertEqual(self.call(identifier)["status"], "interrupted")
        self.assertFalse(self.f.workspace_path.exists())


if __name__ == "__main__":
    unittest.main()
