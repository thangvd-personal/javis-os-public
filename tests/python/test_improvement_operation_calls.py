"""Actual role reservations share the real ledger and schema6 operation ownership."""
from _paths import ROOT, SERVER  # noqa: F401

import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
from improvement_operation_records import OperationRecords
from agent_ledger import CallLedger
import test_improvement_operations as fixtures


class OperationCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OperationMetadataTests.setUpClass()

    def setUp(self):
        self.f = fixtures.OperationMetadataTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.proposing()  # Synthetic host metadata; no native readiness claimed.
        self.operation = self.f.operation()
        self.owner, self.assets, self.store = self.f.owner, self.f.assets, self.f.store
        self.records = OperationRecords(self.assets)

    def reserve(self, *, key="owner-reserve-propose-operation"):
        method = getattr(self.records, "_reserve_attempt_in", None)
        self.assertTrue(callable(method), "Real operation attempt reservation is absent")
        with self.store._Tx(self.store) as connection:
            return method(connection, self.owner, self.operation["operation_id"], expected_generation=1, event_key=key)

    def test_role_attempt_call_and_owner_event_are_atomic_and_exact_replay(self):
        before = self.f.h.history()
        reserved = self.reserve()
        self.assertEqual((reserved["status"], reserved["attempt"], reserved["generation"]), ("reserved", 1, 2))
        self.assertEqual(self.reserve(), reserved)
        with closing(self.store._conn()) as connection:
            attempt = connection.execute("SELECT * FROM improvement_operation_attempts WHERE operation_id=?",
                                         (self.operation["operation_id"],)).fetchone()
            call = CallLedger(self.store).get(attempt["call_id"])
            self.assertEqual((call["purpose"], call["brain_id"], call["attempt"], call["status"]),
                             ("improve_propose", self.owner.brain_id, 1, "reserved"))
            self.assertEqual(call["mirror_ref"], "improvement_operation:" + self.operation["operation_id"])
            self.assertEqual(call["reason_ref"], call["mirror_ref"] + ":attempt:1")
            self.assertIsNone(call["engine_at"])
        after = self.f.h.history()
        self.assertEqual(len(after["agent_calls"]), len(before["agent_calls"]) + 1)
        self.assertEqual(len(after["improvement_operation_attempts"]), 1)
        self.assertEqual(after["improvement_trials"], before["improvement_trials"])
        with self.assertRaises(IM.ConflictError):
            self.reserve(key="second-reserve-same-operation")

    def test_failed_owner_event_rolls_back_the_actual_call_and_attempt(self):
        before = self.f.h.history()
        with patch.object(self.assets, "_event", side_effect=RuntimeError("Synthetic event failure")), self.assertRaises(RuntimeError):
            self.reserve()
        self.assertEqual(self.f.h.history(), before)

    def test_off_denies_new_call_but_preserves_original_reservation_replay(self):
        reserved = self.reserve()
        self.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-after-role-reservation")
        self.assertEqual(self.reserve(), reserved)
        with self.assertRaises(IM.ConflictError):
            self.reserve(key="new-after-original-reservation")

    def test_off_blocks_an_unreserved_operation_without_creating_a_call(self):
        self.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-before-role-reservation")
        before = self.f.h.history()
        with self.assertRaises(PermissionError):
            self.reserve()
        self.assertEqual(self.f.h.history(), before)


if __name__ == "__main__":
    unittest.main()
