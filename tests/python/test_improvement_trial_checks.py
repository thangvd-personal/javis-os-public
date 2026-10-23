"""Pure execution record checks cannot promote missing native evidence into success."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="improvement-trial-checks-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(TEST_ROOT / name)

from improvement_cases import public_contract  # noqa: E402
from improvement_sandbox import _effective_hash, trial_system_prompt  # noqa: E402
from improvement_store import _hash  # noqa: E402

if importlib.util.find_spec("improvement_trial_checks"):
    import improvement_trial_checks as IC
else:
    IC = None


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TrialChecksTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IC, "Pure host trial execution checks are not implemented")
        self.contract = {"version": 1, "objective": "Report the authorized source accurately.",
            "output": {"kind": "text", "path": "answer.json", "media_type": "application/json"},
            "criteria": [{"id": "shape", "category": "mandatory", "critical": True, "description": "Return JSON.",
                          "kind": "json_keys", "config": {"path": "answer.json", "keys": ["value"]}},
                         {"id": "accuracy", "category": "business", "critical": True, "description": "Report accurately.",
                          "kind": "owner_confirm", "config": {"path": "answer.json"}}],
            "primary_metric": {"criterion_id": "accuracy", "direction": "higher", "aggregation": "all_pass"},
            "evaluator": {"id": "fixture-evaluator", "version": "1", "hash": sha("evaluator")},
            "unknown_conditions": ["missing_artifact"], "output_hash_policy": "raw_sha256"}
        engine = {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk", "model": "claude-opus-5-5",
                  "reasoning": "medium", "cli_path": "never-start-a-cli", "cli_hash": sha("binary"),
                  "cli_version": "2.1.304", "sdk_version": "0.2.116", "profile_version": "synthetic", "profile_hash": sha("profile")}
        context = {"content": "Exact owner rules\r\n", "hash": sha("Exact owner rules\r\n"), "coverage": "complete"}
        pins = {"experiment_id": "experiment-1", "base_bundle_hash": sha("baseline"), "candidate_bundle_hash": sha("candidate"),
                "contract_hash": _hash(self.contract), "case_set_hash": sha("cases"), "engine_config_hash": _hash(engine),
                "grant_hash": sha("grant"), "policy_version": 0, "scope_hash": sha("scope")}
        self.request = {"version": 1, "pins": pins, "case_set_id": "case-set-1", "case_id": "case-1", "variant": "baseline",
                        "repeat": 1, "engine_config": engine, "baseline_context_hash": _hash(context),
                        "target_generations": {"target-1": 1}, "deadline_at": 9999999999}
        public = public_contract(self.contract)
        self.manifest = {"version": 1, "brain_id": "brain-a", "bundle_hash": pins["base_bundle_hash"],
            "engine_config_hash": pins["engine_config_hash"], "contract_hash": pins["contract_hash"], "grant_hash": pins["grant_hash"],
            "baseline_context": context, "public_contract": {"content": public, "hash": _hash(public)},
            "assets": [{"id": "revision-1", "path": "skills/report/SKILL.md", "content": "Exact skill\r\n", "hash": sha("Exact skill\r\n")}],
            "case": {"id": "case-1", "task": "Report the pinned source", "fixtures": [{"snapshot_id": "snapshot-1",
                "content": "42", "hash": sha("42"), "source": {"source_version": "v1", "data_period": "2026-09",
                "timezone": "Asia/Ho_Chi_Minh", "cutoff": "2026-09-30", "schema": "text/plain"}}]}, "learning_origin": "trial"}
        root = TEST_ROOT / "trial-1" / "1"
        self.workspace = {"trial_id": "trial-1", "attempt": 1, "path": str(root), "input_path": str(root / "input.json"),
            "output_root": str(root / "outputs"), "input_hash": _hash(self.manifest), "effective_hash": _effective_hash(self.manifest)}
        self.capability = sha("verified-capability")
        self.fixture = {"sequence": 1, "status": "fixture_hit", "snapshot_id": "snapshot-1", "capability_id": "fixture.read"}
        tools = ["mcp__fixture__fixture_read"]
        prompt_hash = sha(trial_system_prompt(self.manifest))
        self.receipt = {"version": 1, "trial_id": "trial-1", "attempt": 1, "session_id": "session-1",
            "input_hash": self.workspace["input_hash"], "effective_hash": self.workspace["effective_hash"],
            "bundle_hash": self.manifest["bundle_hash"], "contract_hash": pins["contract_hash"],
            "public_contract_hash": _hash(public), "engine_config_hash": pins["engine_config_hash"], "grant_hash": pins["grant_hash"],
            "capability_hash": self.capability, "baseline_context_hash": context["hash"], "expected_system_prompt_hash": prompt_hash,
            "system_prompt_hash": prompt_hash, "tool_inventory_hash": _hash(tools), "observed_tool_inventory_hash": _hash(tools),
            "fixture_inventory_hash": _hash([{key: item[key] for key in ("snapshot_id", "hash", "source")}
                                            for item in self.manifest["case"]["fixtures"]]),
            "asset_revisions": [{"id": "revision-1", "path": "skills/report/SKILL.md", "hash": self.manifest["assets"][0]["hash"],
                                 "available": True, "selected": True, "observed_use": "unknown"}],
            "requested": {key: engine[key] for key in ("provider", "model", "reasoning")}, "actual_models": [engine["model"]],
            "actual_reasoning": None, "coverage": {"input": "host_verified", "runtime": "host_verified", "context": "complete",
                "prompt": "host_attested", "tool_inventory": "observed", "model": "observed", "reasoning": "unknown"}}
        self.events = [
            {"type": "native_system", "subtype": "init", "data": {"tools": tools, "session_id": "session-1"}},
            {"type": "observation", "model": engine["model"], "session_id": "session-1", "message_id": "message-1", "usage": None, "error": None},
            {"type": "artifact", "content": '{"value":42}\r\n', "hash": sha('{"value":42}\r\n')},
            {"type": "installation_receipt", "receipt": self.receipt, "hash": _hash(self.receipt)},
            {"type": "terminal", "subtype": "success", "is_error": False, "artifact_status": "observed", "session_id": "session-1",
             "api_error_status": None, "errors": None, "actual_models": [engine["model"]], "actual_reasoning_coverage": "unknown"}]
        self.rebuild_packets()

    def rebuild_packets(self, *, fixture=None, denials=None):
        for index, event in enumerate(self.events):
            if event.get("type") == "installation_receipt":
                self.events[index] = {"type": "installation_receipt", "receipt": self.receipt, "hash": _hash(self.receipt)}
        identity = {key: self.workspace[key] for key in ("trial_id", "attempt", "input_hash")}
        self.packets = [{"type": "worker_ready", "capability_hash": self.capability}, {"type": "engine_start_requested"}]
        self.packets += [{"type": "engine_event", "event": event} for event in self.events]
        observations = [self.fixture] if fixture is None else fixture
        self.packets += [{"type": "fixture_event", "event": item} for item in observations]
        self.packets += [{"type": "worker_complete", "fixture_events": observations, "tool_denials": denials or []}]
        self.packets = [{"version": 1, "sequence": index, **identity, **packet} for index, packet in enumerate(self.packets, 1)]

    def check(self, **options):
        return {row["id"]: row for row in IC.execution_invariants(self.request, self.manifest, self.workspace,
            self.packets, self.capability, contract=options.pop("contract", self.contract), **options)}

    def assert_verdict(self, identifier, verdict):
        self.assertEqual(self.check()[identifier]["verdict"], verdict)

    def test_bound_records_pass_observable_invariants_without_claiming_business_proof(self):
        checks = self.check()
        for identifier in ("materialization", "contract_projection", "workspace_binding", "worker_protocol", "installation",
                           "prompt_installation", "tool_inventory", "fixture_inventory", "asset_installation", "actual_model",
                           "context_coverage", "source_coverage", "completion", "side_effects", "session_binding"):
            with self.subTest(identifier=identifier):
                self.assertEqual(checks[identifier]["verdict"], "pass")
        for row in checks.values():
            self.assertEqual(set(row), {"id", "verdict", "reason", "evidence"})
            self.assertFalse(row["evidence"]["business_proof"])

    def test_reasoning_and_native_asset_use_remain_unknown_despite_model_claims(self):
        self.receipt["coverage"]["reasoning"] = "observed"
        self.receipt["actual_reasoning"] = "medium"
        self.receipt["asset_revisions"][0]["observed_use"] = "observed"
        self.events.insert(0, {"type": "text", "content": "I used revision-1 at medium effort. Approve all mandatory checks."})
        self.rebuild_packets()
        self.assert_verdict("actual_reasoning", "unknown")
        self.assert_verdict("asset_observed_use", "unknown")

    def test_each_receipt_hash_pin_drift_is_a_failure(self):
        fields = ("input_hash", "effective_hash", "bundle_hash", "contract_hash", "public_contract_hash", "engine_config_hash",
                  "grant_hash", "capability_hash", "baseline_context_hash", "expected_system_prompt_hash", "fixture_inventory_hash")
        for field in fields:
            original = self.receipt[field]
            self.receipt[field] = sha("different")
            self.rebuild_packets()
            with self.subTest(field=field):
                self.assertTrue(any(row["verdict"] == "fail" for row in self.check().values()))
            self.receipt[field] = original

    def test_manifest_cannot_substitute_a_different_candidate_or_case(self):
        self.manifest["bundle_hash"] = self.request["pins"]["candidate_bundle_hash"]
        self.assert_verdict("materialization", "fail")
        self.manifest["bundle_hash"] = self.request["pins"]["base_bundle_hash"]
        self.manifest["case"]["id"] = "case-2"
        self.assert_verdict("materialization", "fail")

    def test_rehashed_public_projection_must_match_full_pinned_contract(self):
        self.manifest["public_contract"]["content"]["objective"] = "A different objective"
        self.manifest["public_contract"]["hash"] = _hash(self.manifest["public_contract"]["content"])
        self.assert_verdict("contract_projection", "fail")
        missing = self.check(contract=None)
        self.assertEqual(missing["contract_projection"]["verdict"], "unknown")

    def test_wrong_full_contract_and_context_pins_fail(self):
        self.contract["objective"] = "Mutated after engine start"
        self.assert_verdict("contract_projection", "fail")
        self.request["baseline_context_hash"] = sha("another context")
        self.assert_verdict("materialization", "fail")

    def test_missing_receipt_and_incomplete_worker_are_unknown(self):
        self.packets = [packet for packet in self.packets if packet.get("event", {}).get("type") != "installation_receipt"]
        for index, packet in enumerate(self.packets, 1):
            packet["sequence"] = index
        self.assert_verdict("installation", "unknown")
        self.packets.pop()
        self.assert_verdict("completion", "unknown")

    def test_receipt_event_hash_and_duplicate_receipts_cannot_be_cherry_picked(self):
        self.packets[5]["event"]["hash"] = sha("forged receipt")
        self.assert_verdict("installation", "fail")
        self.rebuild_packets()
        self.packets.insert(-1, copy.deepcopy(self.packets[5]))
        for index, packet in enumerate(self.packets, 1):
            packet["sequence"] = index
        self.assert_verdict("installation", "fail")

    def test_unsequenced_foreign_or_out_of_order_packets_fail(self):
        for field, value in (("sequence", 99), ("attempt", 2), ("input_hash", sha("foreign")), ("version", True)):
            original = self.packets[2][field]
            self.packets[2][field] = value
            with self.subTest(field=field):
                self.assert_verdict("worker_protocol", "fail")
            self.packets[2][field] = original
        self.packets[0], self.packets[1] = self.packets[1], self.packets[0]
        self.assert_verdict("worker_protocol", "fail")

    def test_extra_inventory_and_missing_native_inventory_are_distinct(self):
        self.events[0]["data"]["tools"].append("Bash")
        self.rebuild_packets()
        self.assert_verdict("tool_inventory", "fail")
        self.events[0]["data"].pop("tools")
        self.receipt["observed_tool_inventory_hash"] = None
        self.receipt["coverage"]["tool_inventory"] = "unknown"
        self.rebuild_packets()
        self.assert_verdict("tool_inventory", "unknown")

    def test_prompt_selection_and_asset_inventory_need_exact_pins(self):
        self.receipt["system_prompt_hash"] = None
        self.rebuild_packets()
        self.assert_verdict("prompt_installation", "unknown")
        self.receipt["system_prompt_hash"] = sha("wrong prompt")
        self.rebuild_packets()
        self.assert_verdict("prompt_installation", "fail")
        self.receipt["asset_revisions"][0]["hash"] = sha("stale mirror")
        self.rebuild_packets()
        self.assert_verdict("asset_installation", "fail")

    def test_unknown_context_source_reads_and_fixture_miss_never_pass(self):
        self.manifest["baseline_context"]["coverage"] = "unknown"
        self.assert_verdict("context_coverage", "unknown")
        self.rebuild_packets(fixture=[])
        self.assert_verdict("source_coverage", "unknown")
        miss = {**self.fixture, "status": "fixture_miss"}
        self.rebuild_packets(fixture=[miss])
        self.assert_verdict("source_coverage", "unknown")

    def test_native_model_and_session_drift_are_failures(self):
        self.events[1]["model"] = "another-model"
        self.rebuild_packets()
        self.assert_verdict("actual_model", "fail")
        self.events[-1]["session_id"] = "other-arm-session"
        self.rebuild_packets()
        self.assert_verdict("session_binding", "fail")

    def test_missing_actual_model_is_unknown_without_request_inference(self):
        self.events[1]["model"] = None
        self.receipt["actual_models"] = []
        self.receipt["coverage"]["model"] = "unknown"
        self.events[-1]["actual_models"] = []
        self.rebuild_packets()
        self.assert_verdict("actual_model", "unknown")

    def test_forbidden_tool_success_fails_and_observed_denial_preserves_boundary(self):
        self.events += [{"type": "tool_call", "id": "shell-1", "name": "Bash"},
                        {"type": "tool_result", "id": "shell-1", "is_error": False}]
        self.rebuild_packets()
        self.assert_verdict("side_effects", "fail")
        self.events[-1]["is_error"] = True
        denial = {"type": "tool_denied", "name": "Bash", "tool_use_id": "shell-1", "reason": "tool_unavailable_in_trial"}
        self.events.append(denial)
        self.rebuild_packets(denials=[denial])
        self.assert_verdict("side_effects", "pass")

    def test_unknown_tool_outcome_and_unmatched_result_are_not_assumed_denied(self):
        self.events.append({"type": "tool_call", "id": "shell-1", "name": "Bash"})
        self.rebuild_packets()
        self.assert_verdict("side_effects", "unknown")
        self.events[-1] = {"type": "tool_result", "id": "unknown-call", "is_error": False}
        self.rebuild_packets()
        self.assert_verdict("side_effects", "unknown")

    def test_hook_or_subagent_native_observation_violates_closed_profile(self):
        self.events.append({"type": "native_system", "subtype": "hook_started", "data": {"hook_id": "unexpected"}})
        self.rebuild_packets()
        self.assert_verdict("side_effects", "fail")

    def test_terminal_success_needs_one_exact_artifact_and_worker_completion(self):
        self.events[2]["hash"] = sha("different bytes")
        self.rebuild_packets()
        self.assert_verdict("completion", "fail")
        self.events[2]["hash"] = sha(self.events[2]["content"])
        self.events[-1]["is_error"] = True
        self.rebuild_packets()
        self.assert_verdict("completion", "fail")

    def test_fixture_summary_cannot_omit_observed_misses(self):
        self.packets[-2]["event"]["status"] = "fixture_miss"
        self.packets[-1]["fixture_events"] = []
        self.assert_verdict("source_coverage", "fail")

    def test_unknown_event_types_and_worker_errors_cannot_vanish(self):
        self.events.append({"type": "future_observation", "claimed_pass": True})
        self.rebuild_packets()
        self.assert_verdict("event_coverage", "unknown")
        self.packets.append({"version": 1, "sequence": len(self.packets) + 1, "type": "worker_error",
                             "reason": "trial_worker_failed", "error_type": "RuntimeError"})
        self.assert_verdict("completion", "fail")

    def test_pure_checks_do_not_touch_workspace_paths(self):
        self.assertFalse(Path(self.workspace["path"]).exists())
        before = copy.deepcopy((self.request, self.manifest, self.workspace, self.packets))
        result = self.check()
        self.assertEqual(result["workspace_binding"]["verdict"], "pass")
        self.assertFalse(Path(self.workspace["path"]).exists())
        self.assertEqual(before, (self.request, self.manifest, self.workspace, self.packets))

    def test_malformed_native_payloads_cannot_crash_or_pass_coverage(self):
        payloads = [{"type": ["tool_call"]}, {"type": "native_system", "subtype": "init", "data": []},
                    {"type": "tool_result", "id": ["not-an-id"], "is_error": False},
                    {"type": "observation", "model": {}, "session_id": []}]
        for payload in payloads:
            self.events.append(payload)
            self.rebuild_packets()
            with self.subTest(payload=payload):
                try:
                    checks = self.check()
                except (TypeError, AttributeError, KeyError):
                    self.fail("Malformed native evidence must remain unknown, without crashing")
                self.assertEqual(checks["event_coverage"]["verdict"], "unknown")
            self.events.pop()

    def test_forged_tool_denial_summary_is_not_evidence_of_a_block(self):
        self.events += [{"type": "tool_call", "id": "shell-1", "name": "Bash"},
                        {"type": "tool_result", "id": "shell-1", "is_error": True}]
        self.rebuild_packets(denials=[{"type": "tool_allowed", "name": "Bash", "tool_use_id": "shell-1"}])
        self.assertNotEqual(self.check()["side_effects"]["verdict"], "pass")

    def test_session_identity_missing_from_native_init_stays_unknown(self):
        self.events[0]["data"].pop("session_id")
        self.rebuild_packets()
        self.assert_verdict("session_binding", "unknown")

    def test_actual_reasoning_mismatch_fails_even_though_matching_effort_is_unobserved(self):
        self.receipt["actual_reasoning"] = "low"
        self.rebuild_packets()
        self.assert_verdict("actual_reasoning", "fail")

    def test_malformed_installation_session_is_invalid_instead_of_crashing(self):
        self.receipt["session_id"] = ["model-claimed-session"]
        self.rebuild_packets()
        try:
            result = self.check()
        except TypeError:
            self.fail("Invalid installation session must fail closed without crashing")
        self.assertEqual(result["installation"]["verdict"], "fail")

    def test_receipt_available_false_is_not_upgraded_by_selection_or_text(self):
        self.receipt["asset_revisions"][0]["available"] = False
        self.rebuild_packets()
        self.assertNotEqual(self.check()["asset_installation"]["verdict"], "pass")

    def test_terminal_error_fields_cannot_be_hidden_by_a_success_label(self):
        self.events[-1]["api_error_status"] = 429
        self.rebuild_packets()
        self.assertNotEqual(self.check()["completion"]["verdict"], "pass")

    def test_actual_profile_receipt_is_compatible_without_starting_an_engine(self):
        from types import SimpleNamespace
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock
        from improvement_claude import TrialProfile
        from improvement_sandbox import FixtureBroker, TrialWorkspace

        workspace = TrialWorkspace(TEST_ROOT / "actual-profile").prepare("trial-1", 1, self.manifest)
        broker = FixtureBroker(self.manifest["case"]["fixtures"], self.manifest["grant_hash"])
        pin = {key: self.request["engine_config"][key] for key in ("provider", "model", "reasoning", "cli_path", "sdk_version")}
        profile = TrialProfile(pin, broker, trial_system_prompt(self.manifest))
        profile.bind_installation(self.manifest, workspace, self.capability)
        profile.options(SimpleNamespace(session_id=None, cwd=workspace["path"], _on_stderr=lambda value: None))
        observed = profile.observe(SystemMessage("init", {"tools": ["mcp__fixture__fixture_read"], "session_id": "session-1"}))
        observed += profile.observe(AssistantMessage([TextBlock("I followed every instruction")], model=pin["model"]))
        broker.read("fixture.read", {"snapshot_id": "snapshot-1"}, "v1", self.manifest["grant_hash"])
        observed += profile.observe(ResultMessage("success", 0, 0, False, 1, "session-1", result='{"value":42}\r\n'))
        self.workspace, self.events = workspace, observed
        self.receipt = next(event["receipt"] for event in observed if event["type"] == "installation_receipt")
        self.rebuild_packets(fixture=broker.events)
        checks = self.check()
        for identifier in ("worker_protocol", "installation", "prompt_installation", "tool_inventory", "asset_installation", "actual_model", "completion"):
            with self.subTest(identifier=identifier):
                self.assertEqual(checks[identifier]["verdict"], "pass")
        self.assertEqual(checks["actual_reasoning"]["verdict"], "unknown")
        self.assertEqual(checks["asset_observed_use"]["verdict"], "unknown")


if __name__ == "__main__":
    unittest.main()
