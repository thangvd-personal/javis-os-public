"""Fresh worker protocol and descendant cleanup, without a provider/model call."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="improvement-worker-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(TEST_ROOT / name)

if importlib.util.find_spec("improvement_worker"):
    import improvement_worker as IW
else:
    IW = None


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IW, "Fresh trial worker and process ownership are missing")

    def test_descendant_is_removed_after_worker_crashes(self):
        parent = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--crash-parent"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=os.name != "nt", creationflags=IW.process_flags())
        guard = IW.WorkerGroup(parent.pid)
        try:
            stdout, stderr = parent.communicate("spawn\n", timeout=10)
            self.assertEqual(parent.returncode, 0, stderr)
            descendant = int(stdout.strip())
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.restype = wintypes.HANDLE
                handle = kernel.OpenProcess(0x100000, False, descendant)  # SYNCHRONIZE
                self.assertTrue(handle)
                try:
                    guard.close()
                    self.assertEqual(kernel.WaitForSingleObject(wintypes.HANDLE(handle), 5000), 0)
                finally:
                    kernel.CloseHandle(wintypes.HANDLE(handle))
            else:
                guard.close()
                # A reparented zombie is already dead; distinguish it from a running survivor.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    path = Path(f"/proc/{descendant}/stat")
                    if not path.exists() or path.read_text().split()[2] == "Z":
                        break
                    time.sleep(0.02)
                else:
                    self.fail("Trial descendant survived group cleanup")
        finally:
            guard.close()
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=5)

    def payload(self):
        from improvement_sandbox import TrialWorkspace
        from improvement_store import _hash
        import improvement_claude as IC
        sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
        root = Path(tempfile.mkdtemp(prefix="inputs-", dir=TEST_ROOT))
        pin = {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk", "model": "claude-opus-5-5",
               "reasoning": "medium", "cli_path": str(root / "unused-native"), "cli_hash": "a" * 64,
               "cli_version": "2.1.304", "sdk_version": "0.2.116", "profile_version": IC.PROFILE_VERSION,
               "profile_hash": "b" * 64}
        public = {"version": 1, "objective": "Report input accurately.",
                  "output": {"kind": "text", "path": "result.txt", "media_type": "text/plain"},
                  "criteria": [{"id": "shape", "category": "mandatory", "critical": True,
                                "description": "Return the final artifact.", "kind": "file_exists", "config": {"path": "result.txt"}},
                               {"id": "accuracy", "category": "business", "critical": True,
                                "description": "Accuracy must be verified against the authorized fixture.", "kind": "independent_check"}]}
        manifest = {"version": 1, "brain_id": "brain-a", "bundle_hash": sha("bundle"),
            "engine_config_hash": _hash(pin), "contract_hash": sha("contract"), "grant_hash": sha("grant"),
            "baseline_context": {"content": "Full owner rules", "hash": sha("Full owner rules"), "coverage": "complete"},
            "public_contract": {"content": public, "hash": _hash(public)},
            "assets": [{"id": "revision-a", "path": "skills/report/SKILL.md", "hash": sha("Full skill"), "content": "Full skill"}],
            "case": {"id": "case-a", "task": "Return final text", "fixtures": []}, "learning_origin": "trial"}
        receipt = TrialWorkspace(root).prepare("trial-a", 1, manifest)
        return {"version": 1, "config_pin": pin, "workspace_root": str(root), "receipt": receipt, "capability_hash": "c" * 64}

    def test_worker_verifies_workspace_and_requires_exact_host_ack_before_query(self):
        self.assertTrue(hasattr(IW, "run_request"), "Worker execution protocol is missing")
        payload, events, order = self.payload(), [], []
        testcase = self

        class Adapter:
            def __init__(self, pin):
                pass

            def verify(self):
                return {"supported": True, "capability_hash": "c" * 64}

        class Engine:
            def __init__(self, **kwargs):
                testcase.assertIn("Full owner rules", kwargs["system_prompt"])
                testcase.assertIn("Full skill", kwargs["system_prompt"])
                testcase.assertIn("Accuracy must be verified against the authorized fixture.", kwargs["system_prompt"])
                testcase.assertIn('"path":"result.txt"', kwargs["system_prompt"])
                order.append("engine_created")

            async def query(self, prompt):
                await self.trial_profile.before_query()
                order.append("model_query")
                yield {"type": "artifact", "content": "Exact result\r\n"}

        async def emit(event):
            events.append(event)

        async def ack():
            order.append("host_ack")
            return {"type": "engine_start_ack", "trial_id": "trial-a", "attempt": 1,
                    "input_hash": payload["receipt"]["input_hash"]}

        asyncio.run(IW.run_request(payload, ack, emit, adapter_factory=Adapter, engine_factory=Engine))
        self.assertEqual(order, ["engine_created", "host_ack", "model_query"])
        self.assertEqual([e["type"] for e in events], ["worker_ready", "engine_start_requested", "engine_event", "worker_complete"])
        self.assertEqual(events[2]["event"]["content"], "Exact result\r\n")

        async def wrong_ack():
            return {"type": "engine_start_ack", "trial_id": "another-trial", "attempt": 1,
                    "input_hash": payload["receipt"]["input_hash"]}

        order.clear()
        with self.assertRaisesRegex(ValueError, "trial_engine_start_not_acknowledged"):
            asyncio.run(IW.run_request(payload, wrong_ack, emit, adapter_factory=Adapter, engine_factory=Engine))
        self.assertEqual(order, ["engine_created"])

    def test_invalid_workspace_or_capability_never_constructs_an_engine(self):
        self.assertTrue(hasattr(IW, "run_request"), "Worker execution protocol is missing")
        payload = self.payload()

        class Adapter:
            def __init__(self, pin):
                pass

            def verify(self):
                return {"supported": True, "capability_hash": "d" * 64}

        def forbidden_engine(**kwargs):
            self.fail("Engine constructed with unverified input")

        async def noop(*args):
            return None

        with self.assertRaisesRegex(ValueError, "trial_capability_drift"):
            asyncio.run(IW.run_request(payload, noop, noop, adapter_factory=Adapter, engine_factory=forbidden_engine))
        path = Path(payload["receipt"]["input_path"])
        path.chmod(0o600)
        path.write_text("{}")
        with self.assertRaises(ValueError):
            asyncio.run(IW.run_request(payload, noop, noop, adapter_factory=Adapter, engine_factory=forbidden_engine))

    def test_parent_executor_persists_start_before_ack_and_uses_a_clean_child(self):
        import improvement_claude as IC
        self.assertTrue(hasattr(IC.ClaudeTrialAdapter, "execute"), "Parent trial executor is missing")
        payload = self.payload()
        identity = {key: payload["receipt"][key] for key in ("trial_id", "attempt", "input_hash")}
        packets = [{"type": "worker_ready", **identity, "capability_hash": "c" * 64},
                   {"type": "engine_start_requested", **identity},
                   {"type": "engine_event", **identity, "event": {"type": "artifact", "content": "Exact result\r\n"}},
                   {"type": "worker_complete", **identity, "fixture_events": [], "tool_denials": []}]

        class Process:
            pid = 987654321
            returncode = None

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"".join(json.dumps({"version": 1, "sequence": n, **packet}).encode() + b"\n"
                                                for n, packet in enumerate(packets, 1)))
                self.stderr = io.BytesIO()

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        process, acknowledgements, environment = Process(), [], {}
        original_spawn = IC.subprocess.Popen

        def spawn(*args, **kwargs):
            if not str(args[0][-1]).endswith("improvement_worker.py"):
                return original_spawn(*args, **kwargs)
            self.assertIn("-I", args[0], "Worker must ignore ambient Python paths and user startup modules")
            environment.update(kwargs["env"])
            return process

        async def persist_start():
            self.assertNotIn(b"engine_start_ack", process.stdin.getvalue())
            acknowledgements.append("persisted")

        async def run():
            adapter = IC.ClaudeTrialAdapter(payload["config_pin"], workspace_root=Path(payload["workspace_root"]),
                                           before_engine_start=persist_start)
            request = {"version": 1, "trial_id": "trial-a", "attempt": 1, "deadline_at": time.time() + 20,
                       "engine_config_hash": hashlib.sha256(b"unused").hexdigest(), "capability_hash": "c" * 64}
            from improvement_store import _hash
            request["engine_config_hash"] = _hash(payload["config_pin"])
            with patch.object(adapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}), \
                    patch.object(IC.subprocess, "Popen", side_effect=spawn), patch.object(IW, "WorkerGroup") as guard:
                events = [event async for event in adapter.execute(request, payload["receipt"])]
                guard.return_value.close.assert_called_once()
            return events

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "do-not-pass-host-secret", "X_JAVIS_TURN": "owner-turn"}):
            events = asyncio.run(run())
        self.assertEqual(acknowledgements, ["persisted"])
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("X_JAVIS_TURN", environment)
        self.assertEqual(next(e for e in events if e["type"] == "engine_event")["event"]["content"], "Exact result\r\n")

    def test_unbound_start_boundary_cannot_launch_a_worker(self):
        import improvement_claude as IC
        self.assertTrue(hasattr(IC.ClaudeTrialAdapter, "execute"), "Parent trial executor is missing")
        payload = self.payload()

        async def run():
            adapter = IC.ClaudeTrialAdapter(payload["config_pin"])
            with patch.object(IC.subprocess, "Popen", side_effect=AssertionError("Unbound worker launch")):
                return [event async for event in adapter.execute({}, payload["receipt"])]

        with self.assertRaisesRegex(ValueError, "trial_start_boundary_unbound"):
            asyncio.run(run())

    def test_isolated_worker_entrypoint_ignores_workspace_python_startup_modules(self):
        from improvement_sandbox import worker_environment
        root = Path(tempfile.mkdtemp(prefix="python-isolation-", dir=TEST_ROOT))
        (root / "tmp").mkdir()
        canary = root / "ambient-startup-ran"
        (root / "sitecustomize.py").write_text("from pathlib import Path\nPath(" + repr(str(canary)) + ").write_text('ambient')\n")
        (root / "winproc.py").write_text("raise RuntimeError('Loaded a workspace module instead of the host module')\n")
        environment = worker_environment(root, auth_home=root / "empty-auth")
        environment["PYTHONPATH"] = str(root)  # Deliberately hostile even if env filtering regresses.
        result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SERVER / "improvement_worker.py")],
            input=b'{"version":1}\n', cwd=root, env=environment, capture_output=True, timeout=15,
            creationflags=IW.process_flags())
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout, result.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout)["type"], "worker_error")
        self.assertFalse(canary.exists())


class WorkerSpawnOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def check_interrupted_spawn(self, *, timeout, interrupt_cleanup=False):
        import improvement_claude as IC
        from improvement_store import _hash
        payload = WorkerProcessTests().payload()
        entered, release = threading.Event(), threading.Event()
        closing, release_close = threading.Event(), threading.Event()
        children = []
        original_spawn = subprocess.Popen
        original_group = IW.WorkerGroup

        class Group:
            def __init__(self, pid, **kwargs):
                self.guard = original_group(pid, **kwargs)

            def close(self):
                closing.set()
                if interrupt_cleanup and not release_close.wait(10):
                    raise RuntimeError("test_close_barrier_expired")
                self.guard.close()

        def spawn(*args, **kwargs):
            child = original_spawn(*args, **kwargs)
            if str(args[0][-1]).endswith("improvement_worker.py"):
                children.append(child)
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test_spawn_barrier_expired")
            return child

        async def forbidden_start():
            self.fail("An interrupted spawn must not release a query")

        adapter = IC.ClaudeTrialAdapter(payload["config_pin"], workspace_root=payload["workspace_root"],
                                       before_engine_start=forbidden_start)
        request = {"version": 1, "trial_id": "trial-a", "attempt": 1,
                   "deadline_at": time.time() + (0.6 if timeout else 20),
                   "engine_config_hash": _hash(payload["config_pin"]), "capability_hash": "c" * 64}

        async def consume():
            return [event async for event in adapter.execute(request, payload["receipt"])]

        task = None
        try:
            with patch.object(adapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}), \
                    patch.object(subprocess, "Popen", side_effect=spawn), \
                    patch.object(IW, "WorkerGroup", side_effect=Group) as group:
                task = asyncio.create_task(consume())
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                if timeout:
                    await asyncio.sleep(max(0, request["deadline_at"] - time.time()) + 0.1)
                else:
                    task.cancel()
                    await asyncio.sleep(0.05)
                    task.cancel()
                    await asyncio.sleep(0.05)
                self.assertFalse(task.done(), "Cancellation returned before the spawned child was owned")
                release.set()
                if interrupt_cleanup:
                    self.assertTrue(await asyncio.to_thread(closing.wait, 5))
                    task.cancel()
                    await asyncio.sleep(0.05)
                    task.cancel()
                    await asyncio.sleep(0.05)
                    self.assertFalse(task.done(), "Repeated cancellation interrupted owned worker cleanup")
                    release_close.set()
                with self.assertRaises(TimeoutError if timeout else asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                group.assert_called_once()
                self.assertEqual(group.call_args.args, (children[0].pid,))
                self.assertEqual(len(group.call_args.kwargs['recovery_fences']), 2)
                self.assertEqual(group.call_args.kwargs['suspended'], sys.platform == 'win32')
                self.assertIsNotNone(children[0].poll(), "Interrupted worker survived cleanup")
                self.assertTrue(all(stream.closed for stream in
                                    (children[0].stdin, children[0].stdout, children[0].stderr)))
        finally:
            release.set()
            release_close.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()

    async def test_repeated_cancellation_retains_spawn_until_child_is_owned_and_closed(self):
        await self.check_interrupted_spawn(timeout=False)

    async def test_deadline_retains_spawn_until_child_is_owned_and_closed(self):
        await self.check_interrupted_spawn(timeout=True)

    async def test_repeated_cancellation_also_retains_worker_cleanup(self):
        await self.check_interrupted_spawn(timeout=False, interrupt_cleanup=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--crash-parent"]:
        if sys.stdin.readline().strip() == "spawn":
            descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print(descendant.pid, flush=True)
            os._exit(0)
    else:
        unittest.main()
