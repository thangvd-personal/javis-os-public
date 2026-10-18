"""Task 2: P0 — Authorization Code & Session Security Matrix (`SSO-SEC-002` & `SSO-SEC-003`).

Verifies replay protection, TTL expiration, client credentials, redirect URI binding,
atomic concurrency race condition prevention, and session cookie security guarantees.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from starlette.testclient import TestClient

from javisos.server.main import app, _SSO_STATES, _SME_SESSIONS


def _get_live_auth_code(username: str | None = None, state: str = "test_sec_state") -> str:
    """Helper to obtain a live one-time authorization code from SME_BTOS container."""
    user = username or os.environ.get("G1_SSO_TEST_USERNAME")
    password = os.environ.get("G1_SSO_TEST_PASSWORD")
    if not user or not password:
        raise ValueError(
            "G1_SSO_TEST_USERNAME and G1_SSO_TEST_PASSWORD environment variables are required. Hardcoded fallbacks are strictly prohibited."
        )
    sme_btos_url = os.getenv("SME_BTOS_BASE_URL", "http://localhost:8000").rstrip("/")


    auth_payload = urllib.parse.urlencode({
        "client_id": "javisos-sso-client",
        "redirect_uri": "http://localhost:7777/auth/sme-callback",
        "state": state,
        "username": user,
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
    try:
        opener.open(auth_req, timeout=5)
        raise RuntimeError("Expected 302 redirect from SME_BTOS authorize")
    except urllib.error.HTTPError as exc:
        if exc.code in (302, 303):
            loc = exc.headers.get("Location", "")
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
            return qs["code"][0]
        raise RuntimeError(f"Unexpected status from authorize: {exc.code}") from exc


def _exchange_code_direct(
    code: str,
    client_id: str = "javisos-sso-client",
    client_secret: str = "dev-sso-client-secret",
    redirect_uri: str = "http://localhost:7777/auth/sme-callback",
    headers_extra: dict[str, str] | None = None,
) -> tuple[int, dict]:
    """Helper to directly call POST /api/v1/iam/sso/token on live SME_BTOS container."""
    sme_btos_url = os.getenv("SME_BTOS_BASE_URL", "http://localhost:8000").rstrip("/")
    url = f"{sme_btos_url}/api/v1/iam/sso/token"

    payload = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-SSO-Client-Id": client_id,
        "X-SSO-Client-Secret": client_secret,
    }
    if headers_extra:
        headers.update(headers_extra)

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", "ignore")
        try:
            data = json.loads(err_body)
        except Exception:
            data = {"raw": err_body}
        return exc.code, data


# ==============================================================================
# SSO-SEC-002: Authorization Code Security Matrix
# ==============================================================================


def test_sec_002_code_replay_attack_rejected():
    """Case 1: Single-use code consumption. Replay attempt MUST return 400."""
    code = _get_live_auth_code(state="sec_replay_state")
    assert code.startswith("sso_code_")

    # Hop 1: First exchange succeeds
    status_1, data_1 = _exchange_code_direct(code)
    assert status_1 == 200
    assert "session_token" in data_1

    # Hop 1 (Replay): Second exchange MUST be rejected
    status_2, data_2 = _exchange_code_direct(code)
    assert status_2 == 400
    assert data_2.get("detail") == "INVALID_OR_CONSUMED_CODE"


def test_sec_002_code_expiration_60s():
    """Case 2: Code expired past 60s TTL is rejected with CODE_EXPIRED."""
    code = _get_live_auth_code(state="sec_expiry_state")
    assert code.startswith("sso_code_")

    # Verify code with test clock offset simulating elapsed 65 seconds
    status, data = _exchange_code_direct(
        code,
        headers_extra={"X-Test-Clock-Offset": "65"},
    )
    assert status == 400
    assert data.get("detail") == "CODE_EXPIRED"




def test_sec_002_client_credentials_authentication():
    """Case 3: Missing or invalid SSO client credentials return 401."""
    code = _get_live_auth_code(state="sec_creds_state")

    # Missing Secret
    status_missing, data_missing = _exchange_code_direct(
        code,
        headers_extra={"X-SSO-Client-Secret": ""},
    )
    assert status_missing == 401
    assert data_missing.get("detail") == "MISSING_SSO_CLIENT_CREDENTIALS"

    # Wrong Secret
    status_wrong, data_wrong = _exchange_code_direct(
        code,
        client_secret="attacker_fake_secret_key",
    )
    assert status_wrong == 401
    assert data_wrong.get("detail") == "INVALID_CLIENT_CREDENTIALS"


def test_sec_002_redirect_uri_mismatch():
    """Case 4: Mismatched redirect_uri between authorize and exchange returns 400."""
    code = _get_live_auth_code(state="sec_uri_state")

    status, data = _exchange_code_direct(
        code,
        redirect_uri="http://attacker-controlled.site/steal-code",
    )
    assert status == 400
    assert data.get("detail") == "CLIENT_OR_REDIRECT_URI_MISMATCH"


def test_sec_002_concurrency_atomic_single_use():
    """Case 5: 5 concurrent threads attempting to exchange the SAME code.

    Atomic thread-safe locking in SME_BTOS must ensure EXACTLY 1 succeeds (200),
    and all 4 remaining threads fail (400 INVALID_OR_CONSUMED_CODE).
    """
    code = _get_live_auth_code(state="sec_concurrent_state")
    assert code.startswith("sso_code_")

    def _attempt_exchange():
        return _exchange_code_direct(code)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_attempt_exchange) for _ in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    statuses = [r[0] for r in results]
    success_count = statuses.count(200)
    failed_count = statuses.count(400)

    assert success_count == 1, f"Expected exactly 1 success, got {success_count} (results: {statuses})"
    assert failed_count == 4, f"Expected exactly 4 failures, got {failed_count} (results: {statuses})"

    # All 4 failures must receive INVALID_OR_CONSUMED_CODE
    for status, data in results:
        if status != 200:
            assert data.get("detail") == "INVALID_OR_CONSUMED_CODE"


# ==============================================================================
# SSO-SEC-003: Session & State Security Matrix
# ==============================================================================


def test_sec_003_state_nonce_mismatch_and_expiry():
    """Case 1: Unknown or expired state nonce is immediately rejected."""
    client = TestClient(app, follow_redirects=False)

    # State not in _SSO_STATES
    resp = client.get("/auth/sme-callback?code=dummy_code&state=non_existent_state_nonce")
    assert resp.status_code == 400
    assert "STATE_MISMATCH_OR_EXPIRED" in resp.text

    # State expired past 300s
    expired_state = "expired_test_state_123"
    _SSO_STATES[expired_state] = time.time() - 305
    resp_exp = client.get(f"/auth/sme-callback?code=dummy_code&state={expired_state}")
    assert resp_exp.status_code == 400
    assert "STATE_MISMATCH_OR_EXPIRED" in resp_exp.text


def test_sec_003_state_nonce_replay_attack_rejected():
    """Case 2: State nonce replay attempt fails because state is popped on first use."""
    client = TestClient(app, follow_redirects=False)

    # Initiate state
    start_resp = client.get("/auth/sme-sso/start")
    assert start_resp.status_code == 200
    state = start_resp.json()["state"]

    # First attempt: state is popped from store
    code_1 = _get_live_auth_code(state=state)
    resp_1 = client.get(f"/auth/sme-callback?code={urllib.parse.quote(code_1)}&state={urllib.parse.quote(state)}")
    assert resp_1.status_code == 302

    # Second attempt: using the same state (replay attack)
    code_2 = _get_live_auth_code(state=state)
    resp_2 = client.get(f"/auth/sme-callback?code={urllib.parse.quote(code_2)}&state={urllib.parse.quote(state)}")
    assert resp_2.status_code == 400
    assert "STATE_MISMATCH_OR_EXPIRED" in resp_2.text


def test_sec_003_cookie_opacity_and_flags():
    """Case 3: Session cookie is opaque and carries HttpOnly, SameSite=lax, and Path=/."""
    client = TestClient(app, follow_redirects=False)

    start_resp = client.get("/auth/sme-sso/start")
    state = start_resp.json()["state"]

    code = _get_live_auth_code(state=state)
    cb_resp = client.get(f"/auth/sme-callback?code={urllib.parse.quote(code)}&state={urllib.parse.quote(state)}")
    assert cb_resp.status_code == 302

    set_cookie = cb_resp.headers.get("Set-Cookie", "")
    assert "sme_session_id=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "path=/" in set_cookie.lower()

    # Verify cookie value does not reveal underlying raw secrets
    cookie_val = cb_resp.cookies.get("sme_session_id", "")
    assert cookie_val.startswith("sme_sess_")
    assert len(cookie_val) >= 20
    assert "exec_v1" not in cookie_val
    assert "dev-sso-client-secret" not in cookie_val
    assert "AliceCorp" not in cookie_val
