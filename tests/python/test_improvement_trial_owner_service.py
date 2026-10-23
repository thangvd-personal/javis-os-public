"""Synthetic preflight/worker lifetimes retain actual admitted SQLite trials."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

import test_improvement_trial_associations as fixtures
import improvement_store as IM
import improvement_trial_service as S


class OwnerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(hasattr(S, "OwnerTrialService"), "Process-owned owner trial service is missing")
        self.f = fixtures.AssociationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.association = self.f.create()
        self.owner = self.f.owner
        self.assets = self.f.f.assets
        self.bundle = self.assets.create_bundle(self.owner, [self.f.f.target["revision_id"]], event_key="owner-service-bundle")
        self.service = S.OwnerTrialService(self.f.host)
        self.data = {"event_key": "owner-service-start", "experiment_id": "association-experiment",
            "contract_id": self.f.saved["contract_id"], "case_set_id": self.association["case_set_id"],
            "case_id": "case-0", "base_bundle_id": self.bundle["bundle_id"],
            "candidate_bundle_id": self.bundle["bundle_id"], "variant": "baseline", "repeat": 1,
            "timeout_seconds": 300, "expected_context_generation": 1}
        self.verify = patch("improvement_claude.ClaudeTrialAdapter.verify",
            return_value={"supported": True, "capability_hash": "c" * 64}).start()
        self.addCleanup(patch.stopall)
        self.worker_entered = asyncio.Event()
        self.worker_release = asyncio.Event()
        self.thread_release = threading.Event()

        async def hold(principal, identifier, *, on_claimed):
            current = await asyncio.to_thread(self.service.trials.get, principal, identifier)
            claimed = await asyncio.to_thread(self.service.trials.claim, principal, identifier,
                                              expected_generation=current["generation"])
            on_claimed(claimed)
            self.worker_entered.set()
            await self.worker_release.wait()
        self.run = patch.object(self.service.tasks.runner, "run", side_effect=hold).start()

    async def asyncTearDown(self):
        if hasattr(self, "thread_release"):
            self.thread_release.set()
        if hasattr(self, "service"):
            if getattr(self, "expected_cleanup_failure", False):
                with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
                    await self.service.close()
            else:
                await self.service.close()

    async def thread_entered(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 5), "Synthetic preflight did not enter")

    def gate_preflight(self):
        entered = threading.Event()
        def verify():
            entered.set()
            if not self.thread_release.wait(5):
                raise AssertionError("Test preflight gate timed out")
            return {"supported": True, "capability_hash": "c" * 64}
        self.verify.side_effect = verify
        return entered

    def calls(self):
        with closing(self.assets.store._conn()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM agent_calls")]

    async def test_concurrent_same_event_joins_one_preflight_and_one_actual_trial(self):
        entered = self.gate_preflight()
        first = asyncio.create_task(self.service.start(self.owner, self.data))
        await self.thread_entered(entered)
        second = asyncio.create_task(self.service.start(self.owner, dict(self.data)))
        await asyncio.sleep(0)
        self.thread_release.set()
        left, right = await asyncio.gather(first, second)
        self.assertEqual(left["trial_id"], right["trial_id"])
        self.assertEqual(self.verify.call_count, 1)
        await asyncio.wait_for(self.worker_entered.wait(), 5)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(len(self.calls()), 1)

    async def test_conflicting_inflight_payload_is_rejected_before_another_probe(self):
        entered = self.gate_preflight()
        first = asyncio.create_task(self.service.start(self.owner, self.data))
        await self.thread_entered(entered)
        with self.assertRaises(IM.ConflictError):
            await self.service.start(self.owner, {**self.data, "repeat": 2})
        self.assertEqual(self.verify.call_count, 1)
        self.thread_release.set()
        await first

    async def test_repeated_http_cancellation_retains_the_committed_request(self):
        entered = self.gate_preflight()
        response = asyncio.create_task(self.service.start(self.owner, self.data))
        await self.thread_entered(entered)
        response.cancel()
        await asyncio.sleep(0)
        response.cancel()
        self.thread_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await response
        await asyncio.wait_for(self.worker_entered.wait(), 5)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.run.call_count, 1)

    async def test_shutdown_waits_for_preflight_then_refunds_without_dispatch(self):
        entered = self.gate_preflight()
        response = asyncio.create_task(self.service.start(self.owner, self.data))
        await self.thread_entered(entered)
        closing = asyncio.create_task(self.service.close())
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        self.thread_release.set()
        result = await response
        await closing
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(self.calls()[0]["status"], "refunded")
        self.assertIsNone(self.calls()[0]["engine_at"])
        self.run.assert_not_called()
        with self.assertRaises(RuntimeError):
            await self.service.start(self.owner, self.data)

    async def test_handoff_failure_refunds_the_already_admitted_unclaimed_trial(self):
        with patch.object(self.service.tasks, "start", side_effect=RuntimeError("synthetic handoff failure")):
            with self.assertRaises(RuntimeError):
                await self.service.start(self.owner, self.data)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["status"], "refunded")
        self.run.assert_not_called()

    async def test_explicit_cancel_commits_then_stops_owned_worker(self):
        result = await self.service.start(self.owner, self.data)
        await asyncio.wait_for(self.worker_entered.wait(), 5)
        current = self.service.trials.get(self.owner, result["trial_id"])
        cancelled = await self.service.cancel(self.owner, result["trial_id"],
            expected_generation=current["generation"], event_key="owner-service-cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.calls()[0]["status"], "refunded")
        self.assertFalse(self.service.tasks._tasks)

    async def test_historical_replay_does_not_probe_or_resume_closed_execution(self):
        result = await self.service.start(self.owner, self.data)
        await asyncio.wait_for(self.worker_entered.wait(), 5)
        await self.service.close()
        fresh = S.OwnerTrialService(self.f.host)
        try:
            with patch.object(fresh.tasks.runner, "run", side_effect=AssertionError("No resume")), \
                    patch("improvement_claude.ClaudeTrialAdapter.verify", side_effect=AssertionError("No native probe")):
                repeated = await fresh.start(self.owner, self.data)
            self.assertEqual(repeated["trial_id"], result["trial_id"])
            self.assertEqual(repeated["status"], "not_run")
            self.assertEqual(len(self.calls()), 1)
        finally:
            await fresh.close()

    async def test_close_after_pre_handoff_read_begins_does_not_dispatch_new_worker(self):
        entered = threading.Event()
        original = self.service.trials.get
        def gated_get(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                if not self.thread_release.wait(5):
                    raise AssertionError("Test handoff gate timed out")
            return original(*args, **kwargs)
        with patch.object(self.service.trials, "get", side_effect=gated_get):
            response = asyncio.create_task(self.service.start(self.owner, self.data))
            await self.thread_entered(entered)
            closing = asyncio.create_task(self.service.close())
            await asyncio.sleep(0)
            self.thread_release.set()
            result = await response
            await closing
        self.assertEqual(result["status"], "not_run")
        self.run.assert_not_called()
        self.assertEqual(self.calls()[0]["status"], "refunded")

    async def test_repeated_internal_cancellation_cannot_interrupt_unclaimed_cleanup(self):
        admitted = threading.Event()
        cleanup_read = threading.Event()
        commit_release = threading.Event()
        self.addCleanup(commit_release.set)
        original_admit, original_get = self.service.admission.admit, self.service.trials.get
        def gated_admit(*args):
            result = original_admit(*args)
            admitted.set()
            if not commit_release.wait(5):
                raise AssertionError("Test admission gate timed out")
            return result
        def gated_get(*args, **kwargs):
            if not cleanup_read.is_set():
                cleanup_read.set()
                if not self.thread_release.wait(5):
                    raise AssertionError("Test cleanup gate timed out")
            return original_get(*args, **kwargs)
        with patch.object(self.service.admission, "admit", side_effect=gated_admit), \
                patch.object(self.service.trials, "get", side_effect=gated_get):
            response = asyncio.create_task(self.service.start(self.owner, self.data))
            await self.thread_entered(admitted)
            operation = next(iter(self.service._operations.values()))[1]
            operation.cancel()
            commit_release.set()
            await self.thread_entered(cleanup_read)
            operation.cancel()
            self.thread_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await response
        self.assertEqual(self.calls()[0]["status"], "refunded")
        self.run.assert_not_called()
        await self.service.close()

    async def test_shutdown_during_task_reservation_refunds_without_dispatch(self):
        entered = threading.Event()
        original = self.service.trials.reserve
        def reserve(*args, **kwargs):
            entered.set()
            if not self.thread_release.wait(5):
                raise AssertionError("Test dispatcher reservation gate timed out")
            return original(*args, **kwargs)
        with patch.object(self.service.trials, "reserve", side_effect=reserve):
            response = asyncio.create_task(self.service.start(self.owner, self.data))
            await self.thread_entered(entered)
            stopping = asyncio.Event()
            close_tasks = self.service.tasks.close
            async def dispatcher_close():
                stopping.set()
                return await close_tasks()
            with patch.object(self.service.tasks, "close", side_effect=dispatcher_close):
                closed = asyncio.create_task(self.service.close())
                await asyncio.wait_for(stopping.wait(), 5)
                self.thread_release.set()
                result = await response
                await closed
        self.assertEqual(result["status"], "not_run")
        self.run.assert_not_called()
        self.assertEqual(self.calls()[0]["status"], "refunded")

    async def test_real_cleanup_storage_failure_remains_visible_after_operation_removal(self):
        with patch.object(self.service.tasks, "start", side_effect=OSError("synthetic handoff failure")), \
                patch.object(self.service.trials, "reject_unclaimed", side_effect=OSError("synthetic storage failure")):
            with self.assertRaises(OSError):
                await self.service.start(self.owner, self.data)
        await asyncio.sleep(0)
        self.assertFalse(self.service._operations)
        self.assertEqual(self.calls()[0]["status"], "reserved")
        self.assertIsNone(self.calls()[0]["engine_at"])
        self.expected_cleanup_failure = True
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
                await self.service.close()


if __name__ == "__main__":
    unittest.main()
