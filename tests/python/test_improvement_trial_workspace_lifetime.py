"""Synthetic worker lifetimes retain plaintext only while its owning host needs it."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import json
import queue
import subprocess
import sys
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_improvement_trial_runner as fixtures
import test_improvement_trials as store_fixtures
import improvement_store as IM
from improvement_trial_service import recover_trials


class WorkspaceLifetimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixtures.TrialRunnerTests(); self.f.setUp()
        self.addCleanup(self.f.f.tearDown)
        self.addCleanup(self.f.doCleanups)
        self.workspace = self.f.f.trials.workspace
        self.identifier = self.f.trial["trial_id"]
        self.path, self.journal = self.workspace._paths(self.identifier, 1)

    def assert_cleaned(self):
        self.assertFalse(self.path.exists(), "Terminal workspace retains protected context/fixtures")
        self.assertEqual(json.loads(self.journal.read_text(encoding="utf-8"))["state"], "cleaned")

    async def test_completion_cleans_plaintext_but_keeps_encrypted_products_and_trace(self):
        result = await self.f.run_trial()
        self.assertEqual(result["status"], "completed")
        self.assert_cleaned()
        self.assertEqual(self.f.f.trials.read_artifact(self.f.f.owner, self.identifier, 1), b'{"value":"synthetic"}\r\n')
        self.assertEqual(self.f.f.trials.read_observation(self.f.f.owner, self.identifier, 1, 1)["type"], "worker_ready")
        self.assertIsInstance(self.f.f.trials.read_result(self.f.f.owner, self.identifier, 1), dict)

    async def test_cancel_waits_for_worker_close_before_removing_plaintext(self):
        self.f.hang = True
        self.f.after_cleanup = lambda: self.assertTrue(self.path.exists(), "Workspace removed before worker lifetime settled")
        task = asyncio.create_task(self.f.run_trial())
        await asyncio.wait_for(self.f.acked.wait(), 10)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.f.closed.is_set())
        self.assert_cleaned()
        self.assertEqual(self.f.call()["status"], "interrupted")

    async def test_partial_prepare_failure_cleans_committed_input_without_counting_query(self):
        prepare = self.workspace.prepare
        def fail_after_publish(*args):
            prepare(*args)
            raise OSError("synthetic prepare publication fault")
        with patch.object(self.workspace, "prepare", side_effect=fail_after_publish):
            result = await self.f.run_trial()
        self.assertEqual(result["status"], "not_run")
        self.assert_cleaned()
        self.assertEqual(self.f.call()["status"], "refunded")
        self.assertNotIn("execute", self.f.log)

    async def test_recovery_never_removes_live_expired_worker_until_it_has_closed(self):
        self.f.hang = True
        task = asyncio.create_task(self.f.run_trial())
        await asyncio.wait_for(self.f.acked.wait(), 10)
        try:
            for _ in range(1000):
                if "observe:3" in self.f.log:
                    break
                await asyncio.sleep(.01)
            self.assertIn("observe:3", self.f.log)
            await asyncio.to_thread(self.f.f.trials.recover, self.f.f.owner, now=time.time() + 600)
            self.assertTrue(self.path.exists())
            self.assertFalse(self.f.closed.is_set())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_cleaned()
        self.assertEqual(self.f.call()["status"], "interrupted")

    async def test_cleanup_failure_is_explicit_and_recovery_retries_terminal_journal(self):
        cleanup = self.workspace.cleanup
        with patch.object(self.workspace, "cleanup", side_effect=OSError("synthetic unlink fault")):
            with self.assertRaisesRegex(RuntimeError, "trial_workspace_cleanup_failed"):
                await self.f.run_trial()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.f.call()["status"], "ok")
        with patch.object(self.workspace, "cleanup", wraps=cleanup):
            await asyncio.to_thread(self.f.f.trials.recover, self.f.f.owner)
        self.assert_cleaned()

    async def test_same_brain_closing_lifetime_refunds_next_trial_before_materialization(self):
        handles = self.workspace.acquire_execution(self.f.f.owner.brain_id, "older-closing-trial", 1)
        self.assertIsNotNone(handles)
        try:
            result = await self.f.run_trial()
            self.assertEqual(result["status"], "not_run")
            self.assertEqual(result["attempts"][0]["failure_reason"], "capacity_unavailable")
            self.assertEqual(self.f.call()["status"], "refunded")
            self.assertNotIn("execute", self.f.log)
            self.assertFalse(self.path.exists())
        finally:
            self.workspace.release_execution(handles)

    async def test_repeated_cancel_drains_owned_cleanup_before_returning(self):
        entered, release = threading.Event(), threading.Event()
        cleanup = self.workspace.cleanup
        def blocked(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("synthetic cleanup wait timeout")
            cleanup(*args)
        with patch.object(self.workspace, "cleanup", side_effect=blocked):
            task = asyncio.create_task(self.f.run_trial())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                task.cancel(); await asyncio.sleep(.02); task.cancel(); await asyncio.sleep(.02)
                self.assertFalse(task.done(), "Caller abandoned plaintext cleanup on repeated cancellation")
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_cleaned()
        handles = self.workspace.acquire_execution(self.f.f.owner.brain_id, "next-trial", 1)
        self.assertIsNotNone(handles)
        self.workspace.release_execution(handles)

    async def test_abort_failure_still_drains_owned_evaluation_before_plaintext_cleanup(self):
        await self._assert_evaluation_drains_on_fault("abort")

    async def test_cleanup_read_failure_still_drains_owned_evaluation_before_plaintext_cleanup(self):
        await self._assert_evaluation_drains_on_fault("get")

    async def _assert_evaluation_drains_on_fault(self, operation):
        entered, release, cleanup_started, fault_seen = [threading.Event() for _ in range(4)]
        finish = self.f.f.trials.finish
        read = self.f.f.trials.get
        cleanup = self.workspace.cleanup
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("synthetic evaluation wait timeout")
            return finish(*args, **kwargs)
        def observed_cleanup(*args):
            cleanup_started.set()
            return cleanup(*args)
        def read_during_cleanup(*args, **kwargs):
            if entered.is_set() and not release.is_set():
                fault_seen.set()
                raise OSError("synthetic cleanup read failure")
            return read(*args, **kwargs)
        def abort_fault(*args, **kwargs):
            fault_seen.set()
            raise OSError("synthetic abort storage fault")
        fault = (patch.object(self.f.f.trials, "abort", side_effect=abort_fault)
                 if operation == "abort" else patch.object(self.f.f.trials, "get", side_effect=read_during_cleanup))
        with patch.object(self.f.f.trials, "finish", side_effect=blocked), \
             fault, \
             patch.object(self.workspace, "cleanup", side_effect=observed_cleanup):
            task = asyncio.create_task(self.f.run_trial())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                task.cancel()
                self.assertTrue(await asyncio.to_thread(fault_seen.wait, 5))
                self.assertFalse(await asyncio.to_thread(cleanup_started.wait, .5), "Cleanup started before owned evaluation settled")
                self.assertFalse(task.done(), "Aborted caller abandoned the owned evaluation thread")
                self.assertTrue(self.path.exists(), "Cleanup raced the still-running host evaluation")
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assert_cleaned()


class WorkspaceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = store_fixtures.TrialStoreTests(); self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.workspace = self.f.trials.workspace

    def path(self, row):
        return self.workspace._paths(row["trial_id"], 1)[0]

    def test_expired_prepared_crash_is_cleaned_but_other_brain_is_untouched(self):
        row, token = self.f.prepared()
        path = self.path(row)
        self.f.trials.recover(self.f.other, now=time.time() + 600)
        self.assertTrue(path.exists())
        self.f.trials.recover(self.f.owner, now=time.time() + 600)
        self.assertFalse(path.exists(), "Crash recovery leaves protected plaintext")
        self.assertEqual(self.f.ledger.get(row["attempts"][0]["call_id"])["status"], "refunded")

    def test_explicit_cancel_without_worker_cleans_only_its_prepared_attempt(self):
        row, token = self.f.prepared()
        sentinel = self.workspace.root / "untracked-owner-file"; sentinel.write_text("Keep unrelated bytes")
        self.f.trials.cancel(self.f.owner, row["trial_id"], expected_generation=row["generation"], event_key="cleanup-explicit-cancel")
        self.assertFalse(self.path(row).exists(), "Cancelled prepared plaintext persists")
        self.assertEqual(sentinel.read_text(), "Keep unrelated bytes")

    def test_fresh_foreign_lease_is_preserved_by_recovery(self):
        row, token = self.f.prepared()
        self.f.trials.recover(self.f.owner)
        self.assertTrue(self.path(row).exists())
        self.assertEqual(self.f.trials.get(self.f.owner, row["trial_id"])["status"], "prepared")

    def test_owned_prestart_abort_removes_prepared_plaintext(self):
        row, token = self.f.prepared()
        self.f.trials.abort(self.f.owner, row["trial_id"], expected_generation=row["generation"],
            lease_token=token, reason="worker_failed")
        self.assertFalse(self.path(row).exists(), "Host abort leaves prepared plaintext")

    def test_completed_host_finish_cleans_plaintext_and_preserves_recheck(self):
        import test_improvement_trial_results as results
        fixture = results.TrialResultTests(); fixture.setUp()
        self.addCleanup(fixture.f.tearDown); self.addCleanup(fixture.doCleanups)
        row, token, raw = fixture.staged(supported=True)
        fixture.finish(row, token)
        path, journal = fixture.f.trials.workspace._paths(row["trial_id"], 1)
        self.assertFalse(path.exists(), "Host finish leaves completed plaintext")
        self.assertEqual(fixture.f.trials.read_artifact(fixture.f.owner, row["trial_id"], 1), raw)

    def test_cross_process_live_lock_survives_expiry_but_host_death_allows_cleanup(self):
        row, token = self.f.prepared()
        code = ("import sys; from pathlib import Path; from improvement_sandbox import TrialWorkspace; "
            "w=TrialWorkspace(Path(sys.argv[1])); h=w.acquire_execution(sys.argv[2],sys.argv[3],1); "
            "assert h is not None; print('held',flush=True); sys.stdin.readline()")
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.path.insert(0,sys.argv.pop(1)); " + code,
            str(SERVER), str(self.workspace.root), self.f.owner.brain_id, row["trial_id"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=__import__("winproc").no_window())
        try:
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(child.stdout.readline().strip()), daemon=True).start()
            self.assertEqual(ready.get(timeout=20), "held")
            self.f.trials.recover(self.f.owner, now=time.time() + 600)
            self.assertTrue(self.path(row).exists(), "Recovery deleted a live other-process workspace")
            child.kill(); child.communicate(timeout=10)
            self.f.trials.recover(self.f.owner)
            self.assertFalse(self.path(row).exists(), "Dead-host lock prevents journal recovery")
        finally:
            if child.poll() is None:
                child.kill(); child.communicate(timeout=10)

    def test_different_brain_execution_locks_are_independent(self):
        first = self.workspace.acquire_execution(self.f.owner.brain_id, "owner-one", 1)
        second = self.workspace.acquire_execution(self.f.other.brain_id, "owner-two", 1)
        try:
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
        finally:
            if second: self.workspace.release_execution(second)
            if first: self.workspace.release_execution(first)

    def test_replayed_owner_recovery_retries_cleanup_after_post_commit_failure(self):
        row, token = self.f.prepared()
        manifest = json.loads((self.path(row) / "input.json").read_bytes())
        self.f.trials.cancel(self.f.owner, row["trial_id"], expected_generation=row["generation"], event_key="replay-cleanup-cancel")
        # Reproduce an already terminal historical workspace left by an older
        # host/crash. The durable journal must still prohibit executing it again.
        path, journal = self.workspace._paths(row["trial_id"], 1)
        if not path.exists():
            path.mkdir(); (path / "input.json").write_text("private historical plaintext")
        with patch("improvement_sandbox.TrialWorkspace.cleanup", side_effect=OSError("synthetic unlink failure")):
            with self.assertRaisesRegex(RuntimeError, "trial_workspace_cleanup_failed"):
                recover_trials(self.f.assets, self.f.owner, event_key="cleanup-owner-recovery")
        with closing(self.f.rs._conn()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_events WHERE event_key='cleanup-owner-recovery'").fetchone()[0], 1)
        first = recover_trials(self.f.assets, self.f.owner, event_key="cleanup-owner-recovery")
        self.assertFalse(path.exists(), "Owner recovery replay cannot clean historical terminal workspace")
        self.assertEqual(first, {"not_run": 0, "unknown": 0})
        self.assertEqual(first, recover_trials(self.f.assets, self.f.owner, event_key="cleanup-owner-recovery"))
        with self.assertRaisesRegex(ValueError, "trial_workspace_conflict"):
            self.workspace.prepare(row["trial_id"], 1, manifest)


if __name__ == "__main__":
    unittest.main()
