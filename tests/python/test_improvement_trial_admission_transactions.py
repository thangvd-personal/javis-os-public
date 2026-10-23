"""Qualification, trial, ledger and events share one actual owner admission transaction."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="at-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                  "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources", "JAVIS_SESSIONS_DB": "sessions.sqlite3"}.items():
    os.environ[key] = str(TEST_ROOT / name)

import test_improvement_trials as fixtures
import improvement_store as IM
from resonance_store import GoalStore


class AdmissionTransactionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.TrialStoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.assertTrue(callable(getattr(self.f.cases, "_reserve_qualification_in", None)), "Qualification transaction helper is missing")
        self.assertTrue(callable(getattr(self.f.trials, "_reserve_in", None)), "Trial transaction helper is missing")
        self.assertTrue(callable(getattr(self.f.trials, "_recover_in", None)), "Recovery transaction helper is missing")
        self.request = copy.deepcopy(self.f.request)
        self.request["case_id"] = "case-2"
        self.addCleanup(patch.stopall)
        patch("subprocess.Popen", side_effect=AssertionError("No native/model calls permitted")).start()
        patch("subprocess.run", side_effect=AssertionError("No native/model calls permitted")).start()

    def admit(self, connection, *, request=None):
        request = copy.deepcopy(self.request if request is None else request)
        qualification = self.f.cases._reserve_qualification_in(connection, self.f.owner, request["case_set_id"],
            request["pins"], event_key="admission-qualification")
        request["qualification_id"] = qualification["reservation_id"]
        trial = self.f.trials._reserve_in(connection, self.f.owner, request, event_key="admission-trial")
        return qualification, trial, request

    def counts(self):
        tables = ("improvement_qualification", "improvement_qualification_claims", "improvement_trials",
                  "improvement_trial_attempts", "agent_calls", "improvement_events")
        with closing(self.f.rs._conn()) as connection:
            return {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables}

    def test_outer_rollback_removes_qualification_trial_ledger_and_both_events(self):
        before = self.counts()
        with self.assertRaisesRegex(RuntimeError, "owner admission rollback"):
            with self.f.rs._Tx(self.f.rs) as connection:
                qualification, trial, _ = self.admit(connection)
                self.assertEqual(qualification["status"], "reserved")
                self.assertEqual(trial["status"], "reserved")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0], 1)
                raise RuntimeError("owner admission rollback")
        self.assertEqual(self.counts(), before)

    def test_committed_helpers_replay_through_unchanged_public_apis(self):
        with self.f.rs._Tx(self.f.rs) as connection:
            qualification, trial, request = self.admit(connection)
        self.assertEqual(self.f.cases.reserve_qualification(self.f.owner, request["case_set_id"], request["pins"],
            event_key="admission-qualification"), qualification)
        self.assertEqual(self.f.trials.reserve(self.f.owner, request, event_key="admission-trial"), trial)
        self.assertEqual(self.f.ledger.get(trial["attempts"][0]["call_id"])["status"], "reserved")
        self.assertIsNone(trial["attempts"][0]["engine_at"])
        self.assertEqual(self.counts()["improvement_trials"], 1)
        self.assertEqual(self.counts()["agent_calls"], 1)
        self.assertEqual(self.counts()["improvement_qualification"], 1)

    def test_capacity_rejection_rolls_back_new_qualification_claims_too(self):
        before = self.counts()
        with patch.object(fixtures.fixtures.RS, "_under_ceiling", return_value=False):
            with self.assertRaisesRegex(IM.ConflictError, "capacity_unavailable"):
                with self.f.rs._Tx(self.f.rs) as connection:
                    self.admit(connection)
        self.assertEqual(self.counts(), before)

    def test_inactive_foreign_and_attached_transactions_cannot_call_either_helper(self):
        def reject(connection, error):
            with self.assertRaisesRegex(ValueError, error):
                self.f.cases._reserve_qualification_in(connection, self.f.owner, self.request["case_set_id"],
                    self.request["pins"], event_key="wrong-transaction-qualification")
            with self.assertRaisesRegex(ValueError, error):
                self.f.trials._reserve_in(connection, self.f.owner, self.f.request, event_key="wrong-transaction-trial")
            with self.assertRaisesRegex(ValueError, error):
                self.f.trials._recover_in(connection, self.f.owner)

        before = self.counts()
        with closing(self.f.rs._conn()) as connection:
            reject(connection, "transaction_required")
        foreign = GoalStore(self.f.root / "foreign-admission.sqlite3")
        with foreign._Tx(foreign) as connection:
            reject(connection, "transaction_mismatch")
        with self.f.rs._Tx(self.f.rs) as connection:
            connection.execute("ATTACH DATABASE ? AS extra", (str(self.f.root / "attached.sqlite3"),))
            reject(connection, "transaction_mismatch")
        self.assertEqual(self.counts(), before)

    def test_repeated_and_consumed_qualification_keep_existing_fingerprint_claims(self):
        with self.f.rs._Tx(self.f.rs) as connection:
            qualification, _, request = self.admit(connection)
        consumed = self.f.cases.consume_qualification(self.f.owner, qualification["reservation_id"], request["pins"], event_key="consume-admitted-qualification")
        with self.f.rs._Tx(self.f.rs) as connection:
            repeated = self.f.cases._reserve_qualification_in(connection, self.f.owner, request["case_set_id"], request["pins"],
                event_key="repeat-consumed-admission")
        self.assertEqual(repeated, consumed)
        with closing(self.f.rs._conn()) as connection:
            manifest = json.loads(connection.execute("SELECT manifest_json FROM improvement_case_sets WHERE id=?", (request["case_set_id"],)).fetchone()[0])
        items = [{key: value for key, value in case.items() if key not in ("answer_hash", "fingerprints")} |
                 {"answer": {"expected": "private-answer-" + case["id"].split("-")[-1]}} for case in manifest["cases"]]
        clone = self.f.cases.put_case_set(self.f.owner, items, 99, event_key="cloned-admitted-case-set")
        pins = {**request["pins"], "case_set_hash": clone["manifest_hash"], "experiment_id": "another-experiment"}
        claims = self.counts()["improvement_qualification_claims"]
        with self.assertRaisesRegex(IM.ConflictError, "family_unavailable"):
            with self.f.rs._Tx(self.f.rs) as connection:
                self.f.cases._reserve_qualification_in(connection, self.f.owner, clone["case_set_id"], pins, event_key="clone-qualification-admission")
        self.assertEqual(self.counts()["improvement_qualification_claims"], claims)
        self.assertEqual(self.counts()["improvement_qualification"], 1)

    def test_outer_recovery_and_owner_event_roll_back_together_without_resuming(self):
        trial = self.f.reserve()
        before = self.counts()
        now = self.f.request["deadline_at"] + 1
        with self.assertRaisesRegex(RuntimeError, "owner recovery rollback"):
            with self.f.rs._Tx(self.f.rs) as connection:
                result = self.f.trials._recover_in(connection, self.f.owner, now=now)
                self.assertEqual(result, {"not_run": 1, "unknown": 0})
                self.f.assets._event(connection, self.f.owner, "owner-recovery-atomic", "owner_trial_recover", {}, result)
                raise RuntimeError("owner recovery rollback")
        self.assertEqual(self.counts(), before)
        current = self.f.trials.get(self.f.owner, trial["trial_id"])
        self.assertEqual(current["status"], "reserved")
        self.assertEqual(self.f.ledger.get(current["attempts"][0]["call_id"])["status"], "reserved")
        with self.f.rs._Tx(self.f.rs) as connection:
            result = self.f.trials._recover_in(connection, self.f.owner, now=now)
            self.f.assets._event(connection, self.f.owner, "owner-recovery-atomic", "owner_trial_recover", {}, result)
        self.assertEqual(self.f.trials.get(self.f.owner, trial["trial_id"])["status"], "not_run")
        self.assertEqual(self.f.ledger.get(current["attempts"][0]["call_id"])["status"], "refunded")
        self.assertEqual(self.f.trials.recover(self.f.owner, now=now), {"not_run": 0, "unknown": 0})


if __name__ == "__main__":
    unittest.main()
