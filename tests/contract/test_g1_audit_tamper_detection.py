"""G1-AUDIT-007: verify tampering, deletion, and sequence gaps in the outbox."""

from __future__ import annotations

import json
import sqlite3
import hashlib

from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox


def _populate(outbox: DurableAuditOutbox) -> None:
    ctx = {"tenant_id": "ten_01", "workspace_id": "ws_01", "principal_id": "usr_01", "execution_id": "exec_01", "correlation_id": "corr_01", "device_id": "dev_01"}
    for seq in range(5):
        outbox.append_event(
            event_type="pdp.decision", execution_stage="DECISION_GRANTED", execution_context=ctx,
            resource_id=f"res_{seq}", operation="read", decision_effect="ALLOW", payload_hash="sha256:" + hashlib.sha256(str(seq).encode()).hexdigest(), reason_code="OK_GRANTED",
        )


def test_audit_tamper_and_sequence_gap_are_detected(tmp_path):
    db_path = tmp_path / "audit.db"
    outbox = DurableAuditOutbox(db_path=db_path, stream_id="stream-01")
    _populate(outbox)
    assert outbox.verify_integrity() == {"valid": True, "event_count": 5, "last_sequence_no": 5}

    events = outbox.list_events()
    tampered = dict(events[2])
    tampered["operation"] = "write"
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_outbox SET payload_json = ? WHERE sequence_no = 3", (json.dumps(tampered),))
    result = outbox.verify_integrity()
    assert result["valid"] is False
    assert result["reason_code"] == "AUDIT_EVENT_HASH_MISMATCH"
    assert result["sequence_no"] == 3

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM audit_outbox WHERE sequence_no = 3")
    result = outbox.verify_integrity()
    assert result["valid"] is False
    assert result["reason_code"] == "AUDIT_SEQUENCE_GAP"
    assert result["sequence_no"] == 4
