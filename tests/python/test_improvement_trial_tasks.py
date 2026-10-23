"""Real trial storage/runner ownership without native processes or model queries."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import importlib.util
import threading
import unittest
from unittest.mock import patch

import improvement_store as IM
import test_improvement_trial_runner as fixtures

if importlib.util.find_spec("improvement_trial_tasks"):
    import improvement_trial_tasks as runtime
else:
    runtime = None


class TrialTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(runtime, "Process-owned trial task supervisor is missing")
        self.f = fixtures.TrialRunnerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tasks = runtime.TrialTasks(self.f.f.trials, self.f.runner())
        self.close_failure_expected = False
        self.addAsyncCleanup(self.cleanup_tasks)
        self.owner = self.f.f.owner
        self.request = copy.deepcopy(self.f.f.request)

    async def cleanup_tasks(self):
        if self.close_failure_expected:
            with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
                await self.tasks.close()
        else:
            await self.tasks.close()

    async def start(self, key="supervisor-start-0001"):
        return await self.tasks.start(self.owner, self.request, event_key=key)

    async def wait_closed(self):
        await asyncio.wait_for(self.f.closed.wait(), 10)
        # Cleanup and final evaluation occur after the adapter's finally block.
        for _ in range(1000):
            row = await asyncio.to_thread(self.f.f.trials.get, self.owner, self.f.trial["trial_id"])
            if row["status"] in runtime.TERMINAL:
                return row
            await asyncio.sleep(.005)
        self.fail("Owned trial did not settle")

    async def test_start_returns_after_reservation_and_keeps_exactly_one_owned_runner(self):
        self.f.hang = True
        row = await self.start()
        await asyncio.wait_for(self.f.acked.wait(), 10)
        again = await self.start("supervisor-start-0002")
        self.assertEqual(row["trial_id"], again["trial_id"])
        self.assertEqual(len(self.f.adapters), 1)
        self.assertEqual(len(self.f.f.ledger.rows()), 1)
        current = self.f.f.trials.get(self.owner, row["trial_id"])
        result = await self.tasks.cancel(self.owner, row["trial_id"],
            expected_generation=current["generation"], event_key="supervisor-cancel-0001")
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(self.f.closed.is_set())
        self.assertEqual(self.f.call()["status"], "interrupted")

    async def test_completed_replay_never_dispatches_another_runner(self):
        await self.start()
        result = await self.wait_closed()
        self.assertEqual(result["status"], "completed")
        again = await self.start()
        self.assertEqual(again["status"], "completed")
        self.assertEqual(len(self.f.adapters), 1)

    async def test_cancel_commits_before_worker_cancellation_and_preserves_known_usage(self):
        self.f.hang = True
        row = await self.start()
        await asyncio.wait_for(self.f.acked.wait(), 10)
        # ACK alone precedes the adapter's usage packet. Freeze at its actual
        # blocking yield so autonomous trace rejection cannot race this check.
        for _ in range(1000):
            if "observe:3" in self.f.log:
                break
            await asyncio.sleep(.01)
        self.assertIn("observe:3", self.f.log)
        original = self.f.f.trials.cancel
        observed = []

        def cancel(*args, **kwargs):
            self.assertFalse(self.f.closed.is_set())
            result = original(*args, **kwargs)
            observed.append(result["status"])
            self.assertFalse(self.f.closed.is_set())
            return result

        current = self.f.f.trials.get(self.owner, row["trial_id"])
        with patch.object(self.f.f.trials, "cancel", side_effect=cancel):
            await self.tasks.cancel(self.owner, row["trial_id"], expected_generation=current["generation"],
                                    event_key="supervisor-ordered-cancel")
        self.assertEqual(observed, ["cancelled"])
        self.assertTrue(self.f.closed.is_set())
        self.assertEqual(self.f.call()["status"], "interrupted")
        self.assertIsNotNone(self.f.call()["engine_at"])

    async def test_stale_or_cross_brain_cancel_cannot_stop_owned_worker(self):
        self.f.hang = True
        row = await self.start()
        await asyncio.wait_for(self.f.acked.wait(), 10)
        for principal, generation in ((self.owner, 999), (self.f.f.other, 1)):
            with self.subTest(principal=principal), self.assertRaises((IM.ConflictError, PermissionError)):
                await self.tasks.cancel(principal, row["trial_id"], expected_generation=generation,
                                        event_key="supervisor-invalid-cancel")
        self.assertFalse(self.f.closed.is_set())

    async def test_caller_cancel_during_committing_reservation_retains_dispatch_ownership(self):
        entered, release = threading.Event(), threading.Event()
        original = self.f.f.trials.reserve

        def reserve(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            release.wait(10)
            return result

        with patch.object(self.f.f.trials, "reserve", side_effect=reserve):
            request = asyncio.create_task(self.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            request.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(request.done(), "Committed reservation thread was abandoned")
            request.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
        row = await self.wait_closed()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(len(self.f.adapters), 1)

    async def test_shutdown_drains_committing_start_and_refunds_before_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        original = self.f.f.trials.reserve

        def reserve(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            release.wait(10)
            return result

        with patch.object(self.f.f.trials, "reserve", side_effect=reserve):
            request = asyncio.create_task(self.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            closing = asyncio.create_task(self.tasks.close())
            await asyncio.sleep(.02)
            self.assertFalse(closing.done())
            release.set()
            await request
            await closing
        row = self.f.f.trials.get(self.owner, self.f.trial["trial_id"])
        self.assertIn(row["status"], {"cancelled", "not_run"})
        self.assertIsNone(self.f.call()["engine_at"])
        self.assertEqual(self.f.call()["status"], "refunded")
        self.assertEqual(self.f.adapters, [])

    async def test_shutdown_revokes_then_waits_for_real_runner_cleanup_and_is_idempotent(self):
        self.f.hang = True
        await self.start()
        await asyncio.wait_for(self.f.acked.wait(), 10)
        await self.tasks.close()
        self.assertTrue(self.f.closed.is_set())
        self.assertEqual(self.f.call()["status"], "interrupted")
        await self.tasks.close()
        with self.assertRaises(RuntimeError):
            await self.start("supervisor-after-close")
        self.assertEqual(len(self.f.f.ledger.rows()), 1)

    async def test_cancelling_shutdown_does_not_abandon_active_cleanup(self):
        self.f.hang = True
        await self.start()
        await asyncio.wait_for(self.f.acked.wait(), 10)
        entered, release = threading.Event(), threading.Event()
        original = self.f.f.trials.abort

        def cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            release.wait(10)
            return result

        with patch.object(self.f.f.trials, "abort", side_effect=cancel):
            closing = asyncio.create_task(self.tasks.close())
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            closing.cancel()
            closing.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
        self.assertTrue(self.f.closed.is_set())
        await self.tasks.close()

    async def test_start_and_status_never_resume_a_claim_from_another_host(self):
        claimed = self.f.f.trials.claim(self.owner, self.f.trial["trial_id"], expected_generation=1)
        self.assertEqual(claimed["status"], "reserved")
        # Another host already owns this reservation even though status is still reserved.
        await self.start()
        await asyncio.sleep(.05)
        self.assertEqual(self.f.adapters, [])
        self.assertIsNone(self.f.call()["engine_at"])
        self.assertEqual(self.f.call()["status"], "reserved")

    async def test_shutdown_before_local_claim_cannot_cancel_a_foreign_owned_reservation(self):
        claimed = self.f.f.trials.claim(self.owner, self.f.trial["trial_id"], expected_generation=1)
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.tasks.runner.run

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        with patch.object(self.tasks.runner, "run", side_effect=delayed):
            await self.start()
            await asyncio.wait_for(entered.wait(), 10)
            await self.tasks.close()
        row = self.f.f.trials.get(self.owner, self.f.trial["trial_id"])
        self.assertEqual(row["generation"], claimed["generation"])
        self.assertEqual(row["status"], "reserved")
        self.assertEqual(self.f.call()["status"], "reserved")

    async def test_shutdown_cannot_hide_failed_cleanup_of_a_committed_start(self):
        entered, release = threading.Event(), threading.Event()
        original = self.f.f.trials.reserve

        def reserve(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            release.wait(10)
            return result

        def unavailable(*args, **kwargs):
            raise OSError("synthetic storage unavailable")

        with patch.object(self.f.f.trials, "reserve", side_effect=reserve), \
                patch.object(self.f.f.trials, "cancel", side_effect=unavailable), \
                patch.object(self.f.f.trials, "reject_unclaimed", side_effect=unavailable):
            request = asyncio.create_task(self.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            closing = asyncio.create_task(self.tasks.close())
            await asyncio.sleep(.02)
            release.set()
            with self.assertRaises(OSError):
                await request
            with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
                await closing
        # A failed close stays visibly failed. Do not silently certify source teardown.
        with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
            await self.tasks.close()
        self.close_failure_expected = True

    async def test_failed_completed_task_cannot_hide_unsettled_cleanup_from_shutdown(self):
        failed = asyncio.Event()

        async def fail_before_claim(*args, **kwargs):
            failed.set()
            raise ValueError("synthetic preclaim failure")

        with patch.object(self.tasks.runner, "run", side_effect=fail_before_claim), \
                patch.object(self.f.f.trials, "reject_unclaimed", side_effect=OSError("synthetic storage unavailable")):
            await self.start()
            await asyncio.wait_for(failed.wait(), 10)
            for _ in range(100):
                if self.tasks._cleanup_failed:
                    break
                await asyncio.sleep(.01)
            with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
                await self.tasks.close()
        self.close_failure_expected = True

    async def test_uncommitted_invalid_start_does_not_turn_clean_shutdown_into_failure(self):
        request = {**self.request, "target_generations": {key: 999 for key in self.request["target_generations"]}}
        with self.assertRaises(IM.ConflictError):
            await self.tasks.start(self.owner, request, event_key="supervisor-stale-start")
        await self.tasks.close()

    async def test_post_reservation_read_failure_refunds_instead_of_losing_dispatch_ownership(self):
        with patch.object(self.f.f.trials, "get", side_effect=OSError("synthetic read unavailable")):
            with self.assertRaises(OSError):
                await self.start()
        row = self.f.f.trials.get(self.owner, self.f.trial["trial_id"])
        self.assertEqual(row["status"], "not_run")
        self.assertEqual(self.f.call()["status"], "refunded")
        await self.tasks.close()

    async def test_owned_claim_abort_error_cannot_be_discarded_as_a_foreign_lease(self):
        self.f.supported = False
        failed = threading.Event()

        def abort(*args, **kwargs):
            failed.set()
            raise OSError("synthetic owned abort unavailable")

        with patch.object(self.f.f.trials, "abort", side_effect=abort):
            await self.start()
            self.assertTrue(await asyncio.to_thread(failed.wait, 10))
            for _ in range(100):
                if not self.tasks._tasks:
                    break
                await asyncio.sleep(.01)
            with self.assertRaisesRegex(RuntimeError, "trial_shutdown_incomplete"):
                await self.tasks.close()
        self.close_failure_expected = True


if __name__ == "__main__":
    unittest.main()
