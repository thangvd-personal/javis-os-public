"""Preparation-only coverage for G2 workspace and contract boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from g2_readiness_support import (
    CANARY_B,
    build_same_tenant_workspace_fixture,
    RecordingDownstream,
    RecordingProvider,
)
from javisos.server.iam_contract.port import IamPdpAdapterPort
from javisos.server.iam_contract.schemas import CONTRACT_SCHEMAS_DIR, validate_contract_payload
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.pep_service import GatewayPepError, McpGatewayPepService


def test_workspace_fixture_uses_distinct_canaries_within_one_tenant():
    case = build_same_tenant_workspace_fixture()

    assert case.principal_a["tenant_id"] == case.principal_b["tenant_id"]
    assert case.principal_a["workspace_id"] != case.principal_b["workspace_id"]
    assert case.canary_a != case.canary_b


def test_recording_downstream_does_not_retain_server_credentials():
    downstream = RecordingDownstream()

    downstream({"marker": "CANARY-G2-TEST"}, "private-test-credential")

    assert len(downstream.calls) == 1
    assert downstream.calls[0]["marker"] == "CANARY-G2-TEST"
    assert "private-test-credential" not in repr(downstream.calls)


def test_fixture_adapter_implements_frozen_contract_port():
    case = build_same_tenant_workspace_fixture()

    assert isinstance(case.adapter, IamPdpAdapterPort)


def test_same_tenant_cross_workspace_read_is_denied_before_connector_dispatch(tmp_path):
    case = build_same_tenant_workspace_fixture()
    connector = RecordingDownstream()
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="g2-reader",
        server_credential="g2-test-connector-secret",
        handler=connector,
    )
    pep = McpGatewayPepService(
        adapter=case.adapter,
        catalog=catalog,
        audit_outbox=DurableAuditOutbox(db_path=tmp_path / "g2-read-audit.db", stream_id="g2-read"),
        read_only_gate=True,
    )

    with pytest.raises(GatewayPepError) as denied:
        pep.execute_enterprise_action(
            execution_token=case.execution_token_a,
            resource_id=case.resource_b,
            operation="read",
            connector_id="g2-reader",
            arguments={"marker": case.canary_b},
            idempotency_key="g2-cross-workspace-read",
        )

    assert denied.value.code == "DENY_WORKSPACE_MISMATCH"
    assert case.canary_b not in str(denied.value)
    assert connector.calls == []


def test_same_tenant_cross_workspace_prompt_is_denied_before_provider_dispatch(tmp_path):
    case = build_same_tenant_workspace_fixture()
    provider = RecordingProvider()
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="g2-model-provider",
        server_credential="g2-test-provider-secret",
        handler=provider,
    )
    pep = McpGatewayPepService(
        adapter=case.adapter,
        catalog=catalog,
        audit_outbox=DurableAuditOutbox(db_path=tmp_path / "g2-prompt-audit.db", stream_id="g2-prompt"),
    )

    with pytest.raises(GatewayPepError) as denied:
        pep.execute_enterprise_action(
            execution_token=case.execution_token_a,
            resource_id=case.resource_b,
            operation="prompt_use",
            connector_id="g2-model-provider",
            arguments={
                "provider_id": "anthropic",
                "model_id": "claude-3-5-sonnet-20241022",
                "marker": CANARY_B,
            },
            idempotency_key="g2-cross-workspace-prompt",
        )

    assert denied.value.code == "DENY_WORKSPACE_MISMATCH"
    assert CANARY_B not in str(denied.value)
    assert provider.calls == []


def _require_frozen_contract_schemas(schema_dir: Path) -> None:
    required = (
        "execution-context.schema.json",
        "pdp-evaluate-request.schema.json",
        "pdp-decision.schema.json",
    )
    missing = [name for name in required if not (schema_dir / name).is_file()]
    if missing:
        raise RuntimeError(
            "Frozen IAM/PDP schemas are required at "
            f"{schema_dir}; missing: {', '.join(missing)}. "
            "Restore the ignored brains/SME-BTOS/documents/contract/schemas inputs."
        )


def test_missing_frozen_schema_setup_error_names_required_directory():
    missing_dir = CONTRACT_SCHEMAS_DIR / "not-present"

    with pytest.raises(RuntimeError, match="Frozen IAM/PDP schemas are required") as error:
        _require_frozen_contract_schemas(missing_dir)

    assert str(missing_dir) in str(error.value)


def test_pep_payloads_conform_to_frozen_execution_and_pdp_schemas(tmp_path, monkeypatch):
    _require_frozen_contract_schemas(CONTRACT_SCHEMAS_DIR)
    case = build_same_tenant_workspace_fixture()
    connector = RecordingDownstream()
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="g2-reader",
        server_credential="g2-test-connector-secret",
        handler=connector,
    )
    pep = McpGatewayPepService(
        adapter=case.adapter,
        catalog=catalog,
        audit_outbox=DurableAuditOutbox(db_path=tmp_path / "g2-contract-audit.db", stream_id="g2-contract"),
    )
    captured_requests: list[dict] = []
    captured_decisions: list[dict] = []
    evaluate_pdp = case.adapter.evaluate_pdp

    def capture_pdp(request):
        captured_requests.append(dict(request))
        decision = evaluate_pdp(request)
        captured_decisions.append(decision)
        return decision

    monkeypatch.setattr(case.adapter, "evaluate_pdp", capture_pdp)
    result = pep.execute_enterprise_action(
        execution_token=case.execution_token_a,
        resource_id=case.resource_a,
        operation="read",
        connector_id="g2-reader",
        arguments={"marker": case.canary_a},
        idempotency_key="g2-contract-read",
    )

    assert result.status == "COMPLETED"
    assert len(captured_requests) == len(captured_decisions) == 1
    validate_contract_payload(
        "execution-context.schema.json", captured_requests[0]["execution_context"]
    )
    validate_contract_payload("pdp-evaluate-request.schema.json", captured_requests[0])
    validate_contract_payload("pdp-decision.schema.json", captured_decisions[0])
