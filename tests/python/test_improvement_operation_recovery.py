"""Scoped actual role crash recovery preserves fresh/live owners and audit rows."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_improvement_operation_execution as F


class OperationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = F.OperationExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.execution, self.owner = self.f.execution(), self.f.owner

    def recovery(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_operation_recovery"), "Actual role crash recovery is absent")
        from improvement_operation_recovery import OperationRecovery
        return OperationRecovery(self.execution)

    def test_expired_prepared_crash_refunds_and_cleans_without_live_source_or_native_query(self):
        row, token, receipt = self.f.prepared(self.execution)
        recovery = self.recovery()
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-before-crash-recovery")
        with patch.object(self.f.f.host, "_snapshot_observe", side_effect=AssertionError("Recovery observed live sources")):
            result = recovery.recover(self.owner, row["operation_id"], now=time.time() + 600)
        self.assertEqual((result["recovered"], result["cleaned"]), (1, 1))
        self.assertFalse(Path(receipt["path"]).exists())
        current = self.execution.get(self.owner, row["operation_id"])
        self.assertEqual(current["status"], "not_run")
        self.assertEqual(self.execution.ledger.get(current["attempts"][-1]["call_id"])["status"], "refunded")

    def test_live_lock_blocks_expired_recovery_until_actual_lifetime_closes(self):
        row, token, receipt = self.f.prepared(self.execution)
        recovery = self.recovery()
        lifetime = self.execution.workspace.acquire_execution(self.owner.brain_id, row["operation_id"], 1)
        try:
            result = recovery.recover(self.owner, row["operation_id"], now=time.time() + 600)
            self.assertEqual(result["busy"], 1)
            self.assertEqual(self.execution.get(self.owner, row["operation_id"])["status"], "prepared")
            self.assertTrue(Path(receipt["path"]).exists())
        finally:
            self.execution.workspace.release_execution(lifetime)
        self.assertEqual(recovery.recover(self.owner, row["operation_id"], now=time.time() + 600)["cleaned"], 1)

    def test_terminal_database_does_not_allow_live_workspace_cleanup(self):
        row, token, receipt = self.f.prepared(self.execution)
        recovery = self.recovery()
        lifetime = self.execution.workspace.acquire_execution(self.owner.brain_id, row["operation_id"], 1)
        try:
            self.execution.cancel(self.owner, row["operation_id"], expected_generation=row["generation"], event_key="cancel-during-live-owner")
            result = recovery.recover(self.owner, row["operation_id"], now=time.time() + 600)
            self.assertEqual(result["busy"], 1)
            self.assertTrue(Path(receipt["path"]).exists())
        finally:
            self.execution.workspace.release_execution(lifetime)
        self.assertEqual(recovery.recover(self.owner, row["operation_id"])["cleaned"], 1)

    def test_fresh_foreign_lease_is_not_stolen_and_marked_crash_is_counted(self):
        row, token, receipt = self.f.prepared(self.execution)
        recovery = self.recovery()
        self.assertEqual(recovery.recover(self.owner, row["operation_id"])["fresh"], 1)
        self.assertEqual(self.execution.get(self.owner, row["operation_id"])["status"], "prepared")
        row = self.execution.start_engine(self.owner, row["operation_id"], expected_generation=row["generation"], lease_token=token)
        self.assertEqual(recovery.recover(self.owner, row["operation_id"], now=time.time() + 600)["recovered"], 1)
        current = self.execution.get(self.owner, row["operation_id"])
        self.assertEqual(current["status"], "interrupted")
        call = self.execution.ledger.get(current["attempts"][-1]["call_id"])
        self.assertEqual((call["status"], call["counted"]), ("interrupted", 1))


if __name__ == "__main__":
    unittest.main()
