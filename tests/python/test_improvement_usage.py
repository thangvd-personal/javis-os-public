"""Improvement attribution uses the existing event/index/call pipeline, never synthetic Team bills."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import importlib.util
import json
import sqlite3
import os
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="improvement-usage-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources", "JAVIS_CLAUDE_PROJECTS_DIR": "empty-claude",
                  "JAVIS_CODEX_SESSIONS_DIR": "empty-codex"}.items():
    os.environ[key] = str(TEST_ROOT / name)

import turn_context as TC
import usage_store as US
import usage_index as UI
import resonance_store as RS
import quota_scheduler as QS
from agent_ledger import CallLedger

if importlib.util.find_spec("improvement_usage"):
    import improvement_usage as IU
else:
    IU = None

if importlib.util.find_spec("improvement_telemetry"):
    import improvement_telemetry as IT
else:
    IT = None


def execution(trial="trial-1", **changes):
    return {"experiment_id": "experiment-1", "trial_id": trial, "case_id": "case-1", "variant": "baseline",
            "repeat": 1, "attempt": 1, "execution_id": "improvement_trial:" + trial, "role": "solver",
            "purpose": "improve_probe", "learning_origin": "trial", **changes}


class ExecutionMetadataTests(unittest.TestCase):
    def test_improvement_context_has_no_team_and_drops_prompt_or_credential_fields(self):
        metadata = execution()
        self.assertEqual(TC.execution_metadata({**metadata, "prompt": "private", "api_key": "private"}), metadata)
        self.assertNotIn("team_id", metadata)
        token = TC.bind(TC.make("improvement", execution=metadata))
        try:
            async def read():
                return await asyncio.to_thread(TC.current)
            got = asyncio.run(read())
            self.assertEqual(got["execution"], metadata)
            got["execution"]["trial_id"] = "caller-mutation"
            self.assertEqual(TC.current()["execution"], metadata)
        finally:
            TC.reset(token)

    def test_incomplete_or_malformed_improvement_identity_is_rejected(self):
        for changes in ({"attempt": True}, {"repeat": 0}, {"learning_origin": "production"},
                        {"trial_id": "../outside"}, {"execution_id": "unrelated-execution"},
                        {"purpose": "reply"}, {"team_id": "invented-team"}, {"case_id": None}):
            with self.subTest(changes=changes):
                self.assertIsNone(TC.execution_metadata(execution(**changes)))


class ImprovementUsageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IU, "Improvement views are not bound to the existing usage pipeline")
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_ROOT))
        self.patches = [patch.object(US, "_PATH", self.root / "usage.json"),
                        patch.object(US, "_EVENTS_PATH", self.root / "events.jsonl"),
                        patch.object(US, "_TELEMETRY_ERRORS", {}),
                        patch.object(UI, "_EVENTS_PATH", self.root / "events.jsonl"),
                        patch.object(UI, "DB_PATH", self.root / "usage-index.db"),
                        patch.object(UI, "_claude_dir", return_value=str(self.root / "raw-claude")),
                        patch.object(UI, "_codex_dir", return_value=str(self.root / "empty-codex"))]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        QS.reset()
        self.addCleanup(QS.reset)

    def record(self, tin=100, tout=15, **changes):
        US.record("anthropic-cli", "unpriced-selected-model", tin, tout, cost=changes.pop("cost", None),
                  execution=changes.pop("execution", execution()), source_id="improvement-native", **changes)

    def summary(self, trial="trial-1"):
        return IU.trial_usage("experiment-1", trial)

    def raw_request(self, sid, rid, tin, tout, read=0, create=0):
        raw = self.root / "raw-claude" / "project" / (sid + ".jsonl")
        raw.parent.mkdir(parents=True, exist_ok=True)
        with raw.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "assistant", "entrypoint": "sdk-cli", "isSidechain": False,
                "sessionId": sid, "cwd": str(raw.parent), "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": {"id": rid, "model": "unpriced-selected-model", "usage": {
                    "input_tokens": tin, "output_tokens": tout, "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": create}}}) + "\n")
        return raw

    def native_recorder(self, *, marked=True):
        self.assertIsNotNone(IT, "Raw native telemetry is not bound to the host ledger")
        ledger = CallLedger(RS.GoalStore(self.root / "resonance.sqlite3"))
        call = ledger.reserve(purpose="improve_probe", reason_ref="improvement_trial:trial-1", brain_id="brain-a",
            context_ref="improvement:experiment-1", mirror_ref="improvement_trial:trial-1", obligation=True)
        if marked:
            ledger.mark_engine(call, "anthropic-cli", "unpriced-selected-model")
        return IT.NativeUsageRecorder(ledger, call, execution(), brain_id="brain-a",
            provider="anthropic-cli", model="unpriced-selected-model"), ledger, call

    def test_native_usage_requires_a_real_matching_post_engine_ledger_reference(self):
        self.assertIsNotNone(IT, "Raw native telemetry is not bound to the host ledger")
        with self.assertRaisesRegex(ValueError, "trial_usage_call_unbound"):
            self.native_recorder(marked=False)
        self.assertFalse(US._EVENTS_PATH.exists())

    def test_native_usage_rejects_wrong_brain_configuration_and_attempt(self):
        _, ledger, call = self.native_recorder()
        for changes in ({"brain_id": "other-brain"}, {"provider": "openai"}, {"model": "other-model"},
                        {"execution": execution(attempt=2)}, {"execution": execution("other-trial")}):
            arguments = {"execution": execution(), "brain_id": "brain-a", "provider": "anthropic-cli",
                         "model": "unpriced-selected-model", **changes}
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "trial_usage_call_unbound"):
                IT.NativeUsageRecorder(ledger, call, **arguments)
        self.assertFalse(US._EVENTS_PATH.exists())

    def test_raw_sdk_profile_events_reach_shared_usage_without_summary_or_replay_overlap(self):
        from claude_agent_sdk import AssistantMessage, SystemMessage, ResultMessage
        from improvement_claude import TrialProfile
        from improvement_sandbox import FixtureBroker
        recorder, ledger, call = self.native_recorder()
        profile = TrialProfile({"provider": "anthropic-cli", "model": "unpriced-selected-model", "reasoning": "medium",
            "cli_path": "unused-test-path", "sdk_version": "0.2.116"}, FixtureBroker([], "e" * 64), "Pinned instructions")
        usage_a = {"input_tokens": 20, "output_tokens": 5, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10}
        usage_b = {"input_tokens": 30, "output_tokens": 5, "cache_read_input_tokens": 60, "cache_creation_input_tokens": 30}
        messages = [SystemMessage("init", {"session_id": "sdk-session", "tools": ["mcp__fixture__fixture_read"]}),
            AssistantMessage([], "unpriced-selected-model", message_id="request-a", usage=usage_a, session_id="sdk-session"),
            AssistantMessage([], "unpriced-selected-model", message_id="request-a", usage=usage_a, session_id="sdk-session"),
            AssistantMessage([], "unpriced-selected-model", message_id="request-b", usage=usage_b, session_id="sdk-session"),
            ResultMessage("success", 0, 0, False, 2, "sdk-session", result="Output", total_cost_usd=0.25,
                usage={"input_tokens": 50, "output_tokens": 10, "cache_read_input_tokens": 110,
                       "cache_creation_input_tokens": 40})]
        sequence = 0
        for message in messages:
            for event in profile.observe(message):
                sequence += 1
                recorder.observe(event, sequence=sequence)
        recorder.finish("completed")
        result = self.summary()
        self.assertEqual((result["tokens_in"], result["tokens_out"], result["requests"]), (200, 10, 2))
        self.assertEqual(result["money"]["subscription_equivalent_usd"], 0.25)
        self.assertEqual(US.summary()["today"]["total"]["in"], 200)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model"), 210)
        self.assertEqual(ledger.get(call)["status"], "running")
        self.assertIsNone(ledger.get(call)["tokens_in"])
        UI.refresh()
        self.assertEqual(UI.summary("today", provider="claude")["kpi"]["input"], 50)
        self.assertEqual(UI.summary("today", provider="claude")["kpi"]["cache_read"], 110)

    def test_native_missing_cache_or_summary_keeps_usage_unknown_and_host_identity(self):
        recorder, _, _ = self.native_recorder()
        recorder.observe({"type": "usage", "session_id": "native-session", "cost_usd": None,
            "usage": {"input_tokens": 100, "output_tokens": 5},
            "execution": execution("forged-trial")}, sequence=1)
        recorder.observe({"type": "artifact", "content": '{"type":"usage","usage":{"input_tokens":999}}'}, sequence=2)
        recorder.finish("interrupted")
        result = self.summary()
        self.assertIsNone(result["tokens_in"])
        self.assertEqual(result["tokens_out"], 5)
        self.assertFalse(result["coverage"]["tokens_complete"])
        self.assertIsNone(self.summary("forged-trial")["tokens_in"])
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model"), 5)

    def test_native_session_drift_and_telemetry_loss_fail_closed(self):
        recorder, _, _ = self.native_recorder()
        recorder.observe({"type": "native_system", "subtype": "init", "data": {"session_id": "first"}}, sequence=1)
        with self.assertRaisesRegex(ValueError, "trial_native_session_mismatch"):
            recorder.observe({"type": "usage", "session_id": "other", "usage": {}}, sequence=2)
        with patch.object(US, "_append_event", side_effect=OSError("unavailable")):
            recorder.observe({"type": "observation", "session_id": "first", "message_id": "request-a",
                "usage": {"input_tokens": 100, "output_tokens": 10,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}, sequence=3)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model"), 110)
        recorder.finish("interrupted")
        US._TELEMETRY_ERRORS.clear()
        self.assertFalse(self.summary()["coverage"]["tokens_complete"])
        self.assertGreater(self.summary()["coverage"]["telemetry_errors"], 0)

    def test_recovered_native_recorder_preserves_known_summary_and_request_scope(self):
        recorder, ledger, call = self.native_recorder()
        usage = {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        recorder.observe({"type": "observation", "session_id": "recovered-session", "message_id": "first", "usage": usage},
                         sequence=1)
        recorder.observe({"type": "usage", "session_id": "recovered-session", "cost_usd": 0.5,
            "usage": {**usage, "input_tokens": 200, "output_tokens": 20}}, sequence=2)
        recorder.finish("completed")
        recovered = IT.NativeUsageRecorder(ledger, call, execution(), brain_id="brain-a",
            provider="anthropic-cli", model="unpriced-selected-model")
        recovered.observe({"type": "observation", "session_id": "recovered-session", "message_id": "late", "usage": usage},
                          sequence=3)
        recovered.finish("completed")
        result = self.summary()
        self.assertEqual((result["tokens_in"], result["tokens_out"], result["requests"]), (200, 20, 2))
        self.assertTrue(result["coverage"]["tokens_complete"])

    def test_unidentified_native_request_and_malformed_counts_never_prove_complete_usage(self):
        recorder, _, _ = self.native_recorder()
        recorder.observe({"type": "observation", "session_id": "native-session", "message_id": None,
            "usage": {"input_tokens": True, "output_tokens": "5", "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": -1}}, sequence=1)
        recorder.finish("completed")
        result = self.summary()
        self.assertIsNone(result["tokens_in"])
        self.assertIsNone(result["tokens_out"])
        self.assertIsNone(result["requests"])
        self.assertFalse(result["coverage"]["tokens_complete"])

    def test_missing_native_request_identity_stays_partial_even_with_matching_summary(self):
        recorder, _, _ = self.native_recorder()
        usage = {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        recorder.observe({"type": "observation", "message_id": None, "usage": usage}, sequence=1)
        recorder.observe({"type": "usage", "usage": usage, "cost_usd": 0.25}, sequence=2)
        recorder.finish("completed")
        self.assertIsNone(self.summary()["requests"])
        self.assertFalse(self.summary()["coverage"]["tokens_complete"])
        UI.refresh()
        self.assertEqual(UI.summary("today")["coverage"]["improvement"]["status"], "partial")

    def test_late_native_summary_can_resolve_missing_summary_without_erasing_usage(self):
        recorder, ledger, call = self.native_recorder()
        usage = {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        recorder.observe({"type": "observation", "message_id": "request-a", "usage": usage}, sequence=1)
        recorder.finish("interrupted")
        self.assertFalse(self.summary()["coverage"]["tokens_complete"])
        recovered = IT.NativeUsageRecorder(ledger, call, execution(), brain_id="brain-a",
            provider="anthropic-cli", model="unpriced-selected-model")
        recovered.observe({"type": "usage", "usage": usage, "cost_usd": 0.25}, sequence=2)
        recovered.finish("completed")
        self.assertEqual(self.summary()["requests"], 1)
        self.assertTrue(self.summary()["coverage"]["tokens_complete"])
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model"), 110)

    def test_summary_and_late_requests_count_once_and_keep_missing_money_unknown(self):
        self.record(300, 30, event_kind="cli_fallback", event_id="summary")
        self.assertIsNone(self.summary()["requests"])
        for request_id, tin in (("msg-1", 100), ("msg-2", 200), ("msg-1", 100)):
            self.record(tin, 15, event_kind="cli_detail", event_id=request_id, request_id=request_id)
        result = self.summary()
        self.assertEqual((result["tokens_in"], result["tokens_out"], result["requests"]), (300, 30, 2))
        self.assertIsNone(result["money"]["reported_usd"])
        self.assertIsNone(result["money"]["subscription_equivalent_usd"])
        self.assertFalse(result["coverage"]["money_complete"])
        self.assertEqual(US.summary()["today"]["total"]["in"], 300)

    def test_sdk_and_late_raw_cli_request_identity_is_not_counted_twice(self):
        ex = execution(cli_session_id="native-session", cli_session_new=True)
        self.record(300, 30, execution=ex, event_kind="cli_fallback", event_id="summary")
        for request_id, tin in (("msg-1", 100), ("msg-2", 200)):
            self.record(tin, 15, execution=ex, event_kind="cli_detail", event_id=request_id, request_id=request_id)
        connection = UI._connect()
        try:
            UI._insert_events(connection, "synthetic-native-raw", [{"provider": "claude", "source": "synthetic-native",
                "activity": "background", "model": "unpriced-selected-model", "project": "synthetic-trial",
                "day": US._today(), "ts": int(time.time()), "input": tin, "output": 15, "cache_read": 0,
                "cache_create": 0, "turns": 1, "request_id": request_id, "session_id": "native-session"}
                for request_id, tin in (("msg-1", 100), ("msg-2", 200))])
            connection.commit()
        finally:
            connection.close()
        result = self.summary()
        self.assertEqual((result["tokens_in"], result["requests"]), (300, 2))
        self.assertTrue(result["coverage"]["requests_complete"])

    def test_unknown_usage_and_observed_zero_remain_distinct(self):
        self.record(None, None, event_kind="execution", event_id="interrupted", status="interrupted")
        self.assertIsNone(self.summary()["tokens_in"])
        self.assertEqual(self.summary()["coverage"]["unobserved_attempts"], 1)
        self.record(0, 0, cost=0.0, execution=execution("zero"), event_kind="cli_fallback", event_id="observed-zero")
        self.assertEqual(self.summary("zero")["tokens_in"], 0)
        self.assertEqual(self.summary("zero")["money"]["subscription_equivalent_usd"], 0.0)

    def test_missing_summary_still_deduplicates_late_raw_requests(self):
        ex = execution(cli_session_id="interrupted-session", cli_session_new=True)
        self.record(100, 10, execution=ex, event_kind="cli_detail", event_id="sdk", request_id="msg-1")
        connection = UI._connect()
        try:
            UI._insert_events(connection, "synthetic-interrupted-raw", [{"provider": "claude", "source": "synthetic",
                "activity": "background", "model": "unpriced-selected-model", "project": "synthetic-trial",
                "day": US._today(), "ts": int(time.time()), "input": 100, "output": 10, "cache_read": 0,
                "cache_create": 0, "turns": 1, "request_id": "msg-1", "session_id": "interrupted-session"},
                {"provider": "claude", "source": "synthetic", "activity": "background",
                 "model": "unpriced-selected-model", "project": "synthetic-trial", "day": US._today(),
                 "ts": int(time.time()), "input": 200, "output": 20, "cache_read": 0,
                 "cache_create": 0, "turns": 1, "request_id": "msg-2", "session_id": "interrupted-session"}])
            connection.commit()
        finally:
            connection.close()
        result = self.summary()
        self.assertEqual((result["tokens_in"], result["tokens_out"], result["requests"]), (300, 30, 2))

    def test_unknown_request_does_not_disappear_when_known_requests_match_summary(self):
        self.record(100, 10, event_kind="cli_fallback", event_id="summary")
        self.record(100, 10, event_kind="cli_detail", event_id="known", request_id="known")
        self.record(None, None, event_kind="cli_detail", event_id="unknown", request_id="unknown")
        result = self.summary()
        self.assertIsNone(result["requests"])
        self.assertFalse(result["coverage"]["tokens_complete"])

    def test_unknown_summary_preserves_known_request_usage(self):
        self.record(100, 10, event_kind="cli_detail", event_id="known", request_id="known")
        self.record(None, None, event_kind="cli_fallback", event_id="unknown-summary")
        result = self.summary()
        self.assertEqual((result["tokens_in"], result["tokens_out"]), (100, 10))
        self.assertFalse(result["coverage"]["tokens_complete"])
        UI.refresh()
        self.assertEqual(UI.summary("today", provider="claude")["kpi"]["input"], 100)
        self.assertEqual(UI.summary("today")["coverage"]["improvement"]["status"], "partial")

    def test_late_partial_and_full_raw_cache_details_replace_only_matching_trial_usage(self):
        UI.refresh()
        ex = execution(cli_session_id="cache-session", cli_session_new=True)
        self.record(300, 30, execution=ex, cache_read=60, cache_create=20,
                    event_kind="cli_fallback", event_id="summary")
        self.raw_request("unrelated-session", "chat", 50, 5)
        for raw in (None, ("msg-1", 60, 15, 30, 10), ("msg-2", 160, 15, 30, 10)):
            if raw:
                self.raw_request("cache-session", *raw)
            for _ in range(2):
                UI.refresh()
                kpi = UI.summary("today", provider="claude")["kpi"]
                self.assertEqual((kpi["input"], kpi["output"], kpi["cache_read"], kpi["cache_create"]),
                                 (270, 35, 60, 20))
                connection = UI._connect()
                try:
                    self.assertEqual(connection.execute("SELECT SUM(tokens) FROM file_hourly").fetchone()[0], 385)
                finally:
                    connection.close()

    def test_detail_only_usage_remains_in_global_index_before_and_after_raw_arrival(self):
        UI.refresh()
        ex = execution(cli_session_id="detail-session", cli_session_new=True)
        self.record(100, 10, execution=ex, event_kind="cli_detail", event_id="sdk-a", request_id="request-a")
        self.record(100, 10, execution=ex, event_kind="cli_detail", event_id="sdk-replay", request_id="request-a")
        for raw in (False, True):
            if raw:
                self.raw_request("detail-session", "request-a", 100, 10)
            UI.refresh()
            self.assertEqual(UI.summary("today", provider="claude")["kpi"]["input"], 100)

    def test_conflicting_cache_summary_is_partial_without_negative_projection(self):
        UI.refresh()
        ex = execution(cli_session_id="conflict-session", cli_session_new=True)
        self.record(100, 10, execution=ex, event_kind="cli_fallback", event_id="summary")
        self.raw_request("conflict-session", "request-a", 20, 10, 80, 0)
        UI.refresh()
        result = UI.summary("today", provider="claude")
        self.assertEqual(result["kpi"]["input"], 20)
        self.assertEqual(result["coverage"]["improvement"]["status"], "partial")

    def test_raw_session_collision_across_experiments_is_not_attributed_to_either_trial(self):
        ex = execution(cli_session_id="colliding-session", cli_session_new=True)
        self.record(300, 30, execution=ex, event_kind="cli_fallback", event_id="summary")
        self.record(300, 30, execution=execution("other-trial", experiment_id="other-experiment",
                    cli_session_id="colliding-session", cli_session_new=True),
                    event_kind="cli_fallback", event_id="summary")
        self.raw_request("colliding-session", "one-request", 300, 30)
        UI.refresh()
        result = self.summary()
        self.assertIsNone(result["requests"])
        self.assertFalse(result["coverage"]["requests_complete"])
        self.assertEqual(UI.summary("today")["coverage"]["improvement"]["status"], "partial")

    def test_detail_then_summary_replay_and_correction_update_legacy_and_tpm_once(self):
        with patch("time.time", return_value=1000):
            self.record(100, 10, event_kind="cli_detail", event_id="sdk", request_id="request-a")
            self.record(100, 10, event_kind="cli_detail", event_id="sdk-replay", request_id="request-a")
        self.assertEqual(US.summary()["today"]["total"]["in"], 100)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=1001), 110)
        with patch("time.time", return_value=1010):
            self.record(300, 30, event_kind="cli_fallback", event_id="summary")
            self.record(300, 30, event_kind="cli_fallback", event_id="summary")
        self.assertEqual(US.summary()["today"]["total"]["in"], 300)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=1011), 330)
        with patch("time.time", return_value=1020):
            self.record(200, 20, event_kind="cli_detail", event_id="second-sdk", request_id="request-b")
        self.assertEqual(US.summary()["today"]["total"]["in"], 300)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=1021), 330)

    def test_summary_correction_does_not_rejuvenate_old_tpm_usage(self):
        with patch("time.time", return_value=1000):
            self.record(300, 30, event_kind="cli_fallback", event_id="summary")
        with patch("time.time", return_value=1050):
            self.record(250, 25, event_kind="cli_fallback", event_id="summary")
        self.assertEqual(US.summary()["today"]["total"]["in"], 250)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=1051), 275)
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=1061), 0)

    def test_partial_summary_keeps_known_directions_in_shared_tpm_without_request_overlap(self):
        for tin, tout, expected in ((100, None, 100), (None, 20, 20)):
            with self.subTest(tin=tin, tout=tout):
                trial = "partial-input" if tin else "partial-output"
                with patch("time.time", return_value=1000):
                    self.record(tin, tout, execution=execution(trial), event_kind="cli_fallback", event_id="summary")
                self.assertFalse(self.summary(trial)["coverage"]["tokens_complete"])
        self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=1001), 120)

    def test_replayed_summary_and_request_corrections_keep_original_daily_and_hourly_bucket(self):
        UI.refresh()
        self.record(100, 10, event_kind="cli_fallback", event_id="summary")
        self.record(100, 10, execution=execution("request-only"), event_kind="cli_detail",
                    event_id="sdk-event-a", request_id="same-provider-request")
        events = [json.loads(line) for line in UI._EVENTS_PATH.read_text().splitlines()]
        first_ts = datetime(2026, 10, 20, 18, tzinfo=timezone.utc).timestamp()
        for event in events:
            event.update(day="2026-10-21", ts=first_ts)
        UI._EVENTS_PATH.write_text("".join(json.dumps(event) + "\n" for event in events))
        UI.refresh()
        with UI._EVENTS_PATH.open("a") as stream:
            for event in events:
                event.update(day="2026-10-22", ts=first_ts + 86400, **{"in": 120, "out": 12})
                if event["event_kind"] == "cli_detail":
                    event["event_id"] = "sdk-event-b"
                stream.write(json.dumps(event) + "\n")
        UI.refresh()
        self.assertEqual(UI.summary("today", today=datetime(2026, 10, 21).date())["kpi"]["input"], 240)
        self.assertEqual(UI.summary("today", today=datetime(2026, 10, 22).date())["kpi"]["input"], 0)
        connection = UI._connect()
        try:
            hours = connection.execute("SELECT DISTINCT substr(hour,1,10) FROM file_hourly").fetchall()
            self.assertEqual(hours, [("2026-10-21",)])
        finally:
            connection.close()

    def test_old_attempt_replay_survives_retention_and_does_not_refresh_tpm(self):
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recent = datetime(2026, 2, 3, tzinfo=timezone.utc)
        with patch.object(US.localefmt, "now", return_value=old), patch("time.time", return_value=old.timestamp()):
            self.record(100, 10, event_kind="cli_fallback", event_id="old-summary")
        with patch.object(US.localefmt, "now", return_value=recent), patch("time.time", return_value=recent.timestamp()):
            self.record(100, 10, execution=execution("recent-trial"), event_kind="cli_fallback", event_id="recent-summary")
            US.record("deepseek", "unknown", 5, 0, execution={"team_id": "team", "turn_id": "turn", "attempt": 1},
                      event_id="other-team")
            self.record(120, 12, event_kind="cli_fallback", event_id="old-summary")
            self.assertEqual(US.summary()["all_time"]["total"]["in"], 225)
            self.assertEqual(QS.used("anthropic-cli", "unpriced-selected-model", now=recent.timestamp()), 110)

    def test_old_projection_cursor_backfills_improvement_ids_without_cross_scope_rows(self):
        self.record(100, 15, event_kind="cli_detail", event_id="first-request")
        connection = sqlite3.connect(UI.DB_PATH)
        try:
            connection.execute("CREATE TABLE usage_observations(event_key TEXT PRIMARY KEY,team_id TEXT,task_id TEXT,payload TEXT)")
            connection.execute("CREATE TABLE files_seen(path TEXT PRIMARY KEY,size INTEGER,mtime REAL,offset INTEGER DEFAULT 0)")
            connection.execute("INSERT INTO files_seen VALUES(?,?,?,?)", ("__execution_events_v1__:" + str(UI._EVENTS_PATH),
                                                                       0, 0, UI._EVENTS_PATH.stat().st_size))
            connection.commit()
        finally:
            connection.close()
        self.record(999, 1, execution=execution("other", experiment_id="another-experiment"),
                    event_kind="cli_detail", event_id="other-request")
        events, details = UI.improvement_events("experiment-1", "trial-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["execution"]["trial_id"], "trial-1")
        self.assertNotIn("team_id", events[0]["execution"])
        self.assertEqual(UI.execution_events("invented-team"), ([], []))

    def test_telemetry_failure_is_scoped_and_persisted_error_is_not_zero_usage(self):
        US.note_telemetry_error(execution())
        self.assertEqual(US.improvement_telemetry_errors("experiment-1", "trial-1"), 1)
        self.assertEqual(US.telemetry_errors("experiment-1"), 0)
        self.record(None, None, event_kind="execution", event_id="lost-observation", telemetry_errors=1, status="interrupted")
        US._TELEMETRY_ERRORS.clear()
        result = self.summary()
        self.assertIsNone(result["tokens_in"])
        self.assertEqual(result["coverage"]["telemetry_errors"], 1)

    def test_trial_summary_remains_in_daily_and_hourly_usage_beside_other_chat_logs(self):
        UI.refresh()
        self.record(300, 30, execution=execution(cli_session_id="trial-session", cli_session_new=True),
                    event_kind="cli_fallback", event_id="trial-summary")
        raw = self.root / "raw-claude" / "project" / "chat.jsonl"
        raw.parent.mkdir(parents=True)
        raw.write_text(json.dumps({"type": "assistant", "entrypoint": "sdk-cli", "isSidechain": False,
            "sessionId": "unrelated-chat", "cwd": str(raw.parent), "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": {"id": "chat-request", "model": "unpriced-selected-model", "usage": {
                "input_tokens": 50, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n")
        UI.refresh()
        self.assertEqual(self.summary()["tokens_in"], 300)
        self.assertEqual(UI.summary("today", provider="claude")["kpi"]["input"], 350)
        connection = UI._connect()
        try:
            self.assertEqual(connection.execute("SELECT SUM(tokens) FROM file_hourly WHERE provider='claude'").fetchone()[0], 385)
        finally:
            connection.close()

    def test_shared_call_mirror_survives_generic_reconciliation_and_no_usage_is_unknown(self):
        store = RS.GoalStore(self.root / "resonance.sqlite3")
        ledger = CallLedger(store)
        call = ledger.reserve(purpose="improve_probe", reason_ref="improvement_trial:trial-1", brain_id="brain-a",
            context_ref="improvement:experiment-1", mirror_ref="improvement_trial:trial-1", obligation=True, now=100)
        self.assertIsNotNone(call)
        self.assertTrue(ledger.mark_engine(call, "anthropic-cli", "unpriced-selected-model", now=101))
        ledger.reconcile_stale(now=10000)
        self.assertEqual(ledger.get(call)["status"], "running")
        self.assertFalse(ledger.refund(call))
        result = self.summary()
        self.assertIsNone(result["tokens_in"])
        self.assertEqual(result["coverage"]["unobserved_attempts"], 1)
        self.assertFalse((self.root / "team.sqlite3").exists())
        with store._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goals").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
