"""Technical SQL/Fernet report fixtures test selection, never native readiness.

The complete reports below are deliberately seeded host-storage states. No
native/model execution or business acceptance is claimed by those fixture rows.
"""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import json
import time
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
import improvement_evaluation as E
import improvement_experiment_protocol as P
import improvement_comparison_results as C
from agent_ledger import CallLedger
from improvement_blobs import AssetUnavailable
import test_improvement_experiment_lineage as fixtures


class ExperimentSelectionTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.owner, self.assets, self.store = self.h.owner, self.h.assets, self.h.store
        self.reports = C.ComparisonResults(self.h.h.plans())
        self.seed_technical_complete_cells()
        self.saved = self.reports.create(self.owner, self.h.comparison["id"],
            expected_generation=self.h.frozen["generation"], event_key="technical-complete-tuning-report")
        report = self.reports.read(self.owner, self.saved["report_id"])
        self.assertTrue(report["summary"]["selection_supported"])
        self.assertEqual(report["summary"]["business_eligibility"], "unverified")

    def seed_technical_complete_cells(self, *, wins=True):
        with closing(self.store._conn()) as connection:
            contract = json.loads(connection.execute("SELECT contract_json FROM improvement_contracts WHERE manifest_hash=?",
                                                    (self.h.value["contract_hash"],)).fetchone()[0])
        ledger = CallLedger(self.store)
        for position in range(self.h.frozen["cell_count"]):
            cell = P.matrix_cell(self.h.matrix, position)
            request = copy.deepcopy(self.h.request)
            request.update({key: cell[key] for key in ("case_id", "variant", "repeat")})
            request["lineage"].update(pair_id=cell["pair_id"], cell_position=position)
            suffix = str(position) if self.h.comparison["ordinal"] == 1 else "arm2_" + str(position)
            trial_id, result_id = "it_technical_comparison_" + suffix, "ir_technical_comparison_" + suffix
            raw = IM._json({"value": "technical-" + cell["variant"]}).encode()
            output_hash = self.assets.blobs.write(self.owner.brain_id, raw)
            artifact = E.TrialArtifact(contract["output"]["path"], hashlib.sha256(raw).hexdigest(), raw, raw.decode())
            criteria = [E._row(criterion, "fail" if wins and cell["variant"] == "baseline" and criterion["id"] == contract["primary_metric"]["criterion_id"] else "pass",
                "technical_storage_fixture", {"technical": True}) for criterion in contract["criteria"]]
            evaluation = E._result(contract, {"id": cell["case_id"]}, criteria, artifact)
            report = {"version": 2, "trial_id": trial_id, "attempt": 1, "execution_status": "completed",
                "request_hash": IM._hash(request), "experiment_lineage": request["lineage"], "contract_hash": request["pins"]["contract_hash"],
                "artifact_hash": output_hash, "evaluation": evaluation, "mandatory": [
                    {"id": identifier, "verdict": "pass", "reason": "technical_storage_fixture"} for identifier in sorted(C._NATIVE_CHECKS)],
                "verdict": evaluation["verdict"], "evidence_origin": "synthetic_storage_fixture"}
            report_hash = self.assets.blobs.write(self.owner.brain_id, IM._json(report).encode())
            pointer = {"result_id": result_id, "result_hash": IM._hash(report), "blob_hash": report_hash}
            stamp = time.time()
            with self.store._Tx(self.store) as connection:
                connection.execute("INSERT INTO improvement_trials VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    trial_id, self.owner.brain_id, P.logical_key_v2(request), IM._hash(request), IM._json(request), "completed", 1, 1, stamp, stamp))
                call = ledger.reserve(purpose="improve_probe", reason_ref="improvement_trial:" + trial_id + ":attempt:1",
                    brain_id=self.owner.brain_id, context_ref="improvement:association-experiment", obligation=True,
                    mirror_ref="improvement_trial:" + trial_id, attempt=1, now=stamp, c=connection)
                self.assertTrue(ledger._mark_engine_in(connection, call, "technical_fixture", "unexecuted", stamp))
                self.assertTrue(ledger._settle_in(connection, call, "ok", None, None, None, stamp))
                connection.execute("INSERT INTO improvement_trial_attempts(trial_id,brain_id,attempt,status,call_id,engine_at,result_json,created_at,updated_at) "
                    "VALUES(?,?,1,'completed',?,?,?,?,?)", (trial_id, self.owner.brain_id, call, stamp, IM._json(pointer), stamp, stamp))
                connection.execute("INSERT INTO improvement_experiment_trial_links VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    self.h.comparison["id"], self.owner.brain_id, "association-experiment", position, trial_id, IM._hash(request),
                    cell["pair_id"], cell["case_id"], cell["repeat"], cell["variant"], stamp))
                connection.execute("INSERT INTO improvement_trial_artifacts VALUES(?,?,?,?,?,?,?)", (
                    trial_id, self.owner.brain_id, 1, contract["output"]["path"], output_hash, len(raw), stamp))
                connection.execute("INSERT INTO improvement_trial_results VALUES(?,?,?,?,?,?,?)", (
                    result_id, trial_id, self.owner.brain_id, 1, pointer["result_hash"], report_hash, stamp))

    def select(self, **changes):
        return self.h.h.plans().select(self.owner, "association-experiment", "candidate", ordinal=1,
            expected_generation=changes.pop("generation", self.saved["generation"]), event_key=changes.pop("key", "select-technical-candidate"), **changes)

    def test_candidate_selection_pins_complete_exact_tuning_report_without_final_allocations(self):
        before = {table: self.h.h.count(table) for table in ("improvement_trials", "agent_calls", "improvement_qualification_claims", "improvement_case_exposures")}
        selected = self.select()
        with closing(self.store._conn()) as connection:
            row = connection.execute("SELECT * FROM improvement_experiment_selections").fetchone()
        self.assertEqual(row["choice"], "candidate")
        self.assertEqual(row["ordinal"], 1)
        self.assertEqual(json.loads(row["evidence_json"]), [[self.saved["report_id"], self.saved["report_hash"]]])
        self.assertEqual(selected["generation"], self.saved["generation"] + 1)
        self.assertEqual({table: self.h.h.count(table) for table in before}, before)

    def test_candidate_selection_replay_after_stop_and_revoke_is_cold(self):
        selected = self.select()
        self.h.h.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-selected-context")
        self.h.h.h.records().begin_stop(self.owner, "association-experiment", kind="cancelled", reason="owner_requested",
            expected_generation=selected["generation"], event_key="stop-selected-experiment")
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Historical selection replay cannot decrypt")), \
             patch.object(self.h.h.host, "_snapshot_observe", side_effect=AssertionError("Historical selection replay cannot observe")):
            self.assertEqual(self.select(), selected)

    def test_newest_incomplete_report_cannot_be_ignored_for_an_older_favorable_report(self):
        with self.store._Tx(self.store) as connection:
            connection.execute("UPDATE improvement_trial_attempts SET result_json=NULL WHERE trial_id='it_technical_comparison_0'")
        newer = self.reports.create(self.owner, self.h.comparison["id"], expected_generation=self.saved["generation"],
                                    event_key="technical-newer-incomplete-report")
        with self.assertRaises((PermissionError, AssetUnavailable, IM.ConflictError)):
            self.select(generation=newer["generation"])
        self.assertEqual(self.h.h.count("improvement_experiment_selections"), 0)


    def incomplete_then_restore(self, *, backward_clock=False):
        with self.store._Tx(self.store) as connection:
            pointer = connection.execute("SELECT result_json FROM improvement_trial_attempts WHERE trial_id='it_technical_comparison_0'").fetchone()[0]
            connection.execute("UPDATE improvement_trial_attempts SET result_json=NULL WHERE trial_id='it_technical_comparison_0'")
        clock = patch("time.time", return_value=1) if backward_clock else patch("time.time", wraps=time.time)
        with clock:
            newer = self.reports.create(self.owner, self.h.comparison["id"], expected_generation=self.saved["generation"],
                                        event_key="technical-later-incomplete-report")
        with self.store._Tx(self.store) as connection:
            connection.execute("UPDATE improvement_trial_attempts SET result_json=? WHERE trial_id='it_technical_comparison_0'", (pointer,))
        return newer

    def test_publication_order_does_not_regress_when_wall_clock_moves_backward(self):
        newer = self.incomplete_then_restore(backward_clock=True)
        with self.assertRaises((PermissionError, AssetUnavailable, IM.ConflictError)):
            self.select(generation=newer["generation"])
        self.assertEqual(self.h.h.count("improvement_experiment_selections"), 0)

    def test_explicit_latest_republication_can_pin_an_existing_immutable_report(self):
        newer = self.incomplete_then_restore()
        republished = self.reports.create(self.owner, self.h.comparison["id"], expected_generation=newer["generation"],
                                          event_key="technical-republish-complete-report")
        self.assertEqual(republished["report_id"], self.saved["report_id"])
        selected = self.select(generation=republished["generation"])
        self.assertEqual(selected["generation"], republished["generation"] + 1)


    def test_other_declared_tuning_arm_cannot_be_abandoned_after_a_favorable_result(self):
        _, frozen = self.declare_second()
        with self.assertRaisesRegex(PermissionError, "report_unavailable"):
            self.select(generation=frozen["generation"])
        self.assertEqual(self.h.h.count("improvement_experiment_selections"), 0)

    def declare_second(self):
        candidate = self.h.h.h.candidate
        proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, candidate["patch_hash"]))
        proposal["replacement"] = "Retain owner rules.\nCheck every subtotal twice.\n"
        second = self.h.h.authoring.stage(self.owner, "association-experiment", proposal, event_key="second-selection-proposal")
        bound = self.h.h.plans().bind_arm(self.owner, "association-experiment", second["candidate_id"],
            second["edit_authority"]["authority_id"], expected_generation=self.saved["generation"], event_key="second-selection-arm")
        frozen = self.h.h.plans().freeze(self.owner, "association-experiment", 2, "tuning",
            expected_generation=bound["generation"], event_key="second-selection-tuning")
        return bound, frozen

    def test_all_declared_reports_are_pinned_even_when_the_other_arm_only_ties(self):
        bound, frozen = self.declare_second()
        with closing(self.store._conn()) as connection:
            comparison = dict(connection.execute("SELECT * FROM improvement_comparisons WHERE id=?", (frozen["comparison_id"],)).fetchone())
        matrix = json.loads(self.assets.blobs.read(self.owner.brain_id, comparison["matrix_blob_hash"]))
        request = copy.deepcopy(self.h.request)
        request["pins"]["candidate_bundle_hash"] = bound["envelope_hash"]
        request["lineage"].update(comparison_id=comparison["id"], comparison_hash=P.comparison_hash(comparison),
            matrix_hash=comparison["matrix_hash"], arm_ordinal=2, arm_hash=bound["envelope_hash"], candidate_id=bound["candidate_id"])
        with patch.object(self.h, "comparison", comparison), patch.object(self.h, "matrix", matrix), patch.object(self.h, "request", request):
            self.seed_technical_complete_cells(wins=False)
        other = self.reports.create(self.owner, comparison["id"], expected_generation=frozen["generation"], event_key="complete-other-tuning-arm")
        summary = self.reports.read(self.owner, other["report_id"])["summary"]
        self.assertTrue(summary["complete"])
        self.assertFalse(summary["selection_supported"])
        selected = self.select(generation=other["generation"])
        self.assertEqual(selected["generation"], other["generation"] + 1)
        with closing(self.store._conn()) as connection:
            evidence = json.loads(connection.execute("SELECT evidence_json FROM improvement_experiment_selections").fetchone()[0])
        self.assertEqual(evidence, [[self.saved["report_id"], self.saved["report_hash"]], [other["report_id"], other["report_hash"]]])


    def test_changed_report_policy_cannot_authorize_candidate(self):
        original = C.ComparisonResults._policy
        with patch.object(C.ComparisonResults, "_policy", side_effect=lambda value: {**original(value), "source_hash": "0" * 64}):
            with self.assertRaisesRegex(PermissionError, "report_unavailable"):
                self.select()
        self.assertEqual(self.h.h.count("improvement_experiment_selections"), 0)

    def test_source_drift_during_last_policy_read_rolls_back_selection(self):
        original, calls = C.ComparisonResults._policy, []

        def drift(value):
            result = original(value)
            calls.append(True)
            if len(calls) == 2:
                self.h.h.f.protected.write_bytes(b"Protected source changed at final selection policy read.\n")
            return result

        with patch.object(C.ComparisonResults, "_policy", side_effect=drift), self.assertRaises(IM.ConflictError):
            self.select()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.h.h.count("improvement_experiment_selections"), 0)

    def test_chosen_authority_revocation_during_slow_report_read_cannot_seal(self):
        original, changed = C.ComparisonResults.read, []

        def revoke(reader, *args, **kwargs):
            result = original(reader, *args, **kwargs)
            if not changed:
                changed.append(True)
                self.h.h.authoring.records.revoke_authority(self.owner, self.h.bound["authority_id"],
                    expected_generation=1, event_key="revoke-during-selection-read")
            return result

        with patch.object(C.ComparisonResults, "read", revoke), self.assertRaises((PermissionError, IM.ConflictError)):
            self.select()
        self.assertEqual(self.h.h.count("improvement_experiment_selections"), 0)

    def test_candidate_selection_seals_edits_but_still_does_not_open_final_execution(self):
        selected = self.select()
        candidate = self.h.h.h.candidate
        proposal = json.loads(self.assets.blobs.read(self.owner.brain_id, candidate["patch_hash"]))
        with self.assertRaisesRegex(IM.ConflictError, "sealed"):
            self.h.h.authoring.stage(self.owner, "association-experiment", proposal, event_key="no-selected-arm-edits")
        frozen = self.h.h.plans().freeze(self.owner, "association-experiment", 1, "final",
            expected_generation=selected["generation"], event_key="freeze-after-actual-report-selection")
        self.assertEqual(frozen["kind"], "final")
        from improvement_experiment_admission import ExperimentAdmission
        before = (self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"), self.h.h.count("improvement_qualification_claims"))
        with self.assertRaises((PermissionError, IM.ConflictError)):
            ExperimentAdmission(self.h.h.plans()).admit(self.owner, "association-experiment", frozen["comparison_id"], 0,
                expected_generation=frozen["generation"], event_key="final-remains-unavailable-after-selection")
        self.assertEqual((self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"), self.h.h.count("improvement_qualification_claims")), before)


if __name__ == "__main__":
    unittest.main()
