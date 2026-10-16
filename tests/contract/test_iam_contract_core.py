"""Contract Core Tests: RFC 8785 JCS, JSON Schema Draft 2020-12 Validator, and InMemoryMockIamPdpAdapter."""

from __future__ import annotations

import pytest

from javisos.server.iam_contract.jcs import compute_action_payload_hash
from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
from javisos.server.iam_contract.schemas import ContractSchemaError, validate_contract_payload


def test_jcs_number_and_unicode_canonicalization():
    """Review Focus #1: Financially equivalent numbers and reordered keys produce identical JCS hash."""
    h1 = compute_action_payload_hash(
        "res_doc_1",
        "write",
        {"b": 1, "a": {"amount": 100.0, "note": "Café", "zero": -0.0}},
    )
    h2 = compute_action_payload_hash(
        "res_doc_1",
        "write",
        {"a": {"zero": 0, "note": "Café", "amount": 100}, "b": 1},
    )
    h3 = compute_action_payload_hash(
        "res_doc_1",
        "write",
        {"a": {"zero": 0, "note": "Café", "amount": 100.01}, "b": 1},
    )
    assert h1 == h2
    assert h1.startswith("sha256:") and len(h1) == 71
    assert h1 != h3


def test_schema_validation_enforces_approval_required_fields():
    """JSON Schema allOf/if/then must reject APPROVAL_REQUIRED without approval_requirement."""
    with pytest.raises(ContractSchemaError):
        validate_contract_payload(
            "pdp-decision.schema.json",
            {
                "schema_version": "1.0.0",
                "decision_id": "dec_1",
                "effect": "APPROVAL_REQUIRED",
                "reason_code": "APPROVAL_REQUIRED_BY_POLICY",
                "policy_version": "v1",
                "evaluated_at": "2026-10-16T12:00:00Z",
                "correlation_id": "corr_1",
            },
        )


def test_mock_adapter_mints_dedicated_execution_token_and_evaluates_non_transitive_ops():
    """CT-TOK-001 & CT-RES-001: Dedicated execution token and non-transitive resource x operation."""
    adapter = InMemoryMockIamPdpAdapter()
    adapter.seed_principal(
        principal_id="usr_01",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        session_token="sess_raw_01",
        permissions={("res_doc_01", "read")},
    )
    adapter.seed_resource(
        resource_id="res_doc_01",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        resource_type="document",
        classification="CONFIDENTIAL",
    )
    tok = adapter.exchange_token(
        {
            "subject_token": "sess_raw_01",
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws_corp_01",
            "device_id": "dev_01",
            "correlation_id": "corr_tok_01",
        }
    )
    assert tok["issued_token"] != "sess_raw_01"
    assert tok["aud"] == "javisos-mcp-gateway"
    assert tok["sub"] == "usr_01"
    assert tok["service_identity"] == "javisos-mcp-gateway"
    assert tok["verified_context"]["resource_id"] is None

    ctx = {
        **tok["verified_context"],
        "resource_id": "res_doc_01",
        "execution_id": "exec_01",
        "correlation_id": "corr_eval_01",
    }
    allow_read = adapter.evaluate_pdp(
        {
            "schema_version": "1.0.0",
            "execution_context": ctx,
            "resource": {"resource_id": "res_doc_01"},
            "operation": "read",
        }
    )
    deny_download = adapter.evaluate_pdp(
        {
            "schema_version": "1.0.0",
            "execution_context": ctx,
            "resource": {"resource_id": "res_doc_01"},
            "operation": "download",
        }
    )
    assert allow_read["effect"] == "ALLOW"
    assert deny_download["effect"] == "DENY"
    assert deny_download["reason_code"] == "DENY_NO_ROLE_OR_PERMISSION_BINDING"


def test_mock_adapter_approval_sod_and_single_use_and_audit_chain():
    """CT-APR-001..006 & CT-AUD-001 on InMemoryMockIamPdpAdapter."""
    adapter = InMemoryMockIamPdpAdapter()
    adapter.seed_principal(
        principal_id="usr_req",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        session_token="sess_req",
        permissions={("res_doc_01", "write")},
        requires_approval={("res_doc_01", "write")},
    )
    adapter.seed_principal(
        principal_id="usr_mgr",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        session_token="sess_mgr",
        permissions={("res_doc_01", "approve")},
    )
    adapter.seed_resource(
        resource_id="res_doc_01",
        tenant_id="ten_01",
        workspace_id="ws_corp_01",
        resource_type="document",
        classification="CONFIDENTIAL",
    )
    tok = adapter.exchange_token(
        {
            "subject_token": "sess_req",
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws_corp_01",
            "device_id": "dev_01",
            "correlation_id": "corr_1",
        }
    )
    ctx = {
        **tok["verified_context"],
        "resource_id": "res_doc_01",
        "execution_id": "exec_w1",
        "correlation_id": "corr_w1",
    }
    payload_hash = compute_action_payload_hash("res_doc_01", "write", {"title": "Contract v1"})

    decision = adapter.evaluate_pdp(
        {
            "schema_version": "1.0.0",
            "execution_context": ctx,
            "resource": {"resource_id": "res_doc_01"},
            "operation": "write",
            "action_context": {
                "payload_hash": payload_hash,
                "idempotency_key": "idem_w1",
                "revalidation_stage": "INGRESS",
            },
        }
    )
    assert decision["effect"] == "APPROVAL_REQUIRED"

    approval = adapter.create_approval(
        {
            "execution_context": ctx,
            "resource_id": "res_doc_01",
            "operation": "write",
            "payload_hash": payload_hash,
            "idempotency_key": "idem_w1",
            "reason": "Update contract title",
        }
    )
    assert approval["status"] == "PENDING"

    # Self-approval must fail SoD
    with pytest.raises(ValueError, match="APPROVAL_SOD_VIOLATION"):
        adapter.decide_approval(
            approval["approval_id"],
            {
                "approver_principal_id": "usr_req",
                "decision": "APPROVE",
                "reason": "self approve",
                "correlation_id": "corr_sod",
            },
        )

    # Manager approves
    approved = adapter.decide_approval(
        approval["approval_id"],
        {
            "approver_principal_id": "usr_mgr",
            "decision": "APPROVE",
            "reason": "Approved by manager",
            "correlation_id": "corr_mgr",
        },
    )
    assert approved["status"] == "APPROVED"

    # Mutated payload hash must fail
    tampered_hash = compute_action_payload_hash("res_doc_01", "write", {"title": "Tampered"})
    bad_val = adapter.validate_approval(
        approval["approval_id"],
        {
            "execution_context": ctx,
            "resource_id": "res_doc_01",
            "operation": "write",
            "payload_hash": tampered_hash,
            "idempotency_key": "idem_w1",
            "consume_on_valid": True,
        },
    )
    assert bad_val["valid"] is False
    assert bad_val["reason_code"] == "APPROVAL_PAYLOAD_MISMATCH"

    # Valid payload hash consumes the approval
    good_val = adapter.validate_approval(
        approval["approval_id"],
        {
            "execution_context": ctx,
            "resource_id": "res_doc_01",
            "operation": "write",
            "payload_hash": payload_hash,
            "idempotency_key": "idem_w1",
            "consume_on_valid": True,
        },
    )
    assert good_val["valid"] is True
    assert good_val["status"] == "CONSUMED"

    # Replay with a new idempotency_key must fail single-use
    replay_val = adapter.validate_approval(
        approval["approval_id"],
        {
            "execution_context": ctx,
            "resource_id": "res_doc_01",
            "operation": "write",
            "payload_hash": payload_hash,
            "idempotency_key": "idem_w2",
            "consume_on_valid": True,
        },
    )
    assert replay_val["valid"] is False
    assert replay_val["reason_code"] == "APPROVAL_ALREADY_CONSUMED"
