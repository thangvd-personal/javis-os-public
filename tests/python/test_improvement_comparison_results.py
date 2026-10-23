"""Pure paired-case arithmetic is technical evidence, never dataset acceptance."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import unittest

import improvement_store as IM
import improvement_experiment_protocol as P
import improvement_comparison_results as C

if importlib.util.find_spec("improvement_comparison_results"):
    from improvement_comparison_results import aggregate
else:
    aggregate = None


def dataset(*, kind="final", qualification=20, regression=12, tuning=12, pilot=True, wins=6):
    contract = {"primary_metric": {"criterion_id": "content", "direction": "higher", "aggregation": "all_pass"},
        "criteria": [{"id": "shape", "category": "mandatory", "critical": True},
                     {"id": "protected", "category": "business", "critical": True},
                     {"id": "content", "category": "business", "critical": False}]}
    cases = []
    for split, count in (("qualification", qualification), ("regression", regression), ("tuning", tuning)):
        for index in range(count):
            identifier = split + "-" + str(index)
            cases.append({"id": identifier, "family_key": "family-" + identifier, "split": split,
                "fingerprints": ["family:" + IM._hash(identifier), "source:" + IM._hash([identifier])]})
    manifest = {"version": 1, "seed": 812, "cases": cases}
    value = {key: key.replace("_", "-") for key in ("experiment_id", "context_id", "target_id", "base_revision_id",
              "base_bundle_id", "contract_id", "case_set_id")}
    value.update({key: "a" * 64 for key in ("base_hash", "base_bundle_hash", "baseline_context_hash", "binding_hash",
                  "engine_config_hash", "grant_hash", "scope_hash")})
    value.update(version=1, base_generation=1, context_generation=1, policy_version=1,
        contract_hash=IM._hash(contract), case_set_hash=IM._hash(manifest),
        repeat_policy={"kind": "pilot" if pilot else "feasibility", "repeats": 2 if pilot else 1}, timeout_seconds=120,
        endpoint={"kind": "binary_all_pass", "criterion_id": "content", "direction": "higher"},
        decision_policy={"id": "paired-skill-v1", "version": 1, "multiple_tests": 2},
        evaluator={"id": "technical-evaluator", "version": "1", "hash": "b" * 64}, evidence_class="technical")
    selected = [case["id"] for case in cases if case["split"] in (("tuning",) if kind == "tuning" else ("qualification", "regression"))]
    matrix = P.comparison_matrix(value, "c" * 64, 1, kind, selected, manifest["seed"])
    evidence = []
    for position in range(2 * len(matrix["pairs"])):
        cell = P.matrix_cell(matrix, position)
        prefix, index = cell["case_id"].rsplit("-", 1)
        primary = "fail" if cell["variant"] == "baseline" and prefix != "regression" and int(index) < wins else "pass"
        evidence.append({"position": position, "integrity": "pass",
            "criteria": {"shape": "pass", "protected": "pass", "content": primary}})
    return value, matrix, contract, manifest, evidence


class ComparisonArithmeticTests(unittest.TestCase):
    def evaluate(self, data):
        self.assertTrue(callable(aggregate), "Pure complete-pair comparator is absent")
        return aggregate(*data)

    @staticmethod
    def change(data, case_id, variant, *, repeat=None, criterion=None, verdict=None, integrity=None):
        for position, row in enumerate(data[4]):
            cell = P.matrix_cell(data[1], position)
            if cell["case_id"] == case_id and cell["variant"] == variant and (repeat is None or cell["repeat"] == repeat):
                if criterion is not None:
                    row["criteria"][criterion] = verdict
                if integrity is not None:
                    row["integrity"] = integrity

    def test_complete_synthetic_pilot_can_pass_arithmetic_but_never_business_eligibility(self):
        result = self.evaluate(dataset())
        self.assertTrue(result["complete"])
        self.assertTrue(result["pilot"]["numeric_gate"])
        self.assertEqual(result["outcomes"]["qualification"], {"win": 6, "loss": 0, "tie": 14, "unknown": 0})
        self.assertEqual(result["statistics"]["discordant"], 6)
        self.assertEqual(result["business_eligibility"], "unverified")
        self.assertEqual(result["independence"], "unverified")
        self.assertEqual(result["evidence_class"], "technical")

    def test_five_wins_fail_corrected_threshold_six_pass_and_ties_count_coverage(self):
        five = self.evaluate(dataset(wins=5))
        six = self.evaluate(dataset(wins=6))
        self.assertFalse(five["statistics"]["significant"])
        self.assertFalse(five["pilot"]["numeric_gate"])
        self.assertEqual(five["statistics"]["tail_numerator"], "1")
        self.assertEqual(five["statistics"]["tail_denominator"], "32")
        self.assertEqual(six["statistics"]["tail_denominator"], "64")
        self.assertTrue(six["statistics"]["significant"])

    def test_missing_cell_stays_in_denominator_and_makes_comparison_incomplete(self):
        data = dataset()
        data[4][0] = None
        result = self.evaluate(data)
        self.assertFalse(result["complete"])
        self.assertEqual(result["integrity"]["missing"], 1)
        self.assertFalse(result["pilot"]["numeric_gate"])
        self.assertEqual(sum(result["outcomes"]["qualification"].values()), 20)

    def test_baseline_infrastructure_failure_is_unknown_and_never_a_candidate_win(self):
        data = dataset()
        self.change(data, "qualification-0", "baseline", integrity="fail")
        result = self.evaluate(data)
        self.assertEqual(result["outcomes"]["qualification"], {"win": 5, "loss": 0, "tie": 14, "unknown": 1})
        self.assertFalse(result["complete"])

    def test_repeat_disagreement_counts_one_unstable_case_and_blocks_pilot(self):
        data = dataset()
        self.change(data, "qualification-0", "candidate", repeat=2, criterion="content", verdict="fail")
        result = self.evaluate(data)
        self.assertFalse(result["complete"])
        self.assertEqual(result["stability"]["unstable_cases"], 1)
        self.assertEqual(result["outcomes"]["qualification"]["unknown"], 1)
        self.assertFalse(result["pilot"]["numeric_gate"])

    def test_critical_loss_rejects_despite_primary_improvement(self):
        data = dataset(wins=15)
        self.change(data, "qualification-19", "candidate", criterion="protected", verdict="fail")
        result = self.evaluate(data)
        self.assertEqual(result["required_loss_count"], 1)
        self.assertEqual(result["mechanical_verdict"], "rejected")
        self.assertFalse(result["pilot"]["numeric_gate"])

    def test_critical_unstable_candidate_rejects_if_baseline_preserves_it(self):
        data = dataset()
        self.change(data, "qualification-19", "candidate", repeat=2, criterion="protected", verdict="fail")
        result = self.evaluate(data)
        self.assertEqual(result["mechanical_verdict"], "rejected")
        self.assertFalse(result["complete"])

    def test_required_regression_loss_is_not_offset_by_qualification_wins(self):
        data = dataset(wins=15)
        self.change(data, "regression-0", "candidate", criterion="shape", verdict="fail")
        result = self.evaluate(data)
        self.assertEqual(result["mechanical_verdict"], "rejected")
        self.assertFalse(result["pilot"]["numeric_gate"])

    def test_unknown_required_product_is_not_known_fail_and_blocks_completeness(self):
        data = dataset()
        self.change(data, "regression-0", "candidate", criterion="shape", verdict="unknown")
        result = self.evaluate(data)
        self.assertEqual(result["required_loss_count"], 0)
        self.assertFalse(result["complete"])
        self.assertEqual(result["mechanical_verdict"], "inconclusive")

    def test_small_feasibility_cannot_be_reported_as_a_pilot(self):
        result = self.evaluate(dataset(qualification=6, regression=6, tuning=6, pilot=False, wins=6))
        self.assertTrue(result["complete"])
        self.assertFalse(result["pilot"]["numeric_gate"])

    def test_tuning_can_support_selection_without_claiming_a_qualification_pilot(self):
        result = self.evaluate(dataset(kind="tuning", pilot=False, wins=6))
        self.assertTrue(result["selection_supported"])
        self.assertFalse(result["pilot"]["numeric_gate"])
        self.assertEqual(result["outcomes"]["qualification"], {"win": 0, "loss": 0, "tie": 0, "unknown": 0})

    def test_tie_only_or_baseline_better_never_supports_candidate_selection(self):
        data = dataset(kind="tuning", pilot=False, wins=0)
        self.assertFalse(self.evaluate(data)["selection_supported"])
        self.change(data, "tuning-0", "candidate", criterion="content", verdict="fail")
        self.assertFalse(self.evaluate(data)["selection_supported"])

    def test_substitution_omitted_criterion_duplicate_position_and_endpoint_change_reject(self):
        for mutation in (lambda d: d[4][0].update(position=1), lambda d: d[4][0]["criteria"].pop("protected"),
                         lambda d: d[4][0].update(integrity=True), lambda d: d[0]["endpoint"].update(criterion_id="shape"),
                         lambda d: d[3]["cases"][0].update(family_key="substituted"), lambda d: d[1]["pairs"].pop()):
            data = copy.deepcopy(dataset())
            mutation(data)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.evaluate(data)

    @staticmethod
    def repin(data):
        value, matrix, _, manifest, evidence = data
        by_identity = {(P.matrix_cell(matrix, index)["case_id"], P.matrix_cell(matrix, index)["variant"],
                        P.matrix_cell(matrix, index)["repeat"]): row for index, row in enumerate(evidence)}
        value["case_set_hash"] = IM._hash(manifest)
        rebuilt = P.comparison_matrix(value, matrix["arm_hash"], matrix["ordinal"], matrix["kind"],
            [case["id"] for case in manifest["cases"] if case["split"] in (("tuning",) if matrix["kind"] == "tuning" else ("qualification", "regression"))], manifest["seed"])
        rows = []
        for index in range(2 * len(rebuilt["pairs"])):
            cell = P.matrix_cell(rebuilt, index)
            rows.append({**by_identity[(cell["case_id"], cell["variant"], cell["repeat"])], "position": index})
        return value, rebuilt, data[2], manifest, rows

    def test_qualification_overlap_with_tuning_or_regression_blocks_numerical_gate(self):
        for split in ("tuning", "regression"):
            data = dataset()
            qualification = next(case for case in data[3]["cases"] if case["id"] == "qualification-0")
            other = next(case for case in data[3]["cases"] if case["split"] == split)
            qualification["fingerprints"][1] = other["fingerprints"][1]
            result = self.evaluate(self.repin(data))
            with self.subTest(split=split):
                self.assertFalse(result["pilot"]["declared_separation"])
                self.assertFalse(result["pilot"]["numeric_gate"])

    def test_known_critical_failure_is_retained_when_another_repeat_is_unknown(self):
        data = dataset()
        self.change(data, "qualification-19", "candidate", repeat=1, criterion="protected", verdict="fail")
        self.change(data, "qualification-19", "candidate", repeat=2, criterion="protected", verdict="unknown")
        result = self.evaluate(data)
        self.assertFalse(result["complete"])
        self.assertEqual(result["required_loss_count"], 1)
        self.assertEqual(result["mechanical_verdict"], "rejected")

    def test_regression_primary_loss_blocks_pilot_even_if_not_marked_critical(self):
        data = dataset()
        self.change(data, "regression-0", "candidate", criterion="content", verdict="fail")
        result = self.evaluate(data)
        self.assertEqual(result["outcomes"]["regression"]["loss"], 1)
        self.assertFalse(result["pilot"]["numeric_gate"])
        self.assertEqual(result["mechanical_verdict"], "rejected")


class ResultProjectionTests(unittest.TestCase):
    def fixture(self):
        from test_improvement_experiment_protocol import request_v2
        value, _, contract, _, _ = dataset()
        contract["output"] = {"path": "answer.json"}
        for criterion in contract["criteria"]:
            criterion["kind"] = "json_keys" if criterion["id"] == "shape" else "owner_confirm"
        value["contract_hash"] = IM._hash(contract)
        request = request_v2()
        request["pins"]["contract_hash"] = value["contract_hash"]
        rows = [{**criterion, "verdict": "pass"} for criterion in contract["criteria"]]
        mandatory = [{"id": identifier, "verdict": "pass"} for identifier in (
            "materialization", "contract_projection", "workspace_binding", "worker_protocol", "installation", "tool_inventory",
            "prompt_installation", "asset_installation", "asset_observed_use", "actual_reasoning", "context_coverage",
            "fixture_inventory", "source_coverage", "actual_model", "session_binding", "side_effects", "completion", "event_coverage")]
        report = {"version": 2, "trial_id": "trial-1", "attempt": 1, "execution_status": "completed",
            "request_hash": IM._hash(request), "experiment_lineage": request["lineage"], "contract_hash": value["contract_hash"],
            "artifact_hash": "e" * 64, "mandatory": mandatory, "verdict": "pass",
            "evaluation": {"version": 1, "case_id": request["case_id"], "contract_hash": value["contract_hash"],
                "evaluator": value["evaluator"], "output_hash": "e" * 64, "output_manifest": [{"path": "answer.json", "sha256": "e" * 64, "size": 2}],
                "criteria": rows, "primary_metric": {**contract["primary_metric"], "verdict": "pass"},
                "mandatory_verdict": "pass", "business_verdict": "pass", "verdict": "pass"}}
        return value, contract, request, report

    def project(self, fixture):
        function = getattr(C, "project_result", None)
        self.assertTrue(callable(function), "Verified immutable result projection is absent")
        return function(*fixture[:3], "trial-1", 1, fixture[3])

    def test_product_failure_is_separate_from_native_execution_integrity(self):
        fixture = self.fixture()
        report = fixture[3]
        next(row for row in report["evaluation"]["criteria"] if row["id"] == "content")["verdict"] = "fail"
        report["evaluation"]["primary_metric"]["verdict"] = "fail"
        report["evaluation"].update(business_verdict="fail", verdict="fail")
        report["verdict"] = "fail"
        projected = self.project(fixture)
        self.assertEqual(projected["integrity"], "pass")
        self.assertEqual(projected["criteria"]["content"], "fail")

    def test_missing_native_checks_are_unknown_and_cannot_become_pass(self):
        fixture = self.fixture()
        fixture[3]["mandatory"] = []
        self.assertEqual(self.project(fixture)["integrity"], "unknown")

    def test_result_identity_contract_evaluator_output_criteria_or_endpoint_substitution_rejects(self):
        changes = [lambda f: f[3].update(attempt=2), lambda f: f[3].update(trial_id="other"),
            lambda f: f[3].update(request_hash="0" * 64), lambda f: f[3]["evaluation"].update(case_id="other"),
            lambda f: f[3]["evaluation"].update(evaluator={"id": "other"}),
            lambda f: f[3]["evaluation"].update(output_hash="0" * 64), lambda f: f[3]["evaluation"]["criteria"].pop(),
            lambda f: f[3]["evaluation"]["criteria"][0].update(critical=False),
            lambda f: f[3]["evaluation"]["primary_metric"].update(criterion_id="shape")]
        for mutate in changes:
            fixture = self.fixture()
            mutate(fixture)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.project(fixture)


if __name__ == "__main__":
    unittest.main()
