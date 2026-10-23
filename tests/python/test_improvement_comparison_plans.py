"""Host-derived experiment plans use actual encrypted context and A proposals."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import json
import stat
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

import improvement_cases as IC
import improvement_assets as IA
import improvement_store as IM
import test_improvement_experiments as fixtures
from improvement_skill_candidates import SkillCandidates

if importlib.util.find_spec("improvement_comparison_plans"):
    from improvement_comparison_plans import ExperimentPlans
else:
    ExperimentPlans = None


class ComparisonPlanTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.ExperimentTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.f, self.owner = self.h.f, self.h.owner
        self.assets, self.store, self.host = self.h.assets, self.h.store, self.h.h.host
        self.authoring = SkillCandidates(self.assets, pack_state_dir=self.f.root / "packs")

    def plans(self):
        self.assertIsNotNone(ExperimentPlans, "Host experiment plans are absent")
        return ExperimentPlans(self.host, self.authoring)

    def request(self, **changes):
        public = self.h.h.public
        return {"experiment_id": public["experiment_id"], "contract_id": public["contract_id"],
            "case_set_id": public["case_set_id"], "base_bundle_id": public["base_bundle_id"],
            "expected_context_generation": 1, "repeat_policy": "feasibility", "timeout_seconds": 120,
            **changes}

    def create(self, key="create-host-experiment", **changes):
        return self.plans().create(self.owner, self.request(**changes), event_key=key)

    def count(self, table):
        with closing(self.store._conn()) as connection:
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_create_derives_exact_definition_without_trials_or_private_answer_reads(self):
        before = (self.count("improvement_trials"), self.count("agent_calls"))
        answer_hashes = {IM._hash(case["answer"]) for case in self.h.h.f.cases}
        with patch.object(self.assets.blobs, "read", wraps=self.assets.blobs.read) as reads:
            result = self.create()
        self.assertEqual(result["phase"], "proposed")
        with closing(self.store._conn()) as connection:
            row = connection.execute("SELECT definition_json FROM improvement_experiments").fetchone()
        expected = self.h.definition()
        expected["decision_policy"]["multiple_tests"] = 2
        self.assertEqual(json.loads(row[0]), expected)
        self.assertFalse(answer_hashes & {call.args[1] for call in reads.call_args_list})
        self.assertEqual((self.count("improvement_trials"), self.count("agent_calls")), before)
        self.assertNotIn(str(self.f.root), json.dumps(result))

    def test_create_replay_after_revocation_never_observes_or_regrants(self):
        result = self.create()
        self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-planned-context")
        with patch.object(self.host, "_snapshot_observe", side_effect=AssertionError("Replay cannot observe")), \
             patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")):
            self.assertEqual(self.create(), result)
        with self.assertRaises(PermissionError):
            self.create(key="new-event-after-revocation")

    def test_existing_same_brain_unassociated_set_is_rejected(self):
        unassociated = IC.CaseStore(self.assets).put_case_set(self.owner,
            [{**case, "evaluator_refs": []} for case in self.h.h.f.cases], 543,
            event_key="unassociated-experiment-cases")
        with self.assertRaisesRegex(PermissionError, "unassociated"):
            self.create(case_set_id=unassociated["case_set_id"])
        self.assertEqual(self.count("improvement_experiments"), 0)

    def test_source_drift_during_observation_rolls_back_definition_and_event(self):
        observe = self.host._snapshot_observe
        calls = []

        def drift(principal, binding):
            observe(principal, binding)
            calls.append(True)
            if len(calls) == 1:
                self.f.skill.write_bytes(self.f.skill.read_bytes() + b"\nUnreviewed external change.\n")

        with patch.object(self.host, "_snapshot_observe", side_effect=drift):
            with self.assertRaises((PermissionError, IM.ConflictError)):
                self.create()
        self.assertEqual(self.count("improvement_experiments"), 0)
        with closing(self.store._conn()) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM improvement_events WHERE event_key='create-host-experiment'").fetchone())

    def test_closed_owner_inputs_cannot_supply_definition_pins_or_repeats(self):
        for extra in ({"grant_hash": "0" * 64}, {"repeat_policy": {"kind": "pilot", "repeats": 1}},
                      {"timeout_seconds": True}, {"expected_context_generation": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.create(**extra)

    def bind(self, *, key="bind-real-arm", **changes):
        candidate = self.h.candidate
        return self.plans().bind_arm(self.owner, "association-experiment", candidate["candidate_id"],
            candidate["edit_authority"]["authority_id"], expected_generation=1, event_key=key, **changes)

    def test_bind_verifies_actual_proposal_and_pins_chosen_authority(self):
        self.create()
        bound = self.bind()
        self.assertEqual(bound["ordinal"], 1)
        self.assertEqual(bound["candidate_id"], self.h.candidate["candidate_id"])
        with closing(self.store._conn()) as connection:
            row = connection.execute("SELECT * FROM improvement_experiment_arms").fetchone()
            envelope = json.loads(row["envelope_json"])
        self.assertEqual(row["envelope_hash"], IM._hash(envelope))
        self.assertEqual(envelope["authority_id"], self.h.candidate["edit_authority"]["authority_id"])
        self.assertEqual(envelope["content_hash"], self.h.candidate["content_hash"])
        self.assertEqual(envelope["semantic_status"], "unreviewed")
        self.assertNotIn("after", envelope)

    def test_revoked_authority_cannot_bind_but_fresh_explicit_diff_can(self):
        self.create()
        chosen = self.h.candidate["edit_authority"]["authority_id"]
        self.authoring.records.revoke_authority(self.owner, chosen, expected_generation=1, event_key="revoke-arm-region")
        with self.assertRaisesRegex(PermissionError, "authority"):
            self.bind()
        candidate = self.h.candidate
        exact = self.authoring.authorize_diff(self.owner, candidate["candidate_id"], expected_generation=1,
            before_hash=candidate["base_hash"], after_hash=candidate["content_hash"], event_key="fresh-arm-exact-diff")
        bound = self.plans().bind_arm(self.owner, "association-experiment", candidate["candidate_id"], exact["authority_id"],
            expected_generation=1, event_key="bind-explicit-diff")
        self.assertEqual(bound["authority_id"], exact["authority_id"])
        self.assertEqual(self.authoring.records.get(self.owner, candidate["candidate_id"])["semantic_status"], "unreviewed")

    def test_freeze_tuning_creates_full_matrix_without_exposing_cases_or_reserving_calls(self):
        self.create()
        bound = self.bind()
        before = (self.count("agent_calls"), self.count("improvement_qualification_claims"))
        result = self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
            expected_generation=bound["generation"], event_key="freeze-tuning-arm")
        self.assertEqual(result["cell_count"], 2)
        with closing(self.store._conn()) as connection:
            row = connection.execute("SELECT * FROM improvement_comparisons").fetchone()
        matrix = json.loads(self.assets.blobs.read(self.owner.brain_id, row["matrix_blob_hash"]))
        self.assertEqual([pair["case_id"] for pair in matrix["pairs"]],
                         [case["id"] for case in self.h.h.f.cases if case["split"] == "tuning"])
        self.assertEqual({pair["repeat"] for pair in matrix["pairs"]}, {1})
        self.assertEqual(IM._hash(matrix), row["matrix_hash"])
        self.assertNotIn("private-owner-answer", json.dumps(matrix) + json.dumps(result))
        self.assertEqual((self.count("agent_calls"), self.count("improvement_qualification_claims")), before)
        with self.assertRaises(IM.ConflictError):
            self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
                expected_generation=result["generation"], event_key="second-freeze-same-arm")

    def test_keep_base_atomically_seals_stops_and_retains_capacity_after_revocation(self):
        created = self.create()
        claims = self.count("improvement_qualification_claims")
        self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-before-keep")
        with patch.object(self.host, "_snapshot_observe", side_effect=AssertionError("Stop cannot need sources")):
            result = self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                expected_generation=created["generation"], event_key="keep-current-baseline")
        self.assertEqual((result["phase"], result["stop_kind"]), ("stopping", "completed"))
        with closing(self.store._conn()) as connection:
            selection = connection.execute("SELECT * FROM improvement_experiment_selections").fetchone()
            self.assertEqual(selection["choice"], "keep_base")
            self.assertEqual(selection["event_key"], "keep-current-baseline")
        self.assertEqual(self.count("improvement_qualification_claims"), claims)

    def test_candidate_selection_and_final_freeze_require_verified_reports(self):
        self.create()
        bound = self.bind()
        with self.assertRaisesRegex(PermissionError, "report_unavailable"):
            self.plans().select(self.owner, "association-experiment", "candidate", ordinal=1,
                expected_generation=bound["generation"], event_key="unreviewed-candidate-selection")
        with self.assertRaises(PermissionError):
            self.plans().freeze(self.owner, "association-experiment", 1, "final",
                expected_generation=bound["generation"], event_key="unselected-final-comparison")
        self.assertEqual(self.count("improvement_experiment_selections"), 0)

    def test_sealed_experiment_denies_new_edits_but_preserves_replay_private_read_and_revoke(self):
        from improvement_private_review import PrivateReview
        created = self.create()
        candidate = self.h.candidate
        self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=created["generation"], event_key="seal-before-extra-edits")
        with closing(self.store._conn()) as connection:
            proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, candidate["patch_hash"]))
        with self.assertRaisesRegex(IM.ConflictError, "sealed"):
            self.authoring.stage(self.owner, "association-experiment", proposal, event_key="stage-after-seal")
        raw = self.f.skill.read_bytes()
        with self.assertRaisesRegex(IM.ConflictError, "sealed"):
            self.authoring.declare_region(self.owner, "association-experiment", candidate["base_revision_id"],
                raw.index(b"Retain owner rules."), len(raw), expected_target_generation=1, event_key="region-after-seal")
        with self.assertRaisesRegex(IM.ConflictError, "sealed"):
            self.authoring.authorize_diff(self.owner, candidate["candidate_id"], expected_generation=1,
                before_hash=candidate["base_hash"], after_hash=candidate["content_hash"], event_key="diff-after-seal")
        self.assertEqual(self.authoring.stage(self.owner, "association-experiment", proposal, event_key="legacy-a-stage"), candidate)
        reviewed = PrivateReview(self.assets, pack_state_dir=self.f.root / "packs").candidate(self.owner, candidate["candidate_id"])
        self.assertTrue(reviewed)
        revoked = self.authoring.records.revoke_authority(self.owner, candidate["edit_authority"]["authority_id"],
            expected_generation=1, event_key="revoke-after-seal")
        self.assertEqual(revoked["status"], "revoked")

    def test_pinned_arm_never_switches_to_fresh_diff_after_region_revocation(self):
        self.create()
        bound = self.bind()
        candidate = self.h.candidate
        self.authoring.records.revoke_authority(self.owner, bound["authority_id"], expected_generation=1, event_key="revoke-pinned-arm")
        self.authoring.authorize_diff(self.owner, candidate["candidate_id"], expected_generation=1,
            before_hash=candidate["base_hash"], after_hash=candidate["content_hash"], event_key="other-active-diff")
        with self.assertRaisesRegex(PermissionError, "authority"):
            self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
                expected_generation=bound["generation"], event_key="no-silent-authority-switch")
        self.assertEqual(self.count("improvement_comparisons"), 0)
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot regrant")):
            self.assertEqual(self.bind(), bound)

    def test_second_actual_arm_has_distinct_baseline_comparison_identity(self):
        self.create()
        first = self.bind()
        candidate = self.h.candidate
        proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, candidate["patch_hash"]))
        proposal["replacement"] = "Retain owner rules.\nReconcile unmatched rows first.\n"
        second = self.authoring.stage(self.owner, "association-experiment", proposal, event_key="second-real-proposal")
        bound = self.plans().bind_arm(self.owner, "association-experiment", second["candidate_id"],
            second["edit_authority"]["authority_id"], expected_generation=first["generation"], event_key="bind-second-arm")
        self.assertEqual(bound["ordinal"], 2)
        one = self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
            expected_generation=bound["generation"], event_key="first-arm-fresh-baseline")
        two = self.plans().freeze(self.owner, "association-experiment", 2, "tuning",
            expected_generation=one["generation"], event_key="second-arm-fresh-baseline")
        self.assertNotEqual(one["comparison_id"], two["comparison_id"])
        self.assertNotEqual(one["matrix_hash"], two["matrix_hash"])
        with closing(self.store._conn()) as connection:
            pairs = [json.loads(self.assets.blobs.read(self.owner.brain_id, row[0]))["pairs"][0]["pair_id"]
                     for row in connection.execute("SELECT matrix_blob_hash FROM improvement_comparisons")]
        self.assertEqual(len(set(pairs)), 2)

    def test_permission_drift_during_matrix_blob_publication_rolls_back(self):
        self.create()
        bound = self.bind()
        mode = self.f.skill.stat().st_mode
        original = self.assets.blobs.write

        def publish(*args):
            value = original(*args)
            self.f.skill.chmod(mode & ~stat.S_IWUSR)
            return value

        try:
            with patch.object(self.assets.blobs, "write", side_effect=publish):
                with self.assertRaises((PermissionError, IM.ConflictError, IA.SourceError)):
                    self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
                        expected_generation=bound["generation"], event_key="matrix-source-race")
        finally:
            self.f.skill.chmod(mode)
        self.assertEqual(self.count("improvement_comparisons"), 0)
        self.assertEqual(self.h.records().get(self.owner, "association-experiment")["generation"], bound["generation"])

    def test_keep_base_during_slow_staging_fences_final_writer(self):
        created = self.create()
        proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, self.h.candidate["patch_hash"]))
        observe, stopped = self.authoring.observe, []

        def stop_during_read(*args):
            result = observe(*args)
            if not stopped:
                stopped.append(True)
                self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                    expected_generation=created["generation"], event_key="stop-during-staging")
            return result

        with patch.object(self.authoring, "observe", side_effect=stop_during_read):
            with self.assertRaisesRegex(IM.ConflictError, "sealed"):
                self.authoring.stage(self.owner, "association-experiment", proposal, event_key="staging-late-writer")
        self.assertEqual(self.count("improvement_skill_candidates"), 1)
        self.assertEqual(self.h.records().get(self.owner, "association-experiment")["phase"], "stopping")

    def test_concurrent_keep_base_and_freeze_have_one_generation_winner(self):
        self.create()
        bound = self.bind()
        gate = threading.Barrier(2)

        def action(index):
            gate.wait()
            try:
                if index == 0:
                    return self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                        expected_generation=bound["generation"], event_key="concurrent-keep-base")
                return self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
                    expected_generation=bound["generation"], event_key="concurrent-freeze")
            except IM.ConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(action, (0, 1)))
        self.assertEqual(sum(value is not None for value in values), 1)
        self.assertEqual(self.count("improvement_experiment_selections") + self.count("improvement_comparisons"), 1)

    def test_actual_same_brain_proposal_from_another_context_cannot_be_transplanted(self):
        self.create()
        raw = self.f.skill.read_bytes()
        self.host.create(self.owner, "another-authoring-experiment", self.f.surface, [self.f.target["target_id"]],
            source_refs=self.h.h.f.refs, event_key="another-authoring-context")
        region = self.authoring.declare_region(self.owner, "another-authoring-experiment", self.f.target["revision_id"],
            raw.index(b"Retain owner rules."), len(raw), expected_target_generation=1, event_key="another-authoring-region")
        proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, self.h.candidate["patch_hash"]))
        other = self.authoring.stage(self.owner, "another-authoring-experiment", proposal, event_key="another-authoring-stage")
        with self.assertRaisesRegex(PermissionError, "scope"):
            self.plans().bind_arm(self.owner, "association-experiment", other["candidate_id"], region["authority_id"],
                expected_generation=1, event_key="transplant-another-context")
        self.assertEqual(self.count("improvement_experiment_arms"), 0)

    def test_sealing_selection_and_stop_intent_roll_back_together_on_event_failure(self):
        created = self.create()
        event = self.assets._event

        def fail_selection(connection, principal, key, operation, payload, result):
            if operation == "experiment_select":
                raise OSError("Synthetic event persistence failure")
            return event(connection, principal, key, operation, payload, result)

        with patch.object(self.assets, "_event", side_effect=fail_selection):
            with self.assertRaises(OSError):
                self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                    expected_generation=created["generation"], event_key="atomic-keep-base-failure")
        self.assertEqual(self.count("improvement_experiment_selections"), 0)
        self.assertEqual(self.h.records().get(self.owner, "association-experiment"), created)

    def test_frozen_matrix_replays_without_hot_reads_after_keep_base(self):
        self.create()
        bound = self.bind()
        result = self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
            expected_generation=bound["generation"], event_key="historical-freeze")
        stopped = self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=result["generation"], event_key="historical-keep-base")
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")), \
             patch.object(self.host, "_snapshot_observe", side_effect=AssertionError("Replay cannot observe")):
            self.assertEqual(self.plans().freeze(self.owner, "association-experiment", 1, "tuning",
                expected_generation=bound["generation"], event_key="historical-freeze"), result)
            self.assertEqual(self.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                expected_generation=result["generation"], event_key="historical-keep-base"), stopped)

    def test_corrupt_encrypted_proposal_is_not_bound_even_when_sql_hashes_match(self):
        from improvement_blobs import AssetUnavailable
        self.create()
        candidate = self.h.candidate
        self.assets.blobs._path(self.owner.brain_id, candidate["provenance_hash"]).write_text("enc:broken", encoding="ascii")
        with self.assertRaises(AssetUnavailable):
            self.bind()
        self.assertEqual(self.count("improvement_experiment_arms"), 0)

    def test_protected_context_change_during_last_create_reads_prevents_commit(self):
        observe, read = self.host._snapshot_observe, self.assets.blobs.read
        observations, changed = [], []

        def observed(*args):
            observe(*args)
            observations.append(True)

        def read_after_observation(*args):
            result = read(*args)
            if len(observations) >= 2 and not changed:
                changed.append(True)
                self.f.protected.write_bytes(b"Changed protected owner instructions during final encrypted reads.\n")
            return result

        with patch.object(self.host, "_snapshot_observe", side_effect=observed), \
             patch.object(self.assets.blobs, "read", side_effect=read_after_observation):
            with self.assertRaises(IM.ConflictError):
                self.create()
        self.assertTrue(changed)
        self.assertEqual(self.count("improvement_experiments"), 0)

    def test_protected_context_change_during_last_proposal_read_prevents_bind_commit(self):
        self.create()
        read, calls = self.authoring.read_proposal, []

        def changed_on_final_read(*args):
            result = read(*args)
            calls.append(True)
            if len(calls) == 3:
                self.f.protected.write_bytes(b"Changed protected owner instructions during final proposal read.\n")
            return result

        with patch.object(self.authoring, "read_proposal", side_effect=changed_on_final_read):
            # Use this exact concrete authoring instance for the observation hook.
            plans = self.plans()
            candidate = self.h.candidate
            with self.assertRaises(IM.ConflictError):
                plans.bind_arm(self.owner, "association-experiment", candidate["candidate_id"],
                    candidate["edit_authority"]["authority_id"], expected_generation=1, event_key="protected-context-bind-race")
        self.assertEqual(self.count("improvement_experiment_arms"), 0)


class MatrixTests(unittest.TestCase):
    def definition(self):
        # Closed pure metadata, no store/runtime/data independence assertion.
        from improvement_experiment_protocol import _DEFINITION
        value = {key: "a" * 64 for key in _DEFINITION}
        for key in ("experiment_id", "context_id", "target_id", "base_revision_id", "base_bundle_id", "contract_id", "case_set_id"):
            value[key] = key
        value.update(version=1, base_generation=1, context_generation=1, policy_version=1,
            repeat_policy={"kind": "pilot", "repeats": 2}, timeout_seconds=120, evidence_class="technical",
            endpoint={"kind": "binary_all_pass", "criterion_id": "content", "direction": "higher"},
            decision_policy={"id": "paired-skill-v1", "version": 1, "multiple_tests": 2},
            evaluator={"id": "synthetic", "version": "1", "hash": "b" * 64})
        return value

    def test_maximum_closed_matrix_is_complete_balanced_stable_and_under_byte_bound(self):
        from improvement_experiment_protocol import comparison_matrix
        value = self.definition()
        cases = ["case" + str(index).zfill(4) + "x" * 120 for index in range(1000)]
        matrix = comparison_matrix(value, "c" * 64, 2, "final", cases, 99)
        pairs = matrix["pairs"]
        self.assertEqual(len(pairs), 2000)
        self.assertEqual(len({pair["pair_id"] for pair in pairs}), 2000)
        self.assertEqual({(pair["case_id"], pair["repeat"]) for pair in pairs}, {(case, repeat) for case in cases for repeat in (1, 2)})
        self.assertEqual(sum(pair["first_variant"] == "baseline" for pair in pairs), 1000)
        for index in range(0, len(pairs), 2):
            self.assertNotEqual(pairs[index]["first_variant"], pairs[index + 1]["first_variant"])
        self.assertLessEqual(len(IM._json(matrix).encode("utf-8")), 1048576)
        self.assertEqual(comparison_matrix(value, "c" * 64, 2, "final", list(reversed(cases)), 99), matrix)
        self.assertNotEqual(comparison_matrix(value, "c" * 64, 2, "final", cases, 100), matrix)
        self.assertNotEqual(comparison_matrix(value, "d" * 64, 2, "final", cases, 99), matrix)

    def test_duplicate_cases_unknown_kind_and_third_arm_are_rejected(self):
        from improvement_experiment_protocol import comparison_matrix
        for args in (("c" * 64, 1, "tuning", ["same", "same"], 0),
                     ("c" * 64, 3, "tuning", ["case"], 0), ("c" * 64, 1, "other", ["case"], 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                comparison_matrix(self.definition(), *args)


if __name__ == "__main__":
    unittest.main()
