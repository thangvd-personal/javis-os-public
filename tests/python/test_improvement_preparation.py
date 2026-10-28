"""Host derives real baseline preparation without creating any candidate/arm."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import json
import sqlite3
import subprocess
import threading
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import improvement_store as IM
from improvement_claude import ClaudeTrialAdapter
from improvement_comparison_plans import ExperimentPlans
from improvement_skill_candidates import SkillCandidates
from improvement_trials import TrialStore, _request
from improvement_preparation_protocol import preparation_schedule
import test_improvement_trial_associations as fixtures
from test_improvement_experiment_execution import _acl_only, _REAL_RUN, _REAL_POPEN


class PreparationTests(unittest.TestCase):
    def setUp(self, repeat_policy='feasibility'):
        self.f = fixtures.AssociationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for name, original in (('run', _REAL_RUN), ('Popen', _REAL_POPEN)):
            guard = patch.object(subprocess, name,
                side_effect=lambda *a, _original=original, **kw: _acl_only(_original, *a, **kw))
            guard.start()
            self.addCleanup(guard.stop)
        self.owner, self.host, self.store, self.assets = self.f.owner, self.f.host, self.f.store, self.f.f.assets
        self.association = self.f.create()
        base = self.assets.create_bundle(self.owner, [self.f.f.target["revision_id"]], event_key="preparation-base-bundle")
        self.plans = ExperimentPlans(self.host, SkillCandidates(self.assets, pack_state_dir=self.f.f.root / "packs"))
        self.experiment = self.plans.create(self.owner, {"experiment_id": "association-experiment",
            "contract_id": self.f.saved["contract_id"], "case_set_id": self.association["case_set_id"],
            "base_bundle_id": base["bundle_id"], "expected_context_generation": 1,
            "repeat_policy": repeat_policy, "timeout_seconds": 120}, event_key="owner-create-preparation-experiment")

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_preparation"), "Real host preparation admission is absent")
        from improvement_preparation import PreparationService
        return PreparationService(self.plans)

    def freeze(self):
        return self.service().freeze(self.owner, "association-experiment", expected_generation=1,
                                     event_key="owner-freeze-baseline-preparation")

    def admit(self, **changes):
        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}), \
                patch.object(subprocess, 'run', side_effect=lambda *a, **kw: _acl_only(_REAL_RUN, *a, **kw)), \
                patch.object(subprocess, 'Popen', side_effect=lambda *a, **kw: _acl_only(_REAL_POPEN, *a, **kw)):
            return self.service().admit_next(self.owner, "association-experiment", expected_generation=2,
                event_key=changes.pop("event_key", "owner-next-baseline-preparation"), **changes)

    def test_existing_experiment_without_candidate_can_freeze_and_admit_actual_baseline(self):
        frozen = self.freeze()
        self.assertEqual(frozen["cell_count"], 1)
        admitted = self.admit()
        request = admitted.request
        self.assertEqual((request["version"], request["variant"], request["case_id"]), (3, "baseline", "case-0"))
        self.assertEqual(_request(request), request)
        self.assertNotIn("candidate_bundle_hash", request["pins"])
        self.assertNotIn("lineage", request)
        self.assertEqual(request["preparation"]["preparation_id"], frozen["preparation_id"])
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_skill_candidates").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_experiment_arms").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_comparisons").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_trials").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_preparation_trial_links").fetchone()[0], 1)
            call = connection.execute("SELECT * FROM agent_calls").fetchone()
            self.assertEqual((call["purpose"], call["status"], call["brain_id"]), ("improve_probe", "reserved", self.owner.brain_id))

    def test_next_replay_after_stop_does_not_probe_decrypt_or_mint_new_deadline(self):
        self.freeze()
        original = self.admit()
        self.plans.records.begin_stop(self.owner, "association-experiment", kind="cancelled", reason="owner_requested",
                                      expected_generation=2, event_key="stop-preparation-replay")
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")), \
             patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Replay cannot probe")):
            replay = self.service().admit_next(self.owner, "association-experiment", expected_generation=2,
                                               event_key="owner-next-baseline-preparation")
        self.assertEqual(replay, original)

    def test_case_and_schedule_substitution_cannot_reserve_or_use_legacy_downgrade(self):
        self.freeze()
        admitted = self.admit()
        trials = TrialStore(self.assets)
        for key, value in (("case_id", "case-2"), ("repeat", 2)):
            with self.subTest(key=key), self.assertRaises((ValueError, PermissionError, IM.ConflictError)):
                trials.reserve(self.owner, {**admitted.request, key: value}, event_key="forged-preparation-" + key)
        with self.assertRaises((ValueError, PermissionError)):
            trials.reserve(self.owner, {**admitted.request, "version": 1}, event_key="legacy-preparation-downgrade")
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM agent_calls").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_qualification_claims").fetchone()[0], 0)

    def test_native_unavailable_or_late_source_drift_creates_no_call_or_link(self):
        self.freeze()
        service = self.service()
        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": False}), self.assertRaises(ValueError):
            service.admit_next(self.owner, "association-experiment", expected_generation=2, event_key="unavailable-preparation-native")

        def drift(adapter):
            self.f.f.protected.write_bytes(b"Changed protected instructions during synthetic preflight.\n")
            return {"supported": True, "capability_hash": "c" * 64}

        with patch.object(ClaudeTrialAdapter, "verify", drift), self.assertRaises(IM.ConflictError):
            service.admit_next(self.owner, "association-experiment", expected_generation=2, event_key="drifting-preparation-source")
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM agent_calls").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_preparation_trial_links").fetchone()[0], 0)

    def test_concurrent_next_admissions_cannot_allocate_the_same_baseline_cell(self):
        self.freeze()
        gate = threading.Barrier(2)
        service = self.service()

        def admit(index):
            gate.wait(timeout=10)
            try:
                return service.admit_next(self.owner, "association-experiment", expected_generation=2,
                                          event_key="concurrent-preparation-next-" + str(index))
            except IM.ConflictError:
                return None

        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}), \
                ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, (1, 2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_trials").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM agent_calls").fetchone()[0], 1)

    def test_final_sql_fence_rejects_holdout_alias_added_after_slow_lineage_read(self):
        from improvement_preparation_lineage import load_preparation_lineage, current_preparation_fences
        self.freeze()
        admitted = self.admit()
        with self.store._Tx(self.store) as connection:
            proof = load_preparation_lineage(connection, self.owner.brain_id, admitted.request, blobs=self.assets.blobs,
                                             current=True, require_link=True, trial_id=admitted.trial_id)
            fingerprint = connection.execute("SELECT fingerprint FROM improvement_case_families "
                "WHERE case_set_id=? AND case_id='case-0' ORDER BY fingerprint", (self.association["case_set_id"],)).fetchone()[0]
            # Synthetic storage insertion at the slow-read boundary; no private answer or model input.
            connection.execute("INSERT INTO improvement_case_families VALUES(?,?,?,?,?)", (
                self.association["case_set_id"], "case-1", self.owner.brain_id, fingerprint, "qualification"))
            with self.assertRaisesRegex(PermissionError, "preparation_holdout_alias"):
                current_preparation_fences(connection, self.owner.brain_id, proof)

    def test_binding_manual_arm_cannot_strand_incomplete_preparation(self):
        self.freeze()
        raw = self.f.f.skill.read_bytes()
        start = raw.index(b"Retain owner rules.")
        target = self.f.f.target
        authority = self.plans.authoring.declare_region(self.owner, "association-experiment", target["revision_id"], start, len(raw),
            expected_target_generation=1, event_key="preparation-manual-edit-region")
        candidate = self.plans.authoring.stage(self.owner, "association-experiment", {
            "version": 1, "target_id": target["target_id"], "base_revision_id": target["revision_id"],
            "before_hash": target["source_hash"], "operation": "replace_body_region", "start": start, "end": len(raw),
            "replacement": "Retain owner rules.\nCheck every subtotal.\n", "hypothesis": "Count authorized rows.",
            "failure_signature": "Omitted row.", "expected_behavior": "Each row counted once.",
            "possible_regressions": ["Duplicate row."], "evidence_refs": [], "angle": "execution"}, event_key="preparation-manual-candidate")
        with self.store._Tx(self.store) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "preparation_incomplete"):
                connection.execute("INSERT INTO improvement_experiment_arms VALUES(?,?,1,?,?,1,?,?,?)", (
                    "association-experiment", self.owner.brain_id, candidate["candidate_id"], authority["authority_id"],
                    "f" * 64, "{}", time.time()))
        with self.assertRaisesRegex(IM.ConflictError, "preparation_incomplete"):
            self.plans.bind_arm(self.owner, "association-experiment", candidate["candidate_id"], authority["authority_id"],
                expected_generation=2, event_key="bind-arm-before-preparation-complete")
        self.assertEqual(self.plans.records.get(self.owner, "association-experiment")["phase"], "preparing")

    def test_unfinished_baseline_cannot_be_sealed_as_proposer_input(self):
        self.freeze()
        self.admit()
        with self.assertRaisesRegex(IM.ConflictError, "preparation_results_incomplete"):
            self.service().complete(self.owner, "association-experiment", expected_generation=2,
                                    event_key="complete-unfinished-preparation")
        self.assertEqual(self.plans.records.get(self.owner, "association-experiment")["phase"], "preparing")
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_events WHERE operation='preparation_complete'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
