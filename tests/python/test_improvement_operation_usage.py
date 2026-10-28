"""Genuine role identity uses the existing turn/ledger/usage pipeline, without Trials."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import unittest

import test_improvement_usage as fixtures
from agent_ledger import CallLedger
import resonance_store as RS
import turn_context as TC
import usage_store as US
import usage_index as UI
import improvement_usage as IU


def execution(operation="operation-1", *, role="improver", **changes):
    return {"experiment_id": "experiment-1", "operation_id": operation, "attempt": 1,
            "execution_id": "improvement_operation:" + operation, "role": role,
            "purpose": "improve_evaluate" if role == "semantic_reviewer" else "improve_propose",
            "learning_origin": "compose", **changes}


class OperationMetadataTests(unittest.TestCase):
    def test_host_operation_identity_survives_binding_threads_and_key_resolution(self):
        for role in ("improver", "semantic_reviewer"):
            with self.subTest(role=role):
                expected = execution(role=role)
                self.assertEqual(TC.execution_metadata(expected), expected)
                token = TC.bind(TC.make("improvement", execution=expected))
                try:
                    key = TC.issue_key()
                    self.assertEqual(TC.resolve_key(key)["execution"], expected)

                    async def read():
                        return await asyncio.to_thread(TC.current)

                    copied = asyncio.run(read())
                    self.assertEqual(copied["execution"], expected)
                    copied["execution"]["operation_id"] = "forged-operation"
                    self.assertEqual(TC.current()["execution"], expected)
                finally:
                    TC.reset(token)
                self.assertIsNone(TC.resolve_key(key))

    def test_malformed_operation_cannot_fall_through_as_a_team_or_solver_identity(self):
        self.assertIsNone(TC.execution_metadata({"operation_id": None, "team_id": "team-1", "turn_id": "turn-1"}))
        self.assertIsNone(TC.execution_metadata({"operation_id": "", **fixtures.execution()}))

    def test_mixed_fields_roles_purposes_and_untrusted_authority_are_closed(self):
        for changes in ({"trial_id": None}, {"team_id": None}, {"case_id": "invented-case"}, {"repeat": 1},
                        {"task_id": "team-task"}, {"turn_id": "team-turn"}, {"purpose": "improve_probe"},
                        {"role": "solver"}, {"purpose": "improve_evaluate"}, {"attempt": True}, {"attempt": 0}, {"attempt": 3},
                        {"operation_id": "../outside"}, {"execution_id": "improvement_trial:invented"},
                        {"learning_origin": "production"}, {"native_ready": True}, {"cli_session_new": True}):
            with self.subTest(changes=changes):
                self.assertIsNone(TC.execution_metadata(execution(**changes)))

    def test_canonical_native_session_identity_is_retained_without_case_or_trial(self):
        expected = execution(cli_session_id="actual-session-1", cli_session_new=True)
        self.assertEqual(TC.execution_metadata(expected), expected)
        self.assertIsNone(TC.execution_metadata(execution(cli_session_id="../foreign", cli_session_new=True)))


class _UsageFixture(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ImprovementUsageTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root


class OperationPurposeTests(_UsageFixture):
    def test_existing_callledger_reserves_distinct_real_role_purposes(self):
        store = RS.GoalStore(self.root / "resonance.sqlite3")
        ledger = CallLedger(store)
        for role in ("improver", "semantic_reviewer"):
            value = execution(operation=role + "-operation", role=role)
            call_id = ledger.reserve(purpose=value["purpose"], brain_id="brain-1", context_ref="improvement:experiment-1",
                reason_ref=value["execution_id"] + ":attempt:1", mirror_ref=value["execution_id"], attempt=1, obligation=True)
            call = ledger.get(call_id)
            self.assertEqual((call["purpose"], call["mirror_ref"], call["status"]),
                             (value["purpose"], value["execution_id"], "reserved"))
            self.assertTrue(ledger.refund(call_id))


class OperationIndexTests(_UsageFixture):
    def record(self, operation, tin, tout, *, role="improver"):
        US.record("anthropic-cli", "unpriced-selected-model", tin, tout, execution=execution(operation, role=role),
                  source_id="improvement-native-v1", event_id="request:" + operation, request_id="request:" + operation,
                  event_kind="cli_detail")

    def test_operation_projection_is_separate_from_solver_and_other_role_with_same_attempt(self):
        self.record("proposal-1", 30, 4)
        self.record("review-1", 11, 2, role="semantic_reviewer")
        US.record("anthropic-cli", "unpriced-selected-model", 7, 1, execution=fixtures.execution(),
                  source_id="improvement-native-v1", event_id="solver-request", request_id="solver-request", event_kind="cli_detail")
        events, _ = UI.improvement_events("experiment-1", operation_id="proposal-1")
        self.assertEqual([(row["execution"]["operation_id"], row["in"], row["out"]) for row in events], [("proposal-1", 30, 4)])
        events, _ = UI.improvement_events("experiment-1", operation_id="review-1")
        self.assertEqual([(row["execution"]["role"], row["in"]) for row in events], [("semantic_reviewer", 11)])
        legacy, _ = UI.improvement_events("experiment-1")
        self.assertEqual([row["execution"]["trial_id"] for row in legacy], ["trial-1"])
        with self.assertRaises(ValueError):
            UI.improvement_events("experiment-1", "trial-1", operation_id="proposal-1")

    def test_operation_usage_keeps_actual_replayed_requests_and_unknown_coverage(self):
        view = getattr(IU, "operation_usage", None)
        self.assertTrue(callable(view), "Actual per-operation usage is absent")
        self.record("proposal-1", 30, 4)
        self.record("proposal-1", 30, 4)
        self.record("review-1", 11, 2, role="semantic_reviewer")
        result = view("experiment-1", "proposal-1")
        self.assertEqual((result["tokens_in"], result["tokens_out"], result["attempts"]), (30, 4, 1))
        self.assertEqual(result["operation_id"], "proposal-1")
        self.assertIsNone(result["money"]["reported_usd"])
        self.assertEqual((IU.trial_usage("experiment-1")["tokens_in"], IU.trial_usage("experiment-1")["attempts"]), (None, 0))

    def test_operation_write_failure_is_not_lost_or_charged_to_trial_coverage(self):
        view = getattr(US, "operation_telemetry_errors", None)
        self.assertTrue(callable(view), "Operation-specific telemetry failure signal is absent")
        US.note_telemetry_error(execution("proposal-1"))
        US.note_telemetry_error(execution("review-1", role="semantic_reviewer"))
        self.assertEqual(view("experiment-1", "proposal-1"), 1)
        self.assertEqual(view("experiment-1", "review-1"), 1)
        self.assertEqual(US.improvement_telemetry_errors("experiment-1"), 0)


if __name__ == "__main__":
    unittest.main()
