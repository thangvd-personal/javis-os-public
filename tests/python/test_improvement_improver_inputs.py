"""Actual completed preparation supplies minimized, immutable proposer input."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import importlib.util
import json
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
import improvement_evaluation as EVAL
from improvement_trial_runner import TrialRunner
import test_improvement_preparation_execution as fixtures


class ImproverInputTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.PreparationExecutionTests.setUp
    factory = fixtures.PreparationExecutionTests.factory

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('improvement_improver_inputs'), 'Actual authorized tuning input is absent')
        from improvement_improver_inputs import ImproverInputs
        return ImproverInputs(self.f.plans)

    async def prepare(self, *, private_feedback=False):
        runner = TrialRunner(self.trials, context_loader=self.host.context_loader,
                             live_guard=self.host.live_guard, adapter_factory=self.factory)
        evaluate = EVAL.evaluate_trial
        append = self.trials.append_observation

        def observe(*args, **kwargs):
            self.original_writer = kwargs['lease_token']
            return append(*args, **kwargs)

        def feedback(*args, **kwargs):
            result = evaluate(*args, **kwargs)
            if private_feedback:
                result['reason'] = 'private-owner-answer-0'
                for criterion in result['criteria']:
                    criterion.update(reason='private-owner-answer-0', evidence={'expected': 'private-owner-answer-0'})
            return result

        with patch.object(EVAL, 'evaluate_trial', side_effect=feedback), \
             patch.object(self.trials, 'append_observation', side_effect=observe):
            self.result = await runner.run(self.owner, self.admitted.trial_id)
        self.assertEqual(self.result['status'], 'completed', self.result)
        self.sealed = await asyncio.to_thread(self.f.service().complete, self.owner, 'association-experiment',
            expected_generation=2, event_key='inputs-complete-preparation')
        with closing(self.assets.store._conn()) as connection:
            self.before_exposures = list(connection.execute('SELECT * FROM improvement_case_exposures ORDER BY fingerprint'))

    def build(self):
        return self.service().build(self.owner, 'association-experiment', expected_generation=3)

    def no_publication(self):
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_events WHERE operation='improver_input'").fetchone()[0], 0)
            self.assertEqual(list(connection.execute('SELECT * FROM improvement_case_exposures ORDER BY fingerprint')),
                             getattr(self, 'before_exposures', []))
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)

    async def test_real_completed_tuning_input_never_reads_private_answers_or_other_splits(self):
        await self.prepare(private_feedback=True)
        service = self.service()
        with closing(self.assets.store._conn()) as connection:
            forbidden = {json.loads(row[0])['answer_hash'] for row in connection.execute('SELECT case_json FROM improvement_cases')}
            forbidden.update(row[0] for row in connection.execute('SELECT source_hash FROM improvement_snapshots WHERE id IN (?,?)',
                tuple(snapshot['snapshot_id'] for snapshot in self.f.f.snapshots[1:])))
        read = self.assets.blobs.read

        def guarded(brain, digest):
            self.assertNotIn(digest, forbidden, 'An improver must not read private/holdout blobs')
            return read(brain, digest)

        with patch.object(self.assets.blobs, 'read', side_effect=guarded):
            built = service.build(self.owner, 'association-experiment', expected_generation=3)
        data = built['data']
        self.assertEqual((built['input_hash'], built['input_blob_hash']), (IM._hash(data), IM._hash(data)))
        self.assertEqual(json.loads(read(self.owner.brain_id, built['input_blob_hash'])), data)
        self.assertEqual(data['preparation']['result_refs'], self.sealed['result_refs'])
        self.assertEqual(data['baseline']['content'].encode(), self.f.f.f.skill.read_bytes())
        self.assertEqual(data['baseline_context']['coverage'], 'complete')
        self.assertEqual([item['case']['id'] for item in data['tuning']], ['case-0'])
        encoded = IM._json(data)
        self.assertNotIn('private-owner-answer', encoded)
        self.assertNotIn('Private association fixture 2', encoded)
        self.assertNotIn('Private association fixture 3', encoded)
        self.assertNotIn('evaluator_refs', encoded)
        self.assertEqual(data['tuning'][0]['assessment']['target_defect'], 'unknown')
        self.assertEqual(data['tuning'][0]['assessment']['usage_coverage'], 'partial')
        self.assertTrue(data['tuning'][0]['trace'])
        self.assertTrue(data['tuning'][0]['receipt'])
        self.assertIn('synthetic preparation baseline', data['tuning'][0]['artifact']['content'])
        self.assertEqual(self.build(), built)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM agent_calls').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)
            self.assertGreater(connection.execute('SELECT count(*) FROM improvement_case_exposures').fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_events WHERE operation='improver_input'").fetchone()[0], 1)

    async def test_pending_preparation_cannot_become_model_input(self):
        with self.assertRaisesRegex(IM.ConflictError, 'preparation|state'):
            self.service().build(self.owner, 'association-experiment', expected_generation=2)
        self.no_publication()

    async def test_holdout_fingerprint_alias_is_denied_even_after_slow_input_write(self):
        await self.prepare()
        service, write = self.service(), self.assets.blobs.write
        inserted = False

        def alias(brain, raw):
            nonlocal inserted
            digest = write(brain, raw)
            if not inserted:
                inserted = True
                with self.assets.store._Tx(self.assets.store) as connection:
                    token = connection.execute("SELECT fingerprint FROM improvement_case_families WHERE case_id='case-0' LIMIT 1").fetchone()[0]
                    connection.execute('INSERT INTO improvement_case_families VALUES(?,?,?,?,?)',
                        (self.f.association['case_set_id'], 'case-1', self.owner.brain_id, token, 'qualification'))
            return digest

        with patch.object(self.assets.blobs, 'write', side_effect=alias), self.assertRaisesRegex(PermissionError, 'holdout_alias'):
            service.build(self.owner, 'association-experiment', expected_generation=3)
        self.no_publication()

    async def test_policy_off_during_slow_write_cannot_publish_or_expose(self):
        await self.prepare()
        service, write = self.service(), self.assets.blobs.write

        def off(brain, raw):
            digest = write(brain, raw)
            policy = self.assets.get_policy(self.owner)
            self.assets.set_policy(self.owner, 'off', expected_version=policy['version'], event_key='input-off-at-slow-write')
            return digest

        with patch.object(self.assets.blobs, 'write', side_effect=off), self.assertRaises(PermissionError):
            service.build(self.owner, 'association-experiment', expected_generation=3)
        self.no_publication()

    async def test_source_drift_during_slow_write_cannot_publish(self):
        await self.prepare()
        service, write = self.service(), self.assets.blobs.write

        def drift(brain, raw):
            digest = write(brain, raw)
            self.f.f.f.protected.write_bytes(b'Changed protected owner source.\n')
            return digest

        with patch.object(self.assets.blobs, 'write', side_effect=drift), self.assertRaises(IM.ConflictError):
            service.build(self.owner, 'association-experiment', expected_generation=3)
        self.no_publication()

    async def test_renderer_and_closed_output_bind_actual_evidence_and_body_only(self):
        await self.prepare()
        data = self.build()['data']
        from improvement_generation_protocol import render_proposer_input, generation_output
        rendered = json.loads(render_proposer_input(data))
        self.assertEqual(rendered['authorized_evidence'], data)
        self.assertEqual(rendered['untrusted_data'], True)
        self.assertIn('not_a_target_defect', rendered['output_contract'])
        from improvement_operation_profile import operation_user_prompt
        actual = json.loads(operation_user_prompt({'request': {'kind': 'propose'}, 'data': data}))
        self.assertEqual(actual['proposer_task'], rendered)
        base = data['baseline']
        start = base['content'].encode().index(b'Retain owner rules.')
        body = {'version': 1, 'target_id': base['target_id'], 'base_revision_id': base['revision_id'],
            'before_hash': base['hash'], 'operation': 'replace_body_region', 'start': start,
            'end': len(base['content'].encode()), 'replacement': 'Retain owner rules.\nCheck each subtotal.\n',
            'hypothesis': 'Count authorized rows.', 'failure_signature': 'Omitted row.',
            'expected_behavior': 'Every row counted once.', 'possible_regressions': ['Duplicate row.'],
            'evidence_refs': data['evidence_refs'], 'angle': 'execution'}
        value = {'version': 1, 'kind': 'patches', 'patches': [body]}
        self.assertEqual(generation_output(value, data), value)
        for changes in ({'evidence_refs': [{'kind': 'tuning_trial', 'id': 'unrelated-trial'}]},
                        {'start': 0}, {'replacement': 'Retain owner rules.\nRun scripts/evil.py\n'},
                        {'accepted': True}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, PermissionError)):
                generation_output({**value, 'patches': [{**body, **changes}]}, data)
        with self.assertRaises(ValueError):
            generation_output({**value, 'patches': [body, body, body]}, data)
        with self.assertRaises(ValueError):
            generation_output({**value, 'patches': [body, body]}, data)
        closed = {'version': 1, 'kind': 'not_a_target_defect', 'reason': 'Native receipt is incomplete.',
                  'evidence_refs': data['evidence_refs']}
        self.assertEqual(generation_output(closed, data), closed)

    async def test_new_evaluation_cannot_replace_the_exact_sealed_result_during_input_write(self):
        await self.prepare()
        service, write = self.service(), self.assets.blobs.write
        changed = False

        def reevaluate(brain, raw):
            nonlocal changed
            digest = write(brain, raw)
            if not changed:
                changed = True
                current = self.trials.get(self.owner, self.admitted.trial_id)
                self.trials.reevaluate(self.owner, self.admitted.trial_id, 1, expected_generation=current['generation'],
                                      event_key='input-reevaluated-after-sealing', checker_registry={})
            return digest

        with patch.object(self.assets.blobs, 'write', side_effect=reevaluate), self.assertRaises(IM.ConflictError):
            service.build(self.owner, 'association-experiment', expected_generation=3)
        self.no_publication()


    async def test_new_late_trace_cannot_be_silently_omitted_after_slow_input_write(self):
        await self.prepare()
        service, write = self.service(), self.assets.blobs.write
        appended = False

        def late(brain, raw):
            nonlocal appended
            digest = write(brain, raw)
            if not appended:
                appended = True
                self.trials.append_observation(self.owner, self.admitted.trial_id,
                    {'version': 1, 'sequence': 999, 'type': 'worker_error', 'reason': 'late_native_exit', 'error_type': 'RuntimeError'},
                    lease_token=self.original_writer, attempt=1, late=True)
            return digest

        with patch.object(self.assets.blobs, 'write', side_effect=late), self.assertRaises(IM.ConflictError):
            service.build(self.owner, 'association-experiment', expected_generation=3)
        self.no_publication()


class InputProjectionTests(unittest.TestCase):
    def test_reasoning_text_and_arbitrary_native_data_are_never_proposer_evidence(self):
        from improvement_improver_inputs import project_packet
        for event in ({'type': 'thinking', 'text': 'private-cot'}, {'type': 'text', 'content': 'private-cot'},
                      {'type': 'native_system', 'data': {'thinking': 'private-cot'}}):
            self.assertNotIn('private-cot', IM._json(project_packet({'type': 'engine_event', 'event': event})))
        result = project_packet({'type': 'engine_event', 'event': {'type': 'tool_result', 'id': 'call-1',
                                 'is_error': True, 'thinking': 'private-cot'}})
        self.assertEqual(result['event'], {'type': 'tool_result', 'id': 'call-1', 'is_error': True})
        native = project_packet({'type': 'engine_event', 'event': {'type': 'native_system', 'subtype': 'init',
            'data': {'tools': ['mcp__fixture__fixture_read'], 'session_id': 'real-session', 'thinking': 'private-cot'}}})
        self.assertEqual(native['event']['data'], {'tools': ['mcp__fixture__fixture_read'], 'session_id': 'real-session'})

    def test_credentials_and_required_oversize_input_reject_without_truncation(self):
        from improvement_generation_protocol import input_bytes, render_proposer_input
        for data in ({'artifact': 'Bearer abcdefghijklmnop1234567890'}, {'source': 'x' * (1024 * 1024)}):
            with self.subTest(data_kind=list(data)[0]), self.assertRaises(ValueError):
                input_bytes(data)
            with self.assertRaises(ValueError):
                render_proposer_input(data)


if __name__ == '__main__':
    unittest.main()
