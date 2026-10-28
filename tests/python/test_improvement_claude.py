"""Dedicated trial options and raw result observations do not inherit chat behavior."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="improvement-claude-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(TEST_ROOT / name)

if importlib.util.find_spec("improvement_claude"):
    import improvement_claude as IC
else:
    IC = None


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IC, "A dedicated verified trial profile is not implemented")
        from improvement_sandbox import FixtureBroker
        self.broker = FixtureBroker([], hashlib.sha256(b"grant").hexdigest())
        self.pin = {"provider": "anthropic-cli", "model": "claude-opus-5-5", "reasoning": "medium",
                    "cli_path": str(TEST_ROOT / "claude"), "sdk_version": "0.2.116"}

    def profile(self, before_query=None):
        return IC.TrialProfile(self.pin, self.broker, "Exact owner instructions\r\n", before_query=before_query)

    def engine(self, profile):
        from claude_sdk_engine import ClaudeSDK
        engine = ClaudeSDK(system_prompt="ambient preset", cwd=str(TEST_ROOT), model="wrong-model")
        engine.trial_profile = profile
        engine.allowed_tools = ["Bash", "mcp__javis"]
        engine.mcp_config = "production-mcp.json"
        engine.effort = "low"
        return engine

    def installation(self):
        from improvement_sandbox import TrialWorkspace
        import improvement_sandbox as SB
        self.assertTrue(hasattr(SB, "trial_system_prompt"), "Materialized system prompt needs an exact common hash")
        self.assertTrue(hasattr(IC.TrialProfile, "bind_installation"), "The native profile has no installation binding")
        sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
        public = {"version": 1, "objective": "Return accurate source content.",
                  "output": {"kind": "text", "path": "result.txt", "media_type": "text/plain"},
                  "criteria": [{"id": "artifact", "category": "mandatory", "critical": True,
                    "description": "Return a text artifact.", "kind": "file_exists", "config": {"path": "result.txt"}},
                    {"id": "accuracy", "category": "business", "critical": True,
                     "description": "Accurately reflect the authorized input.", "kind": "independent_check"}]}
        manifest = {"version": 1, "brain_id": "brain-a", "bundle_hash": sha("bundle"), "engine_config_hash": SB._hash(self.pin),
                    "contract_hash": sha("private-contract"), "grant_hash": self.broker.grant_hash,
                    "public_contract": {"content": public, "hash": SB._hash(public)},
                    "baseline_context": {"content": "Exact owner rules\r\n", "hash": sha("Exact owner rules\r\n"), "coverage": "complete"},
                    "assets": [{"id": "revision-a", "path": "skills/report/SKILL.md", "content": "Exact skill\r\n", "hash": sha("Exact skill\r\n")}],
                    "case": {"id": "case-a", "task": "Return source content", "fixtures": []}, "learning_origin": "trial"}
        root = Path(tempfile.mkdtemp(prefix="install-", dir=TEST_ROOT))
        receipt = TrialWorkspace(root).prepare("trial-install", 1, manifest)
        profile = IC.TrialProfile(self.pin, self.broker, SB.trial_system_prompt(manifest))
        profile.bind_installation(manifest, receipt, sha("verified-capability"))
        engine = self.engine(profile)
        engine.cwd = receipt["path"]
        return profile, engine, manifest, receipt

    def test_installation_receipt_distinguishes_selected_assets_from_observed_use(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock
        profile, engine, manifest, workspace = self.installation()
        options = engine._options()
        profile.observe(SystemMessage("init", {"tools": ["mcp__fixture__fixture_read"], "session_id": "session"}))
        profile.observe(AssistantMessage([TextBlock("I used revision-a perfectly")], model="claude-opus-5-5"))
        events = profile.observe(ResultMessage("success", 0, 0, False, 1, "session", result="Exact output\r\n"))
        receipt = next(e["receipt"] for e in events if e["type"] == "installation_receipt")
        self.assertEqual(receipt["input_hash"], workspace["input_hash"])
        self.assertEqual(receipt["public_contract_hash"], manifest["public_contract"]["hash"])
        self.assertEqual(receipt["system_prompt_hash"], hashlib.sha256(options.system_prompt.encode()).hexdigest())
        self.assertEqual(receipt["asset_revisions"], [{"id": "revision-a", "path": "skills/report/SKILL.md",
            "hash": manifest["assets"][0]["hash"], "available": True, "selected": True, "observed_use": "unknown"}])
        self.assertEqual(receipt["coverage"]["tool_inventory"], "observed")
        self.assertEqual(receipt["coverage"]["model"], "observed")
        self.assertEqual(receipt["coverage"]["reasoning"], "unknown")
        self.assertIsNone(receipt["actual_reasoning"])

    def test_missing_native_inventory_is_unknown_and_extra_native_tools_are_rejected(self):
        from claude_agent_sdk import ResultMessage, SystemMessage
        profile, engine, manifest, workspace = self.installation()
        engine._options()
        events = profile.observe(ResultMessage("success", 0, 0, False, 1, "session", result="Output"))
        receipt = next(e["receipt"] for e in events if e["type"] == "installation_receipt")
        self.assertEqual(receipt["coverage"]["tool_inventory"], "unknown")
        self.assertEqual(receipt["coverage"]["model"], "unknown")
        with self.assertRaisesRegex(ValueError, "trial_native_tool_inventory_mismatch"):
            profile.observe(SystemMessage("init", {"tools": ["Bash", "mcp__fixture__fixture_read"]}))

    def test_bound_prompt_or_working_directory_cannot_drift_from_the_manifest(self):
        profile, engine, manifest, workspace = self.installation()
        original = profile.system_prompt
        profile.system_prompt = "Silently reduced owner instructions"
        with self.assertRaisesRegex(ValueError, "trial_prompt_installation_mismatch"):
            engine._options()
        profile.system_prompt = original
        engine.cwd = str(TEST_ROOT)
        with self.assertRaisesRegex(ValueError, "trial_workspace_installation_mismatch"):
            engine._options()

    def test_fixture_broker_must_match_the_bound_manifest_and_grant(self):
        from improvement_sandbox import FixtureBroker
        profile, engine, manifest, workspace = self.installation()
        other = IC.TrialProfile(self.pin, FixtureBroker([], "d" * 64), profile.system_prompt)
        with self.assertRaisesRegex(ValueError, "trial_fixture_installation_mismatch"):
            other.bind_installation(manifest, workspace, "c" * 64)

    def test_trial_options_do_not_load_chat_plugins_settings_or_tools(self):
        profile = self.profile()
        engine = self.engine(profile)
        with patch.object(engine, "_mcp_servers", side_effect=AssertionError("Loaded production MCP")):
            options = engine._options()
        self.assertEqual(options.model, self.pin["model"])
        self.assertEqual(options.effort, "medium")
        self.assertEqual(options.system_prompt, "Exact owner instructions\r\n")
        for name in ("tools", "skills", "setting_sources", "plugins", "add_dirs"):
            self.assertEqual(getattr(options, name), [], name)
        self.assertEqual(options.agents, {})
        self.assertEqual(options.hooks, {})
        self.assertEqual(set(options.mcp_servers), {"fixture"})
        self.assertEqual(options.allowed_tools, ["mcp__fixture__fixture_read"])
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.permission_mode, "default")
        self.assertFalse(options.continue_conversation)
        self.assertIsNone(options.resume)
        self.assertIsNone(options.session_id)
        self.assertIsNone(options.fallback_model)
        self.assertIsNone(options.max_budget_usd)
        self.assertIsNone(options.max_turns)
        self.assertEqual(options.max_buffer_size, 8 * 1024 * 1024)
        self.assertIn("no-session-persistence", options.extra_args)
        self.assertTrue(json.loads(options.settings)["disableAllHooks"])
        denied = asyncio.run(options.can_use_tool("Bash", {"command": "touch outside"}, None))
        self.assertEqual(denied.behavior, "deny")

    def test_resume_or_reusing_a_trial_profile_is_rejected(self):
        profile = self.profile()
        engine = self.engine(profile)
        engine.session_id = "old-arm-session"
        with self.assertRaisesRegex(ValueError, "trial_resume_forbidden"):
            engine._options()

    def test_trial_availability_uses_only_the_exact_pinned_binary(self):
        pinned = Path(self.pin["cli_path"])
        pinned.write_text("Synthetic file only; never execute")
        engine = self.engine(self.profile())
        with patch("claude_cli.find_claude_cli", side_effect=AssertionError("Ambient CLI fallback")):
            self.assertTrue(engine.is_available())
            pinned.unlink()
            self.assertFalse(engine.is_available())

    def test_raw_result_preserves_null_usage_and_final_exact_bytes(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        profile = self.profile()
        interim = profile.observe(AssistantMessage([TextBlock("Intermediate narration")], model="observed-model"))
        self.assertFalse(any(e["type"] == "artifact" for e in interim))
        result = ResultMessage("success", 10, 5, False, 1, "session", result="```json\r\n{}\r\n```\r\n")
        events = profile.observe(result)
        artifact = next(e for e in events if e["type"] == "artifact")
        usage = next(e for e in events if e["type"] == "usage")
        self.assertEqual(artifact["content"].encode(), result.result.encode())
        self.assertIsNone(usage["usage"])
        self.assertIsNone(usage["cost_usd"])
        self.assertEqual(profile.observed_models, {"observed-model"})
        self.assertEqual(profile.actual_reasoning_coverage, "unknown")

    def test_missing_result_and_explicit_zero_usage_remain_distinct(self):
        from claude_agent_sdk import ResultMessage
        events = self.profile().observe(ResultMessage("success", 0, 0, False, 1, "session",
                                                     usage={"input_tokens": 0, "output_tokens": 0}, total_cost_usd=0.0))
        self.assertFalse(any(e["type"] == "artifact" for e in events))
        self.assertEqual(next(e for e in events if e["type"] == "usage")["cost_usd"], 0.0)
        self.assertEqual(next(e for e in events if e["type"] == "terminal")["artifact_status"], "unknown")

    def test_native_unavailable_tool_is_recorded_as_denied_not_just_a_tool_error(self):
        from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
        profile = self.profile()
        profile.observe(AssistantMessage([ToolUseBlock("outside", "Bash", {"command": "forbidden"})], model=self.pin["model"]))
        events = profile.observe(UserMessage([ToolResultBlock("outside", "Tool is not available", is_error=True)]))
        self.assertTrue(any(e["type"] == "tool_denied" and e["name"] == "Bash" for e in events), events)
        self.assertEqual(profile.denials[0]["tool_use_id"], "outside")

    def test_successful_outside_tool_result_is_an_isolation_failure(self):
        from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
        profile = self.profile()
        profile.observe(AssistantMessage([ToolUseBlock("outside", "Read", {"file_path": "outside"})], model=self.pin["model"]))
        with self.assertRaisesRegex(ValueError, "trial_unknown_tool_execution"):
            profile.observe(UserMessage([ToolResultBlock("outside", "Unexpected success", is_error=False)]))

    def test_provider_error_envelopes_keep_quota_status_and_unknown_usage(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage
        profile = self.profile()
        observation = profile.observe(AssistantMessage([], model=self.pin["model"], error="rate_limit"))[0]
        self.assertEqual(observation.get("error"), "rate_limit")
        events = profile.observe(ResultMessage("error_during_execution", 0, 0, True, 1, "session",
                                             errors=["rate_limit"], api_error_status=429))
        terminal = next(e for e in events if e["type"] == "terminal")
        self.assertEqual(terminal.get("api_error_status"), 429)
        self.assertEqual(terminal.get("errors"), ["rate_limit"])
        self.assertIsNone(next(e for e in events if e["type"] == "usage")["usage"])

    def test_transport_exception_keeps_host_error_type_and_status_for_trial_only(self):
        class QuotaFailure(Exception):
            status_code = 429

        class Client:
            def __init__(self, options):
                pass

            async def connect(self):
                raise QuotaFailure("Synthetic quota failure")

            async def disconnect(self):
                pass

        async def run():
            engine = self.engine(self.profile())
            with patch.object(engine, "is_available", return_value=True), patch("claude_agent_sdk.ClaudeSDKClient", Client), \
                    patch("claude_token_gate.xep_hang", new=AsyncMock(return_value="")):
                return [event async for event in engine.query("Pinned task")]

        events = asyncio.run(run())
        error = next(e for e in events if e["type"] == "error")
        self.assertEqual(error.get("error_type"), "QuotaFailure")
        self.assertEqual(error.get("status_code"), 429)

    def test_parent_permission_must_arrive_before_native_query(self):
        from claude_agent_sdk import ResultMessage
        queries = []

        class Client:
            def __init__(self, options):
                pass

            async def connect(self):
                pass

            async def query(self, prompt):
                queries.append(prompt)

            async def receive_response(self):
                yield ResultMessage("success", 0, 0, False, 1, "session", result="Done")

            async def disconnect(self):
                pass

        async def no_permission():
            raise RuntimeError("host_engine_start_not_acknowledged")

        async def run():
            engine = self.engine(self.profile(no_permission))
            with patch.object(engine, "is_available", return_value=True), patch("claude_agent_sdk.ClaudeSDKClient", Client), \
                    patch("claude_token_gate.xep_hang", new=AsyncMock(return_value="")):
                return [event async for event in engine.query("Pinned task")]

        events = asyncio.run(run())
        self.assertFalse(queries)
        self.assertTrue(any(e["type"] == "error" for e in events))

    def test_existing_engine_query_uses_raw_trial_observations_after_ack(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        order = []

        class Client:
            def __init__(self, options):
                self.options = options

            async def connect(self):
                order.append("connected")

            async def query(self, prompt):
                order.append("query")

            async def receive_response(self):
                yield AssistantMessage([TextBlock("Interim, not the product")], model="claude-opus-5-5")
                yield ResultMessage("success", 0, 0, False, 1, "session", result="Exact final\r\n")

            async def disconnect(self):
                order.append("disconnected")

        async def ack():
            order.append("durable_ack")

        async def run():
            engine = self.engine(self.profile(ack))
            with patch.object(engine, "is_available", return_value=True), patch("claude_agent_sdk.ClaudeSDKClient", Client), \
                    patch("claude_token_gate.xep_hang", new=AsyncMock(return_value="")), \
                    patch("claude_sdk_engine.map_message", side_effect=AssertionError("Generic mapper loses unknown usage")):
                return [event async for event in engine.query("Pinned task")]

        events = asyncio.run(run())
        self.assertEqual(order, ["connected", "durable_ack", "query", "disconnected"])
        self.assertEqual([e["content"] for e in events if e["type"] == "artifact"], ["Exact final\r\n"])
        self.assertIsNone(next(e for e in events if e["type"] == "usage")["usage"])


class AdapterVerificationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(IC is not None and hasattr(IC, "ClaudeTrialAdapter"), "Verified trial adapter is missing")
        self.pin = {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk",
                    "model": "claude-opus-5-5", "reasoning": "medium", "cli_path": str(TEST_ROOT / "claude.exe"),
                    "cli_hash": "a" * 64, "cli_version": "2.1.304", "sdk_version": "0.2.116",
                    "profile_version": IC.PROFILE_VERSION, "profile_hash": "b" * 64}
        self.observation = {"platform": "win32", "cli_hash": "a" * 64, "cli_version": "2.1.304",
                            "sdk_version": "0.2.116", "profile_hash": "b" * 64, "managed_policy": "absent",
                            "auth": {"logged_in": True, "method": "claude.ai", "provider": "firstParty", "plan": "max"}}

    def verify(self):
        known = {("win32", "2.1.304", "a" * 64, "0.2.116")}
        with patch.object(IC, "_runtime_observation", return_value=self.observation), \
                patch.object(IC, "VERIFIED_NATIVE_RUNTIMES", known), \
                patch.object(IC, "_profile_qualification", return_value="c" * 64):
            return IC.ClaudeTrialAdapter(self.pin).verify()

    def test_new_profile_is_not_qualified_by_an_old_known_runtime_tuple(self):
        pin = {**self.pin, "profile_hash": IC.profile_hash()}
        with patch.object(IC, "_runtime_observation", side_effect=AssertionError("Unqualified profile probed native auth/version")):
            result = IC.ClaudeTrialAdapter(pin).verify()
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason"], "native_profile_unqualified")

    def test_supported_verification_has_a_pinned_capability_hash_without_account_data(self):
        result = self.verify()
        self.assertTrue(result["supported"], result)
        self.assertEqual(result["requested"]["model"], "claude-opus-5-5")
        self.assertEqual(result["requested"]["reasoning"], "medium")
        self.assertEqual(len(result["capability_hash"]), 64)
        self.assertNotIn("email", json.dumps(result))
        self.assertNotIn("token", json.dumps(result))

    def test_managed_unknown_or_present_policy_is_unsupported(self):
        for state in ("unknown", "present"):
            self.observation["managed_policy"] = state
            self.assertFalse(self.verify()["supported"])

    def test_native_sdk_profile_or_login_drift_never_switches_configuration(self):
        for field, value in (("cli_hash", "c" * 64), ("cli_version", "999"), ("sdk_version", "999"),
                             ("profile_hash", "c" * 64), ("platform", "unverified-os")):
            original = self.observation[field]
            self.observation[field] = value
            with self.subTest(field=field):
                self.assertFalse(self.verify()["supported"])
            self.observation[field] = original
        self.observation["auth"]["plan"] = "enterprise"
        self.assertFalse(self.verify()["supported"])

    def test_unavailable_verification_returns_a_reason_without_creating_an_engine(self):
        with patch.object(IC, "_runtime_observation", side_effect=OSError("native unavailable")), \
                patch.object(IC, "_profile_qualification", return_value="c" * 64), \
                patch("claude_cli.claude_engine", side_effect=AssertionError("Engine created before verification")):
            result = IC.ClaudeTrialAdapter(self.pin).verify()
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason"], "runtime_unavailable")

    def test_unknown_binary_is_never_executed_even_for_version_or_auth(self):
        path = TEST_ROOT / "unverified-binary"
        path.write_text("Never execute this file")
        pin = {**self.pin, "cli_path": str(path)}
        with patch.object(IC, "profile_hash", return_value="b" * 64), \
                patch.object(IC, "_managed_policy", return_value="absent"), \
                patch.object(IC.subprocess, "run", side_effect=AssertionError("Unverified binary executed")):
            observed = IC._runtime_observation(pin)
        self.assertEqual(observed["auth"], {})

    def test_profile_registration_binds_every_runtime_and_source_field_to_evidence(self):
        import improvement_native_qualifications as registry
        key = ("win32", self.pin["cli_version"], self.pin["cli_hash"], self.pin["sdk_version"],
               self.pin["profile_version"], self.pin["profile_hash"])
        with patch.object(registry, "QUALIFIED_PROFILES", {key: "d" * 64}):
            self.assertEqual(registry.profile_qualification(self.pin, "win32"), "d" * 64)
            self.assertIsNone(registry.profile_qualification(self.pin, "linux"))
            for field in ("cli_version", "cli_hash", "sdk_version", "profile_version", "profile_hash"):
                with self.subTest(field=field):
                    self.assertIsNone(registry.profile_qualification({**self.pin, field: "changed"}, "win32"))
        for forged in (True, "caller-ready", None):
            with patch.object(registry, "QUALIFIED_PROFILES", {key: forged}):
                self.assertIsNone(registry.profile_qualification(self.pin, "win32"))


if __name__ == "__main__":
    unittest.main()
