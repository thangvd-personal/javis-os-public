"""Host-side native telemetry into the existing usage pipeline; never a model or billing ledger."""
from __future__ import annotations

import math
import json
import sqlite3
from contextlib import closing

import turn_context
import usage_store
import usage_index


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _tokens(value):
    raw = value if isinstance(value, dict) else {}
    uncached = _count(raw.get("input_tokens"))
    read = _count(raw.get("cache_read_input_tokens"))
    create = _count(raw.get("cache_creation_input_tokens"))
    tin = uncached + read + create if all(v is not None for v in (uncached, read, create)) else None
    return tin, _count(raw.get("output_tokens")), read, create


class NativeUsageRecorder:
    """Consume host-validated adapter events after a genuine durable engine-start marker.

    TrialRunner owns raw trace persistence, call settlement and recovery. This component
    records usage only; no worker/model event can choose its execution identity or engine.
    """

    def __init__(self, ledger, call_id, execution, *, brain_id, provider, model):
        metadata = turn_context.execution_metadata(execution)
        call = ledger.get(call_id)
        operation = bool(metadata and "operation_id" in metadata)
        purpose = metadata.get("purpose") if operation else "improve_probe"
        mirror = metadata.get("execution_id") if operation else "improvement_trial:" + (metadata or {}).get("trial_id", "")
        if (not metadata or not metadata.get("experiment_id") or provider != "anthropic-cli"
                or not isinstance(model, str) or not model
                or not call or call.get("engine_at") is None or call.get("status") == "refunded"
                or call.get("purpose") != purpose or call.get("brain_id") != brain_id
                or call.get("context_ref") != "improvement:" + metadata["experiment_id"]
                or call.get("mirror_ref") != mirror
                or call.get("attempt") != metadata["attempt"]
                or call.get("engine") != provider or call.get("model") != model
                or (operation and (call.get("reason_ref") != mirror + ":attempt:" + str(metadata["attempt"])
                                   or not self._operation_bound(ledger, call_id, metadata, brain_id, call["engine_at"])))):
            raise ValueError("operation_usage_call_unbound" if operation else "trial_usage_call_unbound")
        self._execution = metadata
        self._provider, self._model = provider, model
        self._requests = set()
        self._identity_errors = 0
        self._summary_complete = False
        self._persisted_errors = 0
        if operation:
            events, _ = usage_index.improvement_events(metadata["experiment_id"], operation_id=metadata["operation_id"])
        else:
            events, _ = usage_index.improvement_events(metadata["experiment_id"], metadata["trial_id"])
        for event in events:
            if (event.get("execution", {}).get("attempt") != metadata["attempt"]
                    or event.get("source_id") != "improvement-native-v1"
                    or (operation and event.get("execution", {}).get("execution_id") != metadata["execution_id"])):
                continue
            if operation:
                prior_identity = turn_context.execution_metadata(event.get("execution"))
                if prior_identity is None or any(prior_identity.get(key) != metadata[key] for key in
                        ("experiment_id", "operation_id", "role", "purpose", "learning_origin")):
                    raise ValueError("operation_usage_identity_drift")
            if event.get("provider") != provider or event.get("model") != model:
                raise ValueError("trial_usage_configuration_drift")
            self._session(event.get("execution", {}).get("cli_session_id"))
            if event.get("event_kind") == "cli_detail":
                self._requests.add(event.get("request_id") or event["event_id"])
                self._identity_errors += int(not event.get("request_id"))
            elif event.get("event_kind") == "cli_fallback":
                self._summary_complete = bool(event.get("tokens_known"))
            self._persisted_errors = max(self._persisted_errors, event.get("telemetry_errors") or 0)

    @staticmethod
    def _operation_bound(ledger, call_id, metadata, brain, marker):
        """Historical call ownership only: revocation must not erase late actual usage."""
        from agent_ledger import CallLedger
        from improvement_operation_protocol import operation_request
        from improvement_store import _hash
        if type(ledger) is not CallLedger or type(call_id) is not int or call_id < 1:
            return False
        try:
            with closing(ledger.store._conn()) as connection:
                row = connection.execute("SELECT o.*,a.engine_at AS attempt_engine_at FROM improvement_operations o "
                    "JOIN improvement_operation_attempts a ON a.operation_id=o.id AND a.brain_id=o.brain_id "
                    "WHERE o.id=? AND o.brain_id=? AND a.attempt=? AND a.call_id=?",
                    (metadata["operation_id"], brain, metadata["attempt"], call_id)).fetchone()
            if row is None or row["attempt_engine_at"] != marker:
                return False
            request = operation_request(json.loads(row["request_json"]))
            return (row["experiment_id"] == metadata["experiment_id"] and row["role"] == metadata["role"]
                    and row["purpose"] == metadata["purpose"] and _hash(request) == row["request_hash"]
                    and request["input_hash"] == row["input_blob_hash"]
                    and all(request[key] == row[key] for key in
                            ("experiment_id", "context_id", "kind", "role", "purpose", "candidate_id", "parent_id", "input_hash")))
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return False

    def _session(self, value):
        if value is None:
            return
        existing = self._execution.get("cli_session_id")
        if existing and value != existing:
            raise ValueError("trial_native_session_mismatch")
        proposed = {**self._execution, "cli_session_id": value, "cli_session_new": True}
        if turn_context.execution_metadata(proposed) is None:
            raise ValueError("invalid_trial_native_session")
        self._execution = proposed

    def observe(self, event, *, sequence):
        if not isinstance(event, dict) or type(sequence) is not int or sequence < 1:
            raise ValueError("invalid_trial_usage_event")
        kind = event.get("type")
        if kind == "native_system" and event.get("subtype") == "init":
            self._session((event.get("data") or {}).get("session_id"))
        elif kind in ("observation", "usage"):
            self._session(event.get("session_id"))
            tin, tout, read, create = _tokens(event.get("usage"))
            request_id = None
            if kind == "observation":
                request_id = event.get("message_id")
                if not isinstance(request_id, str) or not 1 <= len(request_id) <= 512:
                    request_id = None
                    self._identity_errors += 1
                event_id = "request:" + request_id if request_id else "unidentified-packet:" + str(sequence)
                self._requests.add(request_id or event_id)
                cost = None
            else:
                event_id = "native-summary"
                raw_cost = event.get("cost_usd")
                cost = raw_cost if type(raw_cost) in (int, float) and math.isfinite(raw_cost) and raw_cost >= 0 else None
                self._summary_complete = tin is not None and tout is not None
            usage_store.record(self._provider, self._model, tin, tout, cost=cost,
                execution=self._execution, source_id="improvement-native-v1", event_id=event_id,
                request_id=request_id, event_kind="cli_detail" if kind == "observation" else "cli_fallback",
                cache_read=read, cache_create=create)

    def finish(self, status):
        if status not in {"completed", "failed", "unknown", "interrupted", "cancelled", "quota_stop"}:
            raise ValueError("invalid_trial_usage_status")
        ex = self._execution
        if "operation_id" in ex:
            errors = usage_store.operation_telemetry_errors(ex["experiment_id"], ex["operation_id"], ex["execution_id"])
        else:
            errors = usage_store.improvement_telemetry_errors(ex["experiment_id"], ex["trial_id"], ex["execution_id"])
        usage_store.record(self._provider, self._model, None, None, execution=ex,
            source_id="improvement-native-v1", event_id="native-execution-terminal", event_kind="execution",
            status=status, expected_requests=len(self._requests),
            telemetry_errors=max(errors, self._persisted_errors),
            usage_complete=self._summary_complete and not self._identity_errors)
