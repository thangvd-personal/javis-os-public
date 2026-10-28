"""Preparation is a genuine baseline request, never a fabricated comparison arm."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import unittest

import improvement_store as IM
import improvement_preparation_protocol as P


def request_v3():
    engine = {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk", "model": "synthetic-model",
              "reasoning": "medium", "cli_path": "synthetic-unexecuted.exe", "cli_hash": "a" * 64,
              "cli_version": "synthetic", "sdk_version": "synthetic", "profile_version": "synthetic", "profile_hash": "b" * 64}
    return {"version": 3, "pins": {"experiment_id": "experiment-1", "base_bundle_hash": "a" * 64,
                "contract_hash": "b" * 64, "case_set_hash": "c" * 64, "engine_config_hash": IM._hash(engine),
                "grant_hash": "d" * 64, "scope_hash": "e" * 64, "policy_version": 0},
            "case_set_id": "case-set-1", "case_id": "case-1", "variant": "baseline", "repeat": 1,
            "engine_config": engine, "baseline_context_hash": "f" * 64, "target_generations": {"target-1": 1},
            "deadline_at": 10000.0, "preparation": {"version": 1, "context_id": "context-1",
                "definition_hash": "1" * 64, "preparation_id": "preparation-1", "schedule_hash": "2" * 64, "cell_position": 0}}


class PreparationProtocolTests(unittest.TestCase):
    def validate(self, value):
        validator = getattr(P, "validate_preparation_request_v3", None)
        self.assertTrue(callable(validator), "Closed genuine preparation request is absent")
        return validator(value)

    def test_baseline_without_candidate_arm_or_comparison_pins_is_valid_and_frozen(self):
        request = request_v3()
        parsed = self.validate(request)
        self.assertEqual(parsed, request)
        parsed["preparation"]["preparation_id"] = "changed"
        self.assertEqual(request["preparation"]["preparation_id"], "preparation-1")

    def test_holdout_candidate_fake_arm_fields_and_bool_numbers_are_rejected(self):
        changes = [lambda r: r.update(variant="candidate"), lambda r: r.update(qualification_id="hidden-holdout"),
                   lambda r: r["pins"].update(candidate_bundle_hash="3" * 64),
                   lambda r: r.update(lineage={"arm_ordinal": 1}), lambda r: r.update(version=True),
                   lambda r: r.update(repeat=True), lambda r: r.update(repeat=3),
                   lambda r: r["preparation"].update(cell_position=True),
                   lambda r: r["preparation"].update(cell_position=2000),
                   lambda r: r["preparation"].update(arm_hash="3" * 64),
                   lambda r: r["preparation"].update(version=True), lambda r: r["pins"].update(policy_version=True),
                   lambda r: r.update(target_generations={"target-1": True}), lambda r: r.update(deadline_at=float("inf")),
                   lambda r: r["engine_config"].update(model="forged-model")]
        for change in changes:
            request = request_v3()
            change(request)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(request)

    def test_logical_identity_is_separate_from_legacy_trial_and_freezes_schedule_cell(self):
        request = request_v3()
        self.validate(request)
        logical = getattr(P, "logical_key_v3", None)
        self.assertTrue(callable(logical), "Preparation logical identity is absent")
        first = logical(request)
        legacy = IM._hash(["experiment-1", "baseline", "case-set-1", "case-1", 1])
        self.assertNotEqual(first, legacy)
        for key, value in (("preparation_id", "preparation-2"), ("schedule_hash", "3" * 64), ("cell_position", 1)):
            changed = copy.deepcopy(request)
            changed["preparation"][key] = value
            self.assertNotEqual(logical(changed), first)
        request["deadline_at"] = 20000.0
        self.assertEqual(logical(request), first)


if __name__ == "__main__":
    unittest.main()
