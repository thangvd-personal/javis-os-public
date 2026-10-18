"""Task 3: P0 — Live Gateway Emergency Halt & Revocation Enforcement (`G1-018-LIVE`).

Verifies baseline ALLOW, real SME_BTOS tenant revocation/suspension, fail-closed PDP enforcement,
zero connector invocation after halt, and durable audit outbox evidence capture.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
import urllib.request
import pytest
from starlette.testclient import TestClient

from javisos.server.main import app, _SME_SESSIONS
from javisos.server.iam_contract.http_adapter import HttpIamPdpAdapter
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.pep_service import GatewayPepError, McpGatewayPepService


def _get_live_execution_token(client: TestClient) -> str:
    """Helper to mint a real execution token via unmocked 2-hop SSO flow."""
    username = os.environ.get("G1_SSO_TEST_USERNAME")
    password = os.environ.get("G1_SSO_TEST_PASSWORD")
    if not username or not password:
        raise ValueError(
            "G1_SSO_TEST_USERNAME and G1_SSO_TEST_PASSWORD environment variables are required. Hardcoded fallbacks are strictly prohibited."
        )

    sme_btos_url = os.getenv("SME_BTOS_BASE_URL", "http://localhost:8000").rstrip("/")

    # 1. Start SSO on JavisOS
    start_resp = client.get("/auth/sme-sso/start")
    assert start_resp.status_code == 200
    state = start_resp.json()["state"]

    # 2. Authorize on live SME_BTOS container
    auth_payload = urllib.parse.urlencode({
        "client_id": "javisos-sso-client",
        "redirect_uri": "http://localhost:7777/auth/sme-callback",
        "state": state,
        "username": username,
        "password": password,
        "action": "approve",
    }).encode("utf-8")

    auth_req = urllib.request.Request(
        f"{sme_btos_url}/api/v1/iam/sso/authorize",
        data=auth_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirectHandler)
    code = None
    try:
        opener.open(auth_req, timeout=5)
    except urllib.error.HTTPError as exc:
        if exc.code in (302, 303):
            loc = exc.headers.get("Location", "")
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
            code = qs["code"][0]

    assert code and code.startswith("sso_code_")

    # 3. Callback with unmocked 2-hop exchange
    cb_resp = client.get(f"/auth/sme-callback?code={urllib.parse.quote(code)}&state={urllib.parse.quote(state)}")
    assert cb_resp.status_code == 302

    # 4. Extract execution token from server-side custody
    sme_sess_id = cb_resp.cookies.get("sme_session_id")
    assert sme_sess_id in _SME_SESSIONS
    return _SME_SESSIONS[sme_sess_id]["execution_token"]


def _run_sme_backend_python(code: str) -> None:
    """Execute administrative Python command directly inside live sme_backend container."""
    cmd = ["docker", "exec", "sme_backend", "python", "manage.py", "shell", "-c", code]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def test_g1_emergency_halt_permission_bump_live(tmp_path):
    """G1-018-LIVE Case 1: Tenant permission version bump immediately halts in-flight access."""
    client = TestClient(app, follow_redirects=False)
    sme_btos_url = os.getenv("SME_BTOS_BASE_URL", "http://localhost:8000").rstrip("/")

    # Ensure clean initial state: permission_version = 1
    _run_sme_backend_python(
        "from apps.platform.iam.models import TenantPermissionState; "
        "from apps.platform.tenant.models import Tenant; "
        "from django.core.cache import cache; "
        "from apps.platform.iam.cache import permission_version_cache_key; "
        "t = Tenant.objects.get(code='t-corp-pilot'); "
        "TenantPermissionState.objects.filter(tenant_id=t.id).update(permission_version=1); "
        "cache.set(permission_version_cache_key(t.id), 1, None)"
    )

    try:
        execution_token = _get_live_execution_token(client)
        assert execution_token.startswith("exec_v1.")

        # Spy connector handler to verify connector safety
        handler_calls = []

        def _spy_handler(args, cred):
            handler_calls.append(dict(args))
            return {"ok": True, "worker_id": args.get("worker_id"), "retrieved": True}

        catalog = ApprovedConnectorCatalog()
        catalog.register_connector(
            connector_id="sme_hrm_iam_reader",
            server_credential="sme-secret-key-live",
            handler=_spy_handler,
        )

        audit_db = tmp_path / "audit_halt_perm.db"
        audit_outbox = DurableAuditOutbox(db_path=audit_db, stream_id="halt_perm_stream")
        http_adapter = HttpIamPdpAdapter(base_url=f"{sme_btos_url}/api/v1/iam-pdp-contract/v1")

        pep = McpGatewayPepService(
            adapter=http_adapter,
            catalog=catalog,
            audit_outbox=audit_outbox,
            required_audience="javisos-mcp-gateway",
            read_only_gate=False,
        )

        # ----------------------------------------------------------------------
        # Bước 1 — Baseline ALLOW (Negative Control)
        # ----------------------------------------------------------------------
        baseline_corr_id = f"corr_baseline_{int(time.time() * 1000)}"
        baseline_res = pep.execute_enterprise_action(
            execution_token=execution_token,
            resource_id="res_worker_01",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            arguments={"worker_id": "res_worker_01"},
            idempotency_key="idem_baseline_perm",
            correlation_id=baseline_corr_id,
        )

        assert baseline_res.effect == "ALLOW"
        assert baseline_res.status == "COMPLETED"
        assert baseline_res.output.get("ok") is True
        assert len(handler_calls) == 1, "Connector handler must be invoked exactly once during baseline"

        # Verify audit outbox captured baseline ALLOW
        baseline_events = [e for e in audit_outbox.list_events() if e.get("correlation_id") == baseline_corr_id]
        assert any(e.get("execution_stage") == "DECISION_GRANTED" and e.get("decision_effect") == "ALLOW" for e in baseline_events)
        assert any(e.get("execution_stage") == "DOWNSTREAM_COMMITTED" for e in baseline_events)

        # ----------------------------------------------------------------------
        # Bước 2 — Kích hoạt Emergency Halt / Revocation trên backend SME_BTOS thật
        # ----------------------------------------------------------------------
        _run_sme_backend_python(
            "from apps.platform.tenant.models import Tenant; "
            "from apps.platform.iam.cache import bump_tenant_permission_version; "
            "t = Tenant.objects.get(code='t-corp-pilot'); "
            "bump_tenant_permission_version(t.id)"
        )

        # ----------------------------------------------------------------------
        # Bước 3 & 4 — Gửi lại request với CÙNG execution token & Kiểm tra Chặn
        # ----------------------------------------------------------------------
        post_halt_corr_id = f"corr_halt_perm_{int(time.time() * 1000)}"

        with pytest.raises(GatewayPepError) as exc_info:
            pep.execute_enterprise_action(
                execution_token=execution_token,
                resource_id="res_worker_01",
                operation="read",
                connector_id="sme_hrm_iam_reader",
                arguments={"worker_id": "res_worker_01"},
                idempotency_key="idem_post_halt_perm",
                correlation_id=post_halt_corr_id,
            )

        # Verify exact error reason code per contract
        assert exc_info.value.effect == "DENY"
        assert exc_info.value.code in (
            "PERMISSION_REVOKED",
            "PERMISSION_VERSION_CHANGED",
            "PERMISSION_OR_IDENTITY_VERSION_STALE",
        )

        # Connector Safety: handler invocation count MUST NOT increase
        assert len(handler_calls) == 1, "Security violation: connector handler must NOT be invoked after emergency halt"

        # Audit Evidence Verification
        halt_events = [e for e in audit_outbox.list_events() if e.get("correlation_id") == post_halt_corr_id]
        assert len(halt_events) >= 1
        denied_decision = next((e for e in halt_events if e.get("execution_stage") == "DECISION_DENIED"), None)
        assert denied_decision is not None, "Missing DECISION_DENIED event in audit outbox for halted request"
        assert denied_decision.get("decision_effect") == "DENY"
        assert denied_decision.get("reason_code") in (
            "PERMISSION_REVOKED",
            "PERMISSION_VERSION_CHANGED",
            "PERMISSION_OR_IDENTITY_VERSION_STALE",
        )

        # Confirm downstream was never dispatched for the halted transaction
        assert not any(e.get("execution_stage") in ("DOWNSTREAM_DISPATCHED", "DOWNSTREAM_COMMITTED") for e in halt_events)

    finally:
        # Teardown: Restore permission_version = 1
        _run_sme_backend_python(
            "from apps.platform.iam.models import TenantPermissionState; "
            "from apps.platform.tenant.models import Tenant; "
            "from django.core.cache import cache; "
            "from apps.platform.iam.cache import permission_version_cache_key; "
            "t = Tenant.objects.get(code='t-corp-pilot'); "
            "TenantPermissionState.objects.filter(tenant_id=t.id).update(permission_version=1); "
            "cache.set(permission_version_cache_key(t.id), 1, None)"
        )


def test_g1_emergency_halt_tenant_suspended_live(tmp_path):
    """G1-018-LIVE Case 2: Tenant suspension instantly blocks enterprise action with TENANT_SUSPENDED."""
    client = TestClient(app, follow_redirects=False)
    sme_btos_url = os.getenv("SME_BTOS_BASE_URL", "http://localhost:8000").rstrip("/")

    # Ensure initial tenant status is ACTIVE
    _run_sme_backend_python(
        "from apps.platform.tenant.models import Tenant; "
        "Tenant.objects.filter(code='t-corp-pilot').update(status=Tenant.Status.ACTIVE)"
    )

    try:
        execution_token = _get_live_execution_token(client)
        handler_calls = []

        catalog = ApprovedConnectorCatalog()
        catalog.register_connector(
            connector_id="sme_hrm_iam_reader",
            server_credential="sme-secret-key-live",
            handler=lambda args, cred: handler_calls.append(args) or {"ok": True},
        )

        audit_db = tmp_path / "audit_halt_tenant.db"
        audit_outbox = DurableAuditOutbox(db_path=audit_db, stream_id="halt_tenant_stream")
        http_adapter = HttpIamPdpAdapter(base_url=f"{sme_btos_url}/api/v1/iam-pdp-contract/v1")

        pep = McpGatewayPepService(
            adapter=http_adapter,
            catalog=catalog,
            audit_outbox=audit_outbox,
            required_audience="javisos-mcp-gateway",
            read_only_gate=False,
        )

        # 1. Baseline ALLOW
        baseline_corr = f"corr_tenant_base_{int(time.time() * 1000)}"
        res_ok = pep.execute_enterprise_action(
            execution_token=execution_token,
            resource_id="res_worker_01",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            idempotency_key="idem_tenant_base",
            correlation_id=baseline_corr,
        )
        assert res_ok.effect == "ALLOW"
        assert len(handler_calls) == 1

        # 2. Suspend Tenant on SME_BTOS backend
        _run_sme_backend_python(
            "from apps.platform.tenant.models import Tenant; "
            "Tenant.objects.filter(code='t-corp-pilot').update(status=Tenant.Status.SUSPENDED)"
        )

        # 3. Re-execute action with SAME token
        halt_corr = f"corr_tenant_halt_{int(time.time() * 1000)}"
        with pytest.raises(GatewayPepError) as exc_info:
            pep.execute_enterprise_action(
                execution_token=execution_token,
                resource_id="res_worker_01",
                operation="read",
                connector_id="sme_hrm_iam_reader",
                idempotency_key="idem_tenant_halt",
                correlation_id=halt_corr,
            )

        assert exc_info.value.effect == "DENY"
        assert exc_info.value.code in ("TENANT_SUSPENDED", "DENY_TENANT_INACTIVE", "SESSION_OR_PRINCIPAL_REVOKED")
        assert len(handler_calls) == 1, "Connector handler must NOT be invoked when tenant is suspended"

        # Verify Audit Outbox evidence
        events = [e for e in audit_outbox.list_events() if e.get("correlation_id") == halt_corr]
        denied_ev = next((e for e in events if e.get("execution_stage") == "DECISION_DENIED"), None)
        assert denied_ev is not None
        assert denied_ev.get("decision_effect") == "DENY"

    finally:
        # Restore tenant status to ACTIVE
        _run_sme_backend_python(
            "from apps.platform.tenant.models import Tenant; "
            "Tenant.objects.filter(code='t-corp-pilot').update(status=Tenant.Status.ACTIVE)"
        )


def test_g1_emergency_halt_fail_closed_on_network_down(tmp_path):
    """G1-018-LIVE Case 3: Gateway fail-closed guarantee when PDP is unreachable."""
    client = TestClient(app, follow_redirects=False)

    execution_token = _get_live_execution_token(client)
    handler_calls = []

    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="sme_hrm_iam_reader",
        server_credential="sme-secret-key-live",
        handler=lambda args, cred: handler_calls.append(args) or {"ok": True},
    )

    audit_db = tmp_path / "audit_fail_closed.db"
    audit_outbox = DurableAuditOutbox(db_path=audit_db, stream_id="fail_closed_stream")

    # Point to an unreachable port to simulate complete network partition / outage
    dead_adapter = HttpIamPdpAdapter(base_url="http://localhost:59999/api/v1/iam-pdp-contract/v1", timeout=1.0)

    pep = McpGatewayPepService(
        adapter=dead_adapter,
        catalog=catalog,
        audit_outbox=audit_outbox,
        required_audience="javisos-mcp-gateway",
        read_only_gate=False,
    )

    fail_corr = f"corr_fail_closed_{int(time.time() * 1000)}"
    with pytest.raises(GatewayPepError) as exc_info:
        pep.execute_enterprise_action(
            execution_token=execution_token,
            resource_id="res_worker_01",
            operation="read",
            connector_id="sme_hrm_iam_reader",
            idempotency_key="idem_fail_closed",
            correlation_id=fail_corr,
        )

    # Must fail-closed with PDP_UNAVAILABLE effect
    assert exc_info.value.effect in ("PDP_UNAVAILABLE", "DENY")
    assert len(handler_calls) == 0, "Handler must never be invoked when PDP is unreachable"

    events = [e for e in audit_outbox.list_events() if e.get("correlation_id") == fail_corr]
    assert any(e.get("execution_stage") == "DECISION_DENIED" for e in events)
