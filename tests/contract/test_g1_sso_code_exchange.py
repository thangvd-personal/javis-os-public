"""Tests for JavisOS SSO Start, Callback, and 2-Hop Token Exchange (Task 3, G1-013)."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from javisos.server.main import app

client = TestClient(app)


def test_sme_sso_start_generates_state_and_redirect_url():
    """/auth/sme-sso/start generates random state and constructs authorize URL."""
    resp = client.get("/auth/sme-sso/start")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert len(data["state"]) >= 16
    assert "redirect_url" in data
    assert "client_id=javisos-sso-client" in data["redirect_url"]
    assert f"state={data['state']}" in data["redirect_url"]
    assert "api/v1/iam/sso/authorize" in data["redirect_url"]


def test_sme_callback_rejects_missing_or_invalid_state():
    """/auth/sme-callback rejects request if state nonce does not match stored state."""
    # State not in store
    resp = client.get("/auth/sme-callback?code=some_code&state=invalid_or_expired_state")
    assert resp.status_code == 400
    assert "STATE_MISMATCH_OR_EXPIRED" in resp.text


def test_sme_callback_executes_2_hop_server_to_server_exchange_and_protects_token():
    """/auth/sme-callback validates state, performs 2 server-to-server hops, and stores token server-side."""
    # 1. Start SSO to generate and store valid state
    start_resp = client.get("/auth/sme-sso/start")
    assert start_resp.status_code == 200
    state = start_resp.json()["state"]

    mock_session_token = "sess_raw_mock_9999"
    mock_execution_token = "exec_tok_mock_8888"

    # Mock server-to-server calls:
    # Hop 1: POST to /api/v1/iam/sso/token -> session_token
    # Hop 2: POST to /api/v1/iam-pdp-contract/v1/tokens/exchange -> execution_token
    with patch("javisos.server.main._exchange_sso_code_for_session_token") as mock_hop1, \
         patch("javisos.server.main._exchange_session_token_for_execution_token") as mock_hop2:

        mock_hop1.return_value = {
            "session_token": mock_session_token,
            "username": "alice_corp",
            "tenant_id": "t-corp-pilot",
        }
        mock_hop2.return_value = {
            "issued_token": mock_execution_token,
            "token_type": "BEARER_EXECUTION_TOKEN",
            "sub": "alice_corp",
            "aud": "javisos-mcp-gateway",
        }

        callback_resp = client.get(
            f"/auth/sme-callback?code=sso_one_time_code_123&state={state}",
            follow_redirects=False,
        )

        assert callback_resp.status_code in (200, 302, 303)
        # Hop 1 called with server credentials and code
        mock_hop1.assert_called_once_with(
            code="sso_one_time_code_123",
            client_id="javisos-sso-client",
            redirect_uri="http://localhost:7777/auth/sme-callback",
        )
        # Hop 2 called with session_token
        mock_hop2.assert_called_once_with(session_token=mock_session_token)

        # Critical security check: Never expose token in URL redirect
        location = callback_resp.headers.get("Location", "")
        assert mock_session_token not in location
        assert mock_execution_token not in location
        assert "token" not in location.lower() or "auth=success" in location

        # Check session cookie set
        assert "sme_session_id" in callback_resp.cookies or "sme_session_id" in callback_resp.headers.get("Set-Cookie", "")
