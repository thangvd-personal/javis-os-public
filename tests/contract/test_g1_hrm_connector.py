"""Tests for Read-Only HRM & IAM Connector on MCP Gateway with Default-Deny (Task 2)."""

from __future__ import annotations

import pytest

from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.connectors.hrm_iam_connector import (
    CONNECTOR_ID_HRM_IAM_READER,
    HrmIamConnectorHandler,
)
from javisos.server.mcp_gateway.pep_service import GatewayPepError, McpGatewayPepService


def _setup_hrm_gateway(tmp_path):
    adapter = InMemoryMockIamPdpAdapter()
    adapter.seed_principal(
        principal_id="alice_corp",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        session_token="sess_alice_01",
        permissions={
            ("res_worker_01", "read"),
            ("res_worker_02", "read"),
            ("res_worker_01", "write"),
            ("res_worker_01", "download"),
            ("res_worker_01", "prompt_use"),
            ("res_org_units", "read"),
            ("res_tenant_sub", "read"),
        },
    )
    adapter.seed_resource(
        resource_id="res_worker_01",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        resource_type="worker_profile",
        classification="CONFIDENTIAL",
    )
    adapter.seed_resource(
        resource_id="res_org_units",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        resource_type="org_units",
        classification="INTERNAL",
    )
    adapter.seed_resource(
        resource_id="res_tenant_sub",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        resource_type="tenant_subscription",
        classification="INTERNAL",
    )

    catalog = ApprovedConnectorCatalog()
    handler = HrmIamConnectorHandler(base_url="http://localhost:8000")
    catalog.register_connector(
        connector_id=CONNECTOR_ID_HRM_IAM_READER,
        server_credential="dev-gateway-service-secret",
        handler=handler,
    )

    outbox = DurableAuditOutbox(
        db_path=tmp_path / "gateway_audit.db",
        stream_id="mcp_gateway:t-corp-pilot:gw_01",
    )
    pep = McpGatewayPepService(adapter=adapter, catalog=catalog, audit_outbox=outbox)

    tok = adapter.exchange_token(
        {
            "subject_token": "sess_alice_01",
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws-corp-main",
            "device_id": "dev_01",
            "correlation_id": "corr_g1_init",
        }
    )
    return adapter, catalog, outbox, pep, tok["issued_token"]


def test_hrm_connector_reads_worker_profile_and_hides_credential(tmp_path):
    """Calling get_worker_profile through PEP invokes connector and strips server_credential."""
    adapter, catalog, outbox, pep, exec_token = _setup_hrm_gateway(tmp_path)

    result = pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_worker_01",
        operation="read",
        connector_id=CONNECTOR_ID_HRM_IAM_READER,
        arguments={"action": "get_worker_profile", "worker_number": "W-001"},
        idempotency_key="idem_read_w1",
    )
    assert result.status == "COMPLETED"
    assert result.effect == "ALLOW"
    assert "dev-gateway-service-secret" not in str(result.output)
    assert result.output.get("action") == "get_worker_profile"


def test_hrm_connector_reads_org_units_and_subscription(tmp_path):
    """Calling list_org_units and get_tenant_subscription succeed under read operation."""
    adapter, catalog, outbox, pep, exec_token = _setup_hrm_gateway(tmp_path)

    res_org = pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_org_units",
        operation="read",
        connector_id=CONNECTOR_ID_HRM_IAM_READER,
        arguments={"action": "list_org_units"},
        idempotency_key="idem_read_org",
    )
    assert res_org.status == "COMPLETED"
    assert res_org.output.get("action") == "list_org_units"

    res_sub = pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_tenant_sub",
        operation="read",
        connector_id=CONNECTOR_ID_HRM_IAM_READER,
        arguments={"action": "get_tenant_subscription"},
        idempotency_key="idem_read_sub",
    )
    assert res_sub.status == "COMPLETED"
    assert res_sub.output.get("action") == "get_tenant_subscription"


def test_pep_enforces_read_only_blocks_mutations_and_downloads(tmp_path):
    """G1-003: write, delete, download are strictly DENY at PEP with DENY_GATE_RESTRICTION_READ_ONLY."""
    adapter, catalog, outbox, pep, exec_token = _setup_hrm_gateway(tmp_path)

    for forbidden_op in ("write", "delete", "download", "export"):
        with pytest.raises(GatewayPepError) as exc_info:
            pep.execute_enterprise_action(
                execution_token=exec_token,
                resource_id="res_worker_01",
                operation=forbidden_op,
                connector_id=CONNECTOR_ID_HRM_IAM_READER,
                arguments={"action": "update_worker", "data": {"name": "Evil"}},
                idempotency_key=f"idem_forbid_{forbidden_op}",
            )
        assert exc_info.value.code == "DENY_GATE_RESTRICTION_READ_ONLY"
        assert exc_info.value.effect == "DENY"

    # share operation is also forbidden
    with pytest.raises(GatewayPepError) as exc_share:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="share",
            connector_id=CONNECTOR_ID_HRM_IAM_READER,
            arguments={"target": "personal"},
            idempotency_key="idem_share",
        )
    assert exc_share.value.code == "DENY_CROSS_WORKSPACE_SHARE_FORBIDDEN"


def test_pep_default_deny_on_missing_model_identity_or_unapproved_model(tmp_path):
    """G1-014 & G1-017: prompt_use without approved model or missing metadata fails-closed."""
    adapter, catalog, outbox, pep, exec_token = _setup_hrm_gateway(tmp_path)

    # 1. Missing model identity
    with pytest.raises(GatewayPepError) as exc_missing:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="prompt_use",
            connector_id=CONNECTOR_ID_HRM_IAM_READER,
            arguments={"prompt": "Summarize worker"},
            idempotency_key="idem_prompt_missing_model",
        )
    assert exc_missing.value.code == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"

    # 2. Model not in approved registry
    with pytest.raises(GatewayPepError) as exc_unapproved:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="prompt_use",
            connector_id=CONNECTOR_ID_HRM_IAM_READER,
            arguments={
                "prompt": "Summarize worker",
                "model_id": "unapproved-random-llm",
                "provider_id": "unknown_ai",
            },
            idempotency_key="idem_prompt_unapproved",
        )
    assert exc_unapproved.value.code == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"

    # 3. Model approved for INTERNAL (e.g. gemini-2.5-flash) but NOT for CONFIDENTIAL
    with pytest.raises(GatewayPepError) as exc_class_mismatch:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",  # CONFIDENTIAL
            operation="prompt_use",
            connector_id=CONNECTOR_ID_HRM_IAM_READER,
            arguments={
                "prompt": "Summarize worker",
                "model_id": "gemini-2.5-flash",
                "provider_id": "google",
            },
            idempotency_key="idem_prompt_class_mismatch",
        )
    assert exc_class_mismatch.value.code == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"

    # 4. Model approved for CONFIDENTIAL (claude-3-5-sonnet-20241022) with ZERO_DATA_RETENTION
    res_allowed = pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_worker_01",
        operation="prompt_use",
        connector_id=CONNECTOR_ID_HRM_IAM_READER,
        arguments={
            "prompt": "Summarize worker",
            "model_id": "claude-3-5-sonnet-20241022",
            "provider_id": "anthropic",
            "zero_retention_verified": True,
        },
        idempotency_key="idem_prompt_allowed",
    )
    assert res_allowed.status == "COMPLETED"
    assert res_allowed.effect == "ALLOW"
