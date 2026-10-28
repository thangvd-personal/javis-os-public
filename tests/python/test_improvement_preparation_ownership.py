"""Multi-cell preparation never burns a later immutable link on a busy runner."""
from _paths import ROOT, SERVER  # noqa: F401
import asyncio
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
from improvement_trial_runner import TrialRunner
import test_improvement_preparation_execution as fixtures


class PreparationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixtures.PreparationExecutionTests()
        self.f.setUp(repeat_policy='pilot')
        self.addCleanup(self.f.doCleanups)
        self.preparation = self.f.f.service()

    def next(self, key='owner-next-pilot-second'):
        return self.f.f.admit(event_key=key)

    def counts(self):
        with closing(self.f.assets.store._conn()) as connection:
            return tuple(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in ('improvement_preparation_trial_links', 'improvement_trials', 'agent_calls'))

    async def complete_first(self):
        runner = TrialRunner(self.f.trials, context_loader=self.f.host.context_loader,
            live_guard=self.f.host.live_guard, adapter_factory=self.f.factory)
        result = await runner.run(self.f.owner, self.f.admitted.trial_id)
        self.assertEqual(result['status'], 'completed', result)
        return result

    async def test_a_quick_next_cannot_reserve_behind_a_pending_cell(self):
        self.assertEqual(self.f.admitted.request['preparation']['cell_position'], 0)
        with self.assertRaises(IM.ConflictError):
            self.next()
        self.assertEqual(self.counts(), (1, 1, 1))
        replay = self.f.f.admit()  # Original admission key remains historical.
        self.assertEqual(replay, self.f.admitted)
        self.assertEqual(self.counts(), (1, 1, 1))

    async def test_sql_completion_cannot_release_the_next_cell_before_cleanup(self):
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        cleanup = self.f.trials.workspace.cleanup

        def closing(*args):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError('Owned cleanup was not released')
            return cleanup(*args)

        with patch.object(self.f.trials.workspace, 'cleanup', side_effect=closing):
            task = asyncio.create_task(self.complete_first())
            try:
                await asyncio.wait_for(entered.wait(), 10)
                row = self.f.trials.get(self.f.owner, self.f.admitted.trial_id)
                self.assertEqual(row['status'], 'completed')
                self.assertIsNotNone(self.f.trials.read_result(self.f.owner, self.f.admitted.trial_id, 1))
                self.assertFalse(task.done())
                with self.assertRaises(IM.ConflictError):
                    self.next()
                self.assertEqual(self.counts(), (1, 1, 1))
            finally:
                release.set()
                await asyncio.wait_for(task, 10)
        second = self.next()
        self.assertEqual(second.request['preparation']['cell_position'], 1)
        self.assertEqual(self.counts(), (2, 2, 2))

    async def test_concurrent_next_during_slow_preflight_reserves_only_one_cell(self):
        from concurrent.futures import ThreadPoolExecutor
        from improvement_claude import ClaudeTrialAdapter
        # Two owner requests contend for the one remaining pilot cell while
        # the first request is still observing native availability.
        await self.complete_first()
        entered, release = threading.Event(), threading.Event()

        def verify(adapter):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Native metadata preflight was not released')
            return {'supported': True, 'capability_hash': 'c' * 64}

        def admit(key):
            return self.preparation.admit_next(self.f.owner, 'association-experiment', expected_generation=2, event_key=key)

        with patch.object(ClaudeTrialAdapter, 'verify', verify), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(admit, 'owner-concurrent-next-first')
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                second = executor.submit(admit, 'owner-concurrent-next-second')
                with self.assertRaises(IM.ConflictError):
                    await asyncio.to_thread(second.result, 5)
            finally:
                release.set()
                actual = await asyncio.to_thread(first.result, 5)
        self.assertEqual(actual.request['preparation']['cell_position'], 1)
        self.assertEqual(self.counts(), (2, 2, 2))

    async def test_two_real_solver_results_can_complete_the_pilot(self):
        await self.complete_first()
        second = self.next()
        self.f.admitted = second
        await self.complete_first()
        sealed = self.preparation.complete(self.f.owner, 'association-experiment', expected_generation=2,
            event_key='owner-complete-two-pilot-results')
        self.assertEqual((sealed['phase'], len(sealed['result_refs'])), ('proposing', 2))
        self.assertEqual(self.counts(), (2, 2, 2))


if __name__ == '__main__':
    unittest.main()
