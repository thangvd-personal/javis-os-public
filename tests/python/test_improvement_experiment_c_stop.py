"""Actual preparation/role ownership participates in the experiment stop fence."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
from improvement_experiment_service import ExperimentService
from improvement_trial_service import OwnerTrialService
import test_improvement_proposals as fixtures


class ExperimentCStopTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ProposalTests.setUp
    prepare = fixtures.ProposalTests.prepare
    factory = fixtures.ProposalTests.factory
    adapter = fixtures.ProposalTests.adapter
    body_patch = fixtures.ProposalTests.body_patch

    def service(self):
        trials = OwnerTrialService(self.host)
        self.addAsyncCleanup(trials.close)
        service = ExperimentService(self.f.plans, trials)
        self.addAsyncCleanup(service.close)
        return service

    def current(self):
        return self.f.plans.records.get(self.owner, 'association-experiment')

    async def stop(self, service, key='stop-all-experiment-work'):
        return await service.cancel(self.owner, 'association-experiment',
            expected_generation=self.current()['generation'], event_key=key)

    async def test_preparation_reservation_cannot_outlive_experiment_terminal_state(self):
        service = self.service()
        with patch.object(self.host, '_snapshot_observe', side_effect=AssertionError('Stop must not capture source')):
            result = await self.stop(service)
        self.assertEqual(result['current']['phase'], 'cancelled')
        trial = service.trials.get(self.owner, self.admitted.trial_id)
        self.assertEqual(trial['status'], 'cancelled')
        with closing(self.assets.store._conn()) as connection:
            call = connection.execute('SELECT c.status,c.engine_at FROM agent_calls c JOIN '
                'improvement_trial_attempts a ON a.call_id=c.id WHERE a.trial_id=?', (self.admitted.trial_id,)).fetchone()
        self.assertEqual(call['status'], 'refunded')
        self.assertIsNone(call['engine_at'])

    async def test_pending_actual_role_is_fenced_even_with_no_native_attempt(self):
        await self.prepare()
        service = self.service()
        self.assertTrue(hasattr(service, 'proposals'), 'Experiment must own its concrete role workflows')
        self.assertIs(service.execution.trial_service, service.trial_service)
        self.assertIs(service.proposals.tasks, service.operation_tasks)
        self.assertIs(service.semantic.tasks, service.operation_tasks)
        self.assertIs(service.execution.workspace.brain_barrier, service.trials.workspace)
        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify',
                   return_value={'supported': True, 'capability_hash': 'c' * 64}):
            begun = await asyncio.to_thread(service.proposals._begin, self.owner,
                {'experiment_id': 'association-experiment', 'expected_generation': 3}, 'begin-pending-role-before-stop')
        self.host.revoke(self.owner, 'association-experiment', expected_generation=1, event_key='revoke-before-pending-role-stop')
        self.assets.set_policy(self.owner, 'off', expected_version=0, event_key='off-before-pending-role-stop')
        with patch.object(self.host, '_snapshot_observe', side_effect=AssertionError('Stop must not capture source')):
            result = await self.stop(service)
        row = service.execution.get(self.owner, begun['operation_id'])
        self.assertEqual((row['status'], row['attempt']), ('cancelled', 0))
        self.assertEqual(result['current']['phase'], 'cancelled')

    async def test_stop_cancels_marked_native_before_joining_entire_proposer(self):
        await self.prepare()
        service = self.service()
        self.assertTrue(hasattr(service, 'proposals'), 'Experiment must own its concrete role workflows')
        self.acked, self.closed = asyncio.Event(), asyncio.Event()
        self.hang, self.body = True, IM._json({'version': 1, 'kind': 'patches', 'patches': [self.body_patch()]})
        service.operation_tasks.runner.adapter_factory = self.adapter
        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify',
                   return_value={'supported': True, 'capability_hash': 'c' * 64}):
            generated = asyncio.create_task(service.proposals.generate(self.owner, 'association-experiment',
                expected_generation=3, event_key='start-native-before-experiment-stop'))
            try:
                await asyncio.wait_for(self.acked.wait(), 10)
                stopped = await asyncio.wait_for(self.stop(service), 10)
                self.assertEqual(stopped['current']['phase'], 'cancelled')
                self.assertTrue(self.closed.is_set())
                self.assertTrue(generated.done())
                with closing(self.assets.store._conn()) as connection:
                    row = connection.execute('SELECT status FROM improvement_operations').fetchone()
                    call = connection.execute("SELECT status,engine_at FROM agent_calls WHERE purpose='improve_propose'").fetchone()
                    self.assertEqual(connection.execute('SELECT count(*) FROM improvement_skill_candidates').fetchone()[0], 0)
                self.assertEqual(row['status'], 'cancelled')
                self.assertIsNotNone(call['engine_at'])
                self.assertNotEqual(call['status'], 'refunded')
            finally:
                generated.cancel()
                await asyncio.gather(generated, return_exceptions=True)

    async def test_stop_waits_for_db_terminal_publication_thread_through_caller_cancellation(self):
        await self.prepare()
        service = self.service()
        self.assertTrue(hasattr(service, 'proposals'), 'Experiment must own its concrete role workflows')
        self.acked, self.closed = asyncio.Event(), asyncio.Event()
        self.hang, self.body = False, IM._json({'version': 1, 'kind': 'patches', 'patches': [self.body_patch()]})
        service.operation_tasks.runner.adapter_factory = self.adapter
        entered, release = threading.Event(), threading.Event()
        publish = service.proposals._publish

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Publication thread was not released')
            return publish(*args, **kwargs)

        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify',
                   return_value={'supported': True, 'capability_hash': 'c' * 64}), \
             patch.object(service.proposals, '_publish', side_effect=delayed):
            generated = asyncio.create_task(service.proposals.generate(self.owner, 'association-experiment',
                expected_generation=3, event_key='start-late-publication-before-stop'))
            stopped = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                stopped = asyncio.create_task(self.stop(service))
                for _ in range(200):
                    if self.current()['phase'] == 'stopping':
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(self.current()['phase'], 'stopping')
                stopped.cancel()
                stopped.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(stopped.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await stopped
                self.assertEqual(self.current()['phase'], 'cancelled')
                with self.assertRaises((IM.ConflictError, PermissionError)):
                    await generated
                with closing(self.assets.store._conn()) as connection:
                    self.assertEqual(connection.execute('SELECT count(*) FROM improvement_proposal_publications').fetchone()[0], 0)
            finally:
                release.set()
                await asyncio.gather(generated, *([] if stopped is None else [stopped]), return_exceptions=True)

    async def test_stop_joins_slow_preflight_and_fences_its_late_operation_creation(self):
        await self.prepare()
        service = self.service()
        entered, release = threading.Event(), threading.Event()

        def preflight(*args):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Preflight was not released')
            return {'supported': True, 'capability_hash': 'c' * 64}

        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify', side_effect=preflight):
            generated = asyncio.create_task(service.proposals.generate(self.owner, 'association-experiment',
                expected_generation=3, event_key='slow-proposal-native-preflight'))
            stopped = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                stopped = asyncio.create_task(self.stop(service))
                for _ in range(200):
                    if self.current()['phase'] == 'stopping':
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(self.current()['phase'], 'stopping')
                self.assertFalse(stopped.done())
                release.set()
                self.assertEqual((await stopped)['current']['phase'], 'cancelled')
                with self.assertRaises((IM.ConflictError, PermissionError)):
                    await generated
                with closing(self.assets.store._conn()) as connection:
                    self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)
            finally:
                release.set()
                await asyncio.gather(generated, *([] if stopped is None else [stopped]), return_exceptions=True)

    async def test_selected_preparation_recovery_never_reads_an_unrelated_corrupt_journal(self):
        service = self.service()
        unrelated = 'unrelated-native-operation'
        _, journal = service.execution.workspace._paths(unrelated, 1)
        journal.parent.mkdir(parents=True, exist_ok=True)
        raw = b'{"state":"corrupt-unrelated-journal"}'
        journal.write_bytes(raw)
        deadline = service.trials.get(self.owner, self.admitted.trial_id)['request']['deadline_at']
        with patch('improvement_trials.time.time', return_value=deadline + 1):
            result = await service.recover(self.owner, 'association-experiment', event_key='recover-selected-preparation')
        self.assertEqual(result['current']['phase'], 'preparing')
        self.assertEqual(service.trials.get(self.owner, self.admitted.trial_id)['status'], 'not_run')
        self.assertEqual(journal.read_bytes(), raw)
        with closing(self.assets.store._conn()) as connection:
            call = connection.execute('SELECT c.status FROM agent_calls c JOIN improvement_trial_attempts a '
                'ON a.call_id=c.id WHERE a.trial_id=?', (self.admitted.trial_id,)).fetchone()
        self.assertEqual(call['status'], 'refunded')

    async def test_all_actual_role_attempts_and_kernel_lifetime_survive_db_terminal_state(self):
        await self.prepare()
        service = self.service()
        self.acked, self.closed = asyncio.Event(), asyncio.Event()
        self.hang, self.body = False, IM._json({'version': 1, 'kind': 'patches', 'patches': [self.body_patch()]})
        self.native_statuses = [503, None]
        service.operation_tasks.runner.adapter_factory = self.adapter
        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify',
                   return_value={'supported': True, 'capability_hash': 'c' * 64}):
            result = await service.proposals.generate(self.owner, 'association-experiment',
                expected_generation=3, event_key='role-with-one-actual-infrastructure-retry')
        self.assertEqual(result['status'], 'proposals_staged')
        self.assertEqual(self.native_queries, 2)
        identifier = result['operation_id']
        row = service.execution.get(self.owner, identifier)
        self.assertEqual([a['status'] for a in row['attempts']], ['interrupted', 'completed'])
        handle = service.execution.workspace.acquire_execution(self.owner.brain_id, identifier, 2)
        self.assertIsNotNone(handle)
        generation = self.current()['generation']
        try:
            stopped = await service.cancel(self.owner, 'association-experiment', expected_generation=generation,
                event_key='stop-db-terminal-with-held-role-kernel')
            self.assertEqual(stopped['current']['phase'], 'stopping')
        finally:
            service.execution.workspace.release_execution(handle)
        _, unrelated = service.execution.workspace._paths('unrelated-corrupt-role', 1)
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        before = b'{"unrelated":"invalid-journal"}'
        unrelated.write_bytes(before)
        proven, cleanup = [], service.execution.workspace.cleanup_proven

        def observed(operation_id, attempt, receipt):
            proven.append((operation_id, attempt))
            return cleanup(operation_id, attempt, receipt)

        with patch.object(service.execution.workspace, 'cleanup_proven', side_effect=observed):
            recovered = await service.recover(self.owner, 'association-experiment', event_key='recover-all-role-attempts-only')
        self.assertEqual(recovered['current']['phase'], 'cancelled')
        self.assertEqual(set(proven), {(identifier, 1), (identifier, 2)})
        self.assertEqual(unrelated.read_bytes(), before)
        replay = await service.cancel(self.owner, 'association-experiment', expected_generation=generation,
            event_key='stop-db-terminal-with-held-role-kernel')
        self.assertEqual(replay['acknowledgment'], stopped['acknowledgment'])
        self.assertEqual(replay['current']['phase'], 'cancelled')
        with closing(self.assets.store._conn()) as connection:
            calls = list(connection.execute("SELECT status,engine_at FROM agent_calls WHERE purpose='improve_propose' ORDER BY id"))
        self.assertEqual([c['status'] for c in calls], ['interrupted', 'ok'])
        self.assertTrue(all(c['engine_at'] is not None for c in calls))


if __name__ == '__main__':
    unittest.main()
