"""Host evaluation and call settlement remain atomic, without any model execution."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_improvement_trials as fixtures
import improvement_store as IM
import improvement_evaluation as EVAL

if importlib.util.find_spec("improvement_trial_results"):
    import improvement_trial_results as RESULTS
else:
    RESULTS = None


class TrialResultTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(RESULTS, "Host trial finalization is not implemented")
        self.f = fixtures.TrialStoreTests()
        self.f.setUp()
        self.results = RESULTS.TrialResults(self.f.trials)
        self.usage = {"experiment_id": "experiment-1", "trial_id": None,
                      "coverage": {"status": "partial"}, "requests": None}
        self.usage_patch = patch("improvement_usage.trial_usage", side_effect=self.usage_snapshot)
        self.usage_patch.start()
        self.addCleanup(self.usage_patch.stop)

    def usage_snapshot(self, experiment, trial):
        return {**copy.deepcopy(self.usage), "experiment_id": experiment, "trial_id": trial}

    def staged(self, *, supported=False):
        if supported:
            contract = self.f.cases.get_contract(self.f.owner, self.contract_id())["contract"]
            contract["evaluator"] = dict(EVAL.EVALUATOR_PIN)
            pinned = self.f.cases.put_contract(self.f.owner, contract, event_key="host-evaluator-contract")
            self.f.request["pins"]["contract_hash"] = pinned["manifest_hash"]
        trial, token, raw, _ = self.f.product_trace()
        trial = self.f.trials.stage_evaluation(self.f.owner, trial["trial_id"],
            expected_generation=trial["generation"], lease_token=token)
        return trial, token, raw

    def contract_id(self):
        with self.f.rs._conn() as connection:
            return connection.execute("SELECT id FROM improvement_contracts WHERE manifest_hash=?",
                                      (self.f.request["pins"]["contract_hash"],)).fetchone()[0]

    def guard(self):
        request = self.f.request
        return {"scope_hash": request["pins"]["scope_hash"], "grant_hash": request["pins"]["grant_hash"],
                "engine_config_hash": request["pins"]["engine_config_hash"],
                "baseline_context_hash": request["baseline_context_hash"],
                "target_generations": request["target_generations"]}

    def finish(self, trial, token, **changes):
        return self.results.finish(self.f.owner, trial["trial_id"], expected_generation=trial["generation"],
            lease_token=token, capability_hash="d" * 64, live_guard=changes.pop("live_guard", self.guard),
            checker_registry={}, **changes)

    def test_terminal_execution_retains_failed_receipt_and_unknown_evidence_in_encrypted_result(self):
        trial, token, raw = self.staged()
        current = self.finish(trial, token)
        self.assertEqual(current["status"], "completed")
        report = self.results.read(self.f.owner, trial["trial_id"], 1)
        self.assertEqual(report["verdict"], "fail")  # The synthetic storage receipt is deliberately incomplete.
        self.assertEqual(report["evaluation"]["reason"], "unsupported_evaluator")
        self.assertEqual(report["artifact_hash"], fixtures.fixtures.digest(raw.decode()))
        self.assertEqual(report["usage"]["coverage"]["status"], "partial")
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"])["status"], "ok")
        self.assertTrue(any(row["verdict"] == "unknown" for row in report["mandatory"]))
        self.assertEqual(next(row for row in report["mandatory"] if row["id"] == "actual_reasoning")["verdict"], "unknown")
        with self.f.rs._conn() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM improvement_trial_results")
            stored = connection.execute("SELECT result_json FROM improvement_trial_attempts").fetchone()[0]
            self.assertNotIn("unsupported_evaluator", stored)
        with self.assertRaises(PermissionError):
            self.results.read(self.f.other, trial["trial_id"], 1)

    def test_guard_is_mandatory_and_rechecked_after_host_evaluation(self):
        trial, token, _ = self.staged()
        with self.assertRaises((ValueError, PermissionError)):
            self.finish(trial, token, live_guard=None)
        calls = []

        def revoked():
            calls.append(1)
            return self.guard() if len(calls) == 1 else {**self.guard(), "grant_hash": "f" * 64}

        with self.assertRaises(IM.ConflictError):
            self.finish(trial, token, live_guard=revoked)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"])["status"], "running")

    def test_result_and_call_settlement_roll_back_together(self):
        trial, token, _ = self.staged()
        with self.f.rs._conn() as connection:
            connection.execute("CREATE TRIGGER injected_result_failure BEFORE UPDATE ON improvement_trial_attempts "
                               "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(trial, token)
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"])["status"], "running")
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_results").fetchone()[0], 0)
            connection.execute("DROP TRIGGER injected_result_failure")
        self.assertEqual(self.finish(trial, token)["status"], "completed")

    def test_changed_output_or_stale_lease_cannot_publish_a_result(self):
        trial, token, _ = self.staged()
        with self.assertRaises(IM.ConflictError):
            self.finish(trial, "forged-token")
        output = self.f.trials.workspace.root / trial["trial_id"] / "attempt-1" / "outputs" / "answer.json"
        output.write_bytes(b"owner changed this output")
        with self.assertRaises(IM.ConflictError):
            self.finish(trial, token)
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"])["status"], "running")

    def test_supported_evaluator_does_not_accept_owner_decisions_from_private_answers(self):
        trial, token, _ = self.staged(supported=True)
        self.finish(trial, token)
        report = self.results.read(self.f.owner, trial["trial_id"], 1)
        self.assertEqual(report["evaluation"]["mandatory_verdict"], "pass")
        self.assertEqual(report["evaluation"]["business_verdict"], "unknown")
        self.assertEqual(report["verdict"], "fail")
        self.assertNotIn("private-answer", json.dumps(report))

    def test_output_changed_during_evaluation_cannot_be_committed(self):
        trial, token, _ = self.staged(supported=True)
        output = self.f.trials.workspace.root / trial["trial_id"] / "attempt-1" / "outputs" / "answer.json"
        original = EVAL.evaluate_trial

        def checker_changed_output(*args, **kwargs):
            result = original(*args, **kwargs)
            output.write_bytes(b"changed while the checker ran")
            return result

        with patch.object(EVAL, "evaluate_trial", side_effect=checker_changed_output), self.assertRaises(IM.ConflictError):
            self.finish(trial, token)
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_results").fetchone()[0], 0)

    def test_cancellation_during_evaluation_preserves_cancelled_counted_call(self):
        trial, token, _ = self.staged()
        original = EVAL.evaluate_trial

        def cancel_while_evaluating(*args, **kwargs):
            self.f.trials.cancel(self.f.owner, trial["trial_id"], expected_generation=trial["generation"],
                                 event_key="cancel-during-host-check")
            return original(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=cancel_while_evaluating), self.assertRaises(IM.ConflictError):
            self.finish(trial, token)
        self.assertEqual(self.f.trials.get(self.f.owner, trial["trial_id"])["status"], "cancelled")
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"])["status"], "interrupted")

    def test_policy_revocation_during_checker_prevents_publishing(self):
        trial, token, _ = self.staged()
        original = EVAL.evaluate_trial

        def revoke_while_evaluating(*args, **kwargs):
            self.f.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="revoke-during-host-check")
            return original(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=revoke_while_evaluating), self.assertRaises(PermissionError):
            self.finish(trial, token)
        self.assertEqual(self.f.trials.get(self.f.owner, trial["trial_id"])["status"], "evaluating")

    def test_evaluating_trace_is_frozen_before_unlocked_checker_runs(self):
        trial, token, _ = self.staged()
        original = EVAL.evaluate_trial

        def append_during_checker(*args, **kwargs):
            with self.f.rs._conn() as connection:
                sequence = connection.execute("SELECT MAX(sequence)+1 FROM improvement_trial_observations").fetchone()[0]
            packet = {"version": 1, "sequence": sequence, "type": "worker_error",
                      "reason": "injected_after_complete", "error_type": "RuntimeError"}
            with self.assertRaises(IM.ConflictError):
                self.f.trials.append_observation(self.f.owner, trial["trial_id"], packet,
                    expected_generation=trial["generation"], lease_token=token)
            return original(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=append_during_checker):
            self.assertEqual(self.finish(trial, token)["status"], "completed")

    def test_owner_recheck_appends_history_without_new_query_or_changing_execution_facts(self):
        trial, token, _ = self.staged(supported=True)
        completed = self.finish(trial, token)
        original = self.results.read(self.f.owner, trial["trial_id"], 1)
        with self.f.rs._conn() as connection:
            first_id = connection.execute("SELECT id FROM improvement_trial_results").fetchone()[0]
        decision = self.f.trials.record_owner_decision(self.f.owner, trial["trial_id"], 1, "content", "pass",
            output_hash=original["artifact_hash"], contract_hash=original["contract_hash"],
            expected_generation=completed["generation"], event_key="confirm-exact-trial-output")
        call_before = self.f.ledger.get(trial["attempts"][0]["call_id"])
        options = {"expected_generation": decision["generation"], "event_key": "recheck-owner-accepted-output", "checker_registry": {}}
        updated = self.results.reevaluate(self.f.owner, trial["trial_id"], 1, **options)
        self.assertEqual(updated, self.results.reevaluate(self.f.owner, trial["trial_id"], 1, **options))
        latest = self.results.read(self.f.owner, trial["trial_id"], 1)
        self.assertEqual(latest["evaluation"]["business_verdict"], "pass")
        self.assertEqual(latest["mandatory"], original["mandatory"])
        self.assertEqual(latest["execution_status"], original["execution_status"])
        self.assertEqual(latest["previous_result_id"], first_id)
        self.assertEqual(self.results.read(self.f.owner, trial["trial_id"], 1, result_id=first_id), original)
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"]), call_before)
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_results").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0], 1)

    def test_new_owner_decision_during_recheck_invalidates_publication(self):
        trial, token, _ = self.staged(supported=True)
        completed = self.finish(trial, token)
        original = self.results.read(self.f.owner, trial["trial_id"], 1)
        evaluate = EVAL.evaluate_trial

        def new_decision(*args, **kwargs):
            self.f.trials.record_owner_decision(self.f.owner, trial["trial_id"], 1, "content", "fail",
                output_hash=original["artifact_hash"], contract_hash=original["contract_hash"],
                expected_generation=completed["generation"], event_key="decision-during-recheck")
            return evaluate(*args, **kwargs)

        with patch.object(EVAL, "evaluate_trial", side_effect=new_decision), self.assertRaises(IM.ConflictError):
            self.results.reevaluate(self.f.owner, trial["trial_id"], 1, expected_generation=completed["generation"],
                                    event_key="recheck-stale-owner-history", checker_registry={})
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_results").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
