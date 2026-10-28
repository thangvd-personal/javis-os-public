"""Actual sealed preparation and native role products publish atomic A drafts."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import importlib.util
import json
import threading
import unittest
from contextlib import aclosing, closing
from unittest.mock import patch

import improvement_store as IM
from improvement_operation_execution import OperationExecution
from improvement_operation_runner import OperationRunner
from improvement_operation_tasks import OperationTasks
from improvement_trial_service import OwnerTrialService
import test_improvement_improver_inputs as inputs
import test_improvement_operation_runner as native


class ProposalTests(unittest.IsolatedAsyncioTestCase):
    setUp = inputs.ImproverInputTests.setUp
    factory = inputs.ImproverInputTests.factory
    prepare = inputs.ImproverInputTests.prepare
    adapter = native.OperationRunnerTests.adapter

    def body_patch(self, replacement='Retain owner rules.\nCheck each subtotal.\n'):
        raw, target = self.f.f.f.skill.read_bytes(), self.f.f.f.target
        return {'version': 1, 'target_id': target['target_id'], 'base_revision_id': target['revision_id'],
            'before_hash': target['source_hash'], 'operation': 'replace_body_region',
            'start': raw.index(b'Retain owner rules.'), 'end': len(raw), 'replacement': replacement,
            'hypothesis': 'Count every authorized row.', 'failure_signature': 'Omitted subtotal.',
            'expected_behavior': 'Every row counted once.', 'possible_regressions': ['Duplicate row.'],
            'evidence_refs': [{'kind': 'tuning_trial', 'id': self.admitted.trial_id}], 'angle': 'execution'}

    async def service(self, bodies=None):
        self.assertIsNotNone(importlib.util.find_spec('improvement_proposals'), 'Actual generated proposal workflow is absent')
        await self.prepare()
        self.execution = OperationExecution(self.f.plans, OwnerTrialService(self.host))
        self.acked, self.closed = asyncio.Event(), asyncio.Event()
        self.hang = False
        self.base_raw = self.f.f.f.skill.read_bytes()
        self.body = json.dumps({'version': 1, 'kind': 'patches', 'patches': [self.body_patch()]})
        delegate = self.adapter
        self.bodies = list(bodies) if bodies is not None else []

        def adapter(pin, **kwargs):
            if self.bodies:
                self.body = self.bodies.pop(0)
            return delegate(pin, **kwargs)

        self.runner = OperationRunner(self.execution, adapter_factory=adapter)
        self.tasks = OperationTasks(self.execution, self.runner)
        from improvement_proposals import ProposalService
        subject = ProposalService(self.execution, self.tasks)
        self.addAsyncCleanup(subject.close)
        self.enterContext(patch('improvement_operation_adapter.ClaudeOperationAdapter.verify',
            return_value={'supported': True, 'capability_hash': 'c' * 64}))
        return subject

    async def generate(self, subject, event_key='owner-generate-proposals'):
        return await subject.generate(self.owner, 'association-experiment', expected_generation=3, event_key=event_key)

    def counts(self):
        with closing(self.assets.store._conn()) as connection:
            return tuple(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
                ('improvement_skill_candidates', 'improvement_generated_candidates', 'improvement_proposal_publications'))

    async def test_actual_native_result_stages_bound_unreviewed_draft_and_replay_never_queries(self):
        subject = await self.service()
        result = await self.generate(subject)
        self.assertEqual(result['status'], 'proposals_staged', result)
        self.assertEqual(result['native_verdict'], 'unknown')
        self.assertFalse(result['business_proof'])
        self.assertEqual(self.counts(), (1, 1, 1))
        with closing(self.assets.store._conn()) as connection:
            candidate = connection.execute('SELECT * FROM improvement_skill_candidates').fetchone()
            link = connection.execute('SELECT * FROM improvement_generated_candidates').fetchone()
            call = connection.execute("SELECT * FROM agent_calls WHERE purpose='improve_propose'").fetchone()
            self.assertEqual(candidate['semantic_status'], 'unreviewed')
            self.assertIsNone(candidate['authority_id'])
            self.assertEqual(link['operation_id'], result['operation_id'])
            self.assertEqual(link['result_id'], result['result_id'])
            self.assertEqual(call['status'], 'ok')
            self.assertIsNotNone(call['engine_at'])
        self.assertTrue(self.closed.is_set())
        self.assertEqual(self.native_queries, 1)
        self.assertEqual(self.f.f.f.skill.read_bytes(), self.base_raw)
        self.assets.set_policy(self.owner, 'off', expected_version=0, event_key='proposal-off-after-stage')
        with patch.object(self.assets.blobs, 'read', side_effect=AssertionError('Replay must be cold')):
            self.assertEqual(await self.generate(subject), result)
        self.assertEqual(self.native_queries, 1)

    async def test_malformed_batch_gets_one_actual_repair_with_same_input_and_allowance(self):
        valid = json.dumps({'version': 1, 'kind': 'patches', 'patches': [self.body_patch()]})
        subject = await self.service(['not JSON', valid])
        result = await self.generate(subject)
        self.assertEqual(result['status'], 'proposals_staged', result)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.native_queries, 2)
        with closing(self.assets.store._conn()) as connection:
            rows = list(connection.execute('SELECT * FROM improvement_operations ORDER BY created_at'))
            self.assertEqual([row['kind'] for row in rows], ['propose', 'repair'])
            self.assertEqual(rows[1]['parent_id'], rows[0]['id'])
            original = json.loads(self.assets.blobs.read(self.owner.brain_id, rows[0]['input_blob_hash']))
            repair = json.loads(self.assets.blobs.read(self.owner.brain_id, rows[1]['input_blob_hash']))
            self.assertEqual(repair['authorized_input'], original)
            self.assertEqual(repair['candidate_limit'], original['candidate_limit'])
            self.assertEqual(repair['invalid_output'], 'not JSON')

    async def test_invalid_repair_cannot_loop_or_partially_stage_a_valid_first_patch(self):
        first = self.body_patch()
        invalid = {'version': 1, 'kind': 'patches', 'patches': [first, {**first, 'start': 0}]}
        subject = await self.service([json.dumps(invalid), json.dumps(invalid)])
        result = await self.generate(subject)
        self.assertEqual(result['status'], 'invalid_output', result)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.native_queries, 2)
        self.assertEqual(await self.generate(subject), result)
        self.assertEqual(self.native_queries, 2)

    async def test_not_target_defect_is_an_actual_outcome_without_inventing_a_candidate(self):
        closed = {'version': 1, 'kind': 'not_a_target_defect', 'reason': 'Baseline native evidence is incomplete.',
                  'evidence_refs': [{'kind': 'tuning_trial', 'id': self.admitted.trial_id}]}
        subject = await self.service([json.dumps(closed)])
        result = await self.generate(subject)
        self.assertEqual(result['status'], 'not_a_target_defect', result)
        self.assertEqual(self.counts(), (0, 0, 1))
        self.assertEqual(self.native_queries, 1)

    async def test_stale_owner_source_after_model_cannot_stage(self):
        subject = await self.service()
        publish = subject._publish

        def drift(*args, **kwargs):
            self.f.f.f.protected.write_bytes(b'Changed owner instructions after model output.\n')
            return publish(*args, **kwargs)

        with patch.object(subject, '_publish', side_effect=drift), self.assertRaises(IM.ConflictError):
            await self.generate(subject)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.native_queries, 1)

    async def test_concurrent_manual_slot_rejects_whole_generated_batch(self):
        subject = await self.service()
        publish = subject._publish

        def stage_manual(*args, **kwargs):
            self.f.plans.authoring.stage(self.owner, 'association-experiment', self.body_patch(), event_key='manual-during-model-generation')
            return publish(*args, **kwargs)

        with patch.object(subject, '_publish', side_effect=stage_manual), self.assertRaises(IM.ConflictError):
            await self.generate(subject)
        self.assertEqual(self.counts(), (1, 0, 0))

    async def test_second_stage_failure_rolls_back_first_candidate_and_generated_links(self):
        subject = await self.service([json.dumps({'version': 1, 'kind': 'patches', 'patches': [self.body_patch(),
            self.body_patch('Retain owner rules.\nCheck every document total.\n')]})])
        stage = self.f.plans.authoring._stage_in
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Synthetic publication interruption')
            return stage(*args, **kwargs)

        with patch.object(self.f.plans.authoring, '_stage_in', side_effect=fail_second), self.assertRaises(OSError):
            await self.generate(subject)
        self.assertEqual(self.counts(), (0, 0, 0))
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_events WHERE operation='skill_candidate_stage'").fetchone()[0], 0)

    async def test_actual_call_model_mismatch_before_product_read_cannot_publish(self):
        subject = await self.service()
        read = subject._read_product

        def foreign(*args, **kwargs):
            with self.assets.store._Tx(self.assets.store) as connection:
                connection.execute("UPDATE agent_calls SET model='foreign-model' WHERE purpose='improve_propose'")
            return read(*args, **kwargs)

        with patch.object(subject, '_read_product', side_effect=foreign), self.assertRaises(IM.ConflictError):
            await self.generate(subject)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_shutdown_owns_slow_input_thread_and_never_probes_after_close(self):
        subject = await self.service()
        entered, release = asyncio.Event(), threading.Event()
        loop, build = asyncio.get_running_loop(), subject.inputs.build

        def slow(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError('Input thread was not released')
            return build(*args, **kwargs)

        with patch.object(subject.inputs, 'build', side_effect=slow), \
             patch('improvement_operation_adapter.ClaudeOperationAdapter.verify', return_value={'supported': True, 'capability_hash': 'c' * 64}) as verify:
            task = asyncio.create_task(self.generate(subject))
            await asyncio.wait_for(entered.wait(), 10)
            closer = asyncio.create_task(subject.close())
            try:
                await asyncio.sleep(.03)
                self.assertFalse(closer.done())
            finally:
                release.set()
            await asyncio.wait_for(closer, 10)
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                await task
            verify.assert_not_called()
        self.assertEqual(self.counts(), (0, 0, 0))
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)

    async def test_repeated_http_cancellation_and_shutdown_drain_actual_marked_worker(self):
        subject = await self.service()
        self.hang = True
        task = asyncio.create_task(self.generate(subject))
        await asyncio.wait_for(self.acked.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(.03)
        self.assertFalse(task.done())
        await asyncio.wait_for(subject.close(), 10)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.closed.is_set())
        self.assertFalse(subject._operations)
        self.assertFalse(self.tasks._tasks)
        self.assertEqual(self.counts(), (0, 0, 0))
        with closing(self.assets.store._conn()) as connection:
            call = connection.execute("SELECT * FROM agent_calls WHERE purpose='improve_propose'").fetchone()
            self.assertIsNotNone(call['engine_at'])
            self.assertEqual(call['status'], 'interrupted')

    async def test_close_waits_for_publication_thread_after_native_row_is_completed(self):
        subject = await self.service()
        entered, release = asyncio.Event(), threading.Event()
        loop, publish = asyncio.get_running_loop(), subject._publish

        def slow(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError('Publisher thread was not released')
            return publish(*args, **kwargs)

        with patch.object(subject, '_publish', side_effect=slow):
            task = asyncio.create_task(self.generate(subject))
            await asyncio.wait_for(entered.wait(), 15)
            closer = asyncio.create_task(subject.close())
            try:
                await asyncio.sleep(.03)
                self.assertFalse(closer.done())
                self.assertFalse(task.done())
                with closing(self.assets.store._conn()) as connection:
                    self.assertEqual(connection.execute('SELECT status FROM improvement_operations').fetchone()[0], 'completed')
            finally:
                release.set()
            await asyncio.wait_for(closer, 10)
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                await task
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_cancel_of_completed_native_role_fences_a_pending_publication(self):
        subject = await self.service()
        publish = subject._publish

        def cancel(principal, payload, event_key, begun, product, batch, basis=None):
            row = self.execution.get(principal, product['row']['id'])
            self.execution.cancel(principal, row['operation_id'], expected_generation=row['generation'], event_key='owner-cancel-completed-proposer')
            return publish(principal, payload, event_key, begun, product, batch, basis)

        with patch.object(subject, '_publish', side_effect=cancel), self.assertRaises(IM.ConflictError):
            await self.generate(subject)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_two_real_drafts_preserve_the_existing_owner_region_without_new_grants(self):
        batch = {'version': 1, 'kind': 'patches', 'patches': [self.body_patch(),
            self.body_patch('Retain owner rules.\nCheck the document grand total.\n')]}
        subject = await self.service([json.dumps(batch)])
        patch_body = self.body_patch()
        grant = self.f.plans.authoring.declare_region(self.owner, 'association-experiment', patch_body['base_revision_id'],
            patch_body['start'], patch_body['end'], expected_target_generation=1, event_key='existing-owner-generated-region')
        result = await self.generate(subject)
        self.assertEqual(result['status'], 'proposals_staged', result)
        self.assertEqual(self.counts(), (2, 2, 1))
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual([row[0] for row in connection.execute('SELECT authority_id FROM improvement_skill_candidates ORDER BY ordinal')],
                             [grant['authority_id'], grant['authority_id']])
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_candidate_authorities').fetchone()[0], 1)
        self.assertEqual(self.f.f.f.skill.read_bytes(), self.base_raw)

    async def test_unqualified_preflight_creates_no_role_operation_or_call(self):
        subject = await self.service()
        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify', return_value={'supported': False}), \
             self.assertRaisesRegex(ValueError, 'native_unavailable'):
            await self.generate(subject)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM agent_calls WHERE purpose='improve_propose'").fetchone()[0], 0)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_missing_native_inventory_cannot_be_repaired_into_permission(self):
        subject = await self.service()
        factory = self.runner.adapter_factory

        def missing(*args, **kwargs):
            adapter = factory(*args, **kwargs)
            execute = adapter.execute

            async def stream(*values):
                async with aclosing(execute(*values)) as packets:
                    async for packet in packets:
                        packet = copy.deepcopy(packet)
                        if packet['type'] == 'engine_event' and packet['event']['type'] == 'native_system':
                            packet['event']['data'].pop('tools', None)
                        yield packet

            adapter.execute = stream
            return adapter

        self.runner.adapter_factory = missing
        with self.assertRaisesRegex(PermissionError, 'native_evidence'):
            await self.generate(subject)
        self.assertEqual(self.counts(), (0, 0, 0))
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 1)

    async def test_one_manual_slot_and_two_same_key_callers_only_create_the_last_real_draft(self):
        subject = await self.service()
        self.f.plans.authoring.stage(self.owner, 'association-experiment',
            self.body_patch('Retain owner rules.\nCheck manually chosen totals.\n'), event_key='manual-before-generated-last-slot')
        first, second = await asyncio.gather(self.generate(subject), self.generate(subject))
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'proposals_staged', first)
        self.assertEqual(self.counts(), (2, 1, 1))
        self.assertEqual(first['candidates'][0]['ordinal'], 2)
        self.assertEqual(self.native_queries, 1)


class GenerationJsonTests(unittest.TestCase):
    def test_duplicate_json_fields_are_not_a_valid_closed_model_output(self):
        from improvement_generation_protocol import parse_generation_output
        raw = '{"version":1,"kind":"not_a_target_defect","kind":"patches","reason":"x","evidence_refs":[]}'
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            parse_generation_output(raw, {})

    def test_proposer_and_repair_receive_the_complete_closed_body_patch_contract(self):
        from improvement_generation_protocol import render_proposer_input, render_repair_input
        original = {'version': 1, 'kind': 'authorized_tuning', 'candidate_limit': 2}
        contract = json.loads(render_proposer_input(original))['output_contract']
        required = contract['patch_manifest']['required']
        for field in ('version', 'target_id', 'base_revision_id', 'before_hash', 'operation', 'start', 'end', 'replacement',
                      'hypothesis', 'failure_signature', 'expected_behavior', 'possible_regressions', 'evidence_refs', 'angle'):
            self.assertIn(field, required)
        self.assertFalse(contract['patch_manifest']['additionalProperties'])
        repaired = json.loads(render_repair_input({'authorized_input': original, 'invalid_output': 'untrusted', 'candidate_limit': 2}))
        self.assertEqual(repaired['output_contract'], contract)


if __name__ == '__main__':
    unittest.main()
