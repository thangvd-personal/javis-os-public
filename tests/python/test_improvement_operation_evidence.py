"""Encrypted actual role evidence, scoped writer replay and late observations."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import json
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
from improvement_blobs import AssetUnavailable
import test_improvement_operation_execution as F


class OperationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = F.OperationExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.execution = self.f.execution()
        self.row, self.token, self.receipt = self.f.prepared(self.execution)
        self.owner = self.f.owner

    def evidence(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_operation_evidence"), "Real role trace is absent")
        from improvement_operation_evidence import OperationEvidence
        return OperationEvidence(self.execution)

    def packet(self, sequence=1):
        return {"version": 1, "sequence": sequence, "type": "worker_ready", "capability_hash": "c" * 64,
                **{key: self.receipt[key] for key in ("operation_id", "attempt", "role", "purpose", "input_hash")}}

    def append(self, evidence, packet, **options):
        return evidence.append(self.owner, self.row["operation_id"], packet, lease_token=self.token,
                               expected_generation=self.row["generation"], **options)

    def test_exact_encrypted_trace_replay_remains_cold_after_off(self):
        evidence, packet = self.evidence(), self.packet()
        first = self.append(evidence, packet)
        self.assertEqual(first["payload_hash"], hashlib.sha256(IM._json(packet).encode()).hexdigest())
        self.assertEqual(evidence.read(self.owner, self.row["operation_id"], 1, 1), packet)
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-after-trace")
        with patch.object(self.f.f.host, "_snapshot_observe", side_effect=AssertionError("Trace replay probed source")):
            self.assertEqual(self.append(evidence, packet), first)
        with self.assertRaises(IM.ConflictError):
            self.append(evidence, {**packet, "capability_hash": "f" * 64})

    def test_foreign_writer_identity_and_sequence_cannot_append(self):
        evidence = self.evidence()
        for change in ({"attempt": True}, {"operation_id": "foreign"}, {"role": "semantic_reviewer"},
                       {"trial_id": "fake-trial"}, {"input_hash": "f" * 64}, {"sequence": 2}):
            with self.subTest(change=change), self.assertRaises((ValueError, IM.ConflictError)):
                self.append(evidence, {**self.packet(), **change})
        with self.assertRaises(IM.ConflictError):
            evidence.append(self.owner, self.row["operation_id"], self.packet(), lease_token="foreign",
                            expected_generation=self.row["generation"])

    def test_late_actual_writer_observation_is_preserved_without_reopening_terminal_operation(self):
        evidence = self.evidence()
        self.append(evidence, self.packet())
        self.row = self.execution.start_engine(self.owner, self.row["operation_id"], expected_generation=self.row["generation"],
                                               lease_token=self.token)
        stopped = self.execution.abort(self.owner, self.row["operation_id"], expected_generation=self.row["generation"],
                                       lease_token=self.token, reason="cancelled")
        packet = {**self.packet(2), "type": "engine_event", "event": {"type": "usage", "usage": {"input_tokens": 7}}}
        del packet["capability_hash"]
        with self.assertRaises(IM.ConflictError):
            self.append(evidence, packet)
        saved = self.append(evidence, packet, late=True)
        self.assertTrue(saved["late"])
        self.assertEqual(evidence.read(self.owner, self.row["operation_id"], 1, 2), packet)
        self.assertEqual(self.execution.get(self.owner, self.row["operation_id"])["status"], stopped["status"])
        self.assertEqual(self.execution.ledger.get(stopped["attempts"][-1]["call_id"])["status"], "interrupted")

    def test_multichunk_unicode_trace_is_exact_and_missing_chunk_cannot_replay_as_verified(self):
        evidence = self.evidence()
        packet = {**self.packet(), "type": "engine_event", "event": {"type": "native_system", "data": "Đối soát\n" * 140000}}
        del packet["capability_hash"]
        self.append(evidence, packet)
        self.assertEqual(evidence.read(self.owner, self.row["operation_id"], 1, 1), packet)
        with closing(self.f.store._conn()) as connection:
            hashes = json.loads(connection.execute("SELECT blob_hashes_json FROM improvement_operation_observations WHERE operation_id=?",
                                                   (self.row["operation_id"],)).fetchone()[0])
        self.assertGreater(len(hashes), 1)
        self.f.assets.blobs._path(self.owner.brain_id, hashes[-1]).unlink()
        with self.assertRaises(AssetUnavailable):
            self.append(evidence, packet)


if __name__ == "__main__":
    unittest.main()
