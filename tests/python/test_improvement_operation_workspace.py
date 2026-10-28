"""Real operation workspace identities and the existing solver brain barrier."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import json
import subprocess
import queue
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import improvement_store as IM
import improvement_operation_protocol as P
from improvement_sandbox import TrialWorkspace
from test_improvement_experiment_execution import _acl_only, _REAL_RUN, _REAL_POPEN


def materialization():
    data = {"task": "Return bounded proposal JSON from supplied authorized tuning evidence.",
            "evidence": [{"ref": "tuning-result-1", "summary": "A synthetic row was omitted."}]}
    return {"version": 1, "brain_id": "brain-1", "operation_id": "operation-1", "engine_config_hash": "a" * 64,
            "request": {"version": 1, "experiment_id": "experiment-1", "context_id": "context-1",
                "definition_hash": "b" * 64, "kind": "propose", "role": "improver", "purpose": "improve_propose",
                "candidate_id": None, "parent_id": None, "input_hash": IM._hash(data)}, "data": data,
            "learning_origin": "compose"}


class OperationWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="operation-workspace-")
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("subprocess.run", side_effect=lambda *a, **kw: _acl_only(_REAL_RUN, *a, **kw)))
        self.stack.enter_context(patch("subprocess.Popen", side_effect=lambda *a, **kw: _acl_only(_REAL_POPEN, *a, **kw)))
        self.barrier = TrialWorkspace(self.root / "trials")

    def workspace(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_operation_workspace"), "Actual operation workspace is absent")
        from improvement_operation_workspace import OperationWorkspace
        return OperationWorkspace(self.root / "operations", brain_barrier=self.barrier)

    def test_manifest_binds_genuine_operation_request_without_trial_or_candidate_arm(self):
        validate = getattr(P, "operation_materialization", None)
        self.assertTrue(callable(validate), "Closed operation materialization is absent")
        value = materialization()
        self.assertEqual(validate(value), value)
        for changes in ({"trial_id": "fake-trial"}, {"learning_origin": "production"},
                        {"data": {"task": "Unbound replacement input"}}, {"native_ready": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate({**value, **changes})

    def test_actual_receipt_replay_and_cleanup_keep_operation_identity_and_exact_hashes(self):
        workspace = self.workspace()
        value = materialization()
        receipt = workspace.prepare("operation-1", 1, value)
        self.assertEqual(receipt["operation_id"], "operation-1")
        self.assertEqual((receipt["role"], receipt["purpose"]), ("improver", "improve_propose"))
        self.assertNotIn("trial_id", receipt)
        self.assertEqual(receipt["input_hash"], hashlib.sha256(IM._json(value).encode()).hexdigest())
        self.assertEqual(workspace.validate(receipt), value)
        self.assertEqual(workspace.prepare("operation-1", 1, value), receipt)
        self.assertTrue(workspace.cleanup_proven("operation-1", 1, receipt))
        self.assertFalse(Path(receipt["path"]).exists())
        with self.assertRaises(ValueError):
            workspace.prepare("operation-1", 1, value)

    def test_solver_and_operation_share_actual_brain_lock_but_other_brain_is_independent(self):
        workspace = self.workspace()
        held = self.barrier.acquire_brain_execution("brain-1")
        self.assertIsNotNone(held)
        try:
            self.assertIsNone(workspace.acquire_execution("brain-1", "operation-1", 1))
            other = workspace.acquire_execution("brain-2", "operation-2", 1)
            self.assertIsNotNone(other)
            workspace.release_execution(other)
        finally:
            held.close()
        owned = workspace.acquire_execution("brain-1", "operation-1", 1)
        self.assertIsNotNone(owned)
        try:
            self.assertIsNone(self.barrier.acquire_brain_execution("brain-1"))
            self.assertFalse(workspace.cleanup_proven("operation-1", 1, None))
        finally:
            workspace.release_execution(owned)

    def test_cleanup_without_journal_cannot_delete_unproven_owner_files(self):
        workspace = self.workspace()
        path, _ = workspace._paths("operation-1", 1)
        path.mkdir(parents=True)
        sentinel = path / "owner.txt"
        sentinel.write_text("Unproven owner content")
        with self.assertRaisesRegex(ValueError, "unproven"):
            workspace.cleanup_proven("operation-1", 1, None)
        self.assertEqual(sentinel.read_text(), "Unproven owner content")

    def test_corrupt_or_foreign_receipt_never_deletes_actual_workspace(self):
        workspace = self.workspace()
        receipt = workspace.prepare("operation-1", 1, materialization())
        path, journal = workspace._paths("operation-1", 1)
        original = journal.read_bytes()
        for field, value in (("path", str(self.root)), ("operation_id", "other-op"),
                             ("role", "semantic_reviewer"), ("input_hash", "f" * 64), ("attempt", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                workspace.cleanup_proven("operation-1", 1, {**receipt, field: value})
            self.assertTrue((path / "input.json").is_file())
        for corrupt in (b"not-json", b"{}", b"x" * 16385):
            journal.write_bytes(corrupt)
            with self.subTest(corrupt=corrupt[:12]), self.assertRaises(ValueError):
                workspace.cleanup_proven("operation-1", 1, receipt)
            self.assertTrue(path.exists())
        journal.write_bytes(original)
        self.assertTrue(workspace.cleanup_proven("operation-1", 1, receipt))

    def test_modified_input_and_cross_role_materialization_cannot_replay(self):
        workspace = self.workspace()
        value = materialization()
        receipt = workspace.prepare("operation-1", 1, value)
        input_path = Path(receipt["input_path"])
        input_path.chmod(0o600)
        input_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "input_changed"):
            workspace.validate(receipt)
        with self.assertRaises(ValueError):
            workspace.prepare("operation-1", 1, value)
        self.assertTrue(workspace.cleanup_proven("operation-1", 1, receipt))

    def test_other_process_lifetime_blocks_cleanup_and_solver_until_host_death(self):
        workspace = self.workspace()
        receipt = workspace.prepare("operation-1", 1, materialization())
        code = ("import sys; from pathlib import Path; "
                "from improvement_sandbox import TrialWorkspace; "
                "from improvement_operation_workspace import OperationWorkspace; "
                "b=TrialWorkspace(Path(sys.argv[1])/'trials'); "
                "w=OperationWorkspace(Path(sys.argv[1])/'operations',brain_barrier=b); "
                "h=w.acquire_execution('brain-1','operation-1',1); "
                "assert h is not None; print('held',flush=True); sys.stdin.readline()")
        child = _REAL_POPEN([sys.executable, "-c", "import sys; sys.path.insert(0,sys.argv.pop(1)); " + code,
                             str(SERVER), str(self.root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, creationflags=__import__("winproc").no_window())
        try:
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(child.stdout.readline().strip()), daemon=True).start()
            self.assertEqual(ready.get(timeout=20), "held")
            self.assertIsNone(self.barrier.acquire_brain_execution("brain-1"))
            self.assertFalse(workspace.cleanup_proven("operation-1", 1, receipt))
            child.kill()
            child.communicate(timeout=10)
            self.assertTrue(workspace.cleanup_proven("operation-1", 1, receipt))
            held = self.barrier.acquire_brain_execution("brain-1")
            self.assertIsNotNone(held)
            held.close()
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def test_owning_runner_cleans_without_releasing_brain_or_attempt_lifetime(self):
        workspace = self.workspace()
        receipt = workspace.prepare("operation-1", 1, materialization())
        owned = workspace.acquire_execution("brain-1", "operation-1", 1)
        self.assertIsNotNone(owned)
        try:
            method = getattr(workspace, "cleanup_owned", None)
            self.assertTrue(callable(method), "Owned cleanup must retain both actual lifetime locks")
            with self.assertRaises(ValueError):
                method("operation-1", 1, receipt, (owned[0], owned[1]))
            self.assertTrue(method("operation-1", 1, receipt, owned))
            self.assertIsNone(self.barrier.acquire_brain_execution("brain-1"))
            self.assertFalse(workspace.cleanup_proven("operation-1", 1, receipt))
        finally:
            workspace.release_execution(owned)
        with self.assertRaises(ValueError):
            method("operation-1", 1, receipt, owned)


if __name__ == "__main__":
    unittest.main()
