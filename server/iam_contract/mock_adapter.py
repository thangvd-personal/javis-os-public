"""In-Memory Mock IAM/PDP Adapter implementing Standard IAM/PDP Contract v1.0 for G0-025."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from javisos.server.iam_contract.jcs import compute_jcs_sha256
from javisos.server.iam_contract.schemas import validate_contract_payload


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InMemoryMockIamPdpAdapter:
    """Reference In-Memory Adapter proving contract neutrality and substitutability (G0-025)."""

    def __init__(self, adapter_id: str = "in-memory-mock-adapter") -> None:
        self.adapter_id = adapter_id
        self._lock = threading.Lock()
        self.unavailable_mode: bool = False
        self.read_only_gate: bool = False
        self.principals: dict[str, dict[str, Any]] = {}
        self.sessions_by_token: dict[str, dict[str, Any]] = {}
        self.execution_tokens: dict[str, dict[str, Any]] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self.delegations: dict[str, dict[str, Any]] = {}
        self.tenant_permission_versions: dict[str, int] = {}
        self.tenant_policy_versions: dict[str, str] = {}
        self.audit_streams: dict[str, list[dict[str, Any]]] = {}
        self.custom_obligations: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.model_allowlist: set[tuple[str, str]] = {
            ("internal", "javis-corp-llm-v1"),
            ("anthropic", "claude-3-5-sonnet-enterprise"),
        }

    # --- Test Seeding Helpers ---

    def seed_principal(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        workspace_id: str,
        session_token: str,
        permissions: set[tuple[str, str]] | None = None,
        requires_approval: set[tuple[str, str]] | None = None,
        data_scope: str = "TENANT",
        org_unit_id: str | None = None,
        allowed_org_units: set[str] | None = None,
        status: str = "ACTIVE",
        identity_version: int = 1,
        permission_version: int = 1,
        device_posture: str = "COMPLIANT",
    ) -> None:
        self.tenant_permission_versions.setdefault(str(tenant_id), permission_version)
        self.tenant_policy_versions.setdefault(str(tenant_id), f"v{permission_version}")
        self.principals[principal_id] = {
            "principal_id": principal_id,
            "tenant_id": str(tenant_id),
            "workspace_id": workspace_id,
            "permissions": set(permissions or set()),
            "requires_approval": set(requires_approval or set()),
            "data_scope": data_scope,
            "org_unit_id": org_unit_id,
            "allowed_org_units": set(allowed_org_units or ({org_unit_id} if org_unit_id else set())),
            "status": status,
            "identity_version": identity_version,
            "device_posture": device_posture,
        }
        self.sessions_by_token[session_token] = {
            "session_id": f"sess_{uuid.uuid4().hex[:10]}",
            "principal_id": principal_id,
            "tenant_id": str(tenant_id),
            "workspace_id": workspace_id,
            "status": "ACTIVE",
            "identity_version": identity_version,
            "permission_version": self.tenant_permission_versions[str(tenant_id)],
        }

    def seed_resource(
        self,
        *,
        resource_id: str,
        tenant_id: str,
        workspace_id: str,
        resource_type: str = "document",
        classification: str = "INTERNAL",
        owner_principal_id: str | None = None,
        org_unit_id: str | None = None,
        storage_locator: str = "vault://corp/default",
        revoked: bool = False,
    ) -> None:
        self.resources[resource_id] = {
            "resource_id": resource_id,
            "tenant_id": str(tenant_id),
            "workspace_id": workspace_id,
            "resource_type": resource_type,
            "classification": classification,
            "owner_principal_id": owner_principal_id,
            "org_unit_id": org_unit_id,
            "storage_locator": storage_locator,
            "revoked": revoked,
        }

    def seed_delegation(
        self,
        *,
        delegation_id: str,
        tenant_id: str,
        delegator_id: str,
        delegatee_id: str,
        scopes: list[str],
        status: str = "ACTIVE",
        expired: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.delegations[delegation_id] = {
            "delegation_id": delegation_id,
            "tenant_id": str(tenant_id),
            "delegator_id": delegator_id,
            "delegatee_id": delegatee_id,
            "scopes": list(scopes),
            "status": status,
            "effective_from": _iso_utc(now - timedelta(hours=2)),
            "effective_until": _iso_utc(now - timedelta(minutes=5) if expired else now + timedelta(hours=4)),
            "expired": expired,
        }

    def bump_tenant_permission_version(self, tenant_id: str) -> int:
        tid = str(tenant_id)
        new_ver = self.tenant_permission_versions.get(tid, 1) + 1
        self.tenant_permission_versions[tid] = new_ver
        self.tenant_policy_versions[tid] = f"v{new_ver}"
        return new_ver

    def bump_user_identity_version(self, principal_id: str) -> int:
        p = self.principals[principal_id]
        p["identity_version"] += 1
        return p["identity_version"]

    def revoke_session(self, session_token: str) -> None:
        if session_token in self.sessions_by_token:
            self.sessions_by_token[session_token]["status"] = "REVOKED"

    # --- Standard Contract Endpoints ---

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "contract_version": "1.0.0",
            "adapter_id": self.adapter_id,
            "provider_name": "In-Memory Mock IAM/PDP Reference Verifier",
            "supported_features": {
                "token_exchange": True,
                "pdp_evaluate": True,
                "action_approvals": True,
                "revocation_check": True,
                "revocation_stream": False,
                "audit_ingest": True,
                "device_posture_verification": True,
                "delegation_evaluation": True,
            },
            "declared_gaps": [],
        }

    def exchange_token(self, req: Mapping[str, Any]) -> dict[str, Any]:
        subject_token = str(req.get("subject_token", ""))
        requested_audience = str(req.get("requested_audience", ""))
        requested_workspace_id = req.get("requested_workspace_id")
        device_id = str(req.get("device_id") or "dev_default_01")
        correlation_id = str(req.get("correlation_id") or f"corr_{uuid.uuid4().hex[:8]}")

        if subject_token.startswith("hub_") or req.get("subject_token_type") == "PERSONAL_TOKEN":
            raise PermissionError("DENY_PERSONAL_TOKEN_IN_CORPORATE")

        if requested_audience not in ("javisos-mcp-gateway", "javisos-corporate-runtime"):
            raise PermissionError("TOKEN_AUDIENCE_INVALID")

        sess = self.sessions_by_token.get(subject_token)
        if not sess or sess["status"] != "ACTIVE":
            raise PermissionError("TOKEN_REVOKED_OR_INVALID")

        principal = self.principals.get(sess["principal_id"])
        if not principal or principal["status"] != "ACTIVE":
            raise PermissionError("PRINCIPAL_INACTIVE")

        if principal.get("device_posture") == "NON_COMPLIANT":
            raise PermissionError("DEVICE_NON_COMPLIANT")

        tenant_id = principal["tenant_id"]
        current_perm_ver = self.tenant_permission_versions.get(tenant_id, 1)
        if sess["permission_version"] != current_perm_ver or sess["identity_version"] != principal["identity_version"]:
            raise PermissionError("TOKEN_VERSION_STALE")

        workspace_id = principal["workspace_id"]
        if requested_workspace_id and requested_workspace_id != workspace_id:
            raise PermissionError("WORKSPACE_MISMATCH")

        now = datetime.now(timezone.utc)
        exp_dt = now + timedelta(minutes=15)
        jti = f"jti_{uuid.uuid4().hex}"
        issued_token = f"exec_tok_{uuid.uuid4().hex}"

        granted_scopes = sorted(f"{r}:{op}" for (r, op) in principal["permissions"])
        requested_scopes = req.get("requested_scopes")
        if requested_scopes:
            granted_scopes = [s for s in requested_scopes if s in granted_scopes]

        policy_version = self.tenant_policy_versions.get(tenant_id, f"v{current_perm_ver}")
        purpose = str(req.get("purpose") or "INTERACTIVE_CHAT")
        verified_context = {
            "schema_version": "1.0.0",
            "principal_id": principal["principal_id"],
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "workspace_tier": "CORPORATE",
            "device_id": device_id,
            "device_posture": principal.get("device_posture", "COMPLIANT"),
            "session_id": sess["session_id"],
            "identity_version": principal["identity_version"],
            "permission_version": current_perm_ver,
            "policy_version": policy_version,
            "delegated_by": req.get("delegated_by"),
            "delegation_id": req.get("delegation_id"),
            "resource_id": None,
            "execution_id": f"exec_{uuid.uuid4().hex[:10]}",
            "correlation_id": correlation_id,
            "initiated_at": _iso_utc(now),
            "purpose": purpose,
        }
        validate_contract_payload("execution-context.schema.json", verified_context)

        response = {
            "verified_context": verified_context,
            "issued_token": issued_token,
            "token_type": "BEARER_EXECUTION_TOKEN",
            "jti": jti,
            "iss": "mock-iam-pdp-adapter",
            "aud": requested_audience,
            "sub": principal["principal_id"],
            "service_identity": requested_audience,
            "scopes": granted_scopes,
            "iat": int(now.timestamp()),
            "exp": int(exp_dt.timestamp()),
            "expires_at": _iso_utc(exp_dt),
            "identity_version": principal["identity_version"],
            "permission_version": current_perm_ver,
            "policy_version": policy_version,
            "delegation": None,
        }
        self.execution_tokens[issued_token] = {
            **response,
            "source_session_token": subject_token,
            "revoked": False,
        }
        return response

    def verify_execution_token(
        self,
        raw_token: str,
        required_audience: str = "javisos-mcp-gateway",
    ) -> dict[str, Any]:
        tok = self.execution_tokens.get(raw_token)
        if not tok:
            raise PermissionError("TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN")
        if tok.get("revoked"):
            raise PermissionError("TOKEN_REVOKED")
        if tok["aud"] != required_audience:
            raise PermissionError("TOKEN_AUDIENCE_MISMATCH")
        if int(time.time()) >= tok["exp"]:
            raise PermissionError("TOKEN_EXPIRED")

        sess = self.sessions_by_token.get(tok["source_session_token"])
        principal = self.principals.get(tok["sub"])
        if not sess or sess["status"] != "ACTIVE" or not principal or principal["status"] != "ACTIVE":
            raise PermissionError("SESSION_OR_PRINCIPAL_REVOKED")
        tid = principal["tenant_id"]
        if (
            principal["identity_version"] != tok["identity_version"]
            or self.tenant_permission_versions.get(tid, 1) != tok["permission_version"]
        ):
            raise PermissionError("PERMISSION_OR_IDENTITY_VERSION_STALE")
        return tok

    def resolve_resource(self, req: Mapping[str, Any]) -> dict[str, Any]:
        resource_id = str(req.get("resource_id", ""))
        if ".." in resource_id or "/" in resource_id or "\\" in resource_id:
            raise ValueError("RESOURCE_PATH_TRAVERSAL_FORBIDDEN")
        res = self.resources.get(resource_id)
        if not res or res["tenant_id"] != str(req.get("tenant_id")) or res["workspace_id"] != str(req.get("workspace_id")):
            raise LookupError("RESOURCE_NOT_FOUND_OR_CROSS_TENANT")
        return {
            "resource_id": res["resource_id"],
            "resource_type": res["resource_type"],
            "tenant_id": res["tenant_id"],
            "workspace_id": res["workspace_id"],
            "classification": res["classification"],
            "owner_principal_id": res["owner_principal_id"],
            "org_unit_id": res["org_unit_id"],
            "resolved_at": _iso_utc(datetime.now(timezone.utc)),
        }

    def evaluate_pdp(self, req: Mapping[str, Any]) -> dict[str, Any]:
        validate_contract_payload("pdp-evaluate-request.schema.json", req)
        ctx = req["execution_context"]
        res_req = req["resource"]
        operation = req["operation"]
        action_ctx = req.get("action_context") or {}
        now = datetime.now(timezone.utc)
        tid = str(ctx["tenant_id"])
        policy_version = self.tenant_policy_versions.get(tid, ctx.get("policy_version", "v1"))
        resource_id = res_req["resource_id"]

        common = {
            "policy_version": policy_version,
            "identity_version": int(ctx.get("identity_version", 1)),
            "correlation_id": ctx["correlation_id"],
            "execution_id": ctx["execution_id"],
            "principal_id": ctx["principal_id"],
            "tenant_id": tid,
            "workspace_id": ctx.get("workspace_id"),
            "resource_id": resource_id,
            "operation": operation,
            "now": now,
        }

        if self.unavailable_mode:
            return self._build_decision(effect="PDP_UNAVAILABLE", reason_code="PDP_ERROR_FAIL_CLOSED", **common)

        if ".." in resource_id or "/" in resource_id or "\\" in resource_id:
            return self._build_decision(effect="DENY", reason_code="DENY_OPERATION_NOT_PERMITTED_FOR_RESOURCE", **common)

        if ctx.get("workspace_tier") != "CORPORATE":
            return self._build_decision(effect="DENY", reason_code="DENY_PERSONAL_TOKEN_IN_CORPORATE", **common)

        if ctx.get("device_posture") == "NON_COMPLIANT":
            return self._build_decision(effect="DENY", reason_code="DENY_DEVICE_POSTURE_UNTRUSTED", **common)

        principal = self.principals.get(ctx["principal_id"])
        if not principal or principal["status"] != "ACTIVE":
            return self._build_decision(effect="DENY", reason_code="DENY_PRINCIPAL_INACTIVE", **common)

        if (
            principal["identity_version"] != ctx["identity_version"]
            or self.tenant_permission_versions.get(tid, 1) != ctx.get("permission_version", 1)
        ):
            return self._build_decision(effect="DENY", reason_code="DENY_IDENTITY_VERSION_STALE", **common)

        if ctx.get("delegation_id") or ctx.get("delegated_by"):
            del_id = ctx.get("delegation_id")
            del_rec = self.delegations.get(str(del_id))
            if (
                not del_rec
                or del_rec["status"] != "ACTIVE"
                or del_rec["expired"]
                or del_rec["tenant_id"] != tid
                or f"{resource_id}:{operation}" not in del_rec["scopes"]
            ):
                return self._build_decision(effect="DENY", reason_code="DENY_DELEGATION_INVALID_OR_EXPIRED", **common)

        reg_res = self.resources.get(resource_id)
        if not reg_res or reg_res.get("revoked"):
            return self._build_decision(effect="DENY", reason_code="DENY_OPERATION_NOT_PERMITTED_FOR_RESOURCE", **common)

        if reg_res["tenant_id"] != tid:
            return self._build_decision(effect="DENY", reason_code="DENY_TENANT_MISMATCH", **common)

        if reg_res["workspace_id"] != ctx["workspace_id"]:
            return self._build_decision(effect="DENY", reason_code="DENY_WORKSPACE_MISMATCH", **common)

        if res_req.get("tenant_id") and str(res_req["tenant_id"]) != reg_res["tenant_id"]:
            return self._build_decision(effect="DENY", reason_code="DENY_TENANT_MISMATCH", **common)

        if self.read_only_gate and operation in ("write", "delete", "share"):
            return self._build_decision(effect="DENY", reason_code="DENY_GATE_RESTRICTION_READ_ONLY", **common)

        if operation == "share" and str(action_ctx.get("target_workspace_id") or "").startswith("ws_personal"):
            return self._build_decision(effect="DENY", reason_code="DENY_CROSS_WORKSPACE_SHARE_FORBIDDEN", **common)

        if operation == "prompt_use":
            provider = str(action_ctx.get("target_provider") or "")
            model = str(action_ctx.get("target_model") or "")
            if (provider, model) not in self.model_allowlist:
                return self._build_decision(effect="DENY", reason_code="DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED", **common)

        if (resource_id, operation) not in principal["permissions"] and ("*", operation) not in principal["permissions"]:
            return self._build_decision(effect="DENY", reason_code="DENY_NO_ROLE_OR_PERMISSION_BINDING", **common)

        scope = principal.get("data_scope", "TENANT")
        if scope == "SELF":
            owner_id = reg_res.get("owner_principal_id") or res_req.get("owner_principal_id")
            if not owner_id:
                return self._build_decision(effect="DENY", reason_code="DENY_DATA_SCOPE_SELF_MISSING_CONTEXT", **common)
            if owner_id != principal["principal_id"]:
                return self._build_decision(effect="DENY", reason_code="DENY_DATA_SCOPE_SELF_MISMATCH", **common)
        elif scope == "ORG_UNIT":
            res_ou = reg_res.get("org_unit_id") or res_req.get("org_unit_id")
            if not res_ou:
                return self._build_decision(effect="DENY", reason_code="DENY_DATA_SCOPE_ORG_UNIT_MISSING_CONTEXT", **common)
            if res_ou not in principal.get("allowed_org_units", set()):
                return self._build_decision(effect="DENY", reason_code="DENY_DATA_SCOPE_ORG_UNIT_MISMATCH", **common)

        if (resource_id, operation) in principal["requires_approval"] or ("*", operation) in principal["requires_approval"]:
            payload_hash = action_ctx.get("payload_hash")
            return self._build_decision(
                effect="APPROVAL_REQUIRED",
                reason_code="APPROVAL_REQUIRED_BY_POLICY",
                approval_requirement={
                    "approval_id": action_ctx.get("approval_id"),
                    "required_tiers": ["PRIMARY"],
                    "separation_of_duties": True,
                    "bound_payload_hash": payload_hash
                    or "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                    "expires_in_seconds": 1800,
                },
                **common,
            )

        obligations = list(self.custom_obligations.get((resource_id, operation), []))
        return self._build_decision(
            effect="ALLOW",
            reason_code="OK_GRANTED",
            obligations=obligations,
            **common,
        )

    def _build_decision(
        self,
        *,
        effect: str,
        reason_code: str,
        policy_version: str,
        identity_version: int,
        correlation_id: str,
        execution_id: str,
        principal_id: str,
        tenant_id: str,
        workspace_id: str | None,
        resource_id: str,
        operation: str,
        now: datetime,
        obligations: list[dict[str, Any]] | None = None,
        approval_requirement: dict[str, Any] | None = None,
        validate_schema: bool = True,
    ) -> dict[str, Any]:
        decision: dict[str, Any] = {
            "schema_version": "1.0.0",
            "decision_id": f"dec_{uuid.uuid4().hex[:12]}",
            "effect": effect,
            "effect_legacy_alias": "REQUIRES_APPROVAL" if effect == "APPROVAL_REQUIRED" else effect,
            "reason_code": reason_code,
            "policy_version": policy_version,
            "identity_version": identity_version,
            "evaluated_at": _iso_utc(now),
            "expires_at": _iso_utc(now + timedelta(minutes=15)),
            "correlation_id": correlation_id,
            "execution_id": execution_id,
            "obligations": obligations or [],
            "approval_requirement": approval_requirement,
            "audit_metadata": {
                "adapter_id": self.adapter_id,
                "principal_id": principal_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "resource_id": resource_id,
                "operation": operation,
            },
        }
        if validate_schema:
            validate_contract_payload("pdp-decision.schema.json", decision)
        return decision

    def create_approval(self, req: Mapping[str, Any]) -> dict[str, Any]:
        ctx = req["execution_context"]
        validate_contract_payload("execution-context.schema.json", ctx)
        if not ctx.get("resource_id"):
            raise ValueError("ExecutionContext.resource_id is required for create_approval")

        now = datetime.now(timezone.utc)
        ttl = int(req.get("expires_in_seconds", 1800))
        approval_id = f"apr_{uuid.uuid4().hex[:12]}"
        tid = str(ctx["tenant_id"])
        policy_version = self.tenant_policy_versions.get(tid, ctx.get("policy_version", "v1"))

        record = {
            "schema_version": "1.0.0",
            "approval_id": approval_id,
            "status": "PENDING",
            "tenant_id": tid,
            "workspace_id": ctx["workspace_id"],
            "requester_id": ctx["principal_id"],
            "approver_ids": {"primary": None, "secondary": None},
            "resource_id": req["resource_id"],
            "operation": req["operation"],
            "payload_hash": req["payload_hash"],
            "idempotency_key": req["idempotency_key"],
            "policy_version": policy_version,
            "separation_of_duties_verified": False,
            "created_at": _iso_utc(now),
            "decided_at": None,
            "expires_at": _iso_utc(now + timedelta(seconds=ttl)),
            "consumed_at": None,
            "revoked_at": None,
            "revoke_reason": None,
            "execution_id": ctx["execution_id"],
            "correlation_id": ctx["correlation_id"],
        }
        validate_contract_payload("approval-record.schema.json", record)
        with self._lock:
            self.approvals[approval_id] = record
        return dict(record)

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        with self._lock:
            rec = self.approvals.get(approval_id)
            if not rec:
                raise LookupError("APPROVAL_NOT_FOUND")
            return dict(rec)

    def decide_approval(self, approval_id: str, req: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            rec = self.approvals.get(approval_id)
            if not rec:
                raise LookupError("APPROVAL_NOT_FOUND")
            approver_id = str(req["approver_principal_id"])
            if approver_id == rec["requester_id"]:
                raise ValueError("APPROVAL_SOD_VIOLATION")

            decision = req["decision"]
            now = datetime.now(timezone.utc)
            if decision == "APPROVE":
                rec["status"] = "APPROVED"
                rec["approver_ids"] = {"primary": approver_id, "secondary": rec["approver_ids"].get("secondary")}
                rec["separation_of_duties_verified"] = True
                rec["decided_at"] = _iso_utc(now)
            elif decision == "REJECT":
                rec["status"] = "REJECTED"
                rec["approver_ids"] = {"primary": approver_id, "secondary": rec["approver_ids"].get("secondary")}
                rec["decided_at"] = _iso_utc(now)
            elif decision == "REVOKE":
                rec["status"] = "REVOKED"
                rec["revoked_at"] = _iso_utc(now)
                rec["revoke_reason"] = req.get("reason")
            validate_contract_payload("approval-record.schema.json", rec)
            return dict(rec)

    def validate_approval(self, approval_id: str, req: Mapping[str, Any]) -> dict[str, Any]:
        ctx = req["execution_context"]
        tid = str(ctx["tenant_id"])
        current_policy_version = self.tenant_policy_versions.get(tid, ctx.get("policy_version", "v1"))

        with self._lock:
            rec = self.approvals.get(approval_id)
            if not rec:
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": "REJECTED",
                    "reason_code": "APPROVAL_NOT_FOUND",
                    "evaluated_policy_version": current_policy_version,
                }

            if rec["status"] == "CONSUMED":
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": "CONSUMED",
                    "reason_code": "APPROVAL_ALREADY_CONSUMED",
                    "evaluated_policy_version": current_policy_version,
                }

            if rec["status"] != "APPROVED":
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": rec["status"],
                    "reason_code": f"APPROVAL_STATUS_{rec['status']}",
                    "evaluated_policy_version": current_policy_version,
                }

            now = datetime.now(timezone.utc)
            if _iso_utc(now) >= rec["expires_at"]:
                rec["status"] = "EXPIRED"
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": "EXPIRED",
                    "reason_code": "APPROVAL_EXPIRED",
                    "evaluated_policy_version": current_policy_version,
                }

            requester = self.principals.get(rec["requester_id"])
            primary_approver_id = (rec.get("approver_ids") or {}).get("primary") or ""
            approver = self.principals.get(primary_approver_id)
            if not requester or requester["status"] != "ACTIVE" or not approver or approver["status"] != "ACTIVE":
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": rec["status"],
                    "reason_code": "APPROVAL_PRINCIPAL_REVOKED",
                    "evaluated_policy_version": current_policy_version,
                }

            if (
                rec["tenant_id"] != tid
                or rec["workspace_id"] != ctx["workspace_id"]
                or rec["resource_id"] != req["resource_id"]
                or rec["operation"] != req["operation"]
                or rec["requester_id"] != ctx["principal_id"]
            ):
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": rec["status"],
                    "reason_code": "APPROVAL_TARGET_OR_PRINCIPAL_MISMATCH",
                    "evaluated_policy_version": current_policy_version,
                }

            if rec["payload_hash"] != req["payload_hash"]:
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": rec["status"],
                    "reason_code": "APPROVAL_PAYLOAD_MISMATCH",
                    "evaluated_policy_version": current_policy_version,
                }

            if rec["policy_version"] != current_policy_version:
                return {
                    "valid": False,
                    "approval_id": approval_id,
                    "status": rec["status"],
                    "reason_code": "APPROVAL_POLICY_STALE",
                    "evaluated_policy_version": current_policy_version,
                }

            if req.get("consume_on_valid", True):
                rec["status"] = "CONSUMED"
                rec["consumed_at"] = _iso_utc(now)

            return {
                "valid": True,
                "approval_id": approval_id,
                "status": rec["status"],
                "reason_code": "OK_APPROVAL_VALID",
                "evaluated_policy_version": current_policy_version,
            }

    def check_revocation(self, req: Mapping[str, Any]) -> dict[str, Any]:
        if self.unavailable_mode:
            raise RuntimeError("PDP_UNAVAILABLE_FAIL_CLOSED")
        ctx = req["execution_context"]
        tid = str(ctx["tenant_id"])
        principal_id = ctx["principal_id"]
        principal = self.principals.get(principal_id)

        reasons: list[str] = []
        current_id_ver = principal["identity_version"] if principal else ctx["identity_version"]
        current_perm_ver = self.tenant_permission_versions.get(tid, ctx.get("permission_version", 1))
        current_pol_ver = self.tenant_policy_versions.get(tid, f"v{current_perm_ver}")

        if not principal or principal["status"] != "ACTIVE":
            reasons.append("USER_DEACTIVATED_OR_LOCKED")
        else:
            if principal["identity_version"] != ctx["identity_version"]:
                reasons.append("IDENTITY_VERSION_CHANGED")
            if current_perm_ver != ctx.get("permission_version", 1):
                reasons.append("PERMISSION_VERSION_CHANGED")
            if current_pol_ver != ctx.get("policy_version"):
                reasons.append("POLICY_VERSION_CHANGED")
            if principal.get("device_posture") == "NON_COMPLIANT":
                reasons.append("DEVICE_REVOKED_OR_NON_COMPLIANT")

        sess_active = any(
            s["session_id"] == ctx.get("session_id") and s["status"] == "ACTIVE"
            for s in self.sessions_by_token.values()
        )
        if not sess_active:
            reasons.append("SESSION_REVOKED_OR_LOGOUT")

        for rid in req.get("resource_ids") or []:
            r = self.resources.get(rid)
            if not r or r.get("revoked"):
                reasons.append("RESOURCE_ACCESS_REVOKED")
                break

        revoked = len(reasons) > 0
        required_actions = (
            [
                "DENY_NEW_REQUESTS",
                "EVICT_DECISION_CACHE",
                "DESTROY_EPHEMERAL_CACHE_KEYS",
                "CANCEL_QUEUED_TASKS",
                "INTERRUPT_RUNNING_TASKS_BEFORE_NEXT_SIDE_EFFECT",
                "PROPAGATE_LINEAGE_ACCESS_REVOKE",
            ]
            if revoked
            else []
        )
        now = datetime.now(timezone.utc)
        res = {
            "schema_version": "1.0.0",
            "revoked": revoked,
            "tenant_id": tid,
            "principal_id": principal_id,
            "session_id": ctx.get("session_id"),
            "resource_id": ctx.get("resource_id"),
            "current_identity_version": current_id_ver,
            "current_policy_version": current_pol_ver,
            "checked_at": _iso_utc(now),
            "decryption_grant_expires_at": None if revoked else _iso_utc(now + timedelta(minutes=15)),
            "correlation_id": ctx["correlation_id"],
            "revocation_reasons": reasons,
            "required_actions": required_actions,
        }
        validate_contract_payload("revocation-event.schema.json", res)
        return res

    def ingest_audit_events(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        accepted = 0
        rejected = 0
        last_seq = 0
        last_hash = "GENESIS"

        with self._lock:
            for ev in events:
                try:
                    validate_contract_payload("audit-event.schema.json", ev)
                    stream_id = ev["stream_id"]
                    chain = self.audit_streams.setdefault(stream_id, [])
                    expected_seq = len(chain) + 1
                    expected_prev = chain[-1]["event_hash"] if chain else "GENESIS"
                    if ev["sequence_no"] != expected_seq or ev["prev_event_hash"] != expected_prev:
                        rejected += 1
                        continue
                    body_without_hash = {k: v for k, v in ev.items() if k != "event_hash"}
                    computed_hash = compute_jcs_sha256(body_without_hash)
                    if ev["event_hash"] != computed_hash:
                        rejected += 1
                        continue
                    chain.append(dict(ev))
                    accepted += 1
                    last_seq = ev["sequence_no"]
                    last_hash = ev["event_hash"]
                except Exception:
                    rejected += 1

        return {
            "accepted_count": accepted,
            "rejected_count": rejected,
            "last_sequence_no": last_seq,
            "chain_head_hash": last_hash,
        }
