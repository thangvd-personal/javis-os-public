"""SQLite-backed Durable Audit Outbox with per-stream JCS Hash Chain (`P0-07`, `G0-021..023`)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from javisos.server.iam_contract.jcs import compute_jcs_sha256
from javisos.server.iam_contract.port import IamPdpAdapterPort
from javisos.server.iam_contract.schemas import validate_contract_payload


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DurableAuditOutbox:
    """Persists staged audit events to local SQLite before flushing to IAM/PDP Audit Sink."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        stream_id: str,
        producer: str = "MCP_GATEWAY_PEP",
    ) -> None:
        self.db_path = Path(db_path)
        self.stream_id = stream_id
        self.producer = producer
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_outbox (
                    sequence_no INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    stream_id TEXT NOT NULL,
                    prev_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    flushed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

    def append_event(
        self,
        *,
        event_type: str,
        execution_stage: str,
        execution_context: Mapping[str, Any],
        resource_id: str,
        operation: str,
        decision_effect: str,
        payload_hash: str = "NONE",
        reason_code: str | None = None,
        downstream_reference_id: str | None = None,
        sanitized_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sequence_no, event_hash FROM audit_outbox WHERE stream_id = ? ORDER BY sequence_no DESC LIMIT 1",
                (self.stream_id,),
            ).fetchone()
            next_seq = (int(row[0]) + 1) if row else 1
            prev_hash = str(row[1]) if row else "GENESIS"

            event_without_hash: dict[str, Any] = {
                "schema_version": "1.0.0",
                "event_id": f"evt_{uuid.uuid4().hex[:14]}",
                "stream_id": self.stream_id,
                "sequence_no": next_seq,
                "event_type": event_type,
                "execution_stage": execution_stage,
                "producer": self.producer,
                "execution_id": str(execution_context.get("execution_id") or "exec_unknown"),
                "correlation_id": str(execution_context.get("correlation_id") or "corr_unknown"),
                "principal_id": str(execution_context.get("principal_id") or "anonymous"),
                "tenant_id": str(execution_context.get("tenant_id") or "unknown"),
                "workspace_id": str(execution_context.get("workspace_id") or "unknown"),
                "device_id": str(execution_context.get("device_id") or "unknown"),
                "resource_id": str(resource_id),
                "operation": str(operation),
                "decision_effect": str(decision_effect),
                "reason_code": reason_code,
                "policy_version": str(execution_context.get("policy_version") or "v1"),
                "payload_hash": payload_hash or "NONE",
                "downstream_reference_id": downstream_reference_id,
                "timestamp": _iso_utc_now(),
                "prev_event_hash": prev_hash,
                "sanitized_metadata": dict(sanitized_metadata or {}),
            }
            event_hash = compute_jcs_sha256(event_without_hash)
            full_event = {**event_without_hash, "event_hash": event_hash}
            validate_contract_payload("audit-event.schema.json", full_event)

            conn.execute(
                """
                INSERT INTO audit_outbox (sequence_no, event_id, stream_id, prev_event_hash, event_hash, payload_json, flushed)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    next_seq,
                    full_event["event_id"],
                    self.stream_id,
                    prev_hash,
                    event_hash,
                    json.dumps(full_event, ensure_ascii=False),
                ),
            )
            conn.commit()
            return full_event

    def list_events(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM audit_outbox WHERE stream_id = ? ORDER BY sequence_no ASC",
                (self.stream_id,),
            ).fetchall()
            return [json.loads(r[0]) for r in rows]

    def flush_to_adapter(self, adapter: IamPdpAdapterPort) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sequence_no, payload_json FROM audit_outbox WHERE stream_id = ? AND flushed = 0 ORDER BY sequence_no ASC",
                (self.stream_id,),
            ).fetchall()
            if not rows:
                return {"accepted_count": 0, "rejected_count": 0}
            events = [json.loads(r[1]) for r in rows]
            res = adapter.ingest_audit_events(events)
            if res.get("accepted_count", 0) == len(events) and res.get("rejected_count", 0) == 0:
                conn.executemany(
                    "UPDATE audit_outbox SET flushed = 1 WHERE stream_id = ? AND sequence_no = ?",
                    [(self.stream_id, r[0]) for r in rows],
                )
                conn.commit()
            return res
