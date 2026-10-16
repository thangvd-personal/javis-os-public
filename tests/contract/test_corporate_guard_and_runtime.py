"""Tests for Task 5: CorporateLocalGuard, LocalVaultLeaseManager, CorporateTaskWorker, and ArtifactLineageTracker."""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter
from javisos.server.corporate_guard import (
    CorporateLocalGuard,
    LocalVaultLeaseManager,
    VaultLockedError,
)
from javisos.server.corporate_runtime import (
    ArtifactLineageTracker,
    CorporateTaskWorker,
)


def test_corporate_guard_disables_brain_fallback_and_blocks_direct_tool_bypass(monkeypatch):
    guard = CorporateLocalGuard(corporate_mode=True)
    with pytest.raises(PermissionError, match="CORPORATE_VAULT_CONTEXT_REQUIRED"):
        guard.resolve_corporate_vault(explicit_vault=None, verified_workspace_id="ws_corp_01")
    with pytest.raises(PermissionError, match="CORPORATE_VAULT_MISMATCH"):
        guard.resolve_corporate_vault(explicit_vault="ws_personal", verified_workspace_id="ws_corp_01")
    with pytest.raises(PermissionError, match="DIRECT_ENTERPRISE_BYPASS_FORBIDDEN"):
        guard.assert_routed_via_gateway(is_corporate=True, via_gateway=False)

    # Also verify mcp_hub.resolve_vault disables _brain_dang_mo() fallback when JAVIS_WORKSPACE_TIER=CORPORATE
    server_dir = str(Path(__file__).resolve().parents[2] / "server")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    import mcp_hub

    monkeypatch.setenv("JAVIS_WORKSPACE_TIER", "CORPORATE")
    vault_root, vault_source, _ = mcp_hub.resolve_vault(None)
    assert vault_root is None
    assert vault_source == "corporate_blocked"


def _seed_mock_adapter() -> InMemoryMockIamPdpAdapter:
    adapter = InMemoryMockIamPdpAdapter()
    adapter.seed_principal(
        principal_id="u_alice",
        tenant_id="t_01",
        workspace_id="ws_corp_01",
        session_token="sess_tok_alice",
        permissions={("res_doc_01", "read"), ("res_doc_01", "write")},
    )
    adapter.seed_resource(
        resource_id="res_doc_01",
        tenant_id="t_01",
        workspace_id="ws_corp_01",
        resource_type="document",
        classification="CONFIDENTIAL",
    )
    return adapter


def test_lease_manager_locks_cache_on_revocation_or_lease_expiry_or_network_failure():
    adapter = _seed_mock_adapter()
    tok = adapter.exchange_token(
        {
            "subject_token": "sess_tok_alice",
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws_corp_01",
            "device_id": "dev_01",
            "correlation_id": "corr_lease_1",
        }
    )
    ctx = {
        **tok["verified_context"],
        "resource_id": "res_doc_01",
        "execution_id": "exec_lease_1",
        "correlation_id": "corr_lease_1",
    }

    lease_mgr = LocalVaultLeaseManager(adapter=adapter, default_lease_seconds=900)
    lease_mgr.store_cached_artifact(
        cache_key="cache_doc_01",
        plaintext=b"top-secret-quarterly-report",
        execution_context=ctx,
        resource_id="res_doc_01",
        retention_seconds=3600,
    )

    # 1. Active lease grants access to encrypted cache item
    assert lease_mgr.read_cached_artifact("cache_doc_01") == b"top-secret-quarterly-report"

    # 2. Simulate network failure on poll/renew after lease expiry -> raises VaultLockedError and wipes RAM key
    lease_mgr.force_expire_lease("cache_doc_01")
    adapter.unavailable_mode = True
    with pytest.raises(VaultLockedError, match="VAULT_LOCKED"):
        lease_mgr.read_cached_artifact("cache_doc_01")
    assert lease_mgr.has_ephemeral_key("cache_doc_01") is False

    # Restore network, re-store artifact, then revoke user role/permission even while retention TTL (1h) is active
    adapter.unavailable_mode = False
    lease_mgr.store_cached_artifact(
        cache_key="cache_doc_02",
        plaintext=b"confidential-strategy",
        execution_context=ctx,
        resource_id="res_doc_01",
        retention_seconds=3600,
    )
    adapter.bump_tenant_permission_version("t_01")
    rev_res = lease_mgr.poll_and_enforce_revocation("cache_doc_02")
    assert rev_res["revoked"] is True
    assert lease_mgr.has_ephemeral_key("cache_doc_02") is False
    with pytest.raises(VaultLockedError, match="VAULT_LOCKED"):
        lease_mgr.read_cached_artifact("cache_doc_02")


def test_worker_revocation_during_downstream_timeout_enters_reconciliation_required():
    adapter = _seed_mock_adapter()
    tok = adapter.exchange_token(
        {
            "subject_token": "sess_tok_alice",
            "subject_token_type": "SESSION_TOKEN",
            "requested_audience": "javisos-mcp-gateway",
            "requested_workspace_id": "ws_corp_01",
            "device_id": "dev_01",
            "correlation_id": "corr_wrk_1",
        }
    )
    ctx = {
        **tok["verified_context"],
        "resource_id": "res_doc_01",
        "execution_id": "exec_wrk_1",
        "correlation_id": "corr_wrk_1",
    }

    worker = CorporateTaskWorker(adapter=adapter)
    task = worker.enqueue_task(
        task_id="task_write_01",
        execution_context=ctx,
        resource_id="res_doc_01",
        operation="read",
        payload={"query": "summary"},
    )

    def _downstream_that_times_out_after_revoke():
        # Mid-flight revocation happens while downstream is in-flight
        adapter.bump_user_identity_version("u_alice")
        worker.request_cancel("task_write_01", reason="PRINCIPAL_REVOKED")
        raise TimeoutError("Downstream connector timed out after request sent")

    result = worker.run_task(
        task_id=task["task_id"],
        downstream_callable=_downstream_that_times_out_after_revoke,
    )
    assert result["status"] == "RECONCILIATION_REQUIRED"
    assert result["retry_scheduled"] is False


def test_lineage_revocation_propagates_to_all_derived_nodes():
    tracker = ArtifactLineageTracker()
    tracker.register_root_resource(
        resource_id="res_doc_01",
        tenant_id="t_01",
        workspace_id="ws_corp_01",
        classification="CONFIDENTIAL",
        policy_version="v1",
    )

    stages = [
        ("extracted_1", "res_doc_01", "extracted_content"),
        ("prompt_1", "extracted_1", "prompt_context"),
        ("output_1", "prompt_1", "output"),
        ("summary_1", "output_1", "summary"),
        ("emb_1", "summary_1", "embedding_index"),
        ("mem_1", "emb_1", "memory"),
        ("cache_1", "mem_1", "cache"),
    ]
    for artifact_id, parent_id, stage in stages:
        tracker.register_derived_artifact(
            artifact_id=artifact_id,
            parent_ids=[parent_id],
            derivation_stage=stage,
            execution_id="exec_lin_01",
        )

    assert tracker.can_access_artifact("cache_1") is True

    revoked_nodes = tracker.propagate_revocation("res_doc_01", reason="SOURCE_PERMISSION_REVOKED")
    assert set(revoked_nodes) == {s[0] for s in stages}

    for artifact_id, _, _ in stages:
        node = tracker.get_node(artifact_id)
        assert node["access_revoked"] is True
        assert node["cleanup_status"] == "cleanup_completed"
        assert tracker.can_access_artifact(artifact_id) is False
        with pytest.raises(PermissionError, match="LINEAGE_ACCESS_REVOKED"):
            tracker.read_for_rag_or_prompt(artifact_id)
