"""Synthetic owner decisions bind protected trial products; no engines or business evidence."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import test_improvement_trials as fixtures
import improvement_evaluation as IE
import improvement_store as IM
from improvement_blobs import AssetUnavailable
from resonance_store import Principal

if importlib.util.find_spec("improvement_trial_decisions"):
    import improvement_trial_decisions as decisions_module
else:
    decisions_module = None


class OwnerDecisionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(decisions_module, "Protected owner decision history is not implemented")
        self.fixture = fixtures.TrialStoreTests()
        self.fixture.setUp()
        self.trials, self.rs = self.fixture.trials, self.fixture.rs
        self.owner, self.other = self.fixture.owner, self.fixture.other
        self.decisions = decisions_module.OwnerDecisionStore(self.trials)

    def terminal(self):
        trial, token, raw, _ = self.fixture.product_trace()
        staged = self.trials.stage_evaluation(self.owner, trial["trial_id"],
            expected_generation=trial["generation"], lease_token=token)
        self.fixture.ledger.settle(staged["attempts"][0]["call_id"], "ok")
        # Synthetic host fault injection until finish is integrated. This is not a model run.
        with self.rs._Tx(self.rs) as connection:
            connection.execute("UPDATE improvement_trials SET status='completed',generation=generation+1 WHERE id=?",
                               (trial["trial_id"],))
            connection.execute("UPDATE improvement_trial_attempts SET status='completed',lease_token=NULL,lease_until=NULL "
                               "WHERE trial_id=? AND attempt=1", (trial["trial_id"],))
        self.trial = self.trials.get(self.owner, trial["trial_id"])
        self.output_hash = fixtures.fixtures.digest(raw.decode("utf-8"))
        return self.trial

    def record(self, *, principal=None, key="owner-decision-0001", **changes):
        fields = {"attempt": 1, "criterion_id": "content", "verdict": "pass", "output_hash": self.output_hash,
                  "contract_hash": self.fixture.request["pins"]["contract_hash"],
                  "expected_generation": self.trial["generation"], **changes}
        return self.decisions.record(principal or self.owner, self.trial["trial_id"], event_key=key, **fields)

    def count(self):
        with self.rs._conn() as connection:
            return connection.execute("SELECT COUNT(*) FROM improvement_trial_owner_decisions").fetchone()[0]

    def test_exact_product_decision_is_typed_and_does_not_change_execution_or_ledger(self):
        trial = self.terminal()
        calls = self.fixture.ledger.rows()
        targets = self.fixture.assets.targets(self.owner)
        result = self.record()
        self.assertEqual(result["actor"], self.owner.by)
        self.assertEqual(result["generation"], trial["generation"] + 1)
        self.assertEqual(result["case_id"], "case-1")
        fields_hash = self.fixture.request["pins"]["contract_hash"]
        self.assertEqual(self.decisions.decisions(self.owner, trial["trial_id"], 1),
            (IE.OwnerDecision("case-1", "content", self.output_hash, fields_hash, "pass"),))
        self.assertEqual(result["contract_hash"], fields_hash)
        current = self.trials.get(self.owner, trial["trial_id"])
        self.assertEqual(current["status"], "completed")
        self.assertEqual(current["attempts"], trial["attempts"])
        self.assertEqual(self.fixture.ledger.rows(), calls)
        self.assertEqual(self.fixture.assets.targets(self.owner), targets)

    def test_foreign_brain_agent_and_fabricated_owner_cannot_read_or_record(self):
        self.terminal()
        for principal in (self.other, Principal("agent", "owner", self.owner.brain_id),
                          SimpleNamespace(kind="owner", id="owner", brain_id=self.owner.brain_id, by="owner:forged")):
            with self.subTest(principal=type(principal).__name__), self.assertRaises(PermissionError):
                self.record(principal=principal)
            with self.assertRaises(PermissionError):
                self.decisions.decisions(principal, self.trial["trial_id"], 1)
        self.assertEqual(self.count(), 0)

    def test_stale_pins_wrong_criterion_and_invalid_identity_leave_no_history(self):
        self.terminal()
        changes = ({"output_hash": "0" * 64}, {"contract_hash": "0" * 64},
                   {"criterion_id": "shape"}, {"criterion_id": "missing"},
                   {"expected_generation": self.trial["generation"] - 1}, {"expected_generation": True},
                   {"attempt": True}, {"attempt": 2}, {"verdict": "approved"}, {"output_hash": "bad"})
        for index, changed in enumerate(changes):
            with self.subTest(changed=changed), self.assertRaises((ValueError, PermissionError)):
                self.record(key=f"invalid-decision-{index:04d}", **changed)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.trials.get(self.owner, self.trial["trial_id"])["generation"], self.trial["generation"])

    def test_live_evaluation_and_nonterminal_attempt_cannot_receive_owner_decisions(self):
        self.terminal()
        for table, status in (("improvement_trials", "evaluating"), ("improvement_trial_attempts", "running")):
            with self.rs._Tx(self.rs) as connection:
                connection.execute(f"UPDATE {table} SET status=? WHERE " +
                    ("id=?" if table == "improvement_trials" else "trial_id=?"), (status, self.trial["trial_id"]))
            with self.assertRaises(IM.ConflictError):
                self.record()
            with self.rs._Tx(self.rs) as connection:
                connection.execute(f"UPDATE {table} SET status='completed' WHERE " +
                    ("id=?" if table == "improvement_trials" else "trial_id=?"), (self.trial["trial_id"],))
        self.assertEqual(self.count(), 0)

    def test_cancelled_without_artifact_has_no_confirmable_product(self):
        self.trial = self.fixture.reserve()
        self.trial = self.trials.cancel(self.owner, self.trial["trial_id"],
            expected_generation=self.trial["generation"], event_key="cancel-without-product")
        self.output_hash = "0" * 64
        with self.assertRaises(PermissionError):
            self.record()

    def test_corrupt_blob_cannot_be_confirmed_from_the_metadata_hash_alone(self):
        self.terminal()
        path = self.trials.blobs._path(self.owner.brain_id, self.output_hash)
        other = self.trials.blobs.write(self.owner.brain_id, b"Synthetic different product")
        path.write_bytes(self.trials.blobs._path(self.owner.brain_id, other).read_bytes())
        with self.assertRaises(AssetUnavailable):
            self.record()
        self.assertEqual(self.count(), 0)

    def test_append_only_integer_history_controls_latest_decision(self):
        self.terminal()
        expected = []
        for index in range(12):
            verdict = ("pass", "fail", "unknown")[index % 3]
            result = self.record(key=f"decision-history-{index:04d}", verdict=verdict,
                                 expected_generation=self.trial["generation"] + index)
            expected.append(verdict)
            self.assertEqual(result["generation"], self.trial["generation"] + index + 1)
        history = self.decisions.decisions(self.owner, self.trial["trial_id"], 1)
        self.assertEqual([item.verdict for item in history], expected)
        with self.rs._Tx(self.rs) as connection:
            for statement in ("UPDATE improvement_trial_owner_decisions SET verdict='pass' WHERE id=1",
                              "DELETE FROM improvement_trial_owner_decisions WHERE id=1"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)

    def test_replay_is_stable_after_later_decisions_and_conflicting_payload_is_rejected(self):
        self.terminal()
        first = self.record()
        self.record(key="later-owner-decision", verdict="fail", expected_generation=first["generation"])
        self.assertEqual(self.record(), first)
        with self.assertRaises(IM.ConflictError):
            self.record(verdict="fail")
        self.assertEqual(self.count(), 2)

    def test_parallel_identical_replays_create_exactly_one_decision(self):
        self.terminal()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.record(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count(), 1)

    def test_parallel_distinct_decisions_on_one_generation_have_one_winner(self):
        self.terminal()

        def submit(index):
            try:
                return self.record(key=f"parallel-decision-{index:04d}")
            except IM.ConflictError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(results.count("stale"), 1)
        self.assertEqual(self.count(), 1)

    def test_event_failure_rolls_back_decision_and_generation_together(self):
        self.terminal()
        with self.rs._conn() as connection:
            connection.execute("CREATE TRIGGER fail_owner_decision_event BEFORE INSERT ON improvement_events "
                "WHEN NEW.operation='trial_owner_decision' BEGIN SELECT RAISE(ABORT,'synthetic_failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.record()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.trials.get(self.owner, self.trial["trial_id"])["generation"], self.trial["generation"])

    def test_generation_update_must_affect_exactly_one_row_or_history_rolls_back(self):
        self.terminal()
        with self.rs._conn() as connection:
            connection.execute("CREATE TRIGGER ignore_owner_generation BEFORE UPDATE OF generation ON improvement_trials "
                               "BEGIN SELECT RAISE(IGNORE); END")
        with self.assertRaises(IM.ConflictError):
            self.record()
        self.assertEqual(self.count(), 0)

    def test_history_and_original_replay_survive_missing_blob_without_fabricating_new_evidence(self):
        self.terminal()
        first = self.record()
        self.trials.blobs._path(self.owner.brain_id, self.output_hash).unlink()
        self.assertEqual(self.record(), first)
        history = self.decisions.decisions(self.owner, self.trial["trial_id"], 1)
        self.assertEqual((history[0].output_hash, history[0].verdict), (self.output_hash, "pass"))
        with self.assertRaises(AssetUnavailable):
            self.record(key="new-decision-no-blob", expected_generation=first["generation"])
        self.assertEqual(self.count(), 1)

    def test_absent_schema_reads_and_record_attempts_do_not_initialize_it(self):
        from improvement_blobs import RevisionBlobs
        from resonance_store import GoalStore
        database = GoalStore(self.fixture.root / "fresh.sqlite3")
        assets = IM.ImprovementStore(database, RevisionBlobs(self.fixture.root / "fresh-assets"))
        service = decisions_module.OwnerDecisionStore(type(self.trials)(assets))
        with self.assertRaises(PermissionError):
            service.decisions(self.owner, "missing-trial", 1)
        with self.assertRaises(PermissionError):
            service.record(self.owner, "missing-trial", 1, "content", "pass", output_hash="0" * 64,
                contract_hash="0" * 64, expected_generation=1, event_key="missing-schema-decision")
        self.assertEqual(IM._schema_version(database), 0)

    def test_actor_is_the_authenticated_owner_even_when_another_owner_appends(self):
        self.terminal()
        actual = Principal("owner", "second-owner", self.owner.brain_id)
        result = self.record(principal=actual)
        self.assertEqual(result["actor"], "owner:second-owner")
        with self.rs._conn() as connection:
            row = connection.execute("SELECT actor FROM improvement_trial_owner_decisions").fetchone()
            event = connection.execute("SELECT actor FROM improvement_events WHERE operation='trial_owner_decision'").fetchone()
        self.assertEqual(row[0], actual.by)
        self.assertEqual(event[0], actual.by)

    def test_model_answer_and_result_claims_never_become_owner_decisions(self):
        with self.rs._conn() as connection:
            case = self.fixture.cases._case(connection, self.owner.brain_id,
                self.fixture.request["case_set_id"], self.fixture.request["case_id"])
            snapshot = case["snapshot_ids"][0]
        forged = {"case_id": "forged-case", "criterion_id": "content", "output_hash": "0" * 64,
                  "contract_hash": self.fixture.request["pins"]["contract_hash"], "verdict": "pass", "actor": "owner"}
        case_set = self.fixture.cases.put_case_set(self.owner, [{"id": "forged-case", "family_key": "forged-family",
            "split": "tuning", "task": "Synthetic untrusted owner claim", "snapshot_ids": [snapshot],
            "answer": {"owner_decisions": [forged]}, "evaluator_refs": []}], 9, event_key="forged-answer-case-set")
        self.fixture.request["case_set_id"] = case_set["case_set_id"]
        self.fixture.request["case_id"] = "forged-case"
        self.fixture.request["pins"]["case_set_hash"] = case_set["manifest_hash"]
        self.terminal()
        with self.rs._Tx(self.rs) as connection:
            connection.execute("UPDATE improvement_trial_attempts SET result_json=? WHERE trial_id=?",
                               (json.dumps({"owner_decisions": [forged], "actor": "owner"}), self.trial["trial_id"]))
        self.assertEqual(self.decisions.decisions(self.owner, self.trial["trial_id"], 1), ())
        self.record()
        self.assertEqual(self.decisions.decisions(self.owner, self.trial["trial_id"], 1)[0].case_id, "forged-case")


if __name__ == "__main__":
    unittest.main()
