"""Real experiment stop/dispatch ownership; no production native execution."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import importlib.util
import subprocess
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import improvement_store as IM
from improvement_trial_service import OwnerTrialService
import test_improvement_experiment_lineage as fixtures

_REAL_RUN, _REAL_POPEN = subprocess.run, subprocess.Popen


def _acl_only(original, command, *args, **kwargs):
    if Path(command[0]).name.lower() not in {"whoami", "whoami.exe", "icacls", "icacls.exe"}:
        raise AssertionError("Only disposable workspace ACL metadata commands are permitted")
    return original(command, *args, **kwargs)

if importlib.util.find_spec("improvement_experiment_service"):
    from improvement_experiment_service import ExperimentService
else:
    ExperimentService = None


class ExperimentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.enterContext(patch("subprocess.run", side_effect=lambda *a, **kw: _acl_only(_REAL_RUN, *a, **kw)))
        self.enterContext(patch("subprocess.Popen", side_effect=lambda *a, **kw: _acl_only(_REAL_POPEN, *a, **kw)))
        self.owner, self.assets = self.h.owner, self.h.assets
        self.plans = self.h.h.plans()
        self.trial_service = OwnerTrialService(self.plans.host)
        self.addAsyncCleanup(self.trial_service.close)
        self.assertIsNotNone(ExperimentService, "Concrete experiment lifetime service is absent")
        self.service = ExperimentService(self.plans, self.trial_service)
        self.addAsyncCleanup(self.service.close)

    async def cancel(self, key="owner-stop", generation=None):
        return await self.service.cancel(self.owner, "association-experiment",
            expected_generation=self.h.frozen["generation"] if generation is None else generation, event_key=key)

    def current(self):
        return self.plans.records.get(self.owner, "association-experiment")

    async def test_empty_experiment_finishes_and_replay_keeps_original_acknowledgment(self):
        self.assertIs(self.service.tasks, self.trial_service.tasks)
        self.assertIs(self.service.trials, self.trial_service.trials)
        result = await self.cancel()
        self.assertEqual(result["acknowledgment"]["phase"], "stopping")
        self.assertEqual(result["current"]["phase"], "cancelled")
        self.assertEqual(result["current"]["generation"], result["acknowledgment"]["generation"] + 1)
        self.assertEqual(await self.cancel(), result)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_events WHERE operation='experiment_finish_stop'").fetchone()[0], 1)

    async def test_foreign_live_brain_lock_retains_capacity_until_explicit_recovery(self):
        workspace = self.service.trials.workspace
        held = workspace.acquire_execution(self.owner.brain_id, "foreign-runner", 1)
        try:
            result = await self.cancel()
            self.assertEqual(result["current"]["phase"], "stopping")
            self.assertEqual(self.current()["phase"], "stopping")
        finally:
            workspace.release_execution(held)
        with patch.object(self.service.tasks, "start", side_effect=AssertionError("Recovery must not dispatch")):
            recovered = await self.service.recover(self.owner, "association-experiment", event_key="recover-stopping")
        self.assertEqual(recovered["current"]["phase"], "cancelled")
        replay = await self.cancel()
        self.assertEqual(replay["acknowledgment"], result["acknowledgment"])
        self.assertEqual(replay["current"]["phase"], "cancelled")

    async def test_actual_linked_reservation_is_cancelled_and_settled_before_finish(self):
        trial = self.h.reserve()
        with patch.object(self.plans.host, "_snapshot_observe", side_effect=AssertionError("Stop cannot observe source")):
            result = await self.cancel()
        self.assertEqual(result["current"]["phase"], "cancelled")
        current = self.service.trials.get(self.owner, trial["trial_id"])
        self.assertEqual(current["status"], "cancelled")
        with closing(self.assets.store._conn()) as connection:
            call = connection.execute("SELECT c.status,c.engine_at FROM agent_calls c JOIN improvement_trial_attempts a ON a.call_id=c.id WHERE a.trial_id=?", (trial["trial_id"],)).fetchone()
        self.assertIsNone(call["engine_at"])
        self.assertEqual(call["status"], "refunded")

    async def test_off_and_revoked_context_do_not_prevent_keep_base_closure(self):
        stopped = self.plans.select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.h.frozen["generation"], event_key="select-base-before-off")
        self.plans.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-before-closure")
        self.assets.set_policy(self.owner, "off", expected_version=self.h.value["policy_version"], event_key="off-before-closure")
        with patch.object(self.plans.host, "_snapshot_observe", side_effect=AssertionError("Recovery cannot observe source")):
            result = await self.service.recover(self.owner, "association-experiment", event_key="finish-keep-base")
        self.assertEqual(result["current"]["phase"], "completed")
        self.assertEqual(result["current"]["generation"], stopped["generation"] + 1)

    async def test_failure_after_cleanup_does_not_release_capacity_and_recovery_retries(self):
        self.h.reserve()
        event = self.assets._event

        def fail(connection, principal, key, operation, payload, result):
            if operation == "experiment_finish_stop":
                raise OSError("Synthetic final transaction failure")
            return event(connection, principal, key, operation, payload, result)

        with patch.object(self.assets, "_event", side_effect=fail):
            with self.assertRaises(OSError):
                await self.cancel()
        self.assertEqual(self.current()["phase"], "stopping")
        recovered = await self.service.recover(self.owner, "association-experiment", event_key="retry-after-final-cas-failure")
        self.assertEqual(recovered["current"]["phase"], "cancelled")

    async def test_repeated_http_cancellation_waits_for_actual_finish_work(self):
        self.h.reserve()
        entered, release = threading.Event(), threading.Event()
        original = self.service.trials.workspace.cleanup_proven

        def delayed(*args):
            result = original(*args)
            entered.set()
            if not release.wait(10):
                raise AssertionError("Technical cleanup was not released")
            return result

        with patch.object(self.service.trials.workspace, "cleanup_proven", side_effect=delayed):
            task = asyncio.create_task(self.cancel())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                task.cancel()
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
                self.assertEqual(self.current()["phase"], "stopping")
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.current()["phase"], "cancelled")

    async def test_foreign_brain_and_stale_generation_cannot_cancel(self):
        with self.assertRaises(PermissionError):
            await self.service.cancel(self.h.h.f.other, "association-experiment", expected_generation=3, event_key="foreign-stop")
        with self.assertRaises(IM.ConflictError):
            await self.cancel(generation=1)
        self.assertEqual(self.current()["phase"], "probing")

    async def test_next_chooses_position_zero_without_caller_cell_selection(self):
        method = getattr(self.service, "next", None)
        self.assertTrue(callable(method), "Host-selected next-slot operation is absent")
        calls = []

        def inspect_slot(principal, experiment_id, comparison_id, position, **kwargs):
            calls.append(position)
            raise PermissionError("Technical scheduler boundary observed")

        with patch.object(self.service.admission, "admit", side_effect=inspect_slot):
            with self.assertRaises(PermissionError):
                await method(self.owner, "association-experiment", self.h.comparison["id"],
                    expected_generation=self.h.frozen["generation"], event_key="next-first-frozen-cell")
        self.assertEqual(calls, [0])

    async def test_next_stale_or_gapped_history_rejects_before_private_admission(self):
        method = getattr(self.service, "next", None)
        self.assertTrue(callable(method), "Host-selected next-slot operation is absent")
        with patch.object(self.service.admission, "admit", side_effect=AssertionError("Stale next cannot decrypt or probe")):
            with self.assertRaises(IM.ConflictError):
                await method(self.owner, "association-experiment", self.h.comparison["id"],
                    expected_generation=1, event_key="stale-next-before-preflight")
        trial = self.h.reserve()
        with patch.object(self.service.admission, "admit", side_effect=AssertionError("Pending/gapped next cannot admit")):
            with self.assertRaises(IM.ConflictError):
                await method(self.owner, "association-experiment", self.h.comparison["id"],
                    expected_generation=self.h.frozen["generation"], event_key="pending-next-before-preflight")
        self.assertEqual(self.service.trials.get(self.owner, trial["trial_id"])["status"], "reserved")

    async def test_next_pending_preflight_is_owned_through_http_cancellation_and_shutdown(self):
        method = getattr(self.service, "next", None)
        self.assertTrue(callable(method), "Host-selected next-slot operation is absent")
        entered, release = threading.Event(), threading.Event()

        def preflight(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Technical preflight was not released")
            raise PermissionError("Technical unavailable candidate")

        with patch.object(self.service.admission, "admit", side_effect=preflight):
            task = asyncio.create_task(method(self.owner, "association-experiment", self.h.comparison["id"],
                expected_generation=self.h.frozen["generation"], event_key="next-owned-preflight"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                task.cancel()
                task.cancel()
                closing = asyncio.create_task(self.service.close())
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
                self.assertFalse(closing.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await closing
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    def blocked(self):
        return self.plans.records.block(self.owner, "association-experiment",
            {"code": "runtime_unavailable", "fingerprint": "0" * 64},
            expected_generation=self.h.frozen["generation"], event_key="host-recorded-runtime-blocker")

    async def test_resume_owner_event_and_metadata_transition_commit_atomically(self):
        from improvement_claude import ClaudeTrialAdapter
        blocked = self.blocked()
        event = self.assets._event

        def fail(connection, principal, key, operation, payload, result):
            if operation == "experiment_owner_resume":
                raise OSError("Synthetic owner resume event failure")
            return event(connection, principal, key, operation, payload, result)

        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": True, "capability_hash": "d" * 64}), \
             patch.object(self.assets, "_event", side_effect=fail):
            with self.assertRaises(OSError):
                await self.service.resume(self.owner, "association-experiment", expected_generation=blocked["generation"],
                    event_key="resume-atomic-owner-event")
        self.assertEqual(self.current()["phase"], "blocked")
        self.assertEqual(self.current()["generation"], blocked["generation"])

    async def test_resume_rechecks_actual_pins_and_replays_cold_after_stop(self):
        from improvement_claude import ClaudeTrialAdapter
        blocked = self.blocked()
        arguments = {"expected_generation": blocked["generation"], "event_key": "owner-runtime-resume"}
        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": True, "capability_hash": "d" * 64}):
            resumed = await self.service.resume(self.owner, "association-experiment", **arguments)
        self.assertEqual(resumed["phase"], "probing")
        await self.cancel(generation=resumed["generation"])
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Replay cannot probe")), \
             patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")):
            self.assertEqual(await self.service.resume(self.owner, "association-experiment", **arguments), resumed)

    async def test_stop_fence_joins_pending_next_before_releasing_capacity(self):
        entered, release = threading.Event(), threading.Event()

        def preflight(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Technical admission ownership was not released")
            raise PermissionError("Stopped technical preflight")

        with patch.object(self.service.admission, "admit", side_effect=preflight):
            start = asyncio.create_task(self.service.next(self.owner, "association-experiment", self.h.comparison["id"],
                expected_generation=self.h.frozen["generation"], event_key="next-preflight-before-stop"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                stopped = asyncio.create_task(self.cancel())
                for _ in range(200):
                    if self.current()["phase"] == "stopping":
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(self.current()["phase"], "stopping")
                self.assertFalse(stopped.done())
                release.set()
                with self.assertRaises(PermissionError):
                    await start
                self.assertEqual((await stopped)["current"]["phase"], "cancelled")
            finally:
                release.set()
                await asyncio.gather(start, return_exceptions=True)



    async def test_stop_joins_an_owned_synchronous_report_before_releasing_experiment(self):
        from improvement_comparison_results import ComparisonResults
        entered, release = threading.Event(), threading.Event()
        create = ComparisonResults.create
        arrived = threading.Event()
        finish = self.service._finish_stop

        def observed_finish(*args):
            arrived.set()
            return finish(*args)

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Comparison report thread was not released')
            return create(*args, **kwargs)

        with patch.object(ComparisonResults, 'create', new=delayed), \
             patch.object(self.service, '_finish_stop', side_effect=observed_finish):
            report = asyncio.create_task(self.service.control(self.owner, 'association-experiment', 'report',
                {'comparison_id': self.h.comparison['id'], 'expected_generation': self.h.frozen['generation'],
                 'event_key': 'report-thread-before-experiment-stop'}))
            stopped = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                stopped = asyncio.create_task(self.cancel())
                for _ in range(200):
                    if self.current()['phase'] == 'stopping':
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(self.current()['phase'], 'stopping')
                self.assertFalse(await asyncio.to_thread(arrived.wait, 2),
                    'Stop reached the final cleanup/CAS before the owned report thread settled')
                self.assertFalse(stopped.done(), 'Stop released capacity before the owned report thread settled')
                release.set()
                with self.assertRaises((IM.ConflictError, PermissionError)):
                    await report
                self.assertEqual((await stopped)['current']['phase'], 'cancelled')
            finally:
                release.set()
                await asyncio.gather(report, *([] if stopped is None else [stopped]), return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
