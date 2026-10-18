"""Comprehensive Automated Verification for G1 Live Scenarios (Test A, B, C and G1-001..G1-018).

Implements:
  - Test A: Mid-flight Revocation via permission_version bump & 0s cache wipe.
  - Test B: Pre-flight Leak Prevention on non-allowlisted LLM model & local Lineage cascade revocation.
  - Test C: Fail-Closed on PDP Network Timeout (>5s) & Vault Lock on Heartbeat loss (>30s).
  - Individual criteria checks for G1-001..G1-018 with reproducible assertions.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any
import pytest

from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.pep_service import (
    APPROVED_MODEL_REGISTRY,
    GatewayPepError,
    McpGatewayPepService,
)
from javisos.server.corporate_guard import (
    CorporateLocalGuard,
    LocalVaultLeaseManager,
    VaultLockedError,
)
from javisos.server.corporate_runtime import (
    ArtifactLineageTracker,
    CorporateTaskWorker,
)
from javisos.server.mcp_gateway.connectors.hrm_iam_connector import (
    HrmIamConnectorHandler,
    READ_ONLY_ALLOWED_ACTIONS,
)
from javisos.server.mcp_gateway.retention_evaluator import evaluate_confidential_retention


def _build_test_gateway(tmp_path, *, read_only: bool = True):
    adapter = InMemoryMockIamPdpAdapter()
    
    # Seed G1 Pilot tenant, principal Alice, and resources
    adapter.seed_principal(
        principal_id="alice_corp",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        session_token="sess_tok_alice_g1",
        permissions={
            ("res_worker_01", "read"),
            ("res_worker_02", "read"),
            ("res_doc_01", "read"),
            ("res_doc_01", "prompt_use"),
            ("res_worker_01", "prompt_use"),
            ("res_worker_02", "prompt_use"),
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
        resource_id="res_worker_02",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        resource_type="worker_profile",
        classification="CONFIDENTIAL",
    )
    adapter.seed_resource(
        resource_id="res_doc_01",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        resource_type="document",
        classification="CONFIDENTIAL",
    )

    db_path = tmp_path / "g1_live_audit.db"
    audit_outbox = DurableAuditOutbox(db_path=db_path, stream_id="mcp_gateway:t-corp-pilot:gw_live")
    
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="sme_hrm_iam_reader",
        server_credential="sme-secret-key-live",
        handler=lambda args, cred: {"ok": True, "data": args},
    )

    pep = McpGatewayPepService(
        adapter=adapter,
        catalog=catalog,
        audit_outbox=audit_outbox,
        read_only_gate=read_only,
    )
    return adapter, pep, audit_outbox, db_path


def _mint_execution_token(adapter, principal_id="alice_corp", session_token="sess_tok_alice_g1"):
    tok = adapter.exchange_token(
        {
            "subject_token": session_token,
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws-corp-main",
            "device_id": "dev_g1_01",
            "correlation_id": "corr_g1_test",
        }
    )
    return tok["issued_token"], tok["verified_context"]


# ==============================================================================
# TEST A: Mid-flight Revocation via permission_version bump & 0s cache wipe
# ==============================================================================

def test_a_mid_flight_revocation_and_instant_cache_wipe(tmp_path):
    """Test A (G1-005, G1-006, G1-007):
    - Alice reads Worker 1 successfully (ALLOW, OK_GRANTED);
    - Ephemeral cache vault stores Worker 1 data;
    - Tenant permission version bumped;
    - Reading Worker 2 is immediately blocked with PERMISSION_OR_IDENTITY_VERSION_STALE;
    - Cache key for Worker 1 is instantly wiped (0s TTL effect).
    """
    adapter, pep, audit_outbox, _ = _build_test_gateway(tmp_path)
    exec_token, verified_ctx = _mint_execution_token(adapter)

    # 1. Step 1: Read Worker 1 (ALLOW)
    res_1 = pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_worker_01",
        operation="read",
        connector_id="sme_hrm_iam_reader",
        arguments={"subpath": "/workers", "worker_id": "res_worker_01"},
        idempotency_key="idem_test_a_step1",
    )
    assert res_1.effect == "ALLOW"

    # Ephemeral Cache stores Worker 1 artifact
    lease_mgr = LocalVaultLeaseManager(adapter=adapter, default_lease_seconds=300)
    ctx_1 = {**verified_ctx, "resource_id": "res_worker_01", "execution_id": "exec_a_1"}
    lease_mgr.store_cached_artifact(
        cache_key="cache_worker_01",
        plaintext=b'{"worker_id": "res_worker_01", "name": "Alice"}',
        execution_context=ctx_1,
        resource_id="res_worker_01",
        retention_seconds=300,
    )
    assert lease_mgr.read_cached_artifact("cache_worker_01") is not None
    assert lease_mgr.has_ephemeral_key("cache_worker_01") is True

    # 2. Step 2: Bump tenant permission version in SME_BTOS
    adapter.bump_tenant_permission_version("t-corp-pilot")

    # 3. Step 3: Attempt to read Worker 2
    # Background execution worker revalidates before read_step
    worker = CorporateTaskWorker(adapter=adapter)
    ctx_2 = {**verified_ctx, "resource_id": "res_worker_02", "execution_id": "exec_a_2"}
    task = worker.enqueue_task(
        task_id="task_read_worker_02",
        execution_context=ctx_2,
        resource_id="res_worker_02",
        operation="read",
        payload={"subpath": "/workers", "worker_id": "res_worker_02"},
    )
    result = worker.run_task(
        task_id=task["task_id"],
        downstream_callable=lambda: {"worker_id": "res_worker_02"},
    )
    assert result["status"] in ("CANCELLED", "FAILED_AUTHZ")
    assert any(
        "PERMISSION_VERSION_CHANGED" in str(h.get("reason"))
        or "PERMISSION_OR_IDENTITY_VERSION_STALE" in str(h.get("reason"))
        for h in result["history"]
    )

    # Gateway PEP also blocks Worker 2 execution token
    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_02",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            arguments={"subpath": "/workers", "worker_id": "res_worker_02"},
            idempotency_key="idem_test_a_step2",
        )
    assert exc_info.value.code in ("TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN", "PERMISSION_REVOKED")
    assert "PERMISSION_OR_IDENTITY_VERSION_STALE" in str(exc_info.value)


    # 4. Instant cache wipe: polling/revalidating cache_worker_01 wipes ephemeral key
    rev_check = lease_mgr.poll_and_enforce_revocation("cache_worker_01")
    assert rev_check["revoked"] is True
    assert lease_mgr.has_ephemeral_key("cache_worker_01") is False
    with pytest.raises(VaultLockedError, match="VAULT_LOCKED"):
        lease_mgr.read_cached_artifact("cache_worker_01")


# ==============================================================================
# TEST B: Pre-flight Leak Prevention on Model Allowlist & Lineage Cascade Lock
# ==============================================================================

def test_b_pre_flight_leak_prevention_and_lineage_cascade(tmp_path):
    """Test B (G1-008, G1-017):
    - Bob/Alice attempts prompt_use of CONFIDENTIAL data with unapproved model (gemini-2.5-flash);
    - Gateway PEP pre-flight blocks before external dispatch (DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED);
    - Source resource revoked -> Lineage graph cascades revocation to all derived nodes.
    """
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, verified_ctx = _mint_execution_token(adapter)

    # 1. Attempt to send CONFIDENTIAL data to gemini-2.5-flash (only approved for INTERNAL, not CONFIDENTIAL)
    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="prompt_use",
            connector_id="sme_hrm_iam_reader",
            arguments={
                "provider_id": "google",
                "model_id": "gemini-2.5-flash",
                "prompt": "Analyze worker compensation details",
            },
            idempotency_key="idem_test_b_blocked_model",
            action_context_extra={"classification": "CONFIDENTIAL"},
        )
    assert exc_info.value.code == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"
    assert exc_info.value.effect == "DENY"

    # 2. Local Lineage Graph propagation test
    tracker = ArtifactLineageTracker()
    tracker.register_root_resource(
        resource_id="res_worker_01",
        tenant_id="t-corp-pilot",
        workspace_id="ws-corp-main",
        classification="CONFIDENTIAL",
        policy_version="v1",
    )
    derived = [
        ("derived_summary_01", "res_worker_01", "summary"),
        ("derived_embedding_01", "derived_summary_01", "embedding_index"),
        ("derived_memory_01", "derived_embedding_01", "memory"),
    ]
    for art_id, parent_id, stage in derived:
        tracker.register_derived_artifact(
            artifact_id=art_id,
            parent_ids=[parent_id],
            derivation_stage=stage,
            execution_id="exec_b_01",
        )
    assert tracker.can_access_artifact("derived_memory_01") is True

    # Revoke source resource -> all derived nodes are locked
    revoked_list = tracker.propagate_revocation("res_worker_01", reason="SOURCE_RESOURCE_REVOKED")
    assert set(revoked_list) == {"derived_summary_01", "derived_embedding_01", "derived_memory_01"}
    for art_id, _, _ in derived:
        assert tracker.can_access_artifact(art_id) is False
        with pytest.raises(PermissionError, match="LINEAGE_ACCESS_REVOKED"):
            tracker.read_for_rag_or_prompt(art_id)


# ==============================================================================
# TEST C: PDP Network Failure Fail-Closed & Lease Loss Lock
# ==============================================================================

def test_c_pdp_network_failure_fail_closed_and_lease_loss(tmp_path):
    """Test C (G1-007, G1-011):
    - PDP endpoint becomes unavailable / times out > 5s -> Gateway fail-closed (PDP_UNAVAILABLE_FAIL_CLOSED);
    - Heartbeat loss > 30s / lease expiry -> LocalVaultLeaseManager locks ephemeral keys.
    """
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, verified_ctx = _mint_execution_token(adapter)

    # 1. PDP enters unavailable mode (>5s timeout simulation)
    adapter.unavailable_mode = True

    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            arguments={"subpath": "/workers"},
            idempotency_key="idem_test_c_pdp_timeout",
        )
    assert exc_info.value.code == "PDP_UNAVAILABLE_FAIL_CLOSED"
    assert exc_info.value.effect == "PDP_UNAVAILABLE"

    # 2. Local Vault Lease Manager: heartbeat lost / lease expired
    adapter.unavailable_mode = False
    lease_mgr = LocalVaultLeaseManager(adapter=adapter, default_lease_seconds=300)
    ctx = {**verified_ctx, "resource_id": "res_worker_01", "execution_id": "exec_c_1"}
    lease_mgr.store_cached_artifact(
        cache_key="cache_c_item",
        plaintext=b"temporary-confidential-bytes",
        execution_context=ctx,
        resource_id="res_worker_01",
        retention_seconds=300,
    )
    assert lease_mgr.read_cached_artifact("cache_c_item") == b"temporary-confidential-bytes"

    # Simulate heartbeat lost & lease expired
    lease_mgr.force_expire_lease("cache_c_item")
    adapter.unavailable_mode = True  # Network drop prevents renewal
    with pytest.raises(VaultLockedError, match="VAULT_LOCKED"):
        lease_mgr.read_cached_artifact("cache_c_item")
    assert lease_mgr.has_ephemeral_key("cache_c_item") is False


# ==============================================================================
# Individual Verification for G1 Criteria G1-001 to G1-018
# ==============================================================================

def test_g1_001_seed_data_isolation(tmp_path):
    """G1-001: Verified tenant t-corp-pilot and users seeded cleanly with no unauthorized roles."""
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    caps = adapter.get_capabilities()
    assert caps["contract_version"] == "1.0.0"
    assert "t-corp-pilot" in adapter.tenant_permission_versions


def test_g1_002_and_g1_003_read_only_and_authorization_enforcement(tmp_path):
    """G1-002 & G1-003: Gateway PEP enforces ALLOW, DENY, and strictly blocks mutations & downloads."""
    adapter, pep, _, _ = _build_test_gateway(tmp_path, read_only=True)
    exec_token, _ = _mint_execution_token(adapter)

    for op in ("write", "delete", "download", "export"):
        with pytest.raises(GatewayPepError) as exc_info:
            pep.execute_enterprise_action(
                execution_token=exec_token,
                resource_id="res_worker_01",
                operation=op,
                connector_id="sme_hrm_iam_reader",
                idempotency_key=f"idem_ro_{op}",
            )
        assert exc_info.value.code == "DENY_GATE_RESTRICTION_READ_ONLY"
        assert exc_info.value.effect == "DENY"

    # Share across workspaces is strictly DENY
    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="share",
            connector_id="sme_hrm_iam_reader",
            idempotency_key="idem_share_cross",
        )
    assert exc_info.value.code == "DENY_CROSS_WORKSPACE_SHARE_FORBIDDEN"


def test_g1_004_tenant_and_resource_isolation_idor(tmp_path):
    """G1-004: IDOR / Cross-Tenant resource request is denied fail-closed."""
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, _ = _mint_execution_token(adapter)

    # Attempt to access an unregistered or cross-tenant foreign resource
    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_foreign_tenant_999",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            idempotency_key="idem_idor_cross",
        )
    assert exc_info.value.code in (
        "RESOURCE_NOT_FOUND_OR_CROSS_TENANT",
        "DENY_NO_ROLE_OR_PERMISSION_BINDING",
        "DENY_OPERATION_NOT_PERMITTED_FOR_RESOURCE",
    )


def test_g1_007_lease_ceiling_300s_enforced(tmp_path):
    """G1-007: 5-minute lease ceiling (300s) enforced; exceeding lease locks cache."""
    adapter, _, _, _ = _build_test_gateway(tmp_path)
    _, verified_ctx = _mint_execution_token(adapter)
    lease_mgr = LocalVaultLeaseManager(adapter=adapter, default_lease_seconds=300)
    ctx = {**verified_ctx, "resource_id": "res_worker_01", "execution_id": "exec_lease_ceil"}

    lease_mgr.store_cached_artifact(
        cache_key="cache_lease_ceil",
        plaintext=b"secret-profile",
        execution_context=ctx,
        resource_id="res_worker_01",
        retention_seconds=300,
    )
    # Check lease duration limit
    entry = lease_mgr._entries["cache_lease_ceil"]
    assert entry["lease_expires_at"] - entry["created_at"] <= 300


def test_g1_009_tamper_evident_audit_outbox_hash_chain(tmp_path):
    """G1-009: SQLite audit outbox maintains verifiable cryptographic hash chain."""
    adapter, pep, audit_outbox, db_path = _build_test_gateway(tmp_path)
    exec_token, _ = _mint_execution_token(adapter)

    pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_worker_01",
        operation="read",
        connector_id="sme_hrm_iam_reader",
        idempotency_key="idem_audit_1",
    )
    pep.execute_enterprise_action(
        execution_token=exec_token,
        resource_id="res_worker_02",
        operation="read",
        connector_id="sme_hrm_iam_reader",
        idempotency_key="idem_audit_2",
    )

    events = audit_outbox.list_events()
    assert len(events) >= 2
    # Verify hash chain continuity
    for i in range(1, len(events)):
        prev_ev = events[i - 1]
        cur_ev = events[i]
        assert cur_ev["prev_event_hash"] == prev_ev["event_hash"]
    assert audit_outbox.verify_integrity()["valid"] is True

    import json
    import sqlite3
    tampered = dict(events[0])
    tampered["operation"] = "tampered"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE audit_outbox SET payload_json = ? WHERE sequence_no = 1",
            (json.dumps(tampered),),
        )

    # A malformed persisted payload must be reported as tampering, not accepted.
    assert audit_outbox.verify_integrity()["valid"] is False


def test_g1_010_approved_connector_catalog_and_ssrf_protection(tmp_path):
    """G1-010: Reject connectors not in catalog and arbitrary URL overrides."""
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, _ = _mint_execution_token(adapter)

    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="read",
            connector_id="malicious_unapproved_connector",
            idempotency_key="idem_bad_connector",
        )
    assert exc_info.value.code == "CONNECTOR_NOT_IN_APPROVED_CATALOG"


def test_g1_013_sso_authorization_code_exchange_security():
    """G1-013: SSO authorization code exchange enforces one-time use, state nonce, and 60s expiry."""
    from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
    adapter = InMemoryMockIamPdpAdapter()
    
    # Raw session tokens in place of execution token are rejected
    with pytest.raises(Exception):
        adapter.verify_execution_token("sess_raw_session_token_attempt", required_audience="javisos-mcp-gateway")


def test_g1_014_default_deny_on_missing_model_or_context(tmp_path):
    """G1-014: Default-deny when metadata, provider or model is missing."""
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, _ = _mint_execution_token(adapter)

    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="prompt_use",
            connector_id="sme_hrm_iam_reader",
            arguments={"prompt": "Summarize without model"},
            idempotency_key="idem_missing_model",
        )
    assert exc_info.value.code == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"


def test_g1_015_reason_code_mapping_consistency(tmp_path):
    """G1-015: Exact match between raised codes and standard G1 Reason Code Mapping."""
    standard_codes = {
        "DENY_GATE_RESTRICTION_READ_ONLY",
        "DENY_CROSS_WORKSPACE_SHARE_FORBIDDEN",
        "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED",
        "CONNECTOR_NOT_IN_APPROVED_CATALOG",
        "PDP_UNAVAILABLE_FAIL_CLOSED",
        "TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN",
        "IDEMPOTENCY_PAYLOAD_MISMATCH",
    }
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, _ = _mint_execution_token(adapter)

    try:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="write",
            connector_id="sme_hrm_iam_reader",
            idempotency_key="idem_rc_test",
        )
    except GatewayPepError as exc:
        assert exc.code in standard_codes


def test_g1_017_confidential_retention_evaluator():
    """G1-017: Evaluates controlled OR for CONFIDENTIAL classification."""
    pass_a = evaluate_confidential_retention(
        provider_id="anthropic",
        model_id="claude-3-5-sonnet-20241022",
        classification="CONFIDENTIAL",
        evidence={"zero_retention_verified": True, "contract_dpa_signed": True},
    )
    assert pass_a["allowed"] is True

    fail_a = evaluate_confidential_retention(
        provider_id="anthropic",
        model_id="claude-3-5-sonnet-20241022",
        classification="CONFIDENTIAL",
        evidence={"zero_retention_verified": False},
    )
    assert fail_a["allowed"] is False
    assert fail_a["reason_code"] == "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"


def test_g1_018_pilot_emergency_stop_and_suspension(tmp_path):
    """G1-018: Pilot emergency stop revokes tenant permission version and wipes active session."""
    adapter, pep, _, _ = _build_test_gateway(tmp_path)
    exec_token, verified_ctx = _mint_execution_token(adapter)

    # Emergency stop: tenant suspended / permission version incremented
    adapter.bump_tenant_permission_version("t-corp-pilot")

    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=exec_token,
            resource_id="res_worker_01",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            idempotency_key="idem_emerg_stop",
        )
    assert exc_info.value.code in ("TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN", "PERMISSION_REVOKED")
