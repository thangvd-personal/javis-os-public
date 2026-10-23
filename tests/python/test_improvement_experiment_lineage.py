"""Actual frozen B plans bind v2 requests to scoped immutable SQL/blob history."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import json
import time
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
import improvement_experiment_protocol as P
import test_improvement_comparison_plans as fixtures
from improvement_trials import TrialStore, _request

if importlib.util.find_spec("improvement_experiment_lineage"):
    from improvement_experiment_lineage import load_lineage
else:
    load_lineage = None


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.ComparisonPlanTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.owner, self.assets, self.store = self.h.owner, self.h.assets, self.h.store
        self.created, self.bound = self.h.create(), self.h.bind()
        self.frozen = self.h.plans().freeze(self.owner, "association-experiment", 1, "tuning",
            expected_generation=self.bound["generation"], event_key="lineage-tuning-matrix")
        with closing(self.store._conn()) as connection:
            self.comparison = dict(connection.execute("SELECT * FROM improvement_comparisons").fetchone())
            self.value = json.loads(connection.execute("SELECT definition_json FROM improvement_experiments").fetchone()[0])
        self.matrix = json.loads(self.assets.blobs.read(self.owner.brain_id, self.comparison["matrix_blob_hash"]))
        position = next(index for index in range(self.frozen["cell_count"]) if P.matrix_cell(self.matrix, index)["variant"] == "baseline")
        cell = P.matrix_cell(self.matrix, position)
        self.request = copy.deepcopy(self.h.h.admitted.request)
        self.request.pop("qualification_id", None)
        self.request.update(version=2, case_id=cell["case_id"], variant=cell["variant"], repeat=cell["repeat"],
            deadline_at=time.time() + 120, lineage={"version": 1, "context_id": self.value["context_id"],
                "definition_hash": self.created["definition_hash"], "comparison_id": self.comparison["id"],
                "comparison_hash": P.comparison_hash(self.comparison), "matrix_hash": self.comparison["matrix_hash"],
                "arm_ordinal": 1, "arm_hash": self.bound["envelope_hash"], "candidate_id": self.h.h.candidate["candidate_id"],
                "pair_id": cell["pair_id"], "cell_position": position})
        self.request["pins"]["candidate_bundle_hash"] = self.bound["envelope_hash"]

    def load(self, request=None, *, current=True, require_link=False, trial_id=None, brain=None):
        self.assertTrue(callable(load_lineage), "Scoped immutable experiment lineage reader is absent")
        with closing(self.store._conn()) as connection:
            return load_lineage(connection, brain or self.owner.brain_id, self.request if request is None else request,
                blobs=self.assets.blobs, current=current, require_link=require_link, trial_id=trial_id)

    def test_actual_unlinked_plan_verifies_all_immutable_lineage_without_answer_reads(self):
        answers = {IM._hash(case["answer"]) for case in self.h.h.h.f.cases}
        with patch.object(self.assets.blobs, "read", wraps=self.assets.blobs.read) as reads:
            result = self.load()
        self.assertEqual(result["definition"], self.value)
        self.assertEqual(result["cell"], P.matrix_cell(self.matrix, self.request["lineage"]["cell_position"]))
        self.assertFalse(answers & {call.args[1] for call in reads.call_args_list})
        with self.assertRaises(PermissionError):
            self.load(require_link=True)

    def test_cross_brain_pair_case_variant_arm_and_comparison_substitutions_reject(self):
        changes = [lambda r: r["lineage"].update(pair_id="0" * 64), lambda r: r.update(case_id="case-2"),
            lambda r: r.update(variant="candidate"), lambda r: r["lineage"].update(candidate_id="other-candidate"),
            lambda r: r["lineage"].update(comparison_hash="0" * 64), lambda r: r["lineage"].update(context_id="other-context"),
            lambda r: r["lineage"].update(matrix_hash="0" * 64)]
        for mutate in changes:
            request = copy.deepcopy(self.request)
            mutate(request)
            with self.subTest(mutate=mutate), self.assertRaises((PermissionError, IM.ConflictError)):
                self.load(request)
        with self.assertRaises(PermissionError):
            self.load(brain=self.h.f.other.brain_id)

    def test_revoked_chosen_authority_denies_current_but_not_immutable_history(self):
        self.h.authoring.records.revoke_authority(self.owner, self.bound["authority_id"],
            expected_generation=1, event_key="revoke-lineage-authority")
        with self.assertRaises(PermissionError):
            self.load()
        self.assertEqual(self.load(current=False)["definition"], self.value)

    def test_stopping_fence_denies_current_history_remains_without_hot_probes(self):
        self.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.frozen["generation"], event_key="stop-before-lineage-dispatch")
        with self.assertRaises(PermissionError):
            self.load()
        with patch.object(self.h.host, "_snapshot_observe", side_effect=AssertionError("No native/source probe")):
            self.assertEqual(self.load(current=False)["definition"], self.value)

    def reserve(self, key="reserve-actual-v2-baseline", request=None):
        return TrialStore(self.assets).reserve(self.owner, self.request if request is None else request, event_key=key)

    def test_actual_v2_reservation_atomically_links_real_trial_and_existing_call(self):
        self.assertEqual(_request(self.request), self.request)
        trial = self.reserve()
        with closing(self.store._conn()) as connection:
            link = connection.execute("SELECT * FROM improvement_experiment_trial_links").fetchone()
            call = connection.execute("SELECT c.* FROM agent_calls c JOIN improvement_trial_attempts a ON a.call_id=c.id "
                                      "WHERE a.trial_id=?", (trial["trial_id"],)).fetchone()
        self.assertEqual(link["trial_id"], trial["trial_id"])
        self.assertEqual(link["request_hash"], IM._hash(self.request))
        self.assertIsNone(call["engine_at"])
        self.assertEqual(trial["request"]["version"], 2)
        self.assertEqual(self.load(require_link=True, trial_id=trial["trial_id"])["cell"]["variant"], "baseline")
        self.assertEqual(self.reserve(), trial)
        with self.assertRaises(IM.ConflictError):
            self.load(require_link=True, trial_id=self.h.h.admitted.trial_id)

    def test_changed_variant_denies_lower_reservation_before_any_blob_or_call(self):
        candidate = copy.deepcopy(self.request)
        candidate["lineage"]["cell_position"] ^= 1
        candidate["variant"] = "candidate"
        before = (self.h.count("improvement_trials"), self.h.count("agent_calls"))
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Changed candidate must not decrypt")):
            with self.assertRaisesRegex(PermissionError, "candidate_execution_unavailable"):
                self.reserve(request=candidate)
            with self.assertRaisesRegex(PermissionError, "candidate_execution_unavailable"):
                self.h.host.context_loader(self.owner, candidate)
        self.assertEqual((self.h.count("improvement_trials"), self.h.count("agent_calls")), before)
        self.assertEqual(self.h.count("improvement_experiment_trial_links"), 0)

    def test_reservation_event_failure_rolls_back_trial_call_and_link(self):
        before = (self.h.count("improvement_trials"), self.h.count("agent_calls"))
        original = self.assets._event

        def fail(connection, principal, key, operation, payload, result):
            if operation == "trial_reserve":
                raise OSError("Synthetic reservation publication failure")
            return original(connection, principal, key, operation, payload, result)

        with patch.object(self.assets, "_event", side_effect=fail), self.assertRaises(OSError):
            self.reserve()
        self.assertEqual((self.h.count("improvement_trials"), self.h.count("agent_calls")), before)
        self.assertEqual(self.h.count("improvement_experiment_trial_links"), 0)

    def test_stopping_denies_claim_but_preserves_exact_historical_link_and_replay(self):
        trial = self.reserve()
        self.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.frozen["generation"], event_key="stop-linked-v2")
        with self.assertRaises(PermissionError):
            TrialStore(self.assets).claim(self.owner, trial["trial_id"], expected_generation=1)
        self.assertEqual(self.load(current=False, require_link=True, trial_id=trial["trial_id"])["cell"]["variant"], "baseline")
        self.assertEqual(self.reserve(), trial)

    def test_host_execution_callbacks_require_actual_link_before_loading_context(self):
        with self.assertRaises(PermissionError):
            self.h.host.context_loader(self.owner, self.request)
        with self.assertRaises(PermissionError):
            self.h.host.live_guard(self.owner, self.request)
        trial = self.reserve()
        context = self.h.host.context_loader(self.owner, trial["request"])
        self.assertEqual(IM._hash(context), self.value["baseline_context_hash"])
        self.assertEqual(self.h.host.live_guard(self.owner, trial["request"])["grant_hash"], self.value["grant_hash"])

    def test_host_rechecks_stop_after_slow_actual_source_observation(self):
        trial = self.reserve()
        observe, stopped = self.h.host._observe, []

        def stop_during_source(*args):
            result = observe(*args)
            if not stopped:
                stopped.append(True)
                self.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                    expected_generation=self.frozen["generation"], event_key="host-guard-stop-race")
            return result

        with patch.object(self.h.host, "_observe", side_effect=stop_during_source), self.assertRaises(PermissionError):
            self.h.host.live_guard(self.owner, trial["request"])

    def test_downgraded_v1_request_cannot_bypass_experiment_ownership_or_stop(self):
        downgraded = copy.deepcopy(self.request)
        downgraded.update(version=1)
        downgraded.pop("lineage")
        downgraded["pins"]["candidate_bundle_hash"] = downgraded["pins"]["base_bundle_hash"]
        before = (self.h.count("improvement_trials"), self.h.count("agent_calls"))
        for phase in ("probing", "stopping"):
            if phase == "stopping":
                self.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
                    expected_generation=self.frozen["generation"], event_key="stop-before-downgrade")
            with self.subTest(phase=phase), patch.object(self.assets.blobs, "read", side_effect=AssertionError("Downgrade cannot decrypt")):
                with self.assertRaisesRegex(PermissionError, "version_required"):
                    self.reserve(key="downgraded-" + phase, request=downgraded)
                with self.assertRaisesRegex(PermissionError, "version_required"):
                    self.h.host.context_loader(self.owner, downgraded)
        self.assertEqual((self.h.count("improvement_trials"), self.h.count("agent_calls")), before)
        self.assertEqual(self.h.count("improvement_experiment_trial_links"), 0)
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Legacy history remains cold")):
            self.assertEqual(self.h.h.h.admit(), self.h.h.admitted)

    def _mutate_during_final_host_matrix_read(self, action):
        trial = self.reserve()
        original, reads = self.assets.blobs.read, []

        def final_read(brain, digest):
            result = original(brain, digest)
            if digest == self.comparison["matrix_blob_hash"]:
                reads.append(True)
                if len(reads) == 2:
                    action()
            return result

        with patch.object(self.assets.blobs, "read", side_effect=final_read), self.assertRaises((PermissionError, IM.ConflictError)):
            self.h.host.live_guard(self.owner, trial["request"])
        self.assertEqual(len(reads), 2)

    def test_final_matrix_read_rechecks_chosen_grant_after_actual_revocation(self):
        self._mutate_during_final_host_matrix_read(lambda:
            self.h.authoring.records.revoke_authority(self.owner, self.bound["authority_id"],
                expected_generation=1, event_key="revoke-during-final-matrix-read"))

    def test_final_matrix_read_rechecks_stop_fence_without_selection_row(self):
        self._mutate_during_final_host_matrix_read(lambda:
            self.h.h.records().begin_stop(self.owner, "association-experiment", kind="cancelled", reason="owner_requested",
                expected_generation=self.frozen["generation"], event_key="stop-during-final-matrix-read"))

    def _mutate_during_final_host_fixture_read(self, action):
        trial = self.reserve()
        original, matrix_reads, changed = self.assets.blobs.read, [], []
        with closing(self.store._conn()) as connection:
            case = json.loads(connection.execute("SELECT case_json FROM improvement_cases WHERE brain_id=? AND case_set_id=? AND id=?",
                (self.owner.brain_id, self.request["case_set_id"], self.request["case_id"])).fetchone()[0])
            fixture = connection.execute("SELECT fixture_hash FROM improvement_snapshots WHERE id=? AND brain_id=?",
                                         (case["snapshot_ids"][0], self.owner.brain_id)).fetchone()[0]

        def final_read(brain, digest):
            result = original(brain, digest)
            if digest == self.comparison["matrix_blob_hash"]:
                matrix_reads.append(True)
            if digest == fixture and len(matrix_reads) == 2 and not changed:
                changed.append(True)
                action()
            return result

        with patch.object(self.assets.blobs, "read", side_effect=final_read), self.assertRaises((PermissionError, IM.ConflictError)):
            self.h.host.live_guard(self.owner, trial["request"])
        self.assertTrue(changed)

    def test_final_fixture_read_rechecks_chosen_grant_after_revocation(self):
        self._mutate_during_final_host_fixture_read(lambda:
            self.h.authoring.records.revoke_authority(self.owner, self.bound["authority_id"], expected_generation=1,
                event_key="revoke-during-final-fixture-read"))

    def test_final_fixture_read_rechecks_stop_fence_without_selection(self):
        self._mutate_during_final_host_fixture_read(lambda:
            self.h.h.records().begin_stop(self.owner, "association-experiment", kind="cancelled", reason="owner_requested",
                expected_generation=self.frozen["generation"], event_key="stop-during-final-fixture-read"))

    def test_final_fixture_read_reobserves_protected_source_after_late_change(self):
        self._mutate_during_final_host_fixture_read(lambda:
            self.h.f.protected.write_bytes(b"Changed protected source during final fixture decryption.\n"))

    def test_source_observer_revalidates_protected_capture_after_encrypted_target_read(self):
        trial = self.reserve()
        original, matrix_reads, changed = self.assets.blobs.read, [], []

        def final_read(brain, digest):
            result = original(brain, digest)
            if digest == self.comparison["matrix_blob_hash"]:
                matrix_reads.append(True)
            if digest == self.value["base_hash"] and len(matrix_reads) == 2 and not changed:
                changed.append(True)
                self.h.f.protected.write_bytes(b"Changed protected source during observer target decryption.\n")
            return result

        with patch.object(self.assets.blobs, "read", side_effect=final_read), self.assertRaises(IM.ConflictError):
            self.h.host.live_guard(self.owner, trial["request"])
        self.assertTrue(changed)

    def test_second_challenger_reserves_distinct_fresh_baseline_for_same_case_repeat(self):
        first = self.reserve()
        candidate = self.h.h.candidate
        proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, candidate["patch_hash"]))
        proposal["replacement"] = "Retain owner rules.\nCheck unmatched rows before totals.\n"
        second = self.h.authoring.stage(self.owner, "association-experiment", proposal, event_key="v2-other-challenger")
        bound = self.h.plans().bind_arm(self.owner, "association-experiment", second["candidate_id"],
            second["edit_authority"]["authority_id"], expected_generation=self.frozen["generation"], event_key="v2-bind-other-challenger")
        frozen = self.h.plans().freeze(self.owner, "association-experiment", 2, "tuning",
            expected_generation=bound["generation"], event_key="v2-freeze-other-challenger")
        with closing(self.store._conn()) as connection:
            comparison = dict(connection.execute("SELECT * FROM improvement_comparisons WHERE id=?", (frozen["comparison_id"],)).fetchone())
        matrix = json.loads(self.assets.blobs.read(self.owner.brain_id, comparison["matrix_blob_hash"]))
        position = next(i for i in range(frozen["cell_count"]) if P.matrix_cell(matrix, i)["variant"] == "baseline")
        request = copy.deepcopy(self.request)
        request["pins"]["candidate_bundle_hash"] = bound["envelope_hash"]
        request["lineage"].update(comparison_id=comparison["id"], comparison_hash=P.comparison_hash(comparison),
            matrix_hash=comparison["matrix_hash"], arm_ordinal=2, arm_hash=bound["envelope_hash"],
            candidate_id=second["candidate_id"], pair_id=P.matrix_cell(matrix, position)["pair_id"], cell_position=position)
        other = self.reserve(key="reserve-actual-second-baseline", request=request)
        self.assertNotEqual(first["trial_id"], other["trial_id"])
        self.assertEqual((request["case_id"], request["repeat"], request["variant"]),
                         (self.request["case_id"], self.request["repeat"], self.request["variant"]))
        self.assertEqual(self.h.count("improvement_experiment_trial_links"), 2)
        self.assertNotEqual(first["attempts"][0]["call_id"], other["attempts"][0]["call_id"])


if __name__ == "__main__":
    unittest.main()
