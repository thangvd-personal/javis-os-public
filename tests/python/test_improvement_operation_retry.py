"""One genuine infrastructure retry; never retry model output for a better answer."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import importlib.util
import sqlite3
import unittest
from unittest.mock import patch

import improvement_store as IM
from improvement_operation_runner import OperationRunner
from improvement_operation_adapter import ClaudeOperationAdapter
import test_improvement_operation_execution as F
import test_improvement_operation_results as P


class OperationRetryTests(unittest.TestCase):
    def setUp(self):
        self.f = F.OperationExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.execution, self.owner = self.f.execution(), self.f.owner
        self.row = self.f.reserve(self.execution)

    def capacity_failure(self):
        held = self.execution.workspace.brain_barrier.acquire_brain_execution(self.owner.brain_id)
        try:
            result = asyncio.run(OperationRunner(self.execution).run(self.owner, self.row["operation_id"]))
            self.assertEqual(result["attempts"][-1]["failure_reason"], "capacity_unavailable")
            return result
        finally:
            held.close()

    def retry(self, row, key="owner-retry-role-infra"):
        method = getattr(self.execution, "retry", None)
        self.assertTrue(callable(method), "Actual bounded role retry is absent")
        with patch.object(ClaudeOperationAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}):
            return method(self.owner, row["operation_id"], expected_generation=row["generation"], event_key=key)

    def test_capacity_retry_has_new_actual_call_same_input_and_original_deadline(self):
        first = self.capacity_failure()
        retried = self.retry(first)
        self.assertEqual((retried["status"], retried["attempt"]), ("reserved", 2))
        self.assertEqual(retried["deadline_at"], first["deadline_at"])
        self.assertEqual(retried["request_hash"], first["request_hash"])
        old, new = retried["attempts"]
        self.assertNotEqual(old["call_id"], new["call_id"])
        self.assertEqual(self.execution.ledger.get(old["call_id"])["status"], "refunded")
        call = self.execution.ledger.get(new["call_id"])
        self.assertEqual(call["reason_ref"], "improvement_operation:" + first["operation_id"] + ":attempt:2")
        self.assertIsNone(call["engine_at"])

    def test_second_capacity_failure_cannot_create_a_third_attempt(self):
        first = self.capacity_failure()
        self.row = self.retry(first)
        second = self.capacity_failure()
        with self.assertRaises(IM.ConflictError):
            self.retry(second, "forbidden-third-role-attempt")
        self.assertEqual(len(self.execution.get(self.owner, first["operation_id"])["attempts"]), 2)

    def test_retry_cannot_cross_a_still_live_old_lifetime(self):
        first = self.capacity_failure()
        handles = self.execution.workspace.acquire_execution(self.owner.brain_id, first["operation_id"], 1)
        try:
            with self.assertRaises(IM.ConflictError):
                self.retry(first)
            self.assertEqual(len(self.execution.get(self.owner, first["operation_id"])["attempts"]), 1)
        finally:
            self.execution.workspace.release_execution(handles)

    def test_off_blocks_retry_but_historical_retry_replay_does_not_regrant(self):
        first = self.capacity_failure()
        retried = self.retry(first)
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-after-infra-retry")
        with patch.object(self.f.f.host, "_snapshot_observe", side_effect=AssertionError("Historical retry probed source")), \
             patch.object(ClaudeOperationAdapter, "verify", side_effect=AssertionError("Historical retry probed native")):
            replay = self.execution.retry(self.owner, first["operation_id"], expected_generation=first["generation"],
                                          event_key="owner-retry-role-infra")
        self.assertEqual(replay, retried)

    def test_completed_product_is_never_a_retryable_answer(self):
        product = P.OperationResultTests()
        product.setUp()
        self.addCleanup(product.doCleanups)
        result = product.products()
        product.trace(content='{"patch":"syntactically invalid proposal; never rerun until it wins"}')
        completed = product.finish(result)
        method = getattr(product.execution, "retry", None)
        self.assertTrue(callable(method), "Actual bounded role retry is absent")
        with patch.object(ClaudeOperationAdapter, "verify", side_effect=AssertionError("Wrong answer triggered native retry")), \
                self.assertRaises(IM.ConflictError):
            method(product.owner, completed["operation_id"], expected_generation=completed["generation"], event_key="forbidden-better-answer-retry")

    def test_owner_cancel_of_interrupted_slot_is_a_durable_fence_before_retry(self):
        first = self.capacity_failure()
        stopped = self.execution.cancel(self.owner, first["operation_id"], expected_generation=first["generation"],
                                        event_key="owner-fence-before-infra-retry")
        self.assertEqual(stopped["status"], "cancelled")
        with self.assertRaises(IM.ConflictError):
            self.retry(stopped)
        self.assertEqual(len(self.execution.get(self.owner, first["operation_id"])["attempts"]), 1)

    def test_reason_string_without_native_error_evidence_cannot_retry_a_model_artifact(self):
        product = P.OperationResultTests()
        product.setUp()
        self.addCleanup(product.doCleanups)
        product.trace(content='{"type":"error","status_code":503,"retryable":true}')
        row = product.execution.abort(product.owner, product.row["operation_id"], expected_generation=product.row["generation"],
                                      lease_token=product.token, reason="native_transient_error")
        with patch.object(ClaudeOperationAdapter, "verify", side_effect=AssertionError("Artifact text triggered native retry")), \
                self.assertRaises(IM.ConflictError):
            product.execution.retry(product.owner, row["operation_id"], expected_generation=row["generation"],
                                    event_key="forbidden-artifact-retry-authority")

    def test_raw_terminal_update_cannot_invent_an_owner_retry_cancellation(self):
        first = self.capacity_failure()
        with self.f.store._Tx(self.f.store) as connection, self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE improvement_operations SET status='cancelled',generation=generation+1 WHERE id=?",
                               (first["operation_id"],))

    def test_off_before_retry_never_reserves_or_probes_a_new_native_attempt(self):
        first = self.capacity_failure()
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-before-first-infra-retry")
        with patch.object(ClaudeOperationAdapter, "verify", side_effect=AssertionError("Off retry probed native")), \
                self.assertRaises(PermissionError):
            self.execution.retry(self.owner, first["operation_id"], expected_generation=first["generation"], event_key="forbidden-retry-after-off")
        self.assertEqual(len(self.execution.get(self.owner, first["operation_id"])["attempts"]), 1)

    def test_retry_cannot_extend_an_expired_first_attempt_deadline(self):
        first = self.capacity_failure()
        with patch("improvement_operation_retry.time.time", return_value=first["deadline_at"] + 1), \
                patch.object(ClaudeOperationAdapter, "verify", side_effect=AssertionError("Expired retry probed native")), \
                self.assertRaises(IM.ConflictError):
            self.execution.retry(self.owner, first["operation_id"], expected_generation=first["generation"], event_key="forbidden-expired-infra-retry")
        self.assertEqual(len(self.execution.get(self.owner, first["operation_id"])["attempts"]), 1)


class NativeRetryClassificationTests(unittest.TestCase):
    def test_only_actual_native_transient_events_can_support_retry(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_operation_retry"), "Host retry classifier is absent")
        from improvement_operation_retry import native_transient
        self.assertTrue(native_transient({"type": "error", "status_code": 503}))
        self.assertTrue(native_transient({"type": "terminal", "is_error": True, "api_error_status": 503}))
        self.assertTrue(native_transient({"type": "error", "error_type": "APITimeoutError"}))
        for value in ({"type": "artifact", "content": '{"type":"error","status_code":503}'},
                      {"type": "terminal", "is_error": False, "api_error_status": 503},
                      {"type": "error", "status_code": 400}, {"type": "error", "status_code": True},
                      {"type": "tool_result", "is_error": True, "status_code": 503},
                      {"type": "usage", "status_code": 503}):
            with self.subTest(value=value):
                self.assertFalse(native_transient(value))


if __name__ == "__main__":
    unittest.main()
