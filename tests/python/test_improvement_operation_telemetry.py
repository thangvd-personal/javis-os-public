"""Real operation/call rows and usage storage; synthetic engine markers/events only."""
from _paths import ROOT, SERVER  # noqa: F401

import unittest
from contextlib import ExitStack, closing
from unittest.mock import patch

from agent_ledger import CallLedger
from improvement_telemetry import NativeUsageRecorder
import usage_store as US
import usage_index as UI
import improvement_usage as IU
import test_improvement_operation_calls as fixtures
from test_improvement_operation_usage import execution


class OperationTelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OperationCallTests.setUpClass()

    def setUp(self):
        self.f = fixtures.OperationCallTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.reserve()
        self.owner, self.store, self.operation = self.f.owner, self.f.store, self.f.operation
        self.root = self.store.path.parent
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for obj, name, value in ((US, "_PATH", self.root / "usage.json"), (US, "_EVENTS_PATH", self.root / "events.jsonl"),
                (US, "_TELEMETRY_ERRORS", {}), (UI, "_EVENTS_PATH", self.root / "events.jsonl"),
                (UI, "DB_PATH", self.root / "usage-index.db"), (UI, "_claude_dir", lambda: str(self.root / "empty-native")),
                (UI, "_codex_dir", lambda: str(self.root / "empty-codex"))):
            self.stack.enter_context(patch.object(obj, name, value))
        self.ledger = CallLedger(self.store)
        with closing(self.store._conn()) as connection:
            self.call_id = connection.execute("SELECT call_id FROM improvement_operation_attempts WHERE operation_id=?",
                                              (self.operation["operation_id"],)).fetchone()[0]

    def metadata(self, **changes):
        return execution(self.operation["operation_id"], **{"experiment_id": self.operation["experiment_id"],
                                                          "role": self.operation["role"], **changes})

    def mark(self):
        self.assertTrue(self.ledger.mark_engine(self.call_id, "anthropic-cli", "unpriced-selected-model"))
        marker = self.ledger.get(self.call_id)["engine_at"]
        # Fixture stands for the future native start transaction, never provider readiness.
        with self.store._Tx(self.store) as connection:
            connection.execute("UPDATE improvement_operation_attempts SET status='running',engine_at=? WHERE operation_id=?",
                               (marker, self.operation["operation_id"]))
            connection.execute("UPDATE improvement_operations SET status='prepared',generation=generation+1 WHERE id=?",
                               (self.operation["operation_id"],))
            connection.execute("UPDATE improvement_operations SET status='running',generation=generation+1 WHERE id=?",
                               (self.operation["operation_id"],))

    def recorder(self, **changes):
        return NativeUsageRecorder(self.ledger, changes.pop("call_id", self.call_id), self.metadata(**changes),
            brain_id=self.owner.brain_id, provider="anthropic-cli", model="unpriced-selected-model")

    def test_real_marked_operation_usage_survives_recovery_without_solver_identity(self):
        self.mark()
        recorder = self.recorder()
        event = {"type": "observation", "model": "unpriced-selected-model", "message_id": "operation-request-1",
                 "session_id": "operation-native-session", "usage": {"input_tokens": 3, "output_tokens": 2,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
        recorder.observe(event, sequence=1)
        recorder.finish("interrupted")
        recovered = self.recorder()
        recovered.observe(event, sequence=1)
        recovered.finish("interrupted")
        result = IU.operation_usage(self.operation["experiment_id"], self.operation["operation_id"])
        self.assertEqual((result["tokens_in"], result["tokens_out"], result["attempts"]), (3, 2, 1))
        self.assertIsNone(result["requests"])
        self.assertEqual(result["coverage"]["status"], "partial")
        events, _ = UI.improvement_events(self.operation["experiment_id"], operation_id=self.operation["operation_id"])
        self.assertTrue(events)
        self.assertTrue(all("trial_id" not in row["execution"] and "case_id" not in row["execution"] for row in events))
        self.assertEqual(IU.trial_usage(self.operation["experiment_id"])["attempts"], 0)

    def test_unmarked_or_foreign_operation_cannot_persist_native_usage(self):
        with self.assertRaises(ValueError):
            self.recorder()
        self.mark()
        with self.assertRaises(ValueError):
            self.recorder(operation_id="invented-operation", execution_id="improvement_operation:invented-operation")
        with self.assertRaises(ValueError):
            self.recorder(role="semantic_reviewer", purpose="improve_evaluate")
        self.assertFalse((self.root / "events.jsonl").exists())

    def test_marked_generic_call_without_operation_attempt_stays_unobserved(self):
        identifier = "orphan-role-operation"
        ex = execution(identifier, experiment_id=self.operation["experiment_id"])
        call_id = self.ledger.reserve(purpose="improve_propose", reason_ref=ex["execution_id"] + ":attempt:1",
            brain_id=self.owner.brain_id, context_ref="improvement:" + ex["experiment_id"],
            mirror_ref=ex["execution_id"], attempt=1, obligation=True)
        self.assertTrue(self.ledger.mark_engine(call_id, "anthropic-cli", "unpriced-selected-model"))
        with self.assertRaises(ValueError):
            NativeUsageRecorder(self.ledger, call_id, ex, brain_id=self.owner.brain_id,
                                provider="anthropic-cli", model="unpriced-selected-model")
        result = IU.operation_usage(ex["experiment_id"], identifier)
        self.assertEqual((result["attempts"], result["coverage"]["unobserved_attempts"]), (1, 1))
        self.assertIsNone(result["requests"])
        self.assertIsNone(result["tokens_in"])
        self.assertEqual(result["coverage"]["status"], "partial")

    def test_ledger_marker_without_matching_operation_marker_cannot_record(self):
        self.assertTrue(self.ledger.mark_engine(self.call_id, "anthropic-cli", "unpriced-selected-model"))
        with self.assertRaises(ValueError):
            self.recorder()
        self.assertFalse((self.root / "events.jsonl").exists())

    def test_actual_semantic_reviewer_uses_its_real_evaluate_call(self):
        metadata = self.f.f
        review = metadata.operation(metadata.request(kind="semantic", candidate_id=metadata.h.candidate["candidate_id"]),
                                    key="create-real-review-operation", generation=3)
        with self.store._Tx(self.store) as connection:
            self.f.records._reserve_attempt_in(connection, self.owner, review["operation_id"], expected_generation=1,
                                               event_key="reserve-real-evaluate-operation")
            self.call_id = connection.execute("SELECT call_id FROM improvement_operation_attempts WHERE operation_id=?",
                                              (review["operation_id"],)).fetchone()[0]
        self.operation = review
        self.mark()
        recorder = self.recorder()
        recorder.observe({"type": "observation", "message_id": "review-request", "session_id": "review-native-session",
                          "usage": {"input_tokens": 11, "output_tokens": 2, "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0}}, sequence=1)
        recorder.finish("interrupted")
        call = self.ledger.get(self.call_id)
        self.assertEqual(call["purpose"], "improve_evaluate")
        result = IU.operation_usage(review["experiment_id"], review["operation_id"])
        self.assertEqual((result["tokens_in"], result["tokens_out"]), (11, 2))
        self.assertEqual(IU.trial_usage(review["experiment_id"])["attempts"], 0)

    def test_usage_after_policy_off_settles_marked_attempt_without_regrant(self):
        self.mark()
        self.assertTrue(self.ledger.settle(self.call_id, "interrupted"))
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-after-native-role-marker")
        recorder = self.recorder()
        recorder.observe({"type": "usage", "session_id": "late-operation-session",
                          "usage": {"input_tokens": 3, "output_tokens": 2}}, sequence=1)
        recorder.finish("interrupted")
        result = IU.operation_usage(self.operation["experiment_id"], self.operation["operation_id"])
        self.assertEqual(result["tokens_out"], 2)
        self.assertIsNone(result["tokens_in"])  # Missing cache components remain unknown.
        self.assertEqual(self.f.assets.get_policy(self.owner), {"mode": "off", "version": 1})

    def test_wrong_marker_arguments_cannot_write_operation_events(self):
        self.mark()
        for changes in ({"brain_id": "foreign-brain"}, {"provider": "other-provider"}, {"model": "other-model"}):
            kwargs = {"brain_id": self.owner.brain_id, "provider": "anthropic-cli", "model": "unpriced-selected-model", **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                NativeUsageRecorder(self.ledger, self.call_id, self.metadata(), **kwargs)
        self.assertFalse((self.root / "events.jsonl").exists())

    def test_failed_event_write_preserves_operation_specific_partial_coverage(self):
        self.mark()
        recorder = self.recorder()
        with patch.object(US, "_append_event", side_effect=OSError("Synthetic usage event write failure")):
            recorder.observe({"type": "observation", "message_id": "lost-operation-request", "session_id": "lost-operation-session",
                "usage": {"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}}, sequence=1)
        recorder.finish("interrupted")
        result = IU.operation_usage(self.operation["experiment_id"], self.operation["operation_id"])
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertIsNone(result["requests"])
        self.assertIsNone(result["tokens_in"])
        self.assertGreater(US.operation_telemetry_errors(self.operation["experiment_id"], self.operation["operation_id"]), 0)
        self.assertEqual(US.improvement_telemetry_errors(self.operation["experiment_id"]), 0)

    def test_recovery_cannot_import_foreign_role_metadata_under_same_operation_id(self):
        self.mark()
        US.record("anthropic-cli", "unpriced-selected-model", 30, 4,
            execution=self.metadata(role="semantic_reviewer", purpose="improve_evaluate", cli_session_id="foreign-role-session",
                                    cli_session_new=True), source_id="improvement-native-v1",
            event_id="foreign-role-request", request_id="foreign-role-request", event_kind="cli_detail")
        with self.assertRaisesRegex(ValueError, "operation_usage_identity_drift"):
            self.recorder()


if __name__ == "__main__":
    unittest.main()
