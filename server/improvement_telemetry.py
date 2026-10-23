"""Host-side native telemetry into the existing usage pipeline; never a model or billing ledger."""
from __future__ import annotations

import math

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
        if (not metadata or not metadata.get("experiment_id") or provider != "anthropic-cli"
                or not isinstance(model, str) or not model
                or not call or call.get("engine_at") is None or call.get("status") == "refunded"
                or call.get("purpose") != "improve_probe" or call.get("brain_id") != brain_id
                or call.get("context_ref") != "improvement:" + metadata["experiment_id"]
                or call.get("mirror_ref") != "improvement_trial:" + metadata["trial_id"]
                or call.get("attempt") != metadata["attempt"]
                or call.get("engine") != provider or call.get("model") != model):
            raise ValueError("trial_usage_call_unbound")
        self._execution = metadata
        self._provider, self._model = provider, model
        self._requests = set()
        self._identity_errors = 0
        self._summary_complete = False
        self._persisted_errors = 0
        events, _ = usage_index.improvement_events(metadata["experiment_id"], metadata["trial_id"])
        for event in events:
            if (event.get("execution", {}).get("attempt") != metadata["attempt"]
                    or event.get("source_id") != "improvement-native-v1"):
                continue
            if event.get("provider") != provider or event.get("model") != model:
                raise ValueError("trial_usage_configuration_drift")
            self._session(event.get("execution", {}).get("cli_session_id"))
            if event.get("event_kind") == "cli_detail":
                self._requests.add(event.get("request_id") or event["event_id"])
                self._identity_errors += int(not event.get("request_id"))
            elif event.get("event_kind") == "cli_fallback":
                self._summary_complete = bool(event.get("tokens_known"))
            self._persisted_errors = max(self._persisted_errors, event.get("telemetry_errors") or 0)

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
        errors = usage_store.improvement_telemetry_errors(ex["experiment_id"], ex["trial_id"], ex["execution_id"])
        usage_store.record(self._provider, self._model, None, None, execution=ex,
            source_id="improvement-native-v1", event_id="native-execution-terminal", event_kind="execution",
            status=status, expected_requests=len(self._requests),
            telemetry_errors=max(errors, self._persisted_errors),
            usage_complete=self._summary_complete and not self._identity_errors)
