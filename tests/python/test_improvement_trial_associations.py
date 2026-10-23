"""Atomic owner imports use actual encrypted Host/CaseStore state and synthetic owner files."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="as-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                  "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources", "JAVIS_SESSIONS_DB": "sessions.sqlite3"}.items():
    os.environ[key] = str(TEST_ROOT / name)

import test_improvement_host_snapshots as fixtures
import improvement_cases as IC
import improvement_store as IM
from improvement_evaluation import EVALUATOR_PIN
from improvement_trial_file_sources import BrainFileFixtures

if importlib.util.find_spec("improvement_trial_associations"):
    import improvement_trial_associations as A
else:
    A = None


class AssociationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(A, "Immutable scoped owner case-set association is not implemented")
        self.f = fixtures.HostSnapshotTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.owner, self.host, self.store = self.f.owner, self.f.host, self.f.database
        self.refs = [self.f.ref]
        for index in (2, 3):
            path = self.f.path.with_name("source-" + str(index) + ".txt")
            path.write_bytes(("Private association fixture " + str(index) + "\r\n").encode())
            self.refs.append("brain-file:reports/" + path.name)
        self.host.fixture_observer = BrainFileFixtures({ref: self.f.metadata for ref in self.refs})
        self.binding = self.host.create(self.owner, "association-experiment", self.f.surface, [self.f.target["target_id"]],
            source_refs=self.refs, event_key="association-host-context")
        self.snapshots = [self.host.capture_snapshot(self.owner, "association-experiment", ref, event_key="association-snapshot-" + str(index))
                          for index, ref in enumerate(self.refs)]
        self.contract = {"version": 1, "objective": "Return the authorized source accurately.",
            "output": {"kind": "text", "path": "answer.json", "media_type": "application/json"}, "criteria": [
                {"id": "shape", "category": "mandatory", "critical": True, "kind": "json_keys",
                 "description": "Return a JSON object with value.", "config": {"path": "answer.json", "keys": ["value"]}},
                {"id": "content", "category": "business", "critical": True, "kind": "owner_confirm",
                 "description": "Owner confirms the exact value is accurate.", "config": {"path": "answer.json"}}],
            "primary_metric": {"criterion_id": "content", "direction": "higher", "aggregation": "all_pass"},
            "evaluator": dict(EVALUATOR_PIN), "unknown_conditions": ["missing_artifact", "config_unknown"], "output_hash_policy": "raw_sha256"}
        self.saved = self.f.cases.put_contract(self.owner, self.contract, event_key="association-contract")
        self.cases = [{"id": "case-" + str(index), "family_key": "family-" + str(index), "split": split,
            "task": "Report the selected private fixture.", "snapshot_ids": [snapshot["snapshot_id"]],
            "answer": {"expected": "private-owner-answer-" + str(index)}}
            for index, (snapshot, split) in enumerate(zip(self.snapshots, ("tuning", "qualification", "regression")))]
        self.service = A.TrialCaseAssociations(self.host)

    def create(self, *, event_key="create-owner-association", contract_id=None, cases=None, seed=47):
        return self.service.create(self.owner, "association-experiment", contract_id or self.saved["contract_id"],
            self.cases if cases is None else cases, seed, event_key=event_key)

    def count(self, table):
        return self.f.count(table)

    def test_real_case_set_and_immutable_scoped_association_commit_without_private_exposure(self):
        result = self.create()
        self.assertEqual(result["context_id"], self.binding["context_id"])
        self.assertEqual(result["contract_id"], self.saved["contract_id"])
        self.assertEqual(result["contract_hash"], self.saved["manifest_hash"])
        self.assertEqual(result["counts"], {"tuning": 1, "qualification": 1, "regression": 1})
        self.assertEqual(self.service.get(self.owner, result["case_set_id"]), result)
        with closing(self.store._conn()) as connection:
            rows = [json.loads(row[0]) for row in connection.execute("SELECT case_json FROM improvement_cases")]
            self.assertEqual([row["evaluator_refs"] for row in rows], [[], [], []])
            events = str(connection.execute("SELECT result_json FROM improvement_events").fetchall())
        self.assertNotIn("private-owner-answer", json.dumps(result) + events)
        for table in ("improvement_case_exposures", "improvement_qualification", "improvement_qualification_claims", "improvement_trials", "agent_calls", "goals"):
            self.assertEqual(self.count(table), 0)

    def test_same_event_replays_without_reading_answers_or_capturing_again(self):
        result = self.create()
        before = self.count("improvement_events")
        self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-associated-context")
        with patch.object(self.host, "_snapshot_observe", side_effect=AssertionError("Historical replay cannot recapture")), \
             patch.object(IC.CaseStore, "evaluator_case", side_effect=AssertionError("No private answer read")):
            self.assertEqual(self.create(), result)
        self.assertEqual(self.count("improvement_events"), before + 1)

    def test_existing_unassociated_component_case_set_is_not_adopted(self):
        component = self.f.cases.put_case_set(self.owner, [{**case, "evaluator_refs": []} for case in self.cases], 47,
                                             event_key="legacy-component-case-set")
        with self.assertRaisesRegex(PermissionError, "unassociated"):
            self.create()
        with self.assertRaisesRegex(PermissionError, "unassociated"):
            self.service.get(self.owner, component["case_set_id"])
        self.assertEqual(self.count("improvement_case_sets"), 1)

    def test_same_case_set_cannot_be_rebound_to_another_contract(self):
        original = self.create()
        contract = copy.deepcopy(self.contract)
        contract["objective"] = "Different owner acceptance objective."
        saved = self.f.cases.put_contract(self.owner, contract, event_key="other-association-contract")
        with self.assertRaisesRegex(IM.ConflictError, "association_conflict"):
            self.create(contract_id=saved["contract_id"], event_key="rebind-case-set-contract")
        self.assertEqual(self.service.get(self.owner, original["case_set_id"]), original)

    def test_same_case_set_cannot_be_rebound_to_another_context(self):
        original = self.create()
        self.host.create(self.owner, "other-association-context", self.f.surface, [self.f.target["target_id"]],
            source_refs=self.refs, event_key="other-association-context")
        with self.assertRaisesRegex(IM.ConflictError, "association_conflict"):
            self.service.create(self.owner, "other-association-context", self.saved["contract_id"], self.cases, 47,
                                event_key="rebind-case-set-context")
        self.assertEqual(self.service.get(self.owner, original["case_set_id"]), original)

    def test_same_event_payload_change_cannot_create_another_case_set(self):
        result = self.create()
        with self.assertRaisesRegex(IM.ConflictError, "event_payload_conflict"):
            self.create(seed=48)
        changed = copy.deepcopy(self.cases)
        changed[1]["answer"] = {"expected": "another private answer"}
        with self.assertRaisesRegex(IM.ConflictError, "event_payload_conflict"):
            self.create(cases=changed)
        self.assertEqual(self.service.get(self.owner, result["case_set_id"]), result)
        self.assertEqual(self.count("improvement_case_sets"), 1)

    def test_extra_evaluator_refs_cannot_arrive_from_owner_json(self):
        cases = copy.deepcopy(self.cases)
        cases[0]["evaluator_refs"] = ["attacker-checker"]
        with self.assertRaises(ValueError):
            self.create(cases=cases)
        self.assertEqual(self.count("improvement_case_sets"), 0)

    def test_new_event_same_association_reuses_rows_without_reading_private_answers(self):
        original = self.create()
        answer_hashes = {IM._hash(case["answer"]) for case in self.cases}
        with patch.object(self.f.blobs, "read", wraps=self.f.blobs.read) as reads, \
             patch.object(IC.CaseStore, "solver_case", side_effect=AssertionError("No solver exposure")), \
             patch.object(IC.CaseStore, "evaluator_case", side_effect=AssertionError("No private evaluator projection")), \
             patch.object(IC.CaseStore, "improver_cases", side_effect=AssertionError("No qualification exposure")):
            self.assertEqual(self.create(event_key="same-association-different-event"), original)
        self.assertFalse(answer_hashes & {call.args[1] for call in reads.call_args_list})
        self.assertEqual(self.count("improvement_case_sets"), 1)
        self.assertEqual(self.count("improvement_case_exposures"), 0)
        self.assertEqual(self.count("improvement_qualification"), 0)

    def test_event_failure_rolls_back_actual_case_rows_and_association_together(self):
        original = self.f.assets._event

        def fail(connection, principal, key, operation, payload, result):
            if operation == "owner_trial_case_set":
                raise OSError("synthetic association event failure")
            return original(connection, principal, key, operation, payload, result)

        before = self.count("improvement_events")
        with patch.object(self.f.assets, "_event", side_effect=fail), self.assertRaises(OSError):
            self.create()
        for table in ("improvement_case_sets", "improvement_cases", "improvement_case_sources", "improvement_case_families"):
            self.assertEqual(self.count(table), 0)
        self.assertEqual(self.count("improvement_events"), before)
        self.assertEqual(self.create()["counts"]["qualification"], 1)

    def test_qualification_snapshot_wrong_grant_rolls_back_entire_case_set(self):
        second = self.f.cases.get_snapshot(self.owner, self.snapshots[1]["snapshot_id"])
        raw = self.f.blobs.read(self.owner.brain_id, second["source_hash"])
        snapshot = self.f.cases.put_snapshot(self.owner, {**second["manifest"], "grant_hash": self.f.binding["grant_hash"]},
            raw, raw, event_key="different-grant-qualification-snapshot")
        cases = copy.deepcopy(self.cases)
        cases[1]["snapshot_ids"] = [snapshot["snapshot_id"]]
        before = self.count("improvement_events")
        with self.assertRaisesRegex(PermissionError, "grant_mismatch"):
            self.create(cases=cases)
        self.assertEqual(self.count("improvement_case_sets"), 0)
        self.assertEqual(self.count("improvement_events"), before)
        self.assertEqual(self.count("improvement_qualification"), 0)

    def test_checker_refs_are_host_derived_and_closed_pins_are_required_without_execution(self):
        contract = copy.deepcopy(self.contract)
        checker_hash = "a" * 64
        contract["criteria"][1].update(kind="host_checker", config={"checker_id": "trusted-checker", "version": "v1", "hash": checker_hash})
        saved = self.f.cases.put_contract(self.owner, contract, event_key="trusted-checker-contract")
        with self.assertRaisesRegex(PermissionError, "checker_unavailable"):
            self.create(contract_id=saved["contract_id"])
        checker = {"version": "v1", "hash": checker_hash, "check": lambda *args: self.fail("No checker execution during import")}
        for change in ({"version": "v2"}, {"hash": "b" * 64}, {"check": None}, {"extra": "caller field"}):
            service = A.TrialCaseAssociations(self.host, checker_registry={"trusted-checker": {**checker, **change}})
            with self.assertRaisesRegex(PermissionError, "checker_unavailable"):
                service.create(self.owner, "association-experiment", saved["contract_id"], self.cases, 47, event_key="wrong-checker-import")
        service = A.TrialCaseAssociations(self.host, checker_registry={"trusted-checker": checker})
        result = service.create(self.owner, "association-experiment", saved["contract_id"], self.cases, 47, event_key="trusted-checker-import")
        with closing(self.store._conn()) as connection:
            rows = [json.loads(row[0]) for row in connection.execute("SELECT case_json FROM improvement_cases WHERE case_set_id=?", (result["case_set_id"],))]
        self.assertEqual([row["evaluator_refs"] for row in rows], [["trusted-checker"]] * 3)

    def test_noncurrent_evaluator_pin_cannot_become_owner_api_contract(self):
        contract = copy.deepcopy(self.contract)
        contract["evaluator"]["hash"] = "f" * 64
        saved = self.f.cases.put_contract(self.owner, contract, event_key="historical-contract-pin")
        with self.assertRaisesRegex(PermissionError, "evaluator_unavailable"):
            self.create(contract_id=saved["contract_id"])
        self.assertEqual(self.count("improvement_case_sets"), 0)

    def test_foreign_brain_wrong_contract_and_closed_request_fail_without_case_rows(self):
        with self.assertRaises(PermissionError):
            self.service.create(self.f.other, "association-experiment", self.saved["contract_id"], self.cases, 47, event_key="foreign-association-create")
        with self.assertRaises(PermissionError):
            self.create(contract_id="missing-contract")
        with self.assertRaises(ValueError):
            self.create(seed=True)
        cases = copy.deepcopy(self.cases)
        cases[2]["authority"] = "complete"
        with self.assertRaises(ValueError):
            self.create(cases=cases)
        self.assertEqual(self.count("improvement_case_sets"), 0)

    def test_revoked_or_disabled_context_cannot_create_new_association(self):
        self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-before-import")
        with self.assertRaisesRegex(PermissionError, "revoked"):
            self.create()
        self.assertEqual(self.count("improvement_case_sets"), 0)

    def test_revocation_after_outside_observation_blocks_writer_without_orphan(self):
        original = self.host._snapshot_observe

        def revoke(principal, binding):
            original(principal, binding)
            self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-during-import")

        with patch.object(self.host, "_snapshot_observe", side_effect=revoke), self.assertRaisesRegex(PermissionError, "revoked"):
            self.create()
        self.assertEqual(self.count("improvement_case_sets"), 0)

    def test_disabled_policy_cannot_create_new_association(self):
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-before-import")
        with self.assertRaises(PermissionError):
            self.create()
        self.assertEqual(self.count("improvement_case_sets"), 0)

    def test_source_change_during_answer_blob_publication_rolls_back_association(self):
        original = self.f.blobs.write

        def change(brain, raw):
            result = original(brain, raw)
            if b"private-owner-answer" in raw:
                self.f.path.write_bytes(b"owner edited source while import was being encrypted")
            return result

        before = self.count("improvement_events")
        with patch.object(self.f.blobs, "write", side_effect=change), self.assertRaises(IM.ConflictError):
            self.create()
        self.assertEqual(self.count("improvement_case_sets"), 0)
        self.assertEqual(self.count("improvement_events"), before)

    def test_concurrent_same_event_has_one_case_set_one_association_and_identical_metadata(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.create(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count("improvement_case_sets"), 1)
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_events WHERE operation='owner_trial_case_set'").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM improvement_events WHERE operation='owner_trial_case_set'")

    def test_cold_and_readonly_metadata_does_not_construct_host_or_migrate(self):
        from resonance_store import GoalStore
        cold = GoalStore(self.f.root / "cold-associations.sqlite3")
        with self.assertRaisesRegex(PermissionError, "unassociated"):
            A.read_association(IM.ImprovementStore(cold, self.f.blobs), self.owner, "missing-case-set")
        self.assertEqual(IM._schema_version(cold), 0)
        result = self.create()
        assets = IM.read_existing(self.store.path, self.f.blobs)
        with patch.object(self.host, "_snapshot_observe", side_effect=AssertionError("Metadata cannot read live sources")), \
             patch.object(IC.CaseStore, "evaluator_case", side_effect=AssertionError("No private answers")):
            self.assertEqual(A.read_association(assets, self.owner, result["case_set_id"]), result)
        with self.assertRaises(PermissionError):
            A.read_association(assets, self.f.other, result["case_set_id"])


if __name__ == "__main__":
    unittest.main()
