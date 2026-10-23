"""Host-selected B slots use native preflight and actual atomic trial accounting."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_improvement_experiment_lineage as fixtures
import improvement_store as IM
from improvement_claude import ClaudeTrialAdapter

if importlib.util.find_spec("improvement_experiment_admission"):
    from improvement_experiment_admission import ExperimentAdmission
else:
    ExperimentAdmission = None


class ExperimentAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.owner, self.assets, self.store = self.h.owner, self.h.assets, self.h.store

    def admit(self, **changes):
        self.assertIsNotNone(ExperimentAdmission, "Concrete v2 experiment admission is absent")
        fields = {"expected_generation": self.h.frozen["generation"], "event_key": "owner-admit-v2-slot", **changes}
        return ExperimentAdmission(self.h.h.plans()).admit(self.owner, "association-experiment", self.h.comparison["id"],
            self.h.request["lineage"]["cell_position"], **fields)

    def test_concurrent_admissions_cannot_allocate_the_same_frozen_cell_twice(self):
        before = (self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"))
        gate = threading.Barrier(2)

        def verify(adapter):
            gate.wait(timeout=10)
            return {"supported": True, "capability_hash": "c" * 64}

        with patch.object(ClaudeTrialAdapter, "verify", verify), ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(self.admit, event_key="concurrent-frozen-cell-" + str(index)) for index in range(2)]
            results = []
            for future in pending:
                try:
                    results.append(future.result(timeout=20))
                except IM.ConflictError as error:
                    results.append(error)
        self.assertEqual(sum(isinstance(result, IM.ConflictError) for result in results), 1)
        self.assertEqual(self.h.h.count("improvement_experiment_trial_links"), 1)
        self.assertEqual((self.h.h.count("improvement_trials"), self.h.h.count("agent_calls")), (before[0] + 1, before[1] + 1))

    def test_native_preflight_precedes_actual_trial_call_link_and_mints_late_deadline(self):
        before = (self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"))
        clock, calls = [1000.0], []

        def verify(adapter):
            calls.append(True)
            self.assertEqual((self.h.h.count("improvement_trials"), self.h.h.count("agent_calls")), before)
            clock[0] = 2000.0
            return {"supported": True, "capability_hash": "c" * 64}

        with patch.object(ClaudeTrialAdapter, "verify", verify), patch("improvement_experiment_admission.time.time", side_effect=lambda: clock[0]):
            result = self.admit()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.request["deadline_at"], 2120.0)
        self.assertEqual(result.request["version"], 2)
        self.assertEqual(self.h.h.count("improvement_experiment_trial_links"), 1)
        self.h.request = result.request
        self.assertEqual(self.h.load(require_link=True, trial_id=result.trial_id)["cell"]["variant"], "baseline")

    def test_unavailable_native_or_post_preflight_source_drift_creates_no_trial_call_or_link(self):
        before = (self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"))
        with patch.object(ClaudeTrialAdapter, "verify", return_value={"supported": False}), self.assertRaises(ValueError):
            self.admit()

        def drift(adapter):
            self.h.h.f.protected.write_bytes(b"Changed protected instructions during synthetic native availability.\n")
            return {"supported": True, "capability_hash": "c" * 64}

        with patch.object(ClaudeTrialAdapter, "verify", drift), self.assertRaises(IM.ConflictError):
            self.admit()
        self.assertEqual((self.h.h.count("improvement_trials"), self.h.h.count("agent_calls")), before)
        self.assertEqual(self.h.h.count("improvement_experiment_trial_links"), 0)

    def test_replay_preserves_exact_deadline_after_stop_without_native_or_blob_reads(self):
        original = self.admit()
        self.h.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=self.h.frozen["generation"], event_key="stop-after-admit-v2")
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Replay cannot probe")), \
             patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")):
            self.assertEqual(self.admit(), original)
        self.assertEqual(self.h.h.count("improvement_experiment_trial_links"), 1)

    def test_candidate_slot_denies_host_admission_before_any_decryption_or_native_probe(self):
        before = (self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"))
        self.assertIsNotNone(ExperimentAdmission)
        admission = ExperimentAdmission(self.h.h.plans())
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Unavailable candidate cannot decrypt")), \
             patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Unavailable candidate cannot probe")):
            with self.assertRaisesRegex(PermissionError, "candidate_execution_unavailable"):
                admission.admit(self.owner, "association-experiment", self.h.comparison["id"],
                    self.h.request["lineage"]["cell_position"] ^ 1, expected_generation=self.h.frozen["generation"],
                    event_key="unavailable-candidate-admission")
        self.assertEqual((self.h.h.count("improvement_trials"), self.h.h.count("agent_calls")), before)
        self.assertEqual(self.h.h.count("improvement_experiment_trial_links"), 0)


if __name__ == "__main__":
    unittest.main()
