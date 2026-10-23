"""One fresh trial process, with host-owned IPC and lifetime-bounded descendants."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

# Isolated Python removes the script directory; restore only this trusted host source root.
_SERVER_DIR = str(Path(__file__).resolve().parent)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)
import winproc


def process_flags():
    return winproc.no_window()


@contextmanager
def _sdk_hidden_processes():
    """Single-query worker scope: hide SDK version/query descendants on Windows.

    SDK0.2.116 does not pass creation flags to its transport. This temporary
    boundary belongs only to this isolated worker, never the shared chat server
    or installed SDK. Preserve caller flags and restore the transport on exit.
    """
    from claude_agent_sdk._internal.transport import subprocess_cli
    original = subprocess_cli.anyio.open_process

    async def hidden(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | winproc.no_window()
        return await original(*args, **kwargs)

    subprocess_cli.anyio.open_process = hidden
    try:
        yield
    finally:
        subprocess_cli.anyio.open_process = original


class WorkerGroup:
    """Attach before sending input: a worker cannot start its engine before ownership exists."""

    def __init__(self, pid):
        self.pid, self.handle = pid, None
        if os.name != "nt":
            if pid == os.getpgid(0) or os.getpgid(pid) != pid:
                raise OSError("trial_worker_process_group_unavailable")
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("basic", BasicLimit), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                        ("peak_job_memory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise OSError("trial_worker_job_unavailable")
        process = None
        try:
            limits = ExtendedLimit()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError("trial_worker_job_unavailable")
            process = kernel.OpenProcess(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
            if not process or not kernel.AssignProcessToJobObject(job, process):
                raise OSError("trial_worker_job_unavailable")
        except BaseException:
            kernel.CloseHandle(job)
            raise
        finally:
            if process:
                kernel.CloseHandle(process)
        self.handle, self._kernel = job, kernel

    def close(self):
        if os.name == "nt":
            if self.handle:
                self._kernel.CloseHandle(self.handle)
                self.handle = None
        elif self.pid is not None:
            winproc.kill_tree(self.pid)
        self.pid = None


async def run_request(payload, read_control, emit, *, adapter_factory=None, engine_factory=None):
    """Validate host materialization before creating an engine; model text is always data."""
    from improvement_cases import _digest, _fields, _frozen
    from improvement_store import _hash, _json
    from improvement_sandbox import FixtureBroker, TrialWorkspace, trial_system_prompt
    from improvement_claude import ClaudeTrialAdapter, TrialProfile
    payload = _frozen(payload)
    _fields(payload, {"version", "config_pin", "workspace_root", "receipt", "capability_hash"})
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("unsupported_trial_worker_protocol")
    _digest(payload["capability_hash"])
    receipt = payload["receipt"]
    manifest = TrialWorkspace(Path(payload["workspace_root"])).validate(receipt)
    pin = payload["config_pin"]
    if manifest["engine_config_hash"] != _hash(pin):
        raise ValueError("trial_configuration_drift")
    verified = (adapter_factory or ClaudeTrialAdapter)(pin).verify()
    if not verified.get("supported") or verified.get("capability_hash") != payload["capability_hash"]:
        raise ValueError("trial_capability_drift")
    identity = {"trial_id": receipt["trial_id"], "attempt": receipt["attempt"], "input_hash": receipt["input_hash"]}
    await emit({"type": "worker_ready", **identity, "capability_hash": payload["capability_hash"]})

    async def before_query():
        await emit({"type": "engine_start_requested", **identity})
        acknowledgement = await read_control()
        if acknowledgement != {"type": "engine_start_ack", **identity}:
            raise ValueError("trial_engine_start_not_acknowledged")

    broker = FixtureBroker(manifest["case"]["fixtures"], manifest["grant_hash"])
    instructions = trial_system_prompt(manifest)
    profile_pin = {key: pin[key] for key in ("provider", "model", "reasoning", "cli_path", "sdk_version")}
    profile = TrialProfile(profile_pin, broker, instructions, before_query=before_query)
    profile.bind_installation(manifest, receipt, payload["capability_hash"])
    if engine_factory is None:
        from claude_cli import claude_engine
        engine_factory = claude_engine
    engine = engine_factory(system_prompt=instructions, cwd=receipt["path"],
                            tag=f"improvement:{receipt['trial_id']}:{receipt['attempt']}",
                            allowed_tools=[], model=pin["model"])
    engine.trial_profile = profile
    prompt = _json({"task": manifest["case"]["task"], "fixtures": [
        {"snapshot_id": item["snapshot_id"], "source": item["source"]} for item in manifest["case"]["fixtures"]]})
    fixture_sequence = 0
    with _sdk_hidden_processes():
        async for event in engine.query(prompt):
            await emit({"type": "engine_event", **identity, "event": event})
            for observation in broker.events[fixture_sequence:]:
                await emit({"type": "fixture_event", **identity, "event": observation})
            fixture_sequence = len(broker.events)
    await emit({"type": "worker_complete", **identity, "fixture_events": broker.events, "tool_denials": profile.denials})


def main():
    # Read one host request before importing any engine module or starting a descendant.
    import asyncio
    import contextlib
    import json
    import sys
    maximum = 8 * 1024 * 1024
    output = sys.stdout.buffer
    sequence = 0

    def read_packet():
        raw = sys.stdin.buffer.readline(maximum + 1)
        if not raw or len(raw) > maximum or not raw.endswith(b"\n"):
            raise ValueError("invalid_trial_worker_packet")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid_trial_worker_packet")
        return value

    async def emit(event):
        nonlocal sequence
        sequence += 1
        raw = json.dumps({"version": 1, "sequence": sequence, **event}, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(raw) > maximum:
            raise ValueError("trial_worker_event_too_large")
        output.write(raw)
        output.flush()

    async def read_control():
        return await asyncio.to_thread(read_packet)

    try:
        payload = read_packet()
        # Logs from regular engine code must never become protocol messages.
        with contextlib.redirect_stdout(sys.stderr):
            asyncio.run(run_request(payload, read_control, emit))
    except Exception as error:
        asyncio.run(emit({"type": "worker_error", "reason": "trial_worker_failed", "error_type": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
