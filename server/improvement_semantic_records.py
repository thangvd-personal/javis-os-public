"""Cold semantic receipt metadata; construction never captures, migrates or probes."""
import json
from contextlib import closing

import improvement_store as IM
from improvement_candidate_records import CandidateRecords, identifier
from improvement_trial_records import _brain


class SemanticReceipts:
    def __init__(self, assets):
        if type(assets) is not IM.ImprovementStore:
            raise TypeError('concrete_semantic_store_required')
        self.assets, self.store, self.blobs = assets, assets.store, assets.blobs

    @staticmethod
    def public(row):
        return {'receipt_id': row['id'], **{key: row[key] for key in ('candidate_id', 'experiment_id', 'operation_id',
            'result_id', 'binding_hash', 'assessment_hash', 'blob_hash', 'created_at')}}

    def row(self, connection, principal, candidate_id, receipt_id=None):
        brain = _brain(principal)
        identifier(candidate_id)
        CandidateRecords._row(connection, brain, candidate_id)
        rows = list(connection.execute('SELECT * FROM improvement_semantic_receipts WHERE candidate_id=? AND brain_id=? ORDER BY created_at,id',
                                       (candidate_id, brain)))
        if receipt_id is not None:
            identifier(receipt_id)
            rows = [row for row in rows if row['id'] == receipt_id]
        if len(rows) != 1:
            raise PermissionError('semantic_receipt_unavailable')
        return rows[0]

    def history(self, principal, candidate_id):
        brain = _brain(principal)
        identifier(candidate_id)
        if IM._schema_version(self.store) < 6:
            return []
        with closing(self.store._conn()) as connection:
            CandidateRecords._row(connection, brain, candidate_id)
            return [self.public(row) for row in connection.execute('SELECT * FROM improvement_semantic_receipts '
                'WHERE brain_id=? AND candidate_id=? ORDER BY created_at,id', (brain, candidate_id))]

    def read(self, principal, candidate_id, receipt_id=None):
        with closing(self.store._conn()) as connection:
            row = self.row(connection, principal, candidate_id, receipt_id)
        value = json.loads(self.blobs.read(_brain(principal), row['blob_hash']))
        binding = json.loads(self.blobs.read(_brain(principal), row['binding_blob_hash']))
        if (IM._hash(value) != row['blob_hash'] or IM._hash(binding) != row['binding_hash']
                or value.get('binding') != binding or IM._hash(value.get('assessment')) != row['assessment_hash']
                or value.get('candidate_id') != candidate_id or value.get('operation_id') != row['operation_id']
                or value.get('result_id') != row['result_id']):
            raise IM.ConflictError('semantic_receipt_changed')
        return self.public(row), value
