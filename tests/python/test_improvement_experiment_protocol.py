"""Closed v2 data validates without host, engine, filesystem or case-store access."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import unittest

import improvement_experiment_protocol as P
import improvement_store as IM


def request_v2():
    engine = {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk", "model": "synthetic-model",
        "reasoning": "medium", "cli_path": "synthetic-unexecuted.exe", "cli_hash": "a" * 64,
        "cli_version": "synthetic", "sdk_version": "synthetic", "profile_version": "synthetic", "profile_hash": "b" * 64}
    pins = {key: "a" * 64 for key in ("base_bundle_hash", "candidate_bundle_hash", "contract_hash", "case_set_hash",
                                     "engine_config_hash", "grant_hash", "scope_hash")}
    pins.update(experiment_id="experiment-1", policy_version=1, engine_config_hash=IM._hash(engine))
    return {"version": 2, "pins": pins, "case_set_id": "case-set", "case_id": "case-1", "variant": "baseline",
        "repeat": 1, "engine_config": engine, "baseline_context_hash": "c" * 64, "target_generations": {"target-1": 1},
        "deadline_at": 10000.0, "lineage": {"version": 1, "context_id": "context-1", "definition_hash": "d" * 64,
            "comparison_id": "comparison-1", "comparison_hash": "e" * 64, "matrix_hash": "f" * 64,
            "arm_ordinal": 1, "arm_hash": pins["candidate_bundle_hash"], "candidate_id": "candidate-1",
            "pair_id": "1" * 64, "cell_position": 0}}


class ProtocolTests(unittest.TestCase):
    def validate(self, value):
        validator = getattr(P, "validate_request_v2", None)
        self.assertTrue(callable(validator), "Closed v2 trial protocol is absent")
        return validator(value)

    def test_valid_request_retains_v2_lineage_and_freezes_caller_data(self):
        request = request_v2()
        result = self.validate(request)
        self.assertEqual(result, request)
        result["lineage"]["candidate_id"] = "mutated"
        self.assertEqual(request["lineage"]["candidate_id"], "candidate-1")

    def test_v2_rejects_substituted_arm_config_extra_fields_and_bool_numbers(self):
        changes = [lambda r: r["lineage"].update(arm_hash="0" * 64),
            lambda r: r["engine_config"].update(model="another-model"),
            lambda r: r.update(accepted_business=True), lambda r: r["lineage"].update(version=True),
            lambda r: r["lineage"].update(cell_position=True), lambda r: r.update(repeat=True),
            lambda r: r.update(version=1), lambda r: r.update(deadline_at=float("inf")),
            lambda r: r["target_generations"].update(other=2), lambda r: r.update(qualification_id=""),
            lambda r: r["pins"].update(policy_version=True)]
        for mutate in changes:
            value = request_v2()
            mutate(value)
            with self.subTest(value=str(value)[:70]), self.assertRaises(ValueError):
                self.validate(value)

    def test_cell_positions_derive_complementary_variants_and_full_pair_identity(self):
        cell = getattr(P, "matrix_cell", None)
        self.assertTrue(callable(cell), "Canonical pair-to-cell projection is absent")
        matrix = {"pairs": [{"pair_id": "a" * 64, "case_id": "case-1", "repeat": 1, "first_variant": "candidate"},
                            {"pair_id": "b" * 64, "case_id": "case-2", "repeat": 2, "first_variant": "baseline"}]}
        self.assertEqual([cell(matrix, i)["variant"] for i in range(4)], ["candidate", "baseline", "baseline", "candidate"])
        self.assertEqual([cell(matrix, i)["pair_id"] for i in range(4)], ["a" * 64, "a" * 64, "b" * 64, "b" * 64])
        for index in (-1, 4, True):
            with self.assertRaises(ValueError):
                cell(matrix, index)

    def test_v2_logical_identity_namespaces_challengers_and_comparisons(self):
        identity = getattr(P, "logical_key_v2", None)
        self.assertTrue(callable(identity), "Namespaced v2 logical identity is absent")
        first = request_v2()
        second = copy.deepcopy(first)
        second["lineage"].update(comparison_id="comparison-2", arm_ordinal=2, candidate_id="candidate-2", arm_hash="2" * 64)
        second["pins"]["candidate_bundle_hash"] = "2" * 64
        self.assertNotEqual(identity(first), identity(second))
        legacy = IM._hash([first["pins"]["experiment_id"], first["variant"], first["case_set_id"], first["case_id"], first["repeat"]])
        self.assertNotEqual(identity(first), legacy)


if __name__ == "__main__":
    unittest.main()
