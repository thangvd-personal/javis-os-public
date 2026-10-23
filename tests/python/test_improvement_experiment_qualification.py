"""B final readiness cannot be erased by legacy pins or renamed holdout metadata."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

import improvement_cases as IC
import improvement_store as IM
from improvement_trials import TrialStore
from improvement_trial_admission import TrialAdmission
from improvement_experiment_admission import ExperimentAdmission
import improvement_experiment_protocol as P
import test_improvement_experiment_lineage as fixtures


class ExperimentQualificationTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.owner, self.assets, self.store = self.h.owner, self.h.assets, self.h.store
        self.cases = IC.CaseStore(self.assets)
        self.legacy = self.h.h.h.admitted.request

    def counts(self):
        return {table: self.h.h.count(table) for table in ("improvement_qualification", "improvement_qualification_claims",
            "improvement_case_exposures", "improvement_trials", "agent_calls", "improvement_events")}

    def reserve(self, *, case_set_id=None, pins=None, key="no-early-qualification", **kwargs):
        return self.cases.reserve_qualification(self.owner, case_set_id or self.legacy["case_set_id"],
            self.legacy["pins"] if pins is None else pins, event_key=key, **kwargs)

    def test_direct_legacy_nine_pins_cannot_open_experiment_qualification(self):
        before = self.counts()
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Unready qualification cannot decrypt")):
            with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
                self.reserve()
        self.assertEqual(self.counts(), before)

    def test_renamed_experiment_id_cannot_hide_the_frozen_b_case_set(self):
        pins = {**self.legacy["pins"], "experiment_id": "renamed-legacy-experiment"}
        before = self.counts()
        with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
            self.reserve(pins=pins)
        self.assertEqual(self.counts(), before)

    def test_renamed_case_set_and_family_still_bind_existing_qualification_source(self):
        source = self.h.h.h.h.f
        changed = [{**case, "id": "renamed-" + case["id"], "family_key": "renamed-" + case["family_key"], "evaluator_refs": []}
                   for case in source.cases]
        cloned = self.cases.put_case_set(self.owner, changed, 797, event_key="renamed-family-case-set")
        pins = {**self.legacy["pins"], "experiment_id": "renamed-everything", "case_set_hash": cloned["manifest_hash"]}
        before = self.counts()
        with patch.object(self.cases, "_row", side_effect=AssertionError("Readiness guard must precede legacy claim handling")):
            with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
                self.reserve(case_set_id=cloned["case_set_id"], pins=pins)
        self.assertEqual(self.counts(), before)

    def test_direct_consumption_cannot_bypass_readiness_or_stop(self):
        with closing(self.store._conn()) as connection:
            reservation = connection.execute("SELECT id FROM improvement_qualification WHERE case_set_id=? AND brain_id=?",
                                            (self.legacy["case_set_id"], self.owner.brain_id)).fetchone()[0]
        self.h.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.h.frozen["generation"], event_key="stop-before-direct-consume")
        before = self.counts()
        with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
            self.cases.consume_qualification(self.owner, reservation, self.legacy["pins"], event_key="no-direct-consume")
        self.assertEqual(self.counts(), before)
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT status FROM improvement_qualification WHERE id=?", (reservation,)).fetchone()[0], "reserved")

    def test_historical_reserve_event_replays_after_stop_without_new_authority(self):
        with closing(self.store._conn()) as connection:
            saved = connection.execute("SELECT event_key,result_json FROM improvement_events WHERE operation='qualification_reserve'").fetchone()
        self.h.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.h.frozen["generation"], event_key="stop-before-historical-claim-read")
        before = self.counts()
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")):
            self.assertEqual(self.reserve(key=saved["event_key"]), json.loads(saved["result_json"]))
        self.assertEqual(self.counts(), before)

    def clone(self, *, relabel=False, rename_family=True):
        source = self.h.h.h.h.f
        changed = [{**case, "id": "alias-" + case["id"],
            "family_key": "alias-" + case["family_key"] if rename_family else case["family_key"],
            "split": {"qualification": "tuning", "tuning": "qualification"}.get(case["split"], case["split"]) if relabel else case["split"],
            "evaluator_refs": []} for case in source.cases]
        saved = self.cases.put_case_set(self.owner, changed, 896, event_key="alias-case-set")
        return saved, changed

    def test_relabeling_original_qualification_as_tuning_does_not_erase_ownership(self):
        cloned, _ = self.clone(relabel=True)
        pins = {**self.legacy["pins"], "experiment_id": "renamed-reclassified", "case_set_hash": cloned["manifest_hash"]}
        before = self.counts()
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("No unavailable private input")):
            with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
                self.reserve(case_set_id=cloned["case_set_id"], pins=pins)
        self.assertEqual(self.counts(), before)

    def test_legacy_trial_cannot_hide_ownership_by_renaming_every_identity(self):
        cloned, cases = self.clone(relabel=True)
        request = copy.deepcopy(self.legacy)
        request.pop("qualification_id", None)
        request.update(case_set_id=cloned["case_set_id"], case_id=next(case["id"] for case in cases if case["split"] == "tuning"))
        request["pins"].update(experiment_id="renamed-reclassified", case_set_hash=cloned["manifest_hash"])
        before = self.counts()
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("No unavailable private input")):
            with self.assertRaisesRegex(PermissionError, "experiment_request_version_required"):
                TrialStore(self.assets).reserve(self.owner, request, event_key="no-renamed-legacy-trial")
            with self.assertRaisesRegex(PermissionError, "experiment_request_version_required"):
                self.h.h.host.context_loader(self.owner, request)
        self.assertEqual(self.counts(), before)

    def test_concurrent_alias_reservations_create_no_claim_or_event(self):
        cloned, _ = self.clone()
        pins = {**self.legacy["pins"], "experiment_id": "concurrent-renamed", "case_set_hash": cloned["manifest_hash"]}
        before = self.counts()

        def reserve(index):
            try:
                self.reserve(case_set_id=cloned["case_set_id"], pins=pins, key="concurrent-unavailable-" + str(index))
            except PermissionError as error:
                return str(error)
            return "unexpected_success"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, range(2)))
        self.assertEqual(outcomes, ["experiment_qualification_lineage_required"] * 2)
        self.assertEqual(self.counts(), before)

    def test_ownership_is_brain_scoped_and_does_not_blanket_block_unrelated_work(self):
        from improvement_experiment_qualification import experiment_owned
        with closing(self.store._conn()) as connection:
            self.assertTrue(experiment_owned(connection, self.owner.brain_id, self.legacy["case_set_id"], self.legacy["pins"]))
            self.assertFalse(experiment_owned(connection, self.h.h.f.other.brain_id, self.legacy["case_set_id"], self.legacy["pins"]))
            self.assertFalse(experiment_owned(connection, self.owner.brain_id, "unrelated-case-set",
                {**self.legacy["pins"], "experiment_id": "unrelated-experiment", "case_set_hash": "0" * 64}))

    def test_existing_consumed_row_cannot_authorize_a_new_b_consumption_event(self):
        with self.store._Tx(self.store) as connection:
            reservation = connection.execute("SELECT id FROM improvement_qualification WHERE case_set_id=? AND brain_id=?",
                                            (self.legacy["case_set_id"], self.owner.brain_id)).fetchone()[0]
            # Technical historical-state fixture; this is not new consumption authority.
            connection.execute("UPDATE improvement_qualification SET status='consumed',consumed_at=1 WHERE id=?", (reservation,))
        before = self.counts()
        with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
            self.cases.consume_qualification(self.owner, reservation, self.legacy["pins"], event_key="no-reuse-consumed-b-row")
        self.assertEqual(self.counts(), before)

    def test_renamed_legacy_owner_admission_rejects_before_source_or_native_preflight(self):
        admission = TrialAdmission(self.h.h.host)
        public = {**self.h.h.h.h.public, "experiment_id": "renamed-owner-admission", "event_key": "no-renamed-owner-start"}
        before = self.counts()
        with patch.object(admission, "_prepare", side_effect=AssertionError("Ownership check must precede all preparation")):
            with self.assertRaisesRegex(PermissionError, "experiment_request_version_required"):
                admission.admit(self.owner, public)
        self.assertEqual(self.counts(), before)

    def final_requests(self):
        # Explicit technical DB fixture for the still-gated post-selection state.
        # This does not claim a production tuning report or accepted proposal.
        key = "technical-final-selection-fixture"
        with self.store._Tx(self.store) as connection:
            connection.execute("INSERT INTO improvement_experiment_selections VALUES(?,?,?,?,?,?,?,?)", (
                "association-experiment", self.owner.brain_id, "candidate", 1, self.h.created["definition_hash"], "[]", key, time.time()))
            self.assets._event(connection, self.owner, key, "technical_final_fixture", {}, {"technical": True})
        frozen = self.h.h.plans().freeze(self.owner, "association-experiment", 1, "final",
            expected_generation=self.h.frozen["generation"], event_key="technical-final-matrix")
        with closing(self.store._conn()) as connection:
            comparison = dict(connection.execute("SELECT * FROM improvement_comparisons WHERE id=?", (frozen["comparison_id"],)).fetchone())
            splits = {row["id"]: row["split"] for row in connection.execute("SELECT id,split FROM improvement_cases WHERE case_set_id=?",
                                                                           (self.legacy["case_set_id"],))}
        matrix = json.loads(self.assets.blobs.read(self.owner.brain_id, comparison["matrix_blob_hash"]))
        requests = []
        for position in range(frozen["cell_count"]):
            cell = P.matrix_cell(matrix, position)
            request = copy.deepcopy(self.h.request)
            request.update({key: cell[key] for key in ("case_id", "repeat", "variant")})
            request["lineage"].update(comparison_id=comparison["id"], comparison_hash=P.comparison_hash(comparison),
                matrix_hash=comparison["matrix_hash"], pair_id=cell["pair_id"], cell_position=position)
            requests.append((splits[cell["case_id"]], request))
        return frozen, requests

    def test_actual_frozen_final_blocks_both_variants_and_regression_without_allocations(self):
        frozen, requests = self.final_requests()
        answers = {IM._hash(case["answer"]) for case in self.h.h.h.h.f.cases}
        before = self.counts()
        seen = set()
        with patch.object(self.assets.blobs, "read", wraps=self.assets.blobs.read) as reads:
            for split, request in requests:
                seen.add((split, request["variant"]))
                # Prove that immutable final lineage is valid before testing its
                # separate missing C/D execution dependency.
                self.assertEqual(self.h.load(request)["comparison"]["kind"], "final")
                with self.subTest(split=split, variant=request["variant"]):
                    with self.assertRaisesRegex(PermissionError, "experiment_(?:final|candidate)_execution_unavailable"):
                        TrialStore(self.assets).reserve(self.owner, request, event_key="no-final-" + str(request["lineage"]["cell_position"]))
                    with self.assertRaisesRegex(PermissionError, "experiment_(?:final|candidate)_execution_unavailable"):
                        with closing(self.store._conn()) as connection:
                            self.h.h.host._bound(connection, self.owner, request, allow_unlinked=True)
                    with self.assertRaises(PermissionError):
                        self.h.h.host.context_loader(self.owner, request)
                    with self.assertRaises((PermissionError, IM.ConflictError)):
                        ExperimentAdmission(self.h.h.plans()).admit(self.owner, "association-experiment",
                            request["lineage"]["comparison_id"], request["lineage"]["cell_position"],
                            expected_generation=frozen["generation"], event_key="no-final-admission-" + str(request["lineage"]["cell_position"]))
        self.assertEqual(seen, {(split, variant) for split in ("qualification", "regression") for variant in ("baseline", "candidate")})
        self.assertFalse(answers & {call.args[1] for call in reads.call_args_list})
        self.assertEqual(self.counts(), before)

    def test_actual_pre_b_consumption_replays_after_stop_but_new_event_is_denied(self):
        from test_improvement_comparison_plans import ComparisonPlanTests
        previous = ComparisonPlanTests()
        previous.setUp()
        self.addCleanup(previous.doCleanups)
        cases = IC.CaseStore(previous.assets)
        request = previous.h.admitted.request
        consumed = cases.consume_qualification(previous.owner, request["qualification_id"], request["pins"],
            event_key="actual-pre-b-consumption")
        created = previous.create()
        previous.plans().select(previous.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=created["generation"], event_key="keep-base-after-historical-consumption")
        with patch.object(previous.assets.blobs, "read", side_effect=AssertionError("Historical replay cannot decrypt")):
            self.assertEqual(cases.consume_qualification(previous.owner, request["qualification_id"], request["pins"],
                event_key="actual-pre-b-consumption"), consumed)
            with self.assertRaisesRegex(PermissionError, "experiment_qualification"):
                cases.consume_qualification(previous.owner, request["qualification_id"], request["pins"],
                    event_key="new-event-is-not-replay")

    def test_keep_base_retains_existing_global_claims_without_allocating_final_work(self):
        before = self.counts()
        self.h.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.h.frozen["generation"], event_key="keep-base-no-final-qualification")
        after = self.counts()
        self.assertEqual({key: count for key, count in after.items() if key != "improvement_events"},
                         {key: count for key, count in before.items() if key != "improvement_events"})
        self.assertEqual(after["improvement_events"], before["improvement_events"] + 1)


if __name__ == "__main__":
    unittest.main()
