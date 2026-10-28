"""New role workflows must reject stale schema before any native preflight."""
from _paths import ROOT, SERVER  # noqa: F401

import unittest
import json
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
import test_improvement_proposals as proposer
import test_improvement_semantic_review as reviewer


async def wrong_input_product(test, subject, snapshot, *, kind, candidate_id=None):
    """Real host metadata/native execution with a deliberately unrelated input."""
    data = {'task': 'Unrelated input without the authorized experiment projection.'}
    blob = test.assets.blobs.write(test.owner.brain_id, IM._json(data).encode())
    value = snapshot['prepared'][0]
    request = {'version': 1, 'experiment_id': value['experiment_id'], 'context_id': value['context_id'],
        'definition_hash': IM._hash(value), 'kind': kind, 'role': 'semantic_reviewer' if kind == 'semantic' else 'improver',
        'purpose': 'improve_evaluate' if kind == 'semantic' else 'improve_propose', 'candidate_id': candidate_id,
        'parent_id': None, 'input_hash': blob}
    with test.assets.store._Tx(test.assets.store) as connection:
        row = subject.execution.records._create_in(connection, test.owner, request, blob, expected_generation=3,
                                                   event_key='actual-wrong-input-' + kind)
    await subject.tasks.start(test.owner, row['operation_id'], expected_generation=1, event_key='native-wrong-input-' + kind)
    result = await subject.tasks.join(test.owner, row['operation_id'])
    test.assertEqual(result['status'], 'completed', result)
    return row, subject._read_product(test.owner, row['operation_id'])


class ProposalAdmissionTests(unittest.IsolatedAsyncioTestCase):
    setUp = proposer.ProposalTests.setUp
    factory = proposer.ProposalTests.factory
    prepare = proposer.ProposalTests.prepare
    adapter = proposer.ProposalTests.adapter
    body_patch = proposer.ProposalTests.body_patch
    service = proposer.ProposalTests.service
    generate = proposer.ProposalTests.generate
    counts = proposer.ProposalTests.counts
    test_normal_proposer_still_preserves_real_unreviewed_draft = proposer.ProposalTests.test_actual_native_result_stages_bound_unreviewed_draft_and_replay_never_queries
    test_normal_repair_still_binds_original_input = proposer.ProposalTests.test_malformed_batch_gets_one_actual_repair_with_same_input_and_allowance

    async def test_stale_schema_rejects_proposer_before_native_probe(self):
        subject = await self.service()
        with self.assets.store._Tx(self.assets.store) as connection:
            connection.execute('DROP TRIGGER improvement_operation_attempt_scope')
        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify', side_effect=AssertionError('Stale schema reached native probe')), \
             self.assertRaisesRegex(IM.ConflictError, 'schema_changed'):
            await self.generate(subject)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)

    async def test_real_proposer_output_from_unrelated_input_cannot_claim_tuning_lineage(self):
        subject = await self.service()
        with closing(self.assets.store._conn()) as connection:
            snapshot = subject.inputs._snapshot(connection, self.owner, 'association-experiment', 3)
        built = subject.inputs.build(self.owner, 'association-experiment', expected_generation=3)
        row, product = await wrong_input_product(self, subject, snapshot, kind='propose')
        begun = {'state': snapshot, 'data': built['data'], 'operation_id': row['operation_id'], 'generation': 4}
        payload = {'experiment_id': 'association-experiment', 'expected_generation': 3}
        batch = json.loads(product['raw'])
        with self.assertRaisesRegex(IM.ConflictError, 'input_changed'):
            subject._publish(self.owner, payload, 'forbidden-unrelated-proposer-publication', begun, product, batch)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_skill_candidates').fetchone()[0], 0)


class SemanticAdmissionTests(unittest.IsolatedAsyncioTestCase):
    setUp = reviewer.SemanticReviewTests.setUp
    factory = reviewer.SemanticReviewTests.factory
    prepare = reviewer.SemanticReviewTests.prepare
    adapter = reviewer.SemanticReviewTests.adapter
    body_patch = reviewer.SemanticReviewTests.body_patch
    service = reviewer.SemanticReviewTests.service
    review = reviewer.SemanticReviewTests.review
    assert_unreviewed = reviewer.SemanticReviewTests.assert_unreviewed
    test_normal_reviewer_still_keeps_native_unknown = reviewer.SemanticReviewTests.test_actual_review_receipt_keeps_model_pass_and_native_unknown_distinct

    async def test_stale_schema_rejects_reviewer_before_native_probe(self):
        subject = await self.service()
        with self.assets.store._Tx(self.assets.store) as connection:
            connection.execute('DROP TRIGGER improvement_operation_attempt_scope')
        with patch('improvement_operation_adapter.ClaudeOperationAdapter.verify', side_effect=AssertionError('Stale schema reached native probe')), \
             self.assertRaisesRegex(IM.ConflictError, 'schema_changed'):
            await self.review(subject)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_operations').fetchone()[0], 0)

    async def test_real_reviewer_output_from_unrelated_input_cannot_mint_semantic_receipt(self):
        subject = await self.service()
        snapshot = subject._snapshot(self.owner, self.candidate['candidate_id'], 3)
        row, product = await wrong_input_product(self, subject, snapshot, kind='semantic', candidate_id=self.candidate['candidate_id'])
        begun = {'snapshot': snapshot, 'operation_id': row['operation_id'], 'generation': 4, 'experiment_id': 'association-experiment'}
        payload = {'candidate_id': self.candidate['candidate_id'], 'expected_generation': 3}
        with self.assertRaisesRegex(IM.ConflictError, 'input_changed'):
            subject._publish(self.owner, payload, 'forbidden-unrelated-semantic-receipt', begun, product)
        with closing(self.assets.store._conn()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM improvement_semantic_receipts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
