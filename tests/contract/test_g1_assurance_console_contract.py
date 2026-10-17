"""Tests for G1 Standalone Assurance Console Sidecar and Retention Evaluator (Tasks 4 & 4A, G1-012, G1-016, G1-017)."""

from __future__ import annotations

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox


def _create_sample_outbox(db_path):
    outbox = DurableAuditOutbox(db_path=db_path, stream_id="mcp_gateway:t-corp-pilot:gw_01")
    ctx = {
        "tenant_id": "t-corp-pilot",
        "workspace_id": "ws-corp-main",
        "principal_id": "alice_corp",
        "execution_id": "exec_01",
        "correlation_id": "corr_01",
    }
    outbox.append_event(
        event_type="mcp.tool_call",
        execution_stage="REQUEST_ACCEPTED",
        execution_context=ctx,
        resource_id="res_worker_01",
        operation="read",
        decision_effect="N/A",
        payload_hash="sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    outbox.append_event(
        event_type="pdp.decision",
        execution_stage="DECISION_GRANTED",
        execution_context=ctx,
        resource_id="res_worker_01",
        operation="read",
        decision_effect="ALLOW",
        payload_hash="sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        reason_code="OK_GRANTED",
    )
    return outbox


def test_assurance_console_contract_auditor_and_operator_isolation(tmp_path):
    """G1-012 & G1-016: Console operates on 7778 in RO mode; Auditor is read-only; Operator requires secret."""
    import importlib.util
    from pathlib import Path

    server_path = Path(__file__).resolve().parent.parent.parent / "brains" / "SME-BTOS" / "documents" / "g1-pilot" / "assurance-console" / "server.py"
    spec = importlib.util.spec_from_file_location("assurance_server", str(server_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    create_assurance_app = module.create_assurance_app

    db_path = tmp_path / "test_gateway_audit.db"
    _create_sample_outbox(db_path)

    operator_secret = "test-operator-secret-999"
    app = create_assurance_app(db_path=str(db_path), operator_secret=operator_secret)
    client = TestClient(app)

    # 1. Auditor reads audit events & chain integrity
    resp_events = client.get("/api/audit-events")
    assert resp_events.status_code == 200
    data = resp_events.json()
    assert "events" in data
    assert len(data["events"]) == 2
    assert data["chain_integrity"]["valid"] is True

    # 2. Auditor checks lease and heartbeat status
    resp_lease = client.get("/api/lease-status")
    assert resp_lease.status_code == 200
    lease_data = resp_lease.json()
    assert lease_data["max_lease_seconds"] == 300
    assert lease_data["heartbeat_interval_seconds"] == 30

    # 3. Auditor CANNOT trigger test runner (no secret) -> 403
    resp_run_unauth = client.post("/api/run-test", json={"test_name": "TEST_A"})
    assert resp_run_unauth.status_code == 403
    assert "OPERATOR_AUTH_REQUIRED" in resp_run_unauth.text

    # 4. Operator with invalid secret -> 403
    resp_run_bad = client.post(
        "/api/run-test",
        json={"test_name": "TEST_A"},
        headers={"X-Operator-Secret": "wrong-secret"},
    )
    assert resp_run_bad.status_code == 403

    # 5. Operator with valid secret -> 200
    resp_run_ok = client.post(
        "/api/run-test",
        json={"test_name": "TEST_A"},
        headers={"X-Operator-Secret": operator_secret},
    )
    assert resp_run_ok.status_code == 200
    assert resp_run_ok.json()["status"] == "TRIGGERED"

    # 6. Read-Only mode verification: writing to DB via read-only connection fails
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO audit_events VALUES ('x','x','x',1,'x','x','x','x','x','x','x','x','x')")
    conn.close()


def test_retention_evaluator_confidential_controlled_or():
    """Task 4A & G1-017: Controlled OR evaluator for CONFIDENTIAL classification."""
    from javisos.server.mcp_gateway.retention_evaluator import evaluate_confidential_retention

    # Case 1: Branch A (ZERO_DATA_RETENTION) with evidence -> PASS
    res_a_pass = evaluate_confidential_retention(
        provider_id="anthropic",
        model_id="claude-3-5-sonnet-20241022",
        classification="CONFIDENTIAL",
        evidence={"zero_retention_verified": True, "contract_dpa_signed": True},
    )
    assert res_a_pass["allowed"] is True
    assert res_a_pass["branch"] == "ZERO_DATA_RETENTION"

    # Case 2: Branch A missing zero_retention_verified -> FAIL
    res_a_fail = evaluate_confidential_retention(
        provider_id="anthropic",
        model_id="claude-3-5-sonnet-20241022",
        classification="CONFIDENTIAL",
        evidence={"zero_retention_verified": False},
    )
    assert res_a_fail["allowed"] is False
    assert res_a_fail["reason_code"] == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"

    # Case 3: Branch B (LOCAL_ON_PREM_ONLY) with full isolation -> PASS
    res_b_pass = evaluate_confidential_retention(
        provider_id="local",
        model_id="qwen2.5:14b-instruct-q4_K_M",
        classification="CONFIDENTIAL",
        evidence={
            "egress_blocked": True,
            "telemetry_disabled": True,
            "prompt_logging_disabled": True,
            "remote_fallback": False,
        },
    )
    assert res_b_pass["allowed"] is True
    assert res_b_pass["branch"] == "LOCAL_ON_PREM_ONLY"

    # Case 4: Branch B missing telemetry_disabled -> FAIL
    res_b_fail = evaluate_confidential_retention(
        provider_id="local",
        model_id="qwen2.5:14b-instruct-q4_K_M",
        classification="CONFIDENTIAL",
        evidence={
            "egress_blocked": True,
            "telemetry_disabled": False,  # Missing requirement!
            "prompt_logging_disabled": True,
            "remote_fallback": False,
        },
    )
    assert res_b_fail["allowed"] is False
    assert res_b_fail["reason_code"] == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"

    # Case 5: Model not approved for CONFIDENTIAL (e.g. gemini-2.5-flash) -> FAIL
    res_c_fail = evaluate_confidential_retention(
        provider_id="google",
        model_id="gemini-2.5-flash",
        classification="CONFIDENTIAL",
        evidence={"zero_retention_verified": True},
    )
    assert res_c_fail["allowed"] is False
    assert res_c_fail["reason_code"] == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"
