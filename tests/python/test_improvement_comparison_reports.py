"""Comparison reports pin missing/exact cells without running a model."""
from _paths import ROOT, SERVER  # noqa: F401

import json
import asyncio
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_comparison_results as C
import improvement_store as IM
from improvement_claude import ClaudeTrialAdapter
import test_improvement_experiment_lineage as fixtures


class ComparisonReportTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.owner, self.assets, self.store = self.h.owner, self.h.assets, self.h.store

    def reports(self):
        factory = getattr(C, "ComparisonResults", None)
        self.assertTrue(callable(factory), "Concrete exact-result report store is absent")
        return factory(self.h.h.plans())

    def create(self, **changes):
        return self.reports().create(self.owner, self.h.comparison["id"],
            expected_generation=changes.pop("expected_generation", self.h.frozen["generation"]),
            event_key=changes.pop("event_key", "create-incomplete-comparison"), **changes)

    def test_complete_expected_schedule_is_reported_even_when_no_trials_exist(self):
        before = (self.h.h.count("improvement_trials"), self.h.h.count("agent_calls"))
        with patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Reports cannot probe native")):
            saved = self.create()
        report = self.reports().read(self.owner, saved["report_id"])
        self.assertEqual(report["result_refs"], [[index, None, None] for index in range(self.h.frozen["cell_count"])] )
        self.assertFalse(report["summary"]["complete"])
        self.assertEqual(report["summary"]["expected_cells"], self.h.frozen["cell_count"])
        self.assertEqual(saved["report_hash"], IM._hash(report))
        self.assertEqual(saved["generation"], self.h.frozen["generation"] + 1)
        self.assertEqual((self.h.h.count("improvement_trials"), self.h.h.count("agent_calls")), before)
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 1)

    def test_event_replay_is_cold_after_stop_and_current_source_revocation(self):
        saved = self.create()
        self.h.h.plans().select(self.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=saved["generation"], event_key="stop-after-incomplete-report")
        self.h.h.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-after-report")
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Replay cannot decrypt")), \
             patch.object(self.h.h.host, "_snapshot_observe", side_effect=AssertionError("Replay cannot observe")):
            self.assertEqual(self.create(), saved)
        with patch.object(self.h.h.host, "_snapshot_observe", side_effect=AssertionError("Historical read cannot observe")), \
             patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("Historical read cannot probe")):
            self.assertFalse(self.reports().read(self.owner, saved["report_id"])["summary"]["complete"])

    def test_stale_generation_and_cross_brain_cannot_publish_or_read_reports(self):
        with self.assertRaises(IM.ConflictError):
            self.create(expected_generation=1)
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 0)

        saved = self.create()
        with self.assertRaises(PermissionError):
            self.reports().read(self.h.h.f.other, saved["report_id"])

    def test_publication_event_failure_rolls_back_report_and_generation(self):
        before, event = self.h.h.count("improvement_events"), self.assets._event

        def fail(connection, principal, key, operation, payload, result):
            if operation == "comparison_report":
                raise OSError("Synthetic report publication failure")
            return event(connection, principal, key, operation, payload, result)

        with patch.object(self.assets, "_event", side_effect=fail), self.assertRaises(OSError):
            self.create()
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 0)
        self.assertEqual(self.h.h.count("improvement_events"), before)
        with closing(self.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT generation FROM improvement_experiments").fetchone()[0], self.h.frozen["generation"])

    def test_stop_during_slow_report_preparation_cannot_publish(self):
        reports = self.reports()
        original, stopped = self.assets.blobs.read, []

        def stop(brain, digest):
            raw = original(brain, digest)
            if digest == self.h.comparison["matrix_blob_hash"] and not stopped:
                stopped.append(True)
                self.h.h.h.records().begin_stop(self.owner, "association-experiment", kind="cancelled", reason="owner_requested",
                    expected_generation=self.h.frozen["generation"], event_key="stop-during-report-read")
            return raw

        with patch.object(self.assets.blobs, "read", side_effect=stop), self.assertRaises((PermissionError, IM.ConflictError)):
            reports.create(self.owner, self.h.comparison["id"], expected_generation=self.h.frozen["generation"], event_key="no-late-report")
        self.assertTrue(stopped)
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 0)


    def test_source_change_during_final_policy_file_reads_cannot_publish_report(self):
        reports = self.reports()
        policy, calls = reports._policy, []

        def drift(value):
            observed = policy(value)
            calls.append(True)
            if len(calls) == 2:
                self.h.h.f.protected.write_bytes(b"Protected instructions changed during final policy source I/O.\n")
            return observed

        with patch.object(reports, "_policy", side_effect=drift), self.assertRaises(IM.ConflictError):
            reports.create(self.owner, self.h.comparison["id"], expected_generation=self.h.frozen["generation"],
                           event_key="no-policy-source-race-report")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 0)


    def test_report_byte_bound_counts_utf8_bytes_before_publication(self):
        reports = self.reports()
        with patch.object(reports, "_evidence", return_value=([], {"technical": "é" * 530000})), \
             self.assertRaisesRegex(ValueError, "comparison_report_too_large"):
            reports.create(self.owner, self.h.comparison["id"], expected_generation=self.h.frozen["generation"], event_key="oversized-report")
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 0)


    def test_cold_existing_reader_needs_no_host_plans_migration_or_native_factory(self):
        saved = self.create()
        factory = getattr(C.ComparisonResults, "from_existing", None)
        self.assertTrue(callable(factory), "Cold comparison history reader is absent")
        with patch("improvement_comparison_plans.ExperimentPlans.__init__", side_effect=AssertionError("GET cannot construct plans")), \
             patch("improvement_trial_host.HostContextStore.__init__", side_effect=AssertionError("GET cannot construct host")), \
             patch.object(IM, "ensure", side_effect=AssertionError("GET cannot migrate")), \
             patch.object(ClaudeTrialAdapter, "verify", side_effect=AssertionError("GET cannot probe native")):
            reader = factory(self.assets)
            self.assertFalse(reader.read(self.owner, saved["report_id"])["summary"]["complete"])
            with self.assertRaisesRegex(PermissionError, "write_unavailable"):
                reader.create(self.owner, self.h.comparison["id"], expected_generation=saved["generation"], event_key="no-cold-reader-write")
        self.assertEqual(self.h.h.count("improvement_comparison_reports"), 1)


class ActualResultReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_improvement_experiment_execution import ExperimentExecutionTests
        self.f = ExperimentExecutionTests()
        self.f.setUp()
        # This helper case has no asyncio runner of its own. Its synchronous
        # resources belong to the actually running outer case, so cleanup errors
        # must fail this test rather than get swallowed by helper.doCleanups().
        self.addCleanup(self.cleanup_fixture)

    def cleanup_fixture(self):
        self.f.stack.close()
        self.assertTrue(self.f.h.doCleanups(), "Nested execution fixture cleanup failed")

    async def run_baseline(self):
        from improvement_trial_runner import TrialRunner
        runner = TrialRunner(self.f.trials, context_loader=self.f.host.context_loader, live_guard=self.f.host.live_guard,
                             adapter_factory=self.f.factory)
        result = await runner.run(self.f.owner, self.f.trial["trial_id"])
        self.assertEqual(result["status"], "completed", result)
        return C.ComparisonResults(self.f.h.h.plans())

    def create(self, reports, *, generation=None, key="actual-result-comparison"):
        return reports.create(self.f.owner, self.f.h.comparison["id"],
            expected_generation=self.f.h.frozen["generation"] if generation is None else generation, event_key=key)

    def reevaluate(self):
        trial = self.f.trials.get(self.f.owner, self.f.trial["trial_id"])
        self.f.trials.reevaluate(self.f.owner, trial["trial_id"], 1,
            expected_generation=trial["generation"], event_key="actual-product-reevaluation", checker_registry={})

    async def test_actual_baseline_result_is_pinned_and_later_reevaluation_cannot_rewrite_report(self):
        reports = await self.run_baseline()
        saved = self.create(reports)
        previous = reports.read(self.f.owner, saved["report_id"])
        exact = next(ref for ref in previous["result_refs"] if ref[1] is not None)
        self.assertFalse(previous["summary"]["complete"])
        self.assertEqual(sum(ref[1] is not None for ref in previous["result_refs"]), 1)
        self.reevaluate()
        reader, called = reports.results.read, []

        def explicit(*args, **kwargs):
            self.assertEqual(kwargs.get("result_id"), exact[1])
            called.append(True)
            return reader(*args, **kwargs)

        with patch.object(reports.results, "read", side_effect=explicit):
            self.assertEqual(reports.read(self.f.owner, saved["report_id"]), previous)
        self.assertTrue(called)
        newer = self.create(reports, generation=saved["generation"], key="report-after-real-reevaluation")
        latest = reports.read(self.f.owner, newer["report_id"])
        self.assertNotEqual(next(ref for ref in latest["result_refs"] if ref[1] is not None), exact)
        self.assertEqual(self.f.executed, 1)

    async def test_result_pointer_change_during_report_read_cannot_publish_mixed_snapshot(self):
        reports = await self.run_baseline()
        reader, changed = reports.results.read, []

        def replace_pointer(*args, **kwargs):
            result = reader(*args, **kwargs)
            if not changed:
                changed.append(True)
                self.reevaluate()
            return result

        with patch.object(reports.results, "read", side_effect=replace_pointer), self.assertRaises(IM.ConflictError):
            self.create(reports)
        self.assertEqual(self.f.h.h.count("improvement_comparison_reports"), 0)
        self.assertEqual(self.f.executed, 1)

    async def test_missing_actual_encrypted_artifact_cannot_remain_usable_result_evidence(self):
        reports = await self.run_baseline()
        with closing(self.f.assets.store._conn()) as connection:
            artifact = connection.execute("SELECT content_hash FROM improvement_trial_artifacts WHERE trial_id=?",
                                          (self.f.trial["trial_id"],)).fetchone()[0]
        path = self.f.assets.blobs._path(self.f.owner.brain_id, artifact)
        self.assertTrue(path.is_relative_to(self.f.assets.blobs.root))
        path.rename(path.with_name(path.name + ".technical-unavailable"))
        saved = self.create(reports)
        report = reports.read(self.f.owner, saved["report_id"])
        self.assertEqual(report["summary"]["integrity"]["missing"], self.f.h.frozen["cell_count"])

    async def test_missing_real_call_marker_prevents_result_selection(self):
        reports = await self.run_baseline()
        with self.f.assets.store._Tx(self.f.assets.store) as connection:
            connection.execute("UPDATE agent_calls SET engine_at=NULL WHERE id=(SELECT call_id FROM improvement_trial_attempts WHERE trial_id=?)",
                               (self.f.trial["trial_id"],))
        saved = self.create(reports)
        report = reports.read(self.f.owner, saved["report_id"])
        self.assertTrue(all(ref[1] is None for ref in report["result_refs"]))


    async def test_corrupt_result_ciphertext_preserves_an_incomplete_expected_cell(self):
        reports = await self.run_baseline()
        with closing(self.f.assets.store._conn()) as connection:
            blob = connection.execute("SELECT blob_hash FROM improvement_trial_results WHERE trial_id=?",
                                      (self.f.trial["trial_id"],)).fetchone()[0]
        path = self.f.assets.blobs._path(self.f.owner.brain_id, blob)
        self.assertTrue(path.is_relative_to(self.f.assets.blobs.root))
        with closing(self.f.assets.store._conn()) as connection:
            artifact = connection.execute("SELECT content_hash FROM improvement_trial_artifacts WHERE trial_id=?",
                                          (self.f.trial["trial_id"],)).fetchone()[0]
        generation = self.f.h.frozen["generation"]
        for index, target in enumerate((path, self.f.assets.blobs._path(self.f.owner.brain_id, artifact))):
            original = target.read_bytes()
            try:
                target.write_text("enc:broken", encoding="ascii")
                saved = self.create(reports, generation=generation, key="corrupt-encrypted-evidence-" + str(index))
                report = reports.read(self.f.owner, saved["report_id"])
                self.assertEqual(report["summary"]["integrity"]["missing"], self.f.h.frozen["cell_count"])
                self.assertFalse(report["summary"]["complete"])
                generation = saved["generation"]
            finally:
                target.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
