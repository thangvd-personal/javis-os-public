"""Actual atomic owner admission, with synthetic native availability and no query."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import json
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

import test_improvement_trial_associations as fixtures
import improvement_store as IM
from improvement_assets import capture_source
from improvement_claude import ClaudeTrialAdapter
from agent_ledger import CallLedger

if importlib.util.find_spec("improvement_trial_admission"):
    import improvement_trial_admission as A
else:
    A = None


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(A, "Atomic owner trial admission is missing")
        self.f = fixtures.AssociationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.owner, self.host = self.f.owner, self.f.host
        self.assets, self.store = self.f.f.assets, self.f.store
        association = self.f.create()
        bundle = self.assets.create_bundle(self.owner, [self.f.f.target["revision_id"]], event_key="admission-base-bundle")
        self.public = {"event_key": "owner-admit-case-1", "experiment_id": "association-experiment",
            "contract_id": self.f.saved["contract_id"], "case_set_id": association["case_set_id"],
            "case_id": "case-1", "base_bundle_id": bundle["bundle_id"], "candidate_bundle_id": bundle["bundle_id"],
            "variant": "baseline", "repeat": 1, "timeout_seconds": 120, "expected_context_generation": 1}
        self.admission = A.TrialAdmission(self.host)
        self.verified = {"supported": True, "reason": None, "capability_hash": "c" * 64}
        self.verify_patch = patch.object(ClaudeTrialAdapter, "verify", return_value=self.verified)
        self.verify = self.verify_patch.start()
        self.addCleanup(self.verify_patch.stop)

    def admit(self, **changes):
        return self.admission.admit(self.owner, {**self.public, **changes})

    def count(self, table):
        return self.f.count(table)

    def assert_no_admission(self):
        for table in ("improvement_qualification", "improvement_qualification_claims", "improvement_trials", "agent_calls"):
            self.assertEqual(self.count(table), 0, table)
        with closing(self.store._conn()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM improvement_events WHERE operation IN ('trial_owner_plan','trial_owner_start')").fetchone()[0], 0)

    def test_real_qualification_trial_ledger_and_intent_commit_without_exposure(self):
        answer_hashes = {IM._hash(case["answer"]) for case in self.f.cases}
        with patch.object(self.f.f.blobs, "read", wraps=self.f.f.blobs.read) as reads:
            result = self.admit()
        self.assertIsInstance(result, A.AdmittedTrial)
        row = self.admission.trials.get(self.owner, result.trial_id)
        self.assertEqual(row["request"], result.request)
        self.assertEqual((row["status"], row["generation"], row["attempt"]), ("reserved", 1, 1))
        self.assertEqual(result.request["pins"]["contract_hash"], self.f.saved["manifest_hash"])
        self.assertEqual(result.request["pins"]["grant_hash"], self.f.binding["grant_hash"])
        self.assertEqual(result.request["pins"]["engine_config_hash"], IM._hash(self.f.f.engine))
        self.assertEqual(result.request["baseline_context_hash"], self.f.binding["baseline_context_hash"])
        self.assertEqual(self.count("improvement_qualification"), 1)
        self.assertEqual(self.count("agent_calls"), 1)
        self.assertEqual(self.count("improvement_case_exposures"), 0)
        self.assertFalse(answer_hashes & {call.args[1] for call in reads.call_args_list})
        with closing(self.store._conn()) as c:
            self.assertEqual(c.execute("SELECT status FROM improvement_qualification").fetchone()[0], "reserved")
            self.assertEqual(c.execute("SELECT engine_at FROM agent_calls").fetchone()[0], None)
            events = [json.loads(row[0]) for row in c.execute("SELECT result_json FROM improvement_events WHERE operation='trial_owner_start'")]
        self.assertEqual(set(events[0]), {"trial_id", "reserve_event_key", "plan_hash"})
        self.assertNotIn("private-owner-answer", json.dumps(events))
        self.assertEqual(self.admission.trials.reserve(self.owner, result.request, event_key=result.reserve_event_key)["trial_id"], result.trial_id)

    def test_native_verify_runs_before_any_qualification_trial_or_ledger_reservation(self):
        completed = [False]
        ordinary_tx = self.store._Tx
        def writer(store):
            self.assertTrue(completed[0], "Native preflight must finish before the writer transaction opens")
            return ordinary_tx(store)
        def available(adapter):
            self.assert_no_admission()
            completed[0] = True
            return self.verified
        with patch.object(ClaudeTrialAdapter, "verify", available), patch.object(self.store, "_Tx", writer):
            self.admit()

    def test_deadline_duration_begins_after_native_preflight_and_never_before_it(self):
        clock = [1000.0]
        def available(adapter):
            clock[0] = 2000.0
            return self.verified
        with patch.object(A.time, "time", side_effect=lambda: clock[0]), patch.object(ClaudeTrialAdapter, "verify", available):
            result = self.admit()
        self.assertEqual(result.request["deadline_at"], 2120.0)

    def test_durable_plan_retains_original_timeout_policy_and_associated_pins(self):
        admitted = self.admit()
        with closing(self.store._conn()) as c:
            record = json.loads(c.execute("SELECT result_json FROM improvement_events WHERE operation='trial_owner_plan'").fetchone()[0])
        self.assertIsNotNone(record.get("plan"), "A hash-only event cannot preserve the actual timeout and association plan")
        self.assertEqual(record["plan"]["timeout_seconds"], 120)
        self.assertEqual(record["plan"]["pins"], admitted.request["pins"])
        self.assertEqual(record["plan"]["case_set_id"], self.public["case_set_id"])
        self.assertEqual(record["plan"]["base_bundle_id"], self.public["base_bundle_id"])
        self.assertEqual(record["plan"]["timeout_policy"]["version"], 1)
        self.assertEqual(record["plan_hash"], IM._hash(record["plan"]))

    def test_replay_returns_fresh_original_request_after_prior_handoff_is_mutated(self):
        result = self.admit()
        expected = copy.deepcopy(result.request)
        result.request["deadline_at"] = 1
        result.request["pins"]["grant_hash"] = "0" * 64
        replay = self.admit()
        self.assertEqual(replay.request, expected)

    def test_same_event_replay_retains_original_deadline_after_revocation_without_probes(self):
        result = self.admit()
        self.host.revoke(self.owner, self.public["experiment_id"], expected_generation=1, event_key="revoke-after-admit")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Replay cannot probe native")), \
             patch.object(self.host, "live_guard", side_effect=AssertionError("Replay cannot observe sources")), \
             patch.object(A.time, "time", return_value=time.time() + 10000):
            replay = self.admit()
        self.assertEqual(replay, result)
        self.assertEqual(self.count("agent_calls"), 1)

    def test_terminal_replay_never_reopens_or_extends_an_attempt(self):
        result = self.admit()
        self.admission.trials.cancel(self.owner, result.trial_id, expected_generation=1, event_key="admission-cancel-slot")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("No query retry")):
            self.assertEqual(self.admit(), result)
        self.assertEqual(self.admission.trials.get(self.owner, result.trial_id)["status"], "cancelled")
        self.assertEqual(self.count("agent_calls"), 1)

    def test_event_payload_conflict_and_existing_logical_slot_fail_before_native_probe(self):
        self.admit()
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Conflict must precede native")):
            with self.assertRaises(IM.ConflictError):
                self.admit(timeout_seconds=121)
            with self.assertRaises(IM.ConflictError):
                self.admit(event_key="different-key-same-slot")
        self.assertEqual(self.count("agent_calls"), 1)

    def test_shared_experiment_plan_prevents_timeout_or_bundle_selection_changes(self):
        self.admit()
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Plan conflict precedes probe")):
            with self.assertRaises(IM.ConflictError):
                self.admit(event_key="different-slot-new-timeout", case_id="case-2", timeout_seconds=121)
        self.assertEqual(self.count("agent_calls"), 1)

    def test_shared_experiment_plan_cannot_switch_to_another_valid_associated_case_set(self):
        self.admit()
        associated = self.f.create(seed=48, event_key="admission-another-case-set")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Plan conflict precedes native")):
            with self.assertRaises(IM.ConflictError):
                self.admit(event_key="other-slot-case-set", case_set_id=associated["case_set_id"])
        self.assertEqual(self.count("agent_calls"), 1)

    def test_other_slots_share_real_qualification_and_plan_without_consumption(self):
        first = self.admit()
        second = self.admit(event_key="owner-admit-candidate", variant="candidate")
        third = self.admit(event_key="owner-admit-tuning", case_id="case-0")
        self.assertNotEqual(first.trial_id, second.trial_id)
        self.assertEqual(first.request["qualification_id"], second.request["qualification_id"])
        self.assertNotIn("qualification_id", third.request)
        self.assertEqual(self.count("improvement_qualification"), 1)
        self.assertEqual(self.count("agent_calls"), 3)
        with closing(self.store._conn()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM improvement_events WHERE operation='trial_owner_plan'").fetchone()[0], 1)

    def test_unsupported_or_malformed_native_capability_reserves_nothing(self):
        for result in ({"supported": False, "reason": "unavailable"}, {"supported": True}, {"supported": 1, "capability_hash": "c" * 64}):
            with self.subTest(result=result), patch.object(ClaudeTrialAdapter, "verify", return_value=result):
                with self.assertRaises((ValueError, PermissionError)):
                    self.admit()
                self.assert_no_admission()

    def test_native_failure_and_source_drift_during_verify_leave_no_intent_or_call(self):
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=OSError("synthetic unavailable native")):
            with self.assertRaises((ValueError, PermissionError)):
                self.admit()
        self.assert_no_admission()
        def drift(adapter):
            self.f.f.protected.write_bytes(b"Owner changed protected instructions during availability probe.")
            return self.verified
        with patch.object(ClaudeTrialAdapter, "verify", drift), self.assertRaises((IM.ConflictError, ValueError)):
            self.admit()
        self.assert_no_admission()

    def test_owner_cross_brain_generation_and_json_authority_injection_fail_before_verify(self):
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Invalid request cannot probe")):
            with self.assertRaises(PermissionError):
                self.admission.admit(self.f.f.other, self.public)
            with self.assertRaises(IM.ConflictError):
                self.admit(expected_context_generation=2)
            for extra in ({"pins": {}}, {"qualification_id": "forged"}, {"deadline_at": time.time() + 300}):
                with self.assertRaises(ValueError):
                    self.admit(**extra)
        self.assert_no_admission()

    def test_wrong_contract_or_unassociated_case_set_cannot_be_admitted(self):
        other = copy.deepcopy(self.f.contract)
        other["objective"] = "Different owner contract"
        saved = self.f.f.cases.put_contract(self.owner, other, event_key="admission-other-contract")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Association must precede probe")):
            with self.assertRaises((ValueError, PermissionError)):
                self.admit(contract_id=saved["contract_id"])
        self.assert_no_admission()

    def test_unassociated_valid_component_case_set_cannot_be_adopted_by_start(self):
        component = self.f.f.cases.put_case_set(self.owner,
            [{**case, "evaluator_refs": []} for case in self.f.cases], 49, event_key="unassociated-admission-case-set")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Association must precede native")):
            with self.assertRaisesRegex(PermissionError, "unassociated"):
                self.admit(case_set_id=component["case_set_id"])
        self.assert_no_admission()

    def test_same_brain_bundle_of_another_target_is_not_an_authorized_baseline_or_candidate(self):
        path = self.f.f.brain / "skills/other/SKILL.md"
        path.parent.mkdir()
        path.write_bytes(b"---\nname: Other\ndescription: Another skill.\n---\nDifferent owner target.\n")
        source, raw = capture_source(self.f.f.brain, "skill", "other", pack_state_dir=self.f.f.root / "packs")
        captured = self.assets.capture(self.owner, source, raw, event_key="admission-another-target")
        bundle = self.assets.create_bundle(self.owner, [captured["revision_id"]], event_key="admission-another-target-bundle")
        for field in ("base_bundle_id", "candidate_bundle_id"):
            with self.subTest(field=field), patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Wrong bundle cannot probe")):
                with self.assertRaises(PermissionError):
                    self.admit(**{field: bundle["bundle_id"]})
        self.assert_no_admission()

    def test_changed_approved_checker_callable_during_native_preflight_is_not_silently_rebound(self):
        contract = copy.deepcopy(self.f.contract)
        contract["criteria"][1].update(kind="host_checker", config={"checker_id": "host-approved", "version": "1", "hash": "d" * 64})
        saved = self.f.f.cases.put_contract(self.owner, contract, event_key="admission-checker-contract")
        registry = {"host-approved": {"version": "1", "hash": "d" * 64,
                    "check": lambda *args: self.fail("Admission must not run checker")}}
        association = fixtures.A.TrialCaseAssociations(self.host, checker_registry=registry).create(self.owner,
            self.public["experiment_id"], saved["contract_id"], self.f.cases, 50, event_key="admission-checker-cases")
        self.public.update(contract_id=saved["contract_id"], case_set_id=association["case_set_id"])
        self.admission = A.TrialAdmission(self.host, checker_registry=registry)
        def changed(adapter):
            registry["host-approved"]["check"] = lambda *args: self.fail("Replacement checker must not run")
            return self.verified
        with patch.object(ClaudeTrialAdapter, "verify", changed), self.assertRaises(IM.ConflictError):
            self.admit()
        self.assert_no_admission()

    def test_invalid_target_blob_is_rejected_before_native_without_private_error_text(self):
        original = self.f.f.blobs.read
        digest = self.f.f.target["source_hash"]
        def corrupted(brain, observed):
            return b"not the frozen source" if observed == digest else original(brain, observed)
        with patch.object(self.f.f.blobs, "read", corrupted), \
             patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Corrupt baseline cannot probe")):
            with self.assertRaises(PermissionError):
                self.admit()
        self.assert_no_admission()

    def test_numeric_enabled_legacy_manifest_is_not_equal_to_boolean_protected_source(self):
        source, raw = capture_source(self.f.f.brain, "skill", "report", pack_state_dir=self.f.f.root / "packs")
        # The low-level historical capture API accepts a truthy dataclass field;
        # that must not make JSON number 1 a valid boolean source declaration.
        captured = self.assets.capture(self.owner, replace(source, enabled=1), raw, event_key="legacy-numeric-enabled")
        bundle = self.assets.create_bundle(self.owner, [captured["revision_id"]], event_key="legacy-enabled-bundle")
        with self.assertRaisesRegex(PermissionError, "baseline_revision_unavailable"):
            self.admit(base_bundle_id=bundle["bundle_id"], candidate_bundle_id=bundle["bundle_id"])
        self.assert_no_admission()

    def test_revoked_source_grant_and_disabled_policy_precede_native_probe(self):
        self.assets.set_policy(self.owner, "off", expected_version=0, event_key="admission-disable-policy")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Off policy cannot probe")):
            with self.assertRaises(PermissionError):
                self.admit()
        self.assert_no_admission()

    def test_event_failures_rollback_plan_qualification_trial_ledger_and_every_event(self):
        for operation in ("qualification_reserve", "trial_reserve", "trial_owner_plan", "trial_owner_start"):
            before = self.count("improvement_events")
            # Qualifiers emit via CaseStore's static event helper. A real SQL
            # trigger injects failure at the same durable boundary for every
            # operation, regardless of which public wrapper emits the event.
            with closing(self.store._conn()) as c:
                c.execute("CREATE TRIGGER fail_admission_event BEFORE INSERT ON improvement_events "
                          "WHEN NEW.operation='" + operation + "' BEGIN SELECT RAISE(ABORT,'synthetic_admission_failure'); END")
            try:
                with self.subTest(operation=operation), self.assertRaises(sqlite3.IntegrityError):
                    self.admit()
            finally:
                with closing(self.store._conn()) as c:
                    c.execute("DROP TRIGGER fail_admission_event")
            self.assert_no_admission()
            self.assertEqual(self.count("improvement_events"), before)

    def test_ledger_capacity_failure_rolls_back_real_qualification_claim(self):
        with patch.object(CallLedger, "reserve", return_value=None), self.assertRaises(IM.ConflictError):
            self.admit()
        self.assert_no_admission()

    def test_protected_source_change_after_trial_write_rolls_back_all_admission(self):
        original = self.assets._event
        def drift(c, p, key, operation, payload, result):
            result = original(c, p, key, operation, payload, result)
            if operation == "trial_reserve":
                self.f.f.protected.write_bytes(b"Owner edited source during admission commit.")
            return result
        with patch.object(self.assets, "_event", drift), self.assertRaises((IM.ConflictError, ValueError)):
            self.admit()
        self.assert_no_admission()

    def test_concurrent_same_event_commits_one_stable_deadline_trial_and_call(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _: self.admit(), range(2)))
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(self.count("agent_calls"), 1)
        self.assertEqual(self.count("improvement_trials"), 1)
        self.assertEqual(self.count("improvement_qualification"), 1)

    def test_current_baseline_required_and_changed_historical_candidate_is_unavailable(self):
        old_bundle = self.public["base_bundle_id"]
        self.f.f.skill.write_bytes(self.f.f.skill.read_bytes() + b"Current owner body change.\n")
        source, raw = capture_source(self.f.f.brain, "skill", "report", pack_state_dir=self.f.f.root / "packs")
        captured = self.assets.capture(self.owner, source, raw, expected_generation=1, event_key="admission-current-source")
        self.host.create(self.owner, "fresh-source-experiment", self.f.f.surface, [captured["target_id"]],
            source_refs=self.f.refs, event_key="admission-fresh-context")
        snapshots = [self.host.capture_snapshot(self.owner, "fresh-source-experiment", ref, event_key="admission-fresh-snapshot-" + str(i))
                     for i, ref in enumerate(self.f.refs)]
        cases = [{**case, "snapshot_ids": [snapshot["snapshot_id"]]} for case, snapshot in zip(self.f.cases, snapshots)]
        association = self.f.service.create(self.owner, "fresh-source-experiment", self.f.saved["contract_id"], cases, 48,
                                           event_key="admission-fresh-association")
        self.public.update(experiment_id="fresh-source-experiment", case_set_id=association["case_set_id"])
        with self.assertRaisesRegex((ValueError, PermissionError), "baseline_revision_unavailable"):
            self.admit()
        current = self.assets.create_bundle(self.owner, [captured["revision_id"]], event_key="admission-current-bundle")
        self.public["base_bundle_id"] = current["bundle_id"]
        self.public["candidate_bundle_id"] = old_bundle
        with self.assertRaisesRegex((ValueError, PermissionError), "candidate_revision_unavailable"):
            self.admit()
        self.assert_no_admission()
        self.public["candidate_bundle_id"] = current["bundle_id"]
        self.assertEqual(self.admit().request["pins"]["base_bundle_hash"], current["manifest_hash"])


if __name__ == "__main__":
    unittest.main()
