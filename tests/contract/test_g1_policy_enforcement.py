"""G1 read-only, tenant-boundary, and IDOR enforcement."""

from __future__ import annotations

import pytest

from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.pep_service import GatewayPepError, McpGatewayPepService


def _gateway(tmp_path):
    adapter = InMemoryMockIamPdpAdapter()
    adapter.seed_principal(principal_id="u1", tenant_id="t1", workspace_id="w1", session_token="s1", permissions={("r1", "read"), ("r1", "write")}, device_posture="COMPLIANT")
    adapter.seed_resource(resource_id="r1", tenant_id="t1", workspace_id="w1", resource_type="worker", classification="INTERNAL")
    adapter.seed_resource(resource_id="r2", tenant_id="t2", workspace_id="w2", resource_type="worker", classification="INTERNAL")
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(connector_id="reader", server_credential="test", handler=lambda args, cred: {"ok": True})
    outbox = DurableAuditOutbox(db_path=tmp_path / "audit.db", stream_id="policy")
    pep = McpGatewayPepService(adapter=adapter, catalog=catalog, audit_outbox=outbox, read_only_gate=True)
    token = adapter.exchange_token({"subject_token": "s1", "subject_token_type": "SESSION_TOKEN", "requested_audience": "javisos-mcp-gateway", "requested_workspace_id": "w1", "device_id": "d1", "device_posture": "COMPLIANT"})["issued_token"]
    return pep, token


def test_g1_read_only_and_idor_enforcement(tmp_path):
    pep, token = _gateway(tmp_path)
    result = pep.execute_enterprise_action(execution_token=token, resource_id="r1", operation="read", connector_id="reader", idempotency_key="read-1")
    assert result.status == "COMPLETED"
    for operation in ("write", "delete", "download", "export", "share"):
        with pytest.raises(GatewayPepError) as denied:
            pep.execute_enterprise_action(execution_token=token, resource_id="r1", operation=operation, connector_id="reader", idempotency_key=f"deny-{operation}")
        assert denied.value.code in {"DENY_GATE_RESTRICTION_READ_ONLY", "DENY_CROSS_WORKSPACE_SHARE_FORBIDDEN"}

    with pytest.raises(GatewayPepError) as cross_tenant:
        pep.execute_enterprise_action(execution_token=token, resource_id="r2", operation="read", connector_id="reader", idempotency_key="idor-1")
    assert cross_tenant.value.code in {"DENY_TENANT_MISMATCH", "RESOURCE_NOT_FOUND_OR_CROSS_TENANT"}
