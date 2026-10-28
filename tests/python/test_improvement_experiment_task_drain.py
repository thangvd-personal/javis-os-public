"""Experiment stop drains actual local tasks, including terminal-row workers."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import threading
import unittest
from unittest.mock import patch

from improvement_trial_tasks import TrialTasks
import test_improvement_trial_runner as fixtures


class ExperimentTaskDrainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.TrialRunnerTests()
        self.f.setUp()
        self.addCleanup(self.f.usage_patch.stop)
        self.trials, self.owner = self.f.f.trials, self.f.f.owner
        self.tasks = TrialTasks(self.trials, self.f.runner())
        self.addAsyncCleanup(self.tasks.close)
        self.request = copy.deepcopy(self.f.f.request)

    def drain_method(self):
        method = getattr(self.tasks, "drain_owned", None)
        self.assertTrue(callable(method), "Scoped owned-task drain is absent")
        return method

    async def running(self):
        self.f.hang = True
        row = await self.tasks.start(self.owner, self.request, event_key="drain-owned-start")
        await asyncio.wait_for(self.f.acked.wait(), 10)
        for _ in range(1000):
            if "observe:3" in self.f.log:
                return row
            await asyncio.sleep(.005)
        self.fail("Synthetic worker did not reach its actual blocking yield")

    async def test_db_terminal_row_does_not_hide_a_still_owned_worker(self):
        drain = self.drain_method()
        row = await self.running()
        current = await asyncio.to_thread(self.trials.get, self.owner, row["trial_id"])
        await asyncio.to_thread(self.trials.cancel, self.owner, row["trial_id"],
            expected_generation=current["generation"], event_key="db-terminal-before-task-drain")
        self.assertFalse(self.f.closed.is_set())
        await drain(self.owner, [row["trial_id"]])
        self.assertTrue(self.f.closed.is_set())
        self.assertNotIn((self.owner.brain_id, row["trial_id"]), self.tasks._tasks)
        path, _ = self.trials.workspace._paths(row["trial_id"], 1)
        self.assertFalse(path.exists())

    async def test_drain_waits_for_matching_committed_reservation_before_task_snapshot(self):
        drain = self.drain_method()
        entered, release = threading.Event(), threading.Event()
        original = self.trials.reserve

        def delayed(*args, **kwargs):
            row = original(*args, **kwargs)
            entered.set()
            if not release.wait(10):
                raise AssertionError("Technical reservation wait was not released")
            return row

        with patch.object(self.trials, "reserve", side_effect=delayed):
            start = asyncio.create_task(self.tasks.start(self.owner, self.request, event_key="committing-before-drain"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                current = await asyncio.to_thread(self.trials.get, self.owner, self.f.trial["trial_id"])
                await asyncio.to_thread(self.trials.cancel, self.owner, current["trial_id"],
                    expected_generation=current["generation"], event_key="stop-committed-before-register")
                closing = asyncio.create_task(drain(self.owner, [current["trial_id"]]))
                await asyncio.sleep(.02)
                self.assertFalse(closing.done(), "Drain took its task snapshot before the reservation handed off")
                release.set()
                await start
                await closing
            finally:
                release.set()
                await asyncio.gather(start, return_exceptions=True)
        self.assertEqual(self.f.adapters, [])
        self.assertEqual(self.tasks._tasks, {})

    async def test_foreign_brain_cannot_drain_a_local_owner_worker(self):
        drain = self.drain_method()
        row = await self.running()
        with self.assertRaises(PermissionError):
            await drain(self.f.f.other, [row["trial_id"]])
        self.assertFalse(self.f.closed.is_set())


    async def test_repeated_caller_cancellation_cannot_abandon_owned_cleanup(self):
        drain = self.drain_method()
        row = await self.running()
        current = await asyncio.to_thread(self.trials.get, self.owner, row["trial_id"])
        await asyncio.to_thread(self.trials.cancel, self.owner, row["trial_id"],
            expected_generation=current["generation"], event_key="terminal-before-slow-drain")
        entered, release = threading.Event(), threading.Event()
        original = self.trials.workspace.cleanup

        def delayed(*args):
            original(*args)
            entered.set()
            if not release.wait(10):
                raise AssertionError("Technical cleanup wait was not released")

        with patch.object(self.trials.workspace, "cleanup", side_effect=delayed):
            closing = asyncio.create_task(drain(self.owner, [row["trial_id"]]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                closing.cancel()
                closing.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(closing.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await closing
            finally:
                release.set()
                await asyncio.gather(closing, return_exceptions=True)
        self.assertTrue(self.f.closed.is_set())
        self.assertEqual(self.tasks._tasks, {})

    async def test_owned_drain_remains_available_during_or_after_supervisor_close(self):
        drain = self.drain_method()
        row = await self.running()
        await self.tasks.close()
        await drain(self.owner, [row["trial_id"]])
        self.assertEqual(self.tasks._tasks, {})
        self.assertTrue(self.f.closed.is_set())

    async def test_empty_scope_never_joins_an_unrelated_same_brain_reservation(self):
        entered, release = threading.Event(), threading.Event()
        original = self.trials.reserve

        def delayed(*args, **kwargs):
            row = original(*args, **kwargs)
            entered.set()
            if not release.wait(10):
                raise AssertionError('Unrelated reservation was not released')
            return row

        with patch.object(self.trials, 'reserve', side_effect=delayed):
            start = asyncio.create_task(self.tasks.start(self.owner, self.request, event_key='unrelated-same-brain-reservation'))
            closing = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                closing = asyncio.create_task(self.tasks.drain_owned(self.owner, []))
                # Keep the unrelated reservation blocked until the empty drain
                # actually finishes; elapsed scheduler time is not a join proof.
                await asyncio.wait_for(asyncio.shield(closing), 5)
                self.assertTrue(closing.done(), 'An empty experiment scope waited for unrelated admission')
                self.assertFalse(release.is_set())
                await closing
                self.assertFalse(start.done())
            finally:
                release.set()
                await asyncio.gather(start, *([] if closing is None else [closing]), return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
