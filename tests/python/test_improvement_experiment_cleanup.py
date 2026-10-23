"""Stop completion requires real execution exclusion and verified cleanup."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

import winproc

import test_improvement_sandbox as fixtures


class ExperimentCleanupTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SandboxTests()
        self.f.setUp()
        self.workspace = self.f.workspaces

    def methods(self):
        acquire = getattr(self.workspace, "acquire_brain_execution", None)
        cleanup = getattr(self.workspace, "cleanup_proven", None)
        self.assertTrue(callable(acquire), "Brain-only execution lock is absent")
        self.assertTrue(callable(cleanup), "Verified attempt cleanup is absent")
        return acquire, cleanup

    def test_brain_lock_uses_the_actual_runner_lock_without_a_trial_identity(self):
        acquire, _ = self.methods()
        held = self.workspace.acquire_execution("brain-a", "runner", 1)
        self.assertIsNotNone(held)
        try:
            self.assertIsNone(acquire("brain-a"))
            other = acquire("brain-b")
            self.assertIsNotNone(other)
            other.close()
        finally:
            self.workspace.release_execution(held)
        handle = acquire("brain-a")
        self.assertIsNotNone(handle)
        handle.close()

    def test_cleanup_preserves_a_valid_cleaned_journal_and_prevents_replay(self):
        acquire, cleanup = self.methods()
        receipt = self.workspace.prepare("trial-a", 1, self.f.request())
        handle = acquire("brain-a")
        try:
            self.assertTrue(cleanup("trial-a", 1, receipt))
            self.assertTrue(cleanup("trial-a", 1, receipt))
        finally:
            handle.close()
        path, journal = self.workspace._paths("trial-a", 1)
        self.assertFalse(path.exists())
        record = json.loads(journal.read_bytes())
        self.assertEqual(record, {"state": "cleaned", "input_hash": receipt["input_hash"], "receipt": receipt})
        with self.assertRaisesRegex(ValueError, "trial_workspace_conflict"):
            self.workspace.prepare("trial-a", 1, self.f.request())

    def test_absent_attempt_is_success_but_busy_lifetime_is_not(self):
        _, cleanup = self.methods()
        self.assertTrue(cleanup("never-materialized", 1, None))
        receipt = self.workspace.prepare("busy-attempt", 1, self.f.request())
        held = self.workspace._try_lifetime_lock(".lifetimes", "busy-attempt:1")
        try:
            self.assertFalse(cleanup("busy-attempt", 1, receipt))
            self.assertTrue(Path(receipt["path"]).exists())
        finally:
            held.close()

    def test_corrupt_or_wrong_journal_cannot_be_relabelled_as_clean(self):
        _, cleanup = self.methods()
        for suffix, mutation in (
            ("hash", lambda record: record.update(input_hash="0" * 64)),
            ("trial", lambda record: record["receipt"].update(trial_id="other")),
            ("path", lambda record: record["receipt"].update(path=str(self.f.root))),
            ("extra", lambda record: record.update(untrusted=True)),
            ("state", lambda record: record.update(state="invented")),
        ):
            with self.subTest(suffix=suffix):
                identifier = "bad-" + suffix
                receipt = self.workspace.prepare(identifier, 1, self.f.request())
                _, journal = self.workspace._paths(identifier, 1)
                record = json.loads(journal.read_bytes())
                mutation(record)
                journal.write_text(json.dumps(record), encoding="utf-8")
                before = journal.read_bytes()
                with self.assertRaises(ValueError):
                    cleanup(identifier, 1, receipt)
                self.assertEqual(journal.read_bytes(), before)
                self.assertTrue(Path(receipt["path"]).exists())

    def test_durable_receipt_mismatch_and_oversized_journal_fail_closed(self):
        _, cleanup = self.methods()
        receipt = self.workspace.prepare("trial-a", 1, self.f.request())
        wrong = copy.deepcopy(receipt)
        wrong["effective_hash"] = "0" * 64
        with self.assertRaises(ValueError):
            cleanup("trial-a", 1, wrong)
        _, journal = self.workspace._paths("trial-a", 1)
        journal.write_bytes(b" " * 16385)
        with self.assertRaises(ValueError):
            cleanup("trial-a", 1, receipt)
        self.assertTrue(Path(receipt["path"]).exists())

    def test_prepared_before_sql_receipt_commit_is_cleaned_using_its_exact_journal(self):
        _, cleanup = self.methods()
        receipt = self.workspace.prepare("precommit", 1, self.f.request())
        self.assertTrue(cleanup("precommit", 1, None))
        self.assertFalse(Path(receipt["path"]).exists())

    def test_legacy_cancel_cleanup_cannot_mask_an_invalid_journal_before_stop_proof(self):
        receipt = self.workspace.prepare("invalid-before-cancel", 1, self.f.request())
        _, journal = self.workspace._paths("invalid-before-cancel", 1)
        record = json.loads(journal.read_bytes())
        record["state"] = "untrusted"
        journal.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.workspace.cleanup("invalid-before-cancel", 1)
        self.assertTrue(Path(receipt["path"]).exists())
        self.assertEqual(json.loads(journal.read_bytes())["state"], "untrusted")

    def test_foreign_process_holds_the_same_brain_execution_lock(self):
        acquire, _ = self.methods()
        program = (
            "import sys\nfrom pathlib import Path\nfrom improvement_sandbox import TrialWorkspace\n"
            "workspace=TrialWorkspace(Path(sys.argv[1]))\n"
            "held=workspace.acquire_execution('brain-a','foreign-worker',1)\n"
            "assert held is not None\nprint('locked',flush=True)\n"
            "try:\n sys.stdin.readline()\nfinally:\n workspace.release_execution(held)\n"
        )
        process = subprocess.Popen([sys.executable, "-c", program, str(self.workspace.root)], cwd=SERVER,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=winproc.no_window())
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            self.assertIsNone(acquire("brain-a"))
            process.communicate("release\n", timeout=10)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        handle = acquire("brain-a")
        self.assertIsNotNone(handle)
        handle.close()


if __name__ == "__main__":
    unittest.main()
