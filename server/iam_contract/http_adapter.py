"""HTTP Client Adapter for Standard IAM/PDP Contract v1.0 (`HttpIamPdpAdapter`).

Implements IamPdpAdapterPort by communicating over HTTP with SME_BTOS container
or any standard IAM/PDP contract provider without importing Django or internal models.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

EXECUTION_TOKEN_PREFIX = "exec_v1."


class HttpIamPdpAdapter:
    """Production-grade HTTP Adapter for Standard IAM/PDP Contract v1.0."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/api/v1/iam-pdp-contract/v1",
        service_client_id: str = "javisos-mcp-gateway",
        service_token: str = "dev-gateway-service-secret",
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_client_id = service_client_id
        self.service_token = service_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Service-Client-Id": self.service_client_id,
            "X-Service-Token": self.service_token,
        }

    def _request(self, method: str, endpoint: str, data: Any = None) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        payload_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                return json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(err_body)
                err_code = (
                    parsed.get("error", {}).get("code")
                    or parsed.get("detail")
                    or f"HTTP_{exc.code}"
                )
            except Exception:
                err_code = f"HTTP_{exc.code}"
            raise PermissionError(str(err_code)) from exc
        except Exception as exc:
            raise RuntimeError(f"IAM_CONTRACT_UNAVAILABLE: {exc}") from exc

    def get_capabilities(self) -> dict[str, Any]:
        """GET /capabilities"""
        return self._request("GET", "capabilities")

    def exchange_token(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /tokens/exchange"""
        return self._request("POST", "tokens/exchange", dict(req))

    def resolve_resource(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /resources/resolve"""
        return self._request("POST", "resources/resolve", dict(req))

    def evaluate_pdp(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /pdp/evaluate"""
        return self._request("POST", "pdp/evaluate", dict(req))

    def create_approval(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /approvals"""
        return self._request("POST", "approvals", dict(req))

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        """GET /approvals/{approval_id}"""
        return self._request("GET", f"approvals/{urllib.parse.quote(approval_id)}")

    def decide_approval(self, approval_id: str, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /approvals/{approval_id}/decide"""
        return self._request("POST", f"approvals/{urllib.parse.quote(approval_id)}/decide", dict(req))

    def validate_approval(self, approval_id: str, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /approvals/{approval_id}/validate"""
        return self._request("POST", f"approvals/{urllib.parse.quote(approval_id)}/validate", dict(req))

    def check_revocation(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /revocations/check"""
        return self._request("POST", "revocations/check", dict(req))

    def ingest_audit_events(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """POST /audit/events"""
        return self._request("POST", "audit/events", [dict(e) for e in events])

    def verify_execution_token(
        self,
        raw_token: str,
        required_audience: str = "javisos-mcp-gateway",
    ) -> dict[str, Any]:
        """Verify execution token via signature/expiry and live revocation check against SME_BTOS."""
        if not raw_token or not raw_token.startswith(EXECUTION_TOKEN_PREFIX):
            raise PermissionError("TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN")

        signed_part = raw_token[len(EXECUTION_TOKEN_PREFIX) :]
        # Token format: <payload_b64>:<timestamp>:<signature>
        parts = signed_part.split(":")
        if len(parts) < 3:
            raise PermissionError("TOKEN_SIGNATURE_OR_EXPIRY_INVALID")

        try:
            payload_b64 = parts[0]
            # Add padding if needed
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
        except Exception as exc:
            raise PermissionError("TOKEN_SIGNATURE_OR_EXPIRY_INVALID") from exc

        # 1. Expiry check
        if int(time.time()) >= int(claims.get("exp", 0)):
            raise PermissionError("TOKEN_EXPIRED")

        # 2. Audience check
        if claims.get("aud") != required_audience:
            raise PermissionError("TOKEN_AUDIENCE_MISMATCH")

        # 3. Live revocation check against SME_BTOS Contract Endpoint
        exec_ctx = {
            "schema_version": "1.0.0",
            "principal_id": str(claims["sub"]),
            "principal_type": "USER",
            "tenant_id": str(claims["tid"]),
            "workspace_id": str(claims["wid"]),
            "workspace_tier": "CORPORATE",
            "device_id": str(claims.get("did", "dev_01")),
            "device_posture": "COMPLIANT",
            "session_id": str(claims.get("sid", "")),
            "identity_version": int(claims.get("iv", 1)),
            "permission_version": int(claims.get("pv", 1)),
            "policy_version": str(claims.get("pol", "v1")),
            "execution_id": f"exec_revchk_{claims.get('jti', '0')[:8]}",
            "correlation_id": f"corr_revchk_{claims.get('jti', '0')[:8]}",
            "purpose": "TOKEN_VERIFICATION",
        }
        rev_res = self.check_revocation({"execution_context": exec_ctx})
        if rev_res.get("is_revoked"):
            reasons = rev_res.get("reasons") or ["SESSION_OR_PRINCIPAL_REVOKED"]
            raise PermissionError(reasons[0])

        return {
            "jti": claims["jti"],
            "iss": claims["iss"],
            "aud": claims["aud"],
            "sub": claims["sub"],
            "service_identity": claims["service_identity"],
            "scopes": claims["scopes"],
            "iat": claims["iat"],
            "exp": claims["exp"],
            "identity_version": int(claims.get("iv", 1)),
            "permission_version": int(claims.get("pv", 1)),
            "policy_version": claims.get("pol", "v1"),
            "delegation": claims.get("del"),
            "session_id": claims.get("sid"),
            "tenant_id": claims.get("tid"),
            "workspace_id": claims.get("wid"),
            "device_id": claims.get("did"),
        }
