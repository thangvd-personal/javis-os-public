"""Task 1: P0 — End-to-End Live SSO Integration Test (`SSO-INT-001`).

Proves unmocked 2-hop SSO token exchange with live SME_BTOS container,
session & cookie security compliance, and Gateway PEP authorization using HttpIamPdpAdapter.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
from starlette.testclient import TestClient

from javisos.server.main import app, _SME_SESSIONS, _SSO_STATES
from javisos.server.iam_contract.http_adapter import HttpIamPdpAdapter
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog
from javisos.server.mcp_gateway.pep_service import McpGatewayPepService


def test_g1_sso_live_end_to_end_unmocked(tmp_path):
    """Verify full SSO lifecycle with live SME_BTOS backend without mocking token exchange."""
    client = TestClient(app, follow_redirects=False)

    # 1. Start SSO flow on JavisOS
    start_resp = client.get("/auth/sme-sso/start")
    assert start_resp.status_code == 200, f"Failed /auth/sme-sso/start: {start_resp.text}"
    start_data = start_resp.json()
    assert start_data.get("ok") is True
    state = start_data["state"]
    assert state in _SSO_STATES

    # Credentials strictly from environment per Rule 4, no hardcoded fallbacks in repository
    username = os.environ.get("G1_SSO_TEST_USERNAME")
    password = os.environ.get("G1_SSO_TEST_PASSWORD")
    if not username or not password:
        raise ValueError(
            "G1_SSO_TEST_USERNAME and G1_SSO_TEST_PASSWORD environment variables are required. Hardcoded fallbacks are strictly prohibited."
        )
    sme_btos_url = os.getenv("SME_BTOS_BASE_URL", "http://localhost:8000").rstrip("/")


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
            return None  # Do not follow redirect automatically

    opener = urllib.request.build_opener(NoRedirectHandler)
    code = None
    try:
        resp = opener.open(auth_req, timeout=5)
        # In case it returned 200 with HTML or JSON
        assert False, f"Expected 302 redirect from SME_BTOS authorize, got HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        assert exc.code in (302, 303), f"Expected 302 redirect, got HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')}"
        location = exc.headers.get("Location")
        assert location, "Missing Location header on 302 redirect"
        parsed_url = urllib.parse.urlparse(location)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        assert "code" in query_params, f"No authorization code in redirect Location: {location}"
        assert query_params.get("state", [""])[0] == state, "Returned state does not match original state nonce"
        code = query_params["code"][0]

    assert code and code.startswith("sso_code_"), "Invalid authorization code format"

    # 3. Complete callback on JavisOS (WITHOUT any mocks on _exchange_... hops)
    callback_resp = client.get(f"/auth/sme-callback?code={urllib.parse.quote(code)}&state={urllib.parse.quote(state)}")
    assert callback_resp.status_code == 302, f"Callback failed: {callback_resp.text}"

    # 4. Validate Location Header & Cookie Security (Constraints 4 & 5)
    location_header = callback_resp.headers.get("Location", "")
    assert location_header == "/?auth=success", f"Unexpected redirect target: {location_header}"
    # Verify zero secret leakage in URL or headers
    assert "token" not in location_header.lower()
    assert "code" not in location_header.lower()
    assert "secret" not in location_header.lower()

    # Verify session cookie properties
    set_cookie_header = callback_resp.headers.get("Set-Cookie", "")
    assert "sme_session_id=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header
    assert "SameSite=lax" in set_cookie_header.lower() or "samesite=lax" in set_cookie_header.lower()

    # Extract opaque session id
    cookie_str = callback_resp.headers["set-cookie"]
    sme_session_id = None
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("sme_session_id="):
            sme_session_id = part.split("=")[1]
            break

    assert sme_session_id and sme_session_id.startswith("sme_sess_"), "Session cookie must be an opaque identifier"
    # Ensure tokens are not leaked inside the cookie value itself
    assert "exec_v1" not in sme_session_id
    assert "iam.session" not in sme_session_id

    # 5. Verify Server-Side Session Custody
    assert sme_session_id in _SME_SESSIONS, "Session was not stored in server-side session custody"
    session_data = _SME_SESSIONS[sme_session_id]
    assert session_data.get("username") == username
    real_execution_token = session_data.get("execution_token")
    assert real_execution_token and real_execution_token.startswith("exec_v1."), "Invalid execution token in session store"

    # 6. Prove Real Gateway Acceptance with HttpIamPdpAdapter (Constraint 2)
    http_adapter = HttpIamPdpAdapter(base_url=f"{sme_btos_url}/api/v1/iam-pdp-contract/v1")
    catalog = ApprovedConnectorCatalog()
    catalog.register_connector(
        connector_id="sme_hrm_iam_reader",
        server_credential="sme-secret-key-live",
        handler=lambda args, cred: {"ok": True, "worker_id": args.get("worker_id"), "retrieved": True},
    )
    audit_db = tmp_path / "audit_live.db"
    audit_outbox = DurableAuditOutbox(db_path=audit_db, stream_id="sso_live_test_stream")


    pep = McpGatewayPepService(
        adapter=http_adapter,
        catalog=catalog,
        audit_outbox=audit_outbox,
        required_audience="javisos-mcp-gateway",
        read_only_gate=False,
    )

    # Perform enterprise read action using the live execution token minted via real SSO
    idem_key = f"idem_live_sso_{int(time.time() * 1000)}"
    exec_result = pep.execute_enterprise_action(
        execution_token=real_execution_token,
        resource_id="res_worker_01",
        operation="read",
        connector_id="sme_hrm_iam_reader",
        arguments={"worker_id": "res_worker_01"},
        idempotency_key=idem_key,
    )

    assert exec_result.status == "COMPLETED"
    assert exec_result.effect == "ALLOW"
    assert exec_result.output.get("ok") is True
    assert exec_result.output.get("retrieved") is True

    # Confirm audit log records the approved transaction
    audit_events = audit_outbox.list_events()
    assert len(audit_events) >= 3
    decision_events = [e for e in audit_events if e.get("event_type") == "pdp.decision"]
    assert any(e.get("decision_effect") == "ALLOW" and e.get("reason_code") == "OK_GRANTED" for e in decision_events)
