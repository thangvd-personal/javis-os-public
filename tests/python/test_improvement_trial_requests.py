"""Bounded real ASGI request streams; no main startup, storage or engine execution."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BOOT = Path(tempfile.mkdtemp(prefix="trial-request-test-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                  "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(BOOT / name)

from starlette.requests import Request

if importlib.util.find_spec("improvement_trial_requests"):
    import improvement_trial_requests as R
else:
    R = None


def contract():
    return {"event_key": "request-contract", "objective": "Summarize an authorized fixture.",
        "output": {"kind": "text", "path": "answer.json", "media_type": "application/json"},
        "criteria": [
            {"id": "shape", "category": "mandatory", "critical": True, "description": "A value object.",
             "kind": "json_keys", "config": {"path": "answer.json", "keys": ["value"]}},
            {"id": "business", "category": "business", "critical": True, "description": "Owner confirmation.",
             "kind": "owner_confirm", "config": {"path": "answer.json"}}],
        "primary_metric": {"criterion_id": "business", "direction": "higher", "aggregation": "all_pass"},
        "unknown_conditions": ["missing_artifact"]}


def bodies():
    event = {"event_key": "request-event-01"}
    return {
        "create_context": {**event, "surface": {"kind": "dashboard", "session_id": "session:1", "language": "vi"},
            "target_ids": ["it_target"], "source_selections": [{"source_ref": "brain-file:reports/input.csv",
                "timezone": "UTC", "data_period": "2026-10", "cutoff": "2026-10-23T00:00:00+00:00", "schema": "text/csv"}]},
        "revoke_context": {**event, "expected_generation": 1},
        "capture_snapshot": {**event, "source_ref": "brain-file:reports/input.csv"},
        "create_contract": contract(),
        "create_case_set": {**event, "contract_id": "ic_contract", "seed": 47, "cases": [
            {"id": "case-1", "family_key": "family-1", "split": "qualification", "task": "Return JSON.",
             "snapshot_ids": ["is_snapshot"], "answer": {"expected": [1, True, None]}}]},
        "create_bundle": {**event, "revision_ids": ["ir_revision"]},
        "start_trial": {**event, "experiment_id": "experiment-1", "contract_id": "ic_contract",
            "case_set_id": "ics_cases", "case_id": "case-1", "base_bundle_id": "ib_base", "candidate_bundle_id": "ib_candidate",
            "variant": "baseline", "repeat": 1, "timeout_seconds": 300, "expected_context_generation": 1},
        "cancel_trial": {**event, "expected_generation": 1},
        "recover_trials": event,
        "record_owner_decision": {**event, "criterion_id": "business", "verdict": "unknown", "output_hash": "a" * 64,
            "contract_hash": "b" * 64, "expected_generation": 1},
        "reevaluate": {**event, "expected_generation": 1}}


class RequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(R, "Strict owner request parser is missing")

    def request(self, raw, *, fragments=None, content_length=None, receive_error=None):
        chunks = list(fragments if fragments is not None else [raw])
        self.received = 0
        async def receive():
            self.received += 1
            if receive_error is not None:
                raise receive_error
            chunk = chunks.pop(0) if chunks else b""
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        headers = [(b"content-type", b"application/json")]
        if content_length is not None:
            headers.append((b"content-length", str(content_length).encode()))
        return Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)

    async def parse(self, operation, value):
        return await R.parse_body(self.request(json.dumps(value, ensure_ascii=False).encode()), operation)

    async def invalid(self, operation, value):
        with self.assertRaises(R.TrialRequestError) as caught:
            await self.parse(operation, value)
        self.assertEqual(caught.exception.code, "invalid_trial_request")
        self.assertEqual(str(caught.exception), "invalid_trial_request")

    async def test_every_closed_operation_accepts_exact_valid_public_schema(self):
        for operation, value in bodies().items():
            with self.subTest(operation=operation):
                self.assertEqual(await self.parse(operation, value), value)

    async def test_fragmented_unicode_stream_and_cached_body_are_bounded(self):
        value = {"event_key": "nguyên-bản-123"}
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.assertEqual(await R.parse_body(self.request(raw, fragments=[raw[i:i+1] for i in range(len(raw))]), "recover_trials"), value)
        request = self.request(b"unused")
        request._body = b"x" * (R.MAX_BODY_BYTES + 1)
        with self.assertRaises(R.TrialRequestError) as caught:
            await R.parse_body(request, "recover_trials")
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(self.received, 0)

    async def test_huge_header_and_overflow_chunk_reject_before_json_or_further_receive(self):
        for request in (self.request(b"{}", content_length=R.MAX_BODY_BYTES + 1),
                        self.request(b"", fragments=[b"x" * (R.MAX_BODY_BYTES + 1), b"must-not-read"])):
            with self.subTest(), patch.object(R.json, "loads", side_effect=AssertionError("JSON must not parse overflow")):
                with self.assertRaises(R.TrialRequestError) as caught:
                    await R.parse_body(request, "recover_trials")
                self.assertEqual(caught.exception.code, "trial_request_too_large")
                self.assertEqual(caught.exception.status_code, 413)

    async def test_lying_small_header_does_not_bypass_stream_limit(self):
        with self.assertRaises(R.TrialRequestError) as caught:
            await R.parse_body(self.request(b"x" * (R.MAX_BODY_BYTES + 1), content_length=1), "recover_trials")
        self.assertEqual(caught.exception.status_code, 413)

    async def test_malformed_duplicate_nested_nonfinite_trailing_and_invalid_unicode_fail_closed(self):
        raw_values = [b"", b"[]", b"{} {}", b'{"event_key":"abcdefgh","event_key":"ijklmnop"}',
            b'{"event_key":"abcdefgh","extra":{"a":1,"a":2}}', b'{"event_key":NaN}', b'{"event_key":1e999}',
            b'{"event_key":"\xff"}', b'{"event_key":"\\ud800"}', b'{"event_key":' + b'9' * 5000 + b'}']
        for raw in raw_values:
            with self.subTest(raw_length=len(raw)), self.assertRaises(R.TrialRequestError) as caught:
                await R.parse_body(self.request(raw), "recover_trials")
            self.assertEqual(str(caught.exception), "invalid_trial_request")

    async def test_depth_is_bounded_before_json_decoder_and_strings_are_not_brackets(self):
        raw = b"[" * (R.MAX_JSON_DEPTH + 1) + b"0" + b"]" * (R.MAX_JSON_DEPTH + 1)
        with patch.object(R.json, "loads", side_effect=AssertionError("deep JSON parsed")):
            with self.assertRaises(R.TrialRequestError):
                await R.parse_body(self.request(raw), "recover_trials")
        value = {"event_key": "bracket-[]{}-\\\""}
        self.assertEqual(await self.parse("recover_trials", value), value)

    async def test_node_limit_rejects_wide_json(self):
        value = bodies()["create_case_set"]
        with patch.object(R, "MAX_JSON_NODES", 10), self.assertRaises(R.TrialRequestError):
            await self.parse("create_case_set", value)

    async def test_unknown_keys_at_every_authority_boundary_are_rejected(self):
        for operation, original in bodies().items():
            value = copy.deepcopy(original)
            value["actor"] = "owner"
            await self.invalid(operation, value)
        for nested, extra in (("surface", {"project_id": "foreign"}),
                               ("source_selections", {"permission_version": 1, "source_hash": "a" * 64})):
            value = bodies()["create_context"]
            (value[nested][0] if isinstance(value[nested], list) else value[nested]).update(extra)
            await self.invalid("create_context", value)

    async def test_boolean_integers_ranges_ids_and_duplicates_are_not_coerced(self):
        for operation, field in (("revoke_context", "expected_generation"), ("start_trial", "repeat"),
                ("start_trial", "timeout_seconds"), ("start_trial", "expected_context_generation"), ("create_case_set", "seed")):
            for invalid in (True, "1", 2 ** 64):
                value = bodies()[operation];value[field] = invalid
                await self.invalid(operation, value)
        for field in ("repeat", "timeout_seconds", "expected_context_generation"):
            value = bodies()["start_trial"];value[field] = 0
            await self.invalid("start_trial", value)
        value = bodies()["start_trial"];value["timeout_seconds"] = 301
        await self.invalid("start_trial", value)
        for operation, field in (("create_context", "target_ids"), ("create_bundle", "revision_ids")):
            value = bodies()[operation];value[field] *= 2
            await self.invalid(operation, value)

    async def test_source_metadata_and_portable_owner_file_scope_are_validated_without_reading_files(self):
        for change in ({"source_ref": "brain-file:../private.txt"}, {"source_ref": "brain-file:.env"},
                       {"source_ref": "https://live.invalid"}, {"timezone": "Unknown/Zone"},
                       {"cutoff": "2026-10-23T00:00:00"}, {"freshness_policy": "snapshot_version"}):
            value = bodies()["create_context"];value["source_selections"][0].update(change)
            await self.invalid("create_context", value)
        value = bodies()["create_context"];value["source_selections"] *= 2
        await self.invalid("create_context", value)

    async def test_cases_remain_private_json_and_cannot_supply_evaluator_refs(self):
        value = bodies()["create_case_set"]
        self.assertEqual((await self.parse("create_case_set", value))["cases"][0]["answer"], {"expected": [1, True, None]})
        for change in ({"evaluator_refs": ["forged-checker"]}, {"split": "test"}, {"snapshot_ids": ["is_x", "is_x"]}):
            value = bodies()["create_case_set"];value["cases"][0].update(change)
            await self.invalid("create_case_set", value)
        value = bodies()["create_case_set"];value["cases"].append({**value["cases"][0], "id": "case-2"})
        await self.invalid("create_case_set", value)

    async def test_answer_size_uses_actual_canonical_asset_limit(self):
        from improvement_blobs import MAX_ASSET_BYTES
        value = bodies()["create_case_set"];value["cases"][0]["answer"] = "a" * MAX_ASSET_BYTES
        await self.invalid("create_case_set", value)

    async def test_structural_checker_configs_are_closed_bounded_and_not_truncated(self):
        for kind, config in (("file_exists", {"path": "answer.json"}),
            ("file_contains", {"path": "answer.json", "min_chars": 1, "must_contain": ["value"]}),
            ("json_keys", {"path": "answer.json", "keys": ["value"]})):
            value = contract();value["criteria"][0].update(kind=kind, config=config)
            self.assertEqual(await self.parse("create_contract", value), value)
        for change in ({"kind": "file_contains", "config": {"path": "answer.json", "min_chars": True}},
                       {"kind": "json_keys", "config": {"path": "answer.json", "keys": ["x"] * 21}},
                       {"kind": "file_exists", "config": {"path": "../escape"}},
                       {"kind": "owner_confirm", "config": {"target": "file"}},
                       {"kind": "file_exists", "config": {"path": "answer.json", "command": "forbidden"}}):
            value = contract();value["criteria"][0].update(change)
            await self.invalid("create_contract", value)

    async def test_checker_pins_are_host_projected_and_callables_never_execute(self):
        from improvement_evaluation import EVALUATOR_PIN
        value = contract();value["criteria"][1].update(kind="host_checker", config={"checker_id": "fixture"})
        public = await self.parse("create_contract", value)
        check = lambda *a: self.fail("checker executed during parsing")
        registry = {"fixture": {"version": "1", "hash": "a" * 64, "check": check}}
        projected = R.project_contract(public, checker_registry=registry)
        self.assertEqual(projected["evaluator"], EVALUATOR_PIN)
        self.assertEqual(projected["output_hash_policy"], "raw_sha256")
        self.assertEqual(projected["criteria"][1]["config"], {"checker_id": "fixture", "version": "1", "hash": "a" * 64})
        self.assertNotIn("event_key", projected)
        for kind in ("host_checker", "command_ok"):
            value["criteria"][1].update(kind=kind, config={"checker_id": "fixture", "version": "forged", "hash": "b" * 64})
            await self.invalid("create_contract", value)
        with self.assertRaises(R.TrialRequestError) as caught:
            R.project_contract(public, checker_registry={})
        self.assertEqual(str(caught.exception), "trial_checker_unavailable")

    async def test_contract_host_fields_categories_and_primary_metric_cannot_be_forged(self):
        for change in ({"version": 1}, {"evaluator": {}}, {"output_hash_policy": "raw_sha256"},
                       {"primary_metric": {"criterion_id": "shape", "direction": "higher", "aggregation": "all_pass"}}):
            value = contract();value.update(change)
            await self.invalid("create_contract", value)

    async def test_component_metadata_failure_is_fixed_without_private_host_path(self):
        with patch("improvement_trial_file_sources._metadata", side_effect=OSError("PRIVATE /owner/source/timezone")):
            with self.assertRaises(R.TrialRequestError) as caught:
                await self.parse("create_context", bodies()["create_context"])
        self.assertEqual(str(caught.exception), "trial_request_unavailable")

    async def test_pure_projection_never_constructs_storage_adapters_or_dispatches_commands(self):
        import improvement_cases as IC
        import improvement_claude as native
        import resonance_store
        with patch.object(IC.CaseStore, "__init__", side_effect=AssertionError("storage created")), \
             patch.object(resonance_store.GoalStore, "__init__", side_effect=AssertionError("ledger created")), \
             patch.object(native, "ClaudeTrialAdapter", side_effect=AssertionError("adapter created")), \
             patch("config.read_settings", side_effect=AssertionError("decrypted settings read")), \
             patch("subprocess.run", side_effect=AssertionError("command dispatched")), \
             patch("subprocess.Popen", side_effect=AssertionError("process dispatched")):
            for operation, value in bodies().items():
                with self.subTest(operation=operation):
                    self.assertEqual(await self.parse(operation, value), value)

    async def test_count_boundaries_do_not_silently_truncate(self):
        for operation, field, limit in (("create_context", "target_ids", 20), ("create_bundle", "revision_ids", 100)):
            value = bodies()[operation];value[field] = [f"id-{i}" for i in range(limit)]
            self.assertEqual(len((await self.parse(operation, value))[field]), limit)
            value[field].append("id-over-limit")
            await self.invalid(operation, value)
        value = contract();value["criteria"] *= 7
        await self.invalid("create_contract", value)

    async def test_signed_seed_bounds_are_preserved_and_json_float_integers_rejected(self):
        for seed in (-2 ** 63, 2 ** 63 - 1):
            value = bodies()["create_case_set"];value["seed"] = seed
            self.assertEqual((await self.parse("create_case_set", value))["seed"], seed)
        value = bodies()["start_trial"];value["repeat"] = 1.0
        await self.invalid("start_trial", value)

    async def test_read_failures_are_fixed_and_cancellation_propagates(self):
        for error in (RuntimeError("PRIVATE /secret/path/body"),):
            with self.assertRaises(R.TrialRequestError) as caught:
                await R.parse_body(self.request(b"", receive_error=error), "recover_trials")
            self.assertEqual(str(caught.exception), "trial_request_unavailable")
        with self.assertRaises(asyncio.CancelledError):
            await R.parse_body(self.request(b"", receive_error=asyncio.CancelledError()), "recover_trials")

    async def test_unknown_operation_and_missing_event_key_fail_before_any_authority_action(self):
        request = self.request(b"{}")
        with self.assertRaises(R.TrialRequestError):
            await R.parse_body(request, "engine_start")
        self.assertEqual(self.received, 0)
        await self.invalid("recover_trials", {})
        for key in ("short", "x" * 161):
            await self.invalid("recover_trials", {"event_key": key})


if __name__ == "__main__":
    unittest.main()
