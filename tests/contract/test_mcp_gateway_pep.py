"""Tests for Server-Side MCP Gateway PEP, ApprovedConnectorCatalog, and DurableAuditOutbox (Task 4)."""

from __future__ import annotations

import pytest

from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.pep_service import GatewayPepError, McpGatewayPepService


def _setup_gateway(tmp_path):
    adapter = InMemoryMockIamPdpAdapter()
    adapter.seed_principal(
        principal_id="usr_01",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        session_token="sess_raw_01",
        permissions={("res_doc_01", "read"), ("res_doc_01", "write")},
    )
    adapter.seed_resource(
        resource_id="res_doc_01",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        resource_type="document",
        classification="CONFIDENTIAL",
    )
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="erp_docs",
        server_credential="secret_erp_api_key_999",
        handler=lambda args, cred: {"ok": True, "doc": args.get("doc_id"), "cred_present": bool(cred)},
    )
    outbox = DurableAuditOutbox(db_path=tmp_path / "gateway_audit.db", stream_id="mcp_gateway:ten_01:gw_01")
    pep = McpGatewayPepService(adapter=adapter, catalog=catalog, audit_outbox=outbox)
    tok = adapter.exchange_token(
        {
            "subject_token": "sess_raw_01",
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws_corp_01",
            "device_id": "dev_01",
            "correlation_id": "corr_init",
        }
    )
    return adapter, catalog, outbox, pep, tok["issued_token"]


def test_gateway_rejects_raw_session_token_and_personal_hub_token(tmp_path):
    """ADR-01 & G0-003: Gateway rejects raw session token and personal .hub_token."""
    _, _, _, pep, _ = _setup_gateway(tmp_path)

    with pytest.raises(GatewayPepError) as exc_sess:
        pep.execute_enterprise_action(
            execution_token="sess_raw_01",
            resource_id="res_doc_01",
            operation="read",
            connector_id="erp_docs",
            arguments={"doc_id": "1"},
            idempotency_key="idem_01",
        )
    assert exc_sess.value.code == "TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN"

    with pytest.raises(GatewayPepError) as exc_hub:
        pep.execute_enterprise_action(
            execution_token="hub_personal_secret_token",
            resource_id="res_doc_01",
            operation="read",
            connector_id="erp_docs",
            arguments={"doc_id": "1"},
            idempotency_key="idem_02",
        )
    assert exc_hub.value.code == "TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN"


def test_gateway_blocks_allow_with_unhandled_mandatory_obligation(tmp_path):
    """Review Focus #2: ALLOW with unknown mandatory obligation must fail-closed."""
    adapter, _, _, pep, exec_token = _setup_gateway(tmp_path)
    adapter.custom_obligations[("res_doc_01", "read")] = [
        {
            "obligation_type": "WATERMARK_OUTPUT",
            "mandatory": True,
            "parameters": {"mode": "UNSUPPORTED_HARDWARE_HSM_WATERMARK"},
        }
    ]

    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_doc_01",
            operation="read",
            connector_id="erp_docs",
            arguments={"doc_id": "1"},
            idempotency_key="idem_obl_1",
        )
    assert exc_info.value.code == "AUTHZ_OBLIGATION_UNFULFILLED"


def test_gateway_rejects_unapproved_connector_and_custom_url_override(tmp_path):
    """Review Focus #4 & G0-005: Reject unapproved connector_id or SSRF URL override."""
    _, _, _, pep, exec_token = _setup_gateway(tmp_path)

    with pytest.raises(GatewayPepError) as exc_conn:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_doc_01",
            operation="read",
            connector_id="unregistered_internal_db",
            arguments={"doc_id": "1"},
            idempotency_key="idem_ssrf_1",
        )
    assert exc_conn.value.code == "CONNECTOR_NOT_IN_APPROVED_CATALOG"

    with pytest.raises(GatewayPepError) as exc_url:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_doc_01",
            operation="read",
            connector_id="erp_docs",
            arguments={"doc_id": "1", "endpoint_url": "http://169.254.169.254/latest/meta-data"},
            idempotency_key="idem_ssrf_2",
        )
    assert exc_url.value.code == "CONNECTOR_NOT_IN_APPROVED_CATALOG"


def test_gateway_downstream_timeout_sets_reconciliation_required_without_blind_retry(tmp_path):
    """G0-013 & G0-023: Downstream timeout transitions to RECONCILIATION_REQUIRED with 0 blind retries."""
    adapter, catalog, outbox, pep, exec_token = _setup_gateway(tmp_path)
    calls = {"count": 0}

    def timeout_writer(args, cred):
        calls["count"] += 1
        raise TimeoutError("Downstream ERP write timed out after dispatch")

    catalog.register_connector(
        connector_id="erp_writer",
        server_credential="secret_write_key",
        handler=timeout_writer,
    )

    result = pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_doc_01",
        operation="write",
        connector_id="erp_writer",
        arguments={"amount": 500},
        idempotency_key="idem_timeout_1",
    )
    assert result.status == "RECONCILIATION_REQUIRED"
    assert calls["count"] == 1

    # Re-submitting same idempotency_key with different payload must fail with IDEMPOTENCY_PAYLOAD_MISMATCH
    with pytest.raises(GatewayPepError) as exc_idem:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_doc_01",
            operation="write",
            connector_id="erp_writer",
            arguments={"amount": 999},
            idempotency_key="idem_timeout_1",
        )
    assert exc_idem.value.code == "IDEMPOTENCY_PAYLOAD_MISMATCH"

    # Flush durable outbox to adapter and verify staged events & hash chain
    sync_res = outbox.flush_to_adapter(adapter)
    assert sync_res["accepted_count"] >= 3
    assert sync_res["rejected_count"] == 0
