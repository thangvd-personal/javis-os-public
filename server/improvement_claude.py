"""A closed trial profile for the existing Claude engine; release capability is gated separately."""
from __future__ import annotations

import hashlib
import asyncio
import importlib.metadata
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import winproc

from improvement_blobs import MAX_ASSET_BYTES, check_ancestry
from improvement_cases import _digest, _fields, _frozen, _text
from improvement_store import _hash

PROFILE_VERSION = "claude-isolated-trial-v1"
VERIFIED_NATIVE_RUNTIMES = {("win32", "2.1.304",
    "019e5174d5d954585edb1a946fe280e44a973050b4897034adf0e16306f85fdd", "0.2.116"),
    ("win32", "2.1.308", "c9796ba22a9221586b36371b36f5b66198d66eb4badf6a4cf4a5f7b07becea53", "0.2.116")}


async def _settled_owned(future):
    """Retain an owned operation through repeated caller cancellation."""
    cancelled = None
    while True:
        try:
            return await asyncio.shield(future), cancelled
        except asyncio.CancelledError as error:
            if future.cancelled():
                raise
            cancelled = error


def profile_hash():
    root = Path(__file__).parent
    return _hash({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                  ("improvement_claude.py", "improvement_sandbox.py", "improvement_worker.py", "claude_sdk_engine.py")})


def _managed_policy():
    """Detect policy presence without reading policy contents or login files."""
    roots = {"win32": Path(r"C:\Program Files\ClaudeCode"),
             "darwin": Path("/Library/Application Support/ClaudeCode"), "linux": Path("/etc/claude-code")}
    if sys.platform not in roots:
        return "unknown"
    paths = [roots[sys.platform] / name for name in ("managed-settings.json", "managed-settings.d", "managed-mcp.json")]
    paths.append(Path.home() / ".claude" / "remote-settings.json")
    try:
        for path in paths:
            try:
                path.lstat()
                return "present"
            except FileNotFoundError:
                pass
        if sys.platform == "win32":
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                    try:
                        with winreg.OpenKey(hive, r"SOFTWARE\Policies\ClaudeCode", 0, winreg.KEY_READ | view):
                            return "present"
                    except FileNotFoundError:
                        pass
        elif sys.platform == "darwin":
            # No macOS native runtime has been verified; do not infer MDM absence from files.
            return "unknown"
    except OSError:
        return "unknown"
    return "absent"


def _runtime_observation(pin):
    from improvement_sandbox import worker_environment
    binary = Path(pin["cli_path"])
    if not binary.is_absolute():
        raise ValueError("invalid_trial_binary")
    check_ancestry(binary)
    with binary.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    managed = _managed_policy()
    result = {"platform": sys.platform, "cli_hash": digest,
              "sdk_version": importlib.metadata.version("claude-agent-sdk"),
              "profile_hash": profile_hash(), "managed_policy": managed, "auth": {}}
    known = (sys.platform, pin["cli_version"], digest, result["sdk_version"])
    if (managed != "absent" or digest != pin["cli_hash"] or known not in VERIFIED_NATIVE_RUNTIMES
            or result["profile_hash"] != pin["profile_hash"]):
        return result
    with tempfile.TemporaryDirectory(prefix="improvement-verify-") as scratch:
        environment = worker_environment(Path(scratch))
        version = subprocess.run([str(binary), "--version"], env=environment, capture_output=True,
                                 text=True, timeout=10, check=True, creationflags=winproc.no_window())
        result["cli_version"] = version.stdout.strip().split()[0]
        auth = subprocess.run([str(binary), "auth", "status", "--json"], env=environment, capture_output=True,
                              text=True, timeout=15, check=True, creationflags=winproc.no_window())
        status = json.loads(auth.stdout)
        result["auth"] = {"logged_in": status.get("loggedIn") is True, "method": status.get("authMethod"),
                          "provider": status.get("apiProvider"), "plan": status.get("subscriptionType")}
    return result


class ClaudeTrialAdapter:
    def __init__(self, config_pin, *, workspace_root=None, before_engine_start=None):
        self.pin = _frozen(config_pin)
        _fields(self.pin, {"version", "provider", "engine_id", "model", "reasoning", "cli_path", "cli_hash",
                           "cli_version", "sdk_version", "profile_version", "profile_hash"})
        if type(self.pin["version"]) is not int or self.pin["version"] != 1:
            raise ValueError("unsupported_trial_configuration")
        for name in ("cli_hash", "profile_hash"):
            _digest(self.pin[name])
        for name, value in self.pin.items():
            if name != "version":
                _text(value)
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None
        self.before_engine_start = before_engine_start

    def verify(self):
        requested = {key: self.pin[key] for key in ("provider", "engine_id", "model", "reasoning")}

        def unsupported(reason):
            return {"supported": False, "reason": reason, "requested": requested}

        if (self.pin["provider"] != "anthropic-cli" or self.pin["engine_id"] != "claude-sdk"
                or self.pin["profile_version"] != PROFILE_VERSION
                or self.pin["reasoning"] not in ("low", "medium", "high", "xhigh", "max")):
            return unsupported("unsupported_trial_configuration")
        try:
            observed = _runtime_observation(self.pin)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError):
            return unsupported("runtime_unavailable")
        if observed.get("managed_policy") != "absent":
            return unsupported("managed_policy_unverified")
        if any(observed.get(field) != self.pin[field] for field in ("cli_hash", "cli_version", "sdk_version", "profile_hash")):
            return unsupported("trial_runtime_drift")
        runtime = tuple(observed.get(key) for key in ("platform", "cli_version", "cli_hash", "sdk_version"))
        if runtime not in VERIFIED_NATIVE_RUNTIMES:
            return unsupported("native_enforcement_unverified")
        if observed.get("auth") != {"logged_in": True, "method": "claude.ai", "provider": "firstParty", "plan": "max"}:
            return unsupported("native_auth_policy_unverified")
        return {"supported": True, "reason": None, "requested": requested,
                "capability_hash": _hash({"pin": self.pin, "runtime": observed}), "runtime": observed}

    async def execute(self, request, workspace):
        """The parent persists engine start before releasing a query in a clean process."""
        from improvement_sandbox import TrialWorkspace, _private_directory, worker_environment
        from improvement_store import _json
        from improvement_worker import WorkerGroup, process_flags
        if self.workspace_root is None or not callable(self.before_engine_start):
            raise ValueError("trial_start_boundary_unbound")
        request = _frozen(request)
        _fields(request, {"version", "trial_id", "attempt", "deadline_at", "engine_config_hash", "capability_hash"})
        if type(request["version"]) is not int or request["version"] != 1 or type(request["attempt"]) is not int:
            raise ValueError("invalid_trial_execution_request")
        deadline = request["deadline_at"]
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.time():
            raise ValueError("trial_deadline_expired")
        for name in ("engine_config_hash", "capability_hash"):
            _digest(request[name])
        receipt = _frozen(workspace)
        manifest = await asyncio.to_thread(TrialWorkspace(self.workspace_root).validate, receipt)
        if (request["trial_id"] != receipt["trial_id"] or request["attempt"] != receipt["attempt"]
                or request["engine_config_hash"] != manifest["engine_config_hash"]
                or request["engine_config_hash"] != _hash(self.pin)):
            raise ValueError("trial_configuration_drift")
        verified = await asyncio.to_thread(self.verify)
        if not verified.get("supported") or verified.get("capability_hash") != request["capability_hash"]:
            raise ValueError("trial_capability_drift")
        await asyncio.to_thread(_private_directory, Path(receipt["path"]) / "tmp")
        environment = worker_environment(Path(receipt["path"]))
        identity = {key: receipt[key] for key in ("trial_id", "attempt", "input_hash")}
        process = guard = None
        stderr_task = None
        maximum = 8 * 1024 * 1024

        async def send(packet):
            raw = _json(packet).encode("utf-8") + b"\n"

            def write():
                process.stdin.write(raw)
                process.stdin.flush()

            await asyncio.to_thread(write)

        async def drain_stderr():
            # Drain to avoid a pipe deadlock; raw stderr is never a public result or artifact.
            while await asyncio.to_thread(process.stderr.read, 4096):
                pass

        async def close_worker():
            if guard is not None:
                await asyncio.to_thread(guard.close)
            elif process is not None and process.poll() is None:
                await asyncio.to_thread(winproc.kill_tree, process.pid)
            if process is not None:
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            if stderr_task is not None:
                await asyncio.gather(stderr_task, return_exceptions=True)

        try:
            async with asyncio.timeout(max(0.0, deadline - time.time())):
                spawn = asyncio.create_task(asyncio.to_thread(subprocess.Popen,
                    [sys.executable, "-I", "-X", "utf8", str(Path(__file__).with_name("improvement_worker.py"))],
                    cwd=receipt["path"], env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, start_new_session=sys.platform != "win32", creationflags=process_flags()))
                spawned, cancelled = await _settled_owned(asyncio.gather(spawn, return_exceptions=True))
                if isinstance(spawned[0], BaseException):
                    raise spawned[0]
                process = spawned[0]
                # The child blocks on its first input until its whole lifetime is owned.
                guard = WorkerGroup(process.pid)
                if cancelled is not None:
                    raise cancelled
                stderr_task = asyncio.create_task(drain_stderr())
                await send({"version": 1, "config_pin": self.pin, "workspace_root": str(self.workspace_root),
                            "receipt": receipt, "capability_hash": request["capability_hash"]})
                sequence, state = 0, "starting"
                while True:
                    raw = await asyncio.to_thread(process.stdout.readline, maximum + 1)
                    if not raw or len(raw) > maximum or not raw.endswith(b"\n"):
                        raise ValueError("trial_worker_protocol_incomplete")
                    packet = json.loads(raw)
                    sequence += 1
                    if (not isinstance(packet, dict) or type(packet.get("sequence")) is not int
                            or packet.get("sequence") != sequence or type(packet.get("version")) is not int
                            or packet.get("version") != 1):
                        raise ValueError("invalid_trial_worker_packet")
                    kind = packet.get("type")
                    if kind == "worker_error":
                        _fields(packet, {"version", "sequence", "type", "reason", "error_type"})
                        yield packet
                        raise RuntimeError("trial_worker_failed")
                    extras = {"worker_ready": {"capability_hash"}, "engine_start_requested": set(),
                              "engine_event": {"event"}, "fixture_event": {"event"},
                              "worker_complete": {"fixture_events", "tool_denials"}}
                    if kind not in extras:
                        raise ValueError("invalid_trial_worker_packet")
                    _fields(packet, {"version", "sequence", "type", *identity, *extras[kind]})
                    if any(packet.get(key) != value for key, value in identity.items()):
                        raise ValueError("trial_worker_identity_drift")
                    if kind == "worker_ready":
                        if state != "starting" or packet["capability_hash"] != request["capability_hash"]:
                            raise ValueError("trial_capability_drift")
                        state = "ready"
                    elif kind == "engine_start_requested":
                        if state != "ready":
                            raise ValueError("trial_worker_start_repeated")
                        await self.before_engine_start()
                        await send({"type": "engine_start_ack", **identity})
                        state = "running"
                    elif state != "running":
                        raise ValueError("trial_worker_event_before_start")
                    yield packet
                    if kind == "worker_complete":
                        break
                if await asyncio.to_thread(process.wait, timeout=5) != 0:
                    raise RuntimeError("trial_worker_failed")
        finally:
            closed, cancelled = await _settled_owned(asyncio.gather(
                asyncio.create_task(close_worker()), return_exceptions=True))
            if isinstance(closed[0], BaseException):
                raise closed[0]
            if cancelled is not None:
                raise cancelled


class TrialProfile:
    """Host-created options and raw telemetry, used only in an isolated child process."""

    def __init__(self, pin, broker, system_prompt, *, before_query=None):
        self.pin = _frozen(pin)
        _fields(self.pin, {"provider", "model", "reasoning", "cli_path", "sdk_version"})
        if self.pin["provider"] != "anthropic-cli" or self.pin["reasoning"] not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("unsupported_trial_configuration")
        for value in self.pin.values():
            _text(value)
        if not isinstance(system_prompt, str) or len(system_prompt.encode()) > MAX_ASSET_BYTES:
            raise ValueError("invalid_trial_system_prompt")
        self.broker, self.system_prompt = broker, system_prompt
        self._before_query = before_query
        self._started = False
        self.observed_models = set()
        self.actual_reasoning_coverage = "unknown"
        self.denials = []
        self._installation = None
        self._selection = None
        self._native_tools = None
        self._native_session_id = None
        self._tool_names = {}
        self._denied_calls = set()

    def bind_installation(self, manifest, workspace, capability_hash):
        from improvement_sandbox import TrialWorkspace, _manifest, trial_system_prompt
        if self._installation is not None or self._started:
            raise ValueError("trial_installation_already_bound")
        _digest(capability_hash)
        manifest = _manifest(manifest)
        workspace = _frozen(workspace)
        actual = TrialWorkspace(Path(workspace["path"]).parents[1]).validate(workspace)
        if _hash(actual) != _hash(manifest):
            raise ValueError("trial_installation_input_mismatch")
        if (self.broker.grant_hash != manifest["grant_hash"]
                or _hash(list(self.broker.fixtures.values())) != _hash(manifest["case"]["fixtures"])):
            raise ValueError("trial_fixture_installation_mismatch")
        prompt_hash = hashlib.sha256(trial_system_prompt(manifest).encode("utf-8")).hexdigest()
        if hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest() != prompt_hash:
            raise ValueError("trial_prompt_installation_mismatch")
        self._installation = {"manifest": manifest, "workspace": workspace,
                              "capability_hash": capability_hash, "prompt_hash": prompt_hash}

    def installation_receipt(self, session_id):
        if self._installation is None:
            return None
        bound = self._installation
        manifest, workspace = bound["manifest"], bound["workspace"]
        selection = self._selection or {}
        tools = ["mcp__fixture__fixture_read"]
        model_coverage = ("observed" if self.observed_models == {self.pin["model"]}
                          else "mismatch" if self.observed_models else "unknown")
        return {"version": 1, "trial_id": workspace["trial_id"], "attempt": workspace["attempt"], "session_id": session_id,
                "input_hash": workspace["input_hash"], "effective_hash": workspace["effective_hash"],
                "bundle_hash": manifest["bundle_hash"], "contract_hash": manifest["contract_hash"],
                "public_contract_hash": manifest["public_contract"]["hash"], "engine_config_hash": manifest["engine_config_hash"],
                "grant_hash": manifest["grant_hash"], "capability_hash": bound["capability_hash"],
                "baseline_context_hash": manifest["baseline_context"]["hash"],
                "expected_system_prompt_hash": bound["prompt_hash"], "system_prompt_hash": selection.get("system_prompt_hash"),
                "tool_inventory_hash": _hash(tools),
                "observed_tool_inventory_hash": _hash(self._native_tools) if self._native_tools is not None else None,
                "fixture_inventory_hash": _hash([{key: item[key] for key in ("snapshot_id", "hash", "source")}
                                                 for item in manifest["case"]["fixtures"]]),
                "asset_revisions": [{**{key: asset[key] for key in ("id", "path", "hash")}, "available": True,
                                     "selected": self._selection is not None, "observed_use": "unknown"}
                                    for asset in manifest["assets"]],
                "requested": {key: self.pin[key] for key in ("provider", "model", "reasoning")},
                "actual_models": sorted(self.observed_models), "actual_reasoning": None,
                "coverage": {"input": "host_verified", "runtime": "host_verified",
                             "context": manifest["baseline_context"]["coverage"],
                             "prompt": "host_attested" if self._selection is not None else "unknown",
                             "tool_inventory": "observed" if self._native_tools == tools else "unknown",
                             "model": model_coverage, "reasoning": "unknown"}}

    async def before_query(self):
        if self._started:
            raise ValueError("trial_profile_already_used")
        self._started = True
        if self._before_query is None:
            raise RuntimeError("host_engine_start_not_acknowledged")
        await self._before_query()

    def options(self, engine):
        from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny, create_sdk_mcp_server, tool
        if engine.session_id:
            raise ValueError("trial_resume_forbidden")
        if self._installation is not None:
            if hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest() != self._installation["prompt_hash"]:
                raise ValueError("trial_prompt_installation_mismatch")
            if Path(engine.cwd).resolve() != Path(self._installation["workspace"]["path"]).resolve():
                raise ValueError("trial_workspace_installation_mismatch")

        @tool("fixture_read", "Read a pinned authorized fixture by snapshot identity and source version",
              {"snapshot_id": str, "source_version": str})
        async def fixture_read(args):
            if isinstance(args, dict) and set(args) == {"snapshot_id", "source_version"}:
                result = self.broker.read("fixture.read", {"snapshot_id": args["snapshot_id"]},
                                          args["source_version"], self.broker.grant_hash)
            else:
                result = self.broker.read("fixture.read", {}, None, self.broker.grant_hash)
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "is_error": result["status"] != "fixture_hit"}

        async def deny(name, args, context):
            self.denials.append({"type": "tool_denied", "name": str(name), "reason": "outside_trial_fixture_grant"})
            return PermissionResultDeny(message="Only the pinned fixture tool is authorized in this trial")

        options = ClaudeAgentOptions(cli_path=self.pin["cli_path"], cwd=engine.cwd,
            system_prompt=self.system_prompt, model=self.pin["model"], effort=self.pin["reasoning"],
            tools=[], skills=[], setting_sources=[], plugins=[], agents={}, hooks={}, add_dirs=[],
            mcp_servers={"fixture": create_sdk_mcp_server("fixture", tools=[fixture_read])}, strict_mcp_config=True,
            allowed_tools=["mcp__fixture__fixture_read"], permission_mode="default", can_use_tool=deny,
            settings='{"fastMode":false,"disableAllHooks":true}',
            extra_args={"no-session-persistence": None, "disable-slash-commands": None, "no-chrome": None,
                        "restricted": None, "safe-mode": None},
            max_buffer_size=8 * 1024 * 1024, stderr=engine._on_stderr)
        self._selection = {"system_prompt_hash": hashlib.sha256(options.system_prompt.encode("utf-8")).hexdigest()}
        return options

    def observe(self, message):
        """Never fabricate a missing usage field or assemble an artifact from narration."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, ToolUseBlock, UserMessage, ToolResultBlock
        events = []
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                tools = message.data.get("tools")
                if tools is not None:
                    if tools != ["mcp__fixture__fixture_read"]:
                        raise ValueError("trial_native_tool_inventory_mismatch")
                    self._native_tools = list(tools)
                self._native_session_id = message.data.get("session_id")
            events.append({"type": "native_system", "subtype": message.subtype, "data": _frozen(message.data)})
        elif isinstance(message, AssistantMessage):
            if message.model:
                self.observed_models.add(message.model)
            events.append({"type": "observation", "model": message.model, "message_id": message.message_id,
                           "usage": _frozen(message.usage), "session_id": message.session_id, "error": message.error})
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    self._tool_names[block.id] = block.name
                    events.append({"type": "tool_call", "name": block.name, "id": block.id})
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    name = self._tool_names.get(block.tool_use_id)
                    if name is not None and name != "mcp__fixture__fixture_read":
                        if not block.is_error:
                            raise ValueError("trial_unknown_tool_execution")
                        if block.tool_use_id not in self._denied_calls:
                            denial = {"type": "tool_denied", "name": name, "tool_use_id": block.tool_use_id,
                                      "reason": "tool_unavailable_in_trial"}
                            self._denied_calls.add(block.tool_use_id)
                            self.denials.append(denial)
                            events.append(denial)
                    events.append({"type": "tool_result", "id": block.tool_use_id, "is_error": block.is_error})
        elif isinstance(message, ResultMessage):
            if self._native_session_id and self._native_session_id != message.session_id:
                raise ValueError("trial_native_session_mismatch")
            events.append({"type": "usage", "usage": _frozen(message.usage), "cost_usd": message.total_cost_usd,
                           "model_usage": _frozen(message.model_usage), "session_id": message.session_id})
            content = message.result
            artifact_status = "unknown"
            if isinstance(content, str) and len(content.encode("utf-8")) <= MAX_ASSET_BYTES:
                events.append({"type": "artifact", "content": content,
                               "hash": hashlib.sha256(content.encode("utf-8")).hexdigest()})
                artifact_status = "observed"
            receipt = self.installation_receipt(message.session_id)
            if receipt is not None:
                events.append({"type": "installation_receipt", "receipt": receipt, "hash": _hash(receipt)})
            events.append({"type": "terminal", "subtype": message.subtype, "is_error": message.is_error,
                           "artifact_status": artifact_status, "session_id": message.session_id,
                           "api_error_status": message.api_error_status, "errors": _frozen(message.errors),
                           "actual_models": sorted(self.observed_models), "actual_reasoning_coverage": "unknown"})
        return events
