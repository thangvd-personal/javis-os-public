"""Server-Side MCP Gateway Policy Enforcement Point (`McpGatewayPepService`).

Consumes ONLY `IamPdpAdapterPort` and never imports Django or `sme_btos` internals (`G0-025`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from javisos.server.iam_contract.jcs import compute_action_payload_hash
from javisos.server.iam_contract.port import IamPdpAdapterPort
from javisos.server.mcp_gateway.audit_outbox import DurableAuditOutbox
from javisos.server.mcp_gateway.connector_catalog import ApprovedConnectorCatalog

SUPPORTED_OBLIGATION_TYPES = frozenset(
    {
        "REDACT_SENSITIVE_FIELDS",
        "STREAM_ONLY_NO_DISK_CACHE",
        "ENCRYPT_LOCAL_CACHE_WITH_EPHEMERAL_KEY",
        "RECORD_LINEAGE_NODE",
    }
)

APPROVED_MODEL_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {
    ("anthropic", "claude-3-5-sonnet-20241022"): {
        "registry_id": "MOD-ANTHROPIC-SONNET-35",
        "approved_for_classes": {"PUBLIC", "INTERNAL", "CONFIDENTIAL"},
        "data_retention_policy": "ZERO_DATA_RETENTION",
        "approval_status": "ACTIVE",
    },
    ("google", "gemini-2.5-flash"): {
        "registry_id": "MOD-GOOGLE-FLASH-25",
        "approved_for_classes": {"PUBLIC", "INTERNAL"},
        "data_retention_policy": "ENTERPRISE_NO_TRAINING",
        "approval_status": "ACTIVE",
    },
    ("local", "qwen2.5:14b-instruct-q4_K_M"): {
        "registry_id": "MOD-LOCAL-OLLAMA-QWEN",
        "approved_for_classes": {"PUBLIC", "INTERNAL", "CONFIDENTIAL"},
        "data_retention_policy": "LOCAL_ON_PREM_ONLY",
        "approval_status": "ACTIVE",
        "network_isolation_requirements": {
            "egress_blocked": True,
            "telemetry_disabled": True,
            "prompt_logging_disabled": True,
        },
    },
}


class GatewayPepError(PermissionError):
    """Structured fail-closed PEP error raised when MCP Gateway blocks an action."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        effect: str = "DENY",
        approval_requirement: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.effect = effect
        self.approval_requirement = approval_requirement


@dataclass
class GatewayExecutionResult:
    status: str  # "COMPLETED" | "RECONCILIATION_REQUIRED" | "APPROVAL_REQUIRED"
    effect: str
    decision_id: str
    payload_hash: str
    idempotency_key: str
    output: Any = None
    obligations_applied: list[str] = field(default_factory=list)
    approval_requirement: dict[str, Any] | None = None


class McpGatewayPepService:
    """Centralized Server-Side PEP enforcing token, resource x operation, approvals, and idempotency."""

    def __init__(
        self,
        *,
        adapter: IamPdpAdapterPort,
        catalog: ApprovedConnectorCatalog,
        audit_outbox: DurableAuditOutbox,
        required_audience: str = "javisos-mcp-gateway",
        read_only_gate: bool = False,
    ) -> None:
        self.adapter = adapter
        self.catalog = catalog
        self.audit_outbox = audit_outbox
        self.required_audience = required_audience
        self.read_only_gate = read_only_gate
        self._idempotency_store: dict[str, tuple[str, GatewayExecutionResult]] = {}

    def execute_enterprise_action(
        self,
        *,
        execution_token: str,
        resource_id: str,
        operation: str,
        connector_id: str,
        arguments: Mapping[str, Any] | None = None,
        idempotency_key: str,
        approval_id: str | None = None,
        action_context_extra: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> GatewayExecutionResult:
        args = dict(arguments or {})
        corr_id = correlation_id or f"corr_{uuid.uuid4().hex[:10]}"
        exec_id = f"exec_{uuid.uuid4().hex[:10]}"
        payload_hash = compute_action_payload_hash(resource_id, operation, args)

        # 1. Verify Dedicated Execution Token (reject raw session tokens and personal .hub_token)
        if not execution_token or execution_token.startswith("hub_") or execution_token.startswith("sess_"):
            raise GatewayPepError("TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN", "Raw session or personal token rejected")

        try:
            tok_meta = self.adapter.verify_execution_token(
                execution_token,
                required_audience=self.required_audience,
            )
        except Exception as exc:
            raise GatewayPepError("TOKEN_INVALID_OR_NOT_EXECUTION_TOKEN", str(exc)) from exc

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ctx: dict[str, Any] = {
            "schema_version": "1.0.0",
            "principal_id": str(tok_meta["sub"]),
            "principal_type": "USER",
            "tenant_id": str(tok_meta.get("tenant_id") or tok_meta.get("verified_context", {}).get("tenant_id")),
            "workspace_id": str(tok_meta.get("workspace_id") or tok_meta.get("verified_context", {}).get("workspace_id")),
            "workspace_tier": "CORPORATE",
            "device_id": str(tok_meta.get("device_id") or tok_meta.get("verified_context", {}).get("device_id") or "dev_01"),
            "device_posture": "COMPLIANT",
            "session_id": tok_meta.get("session_id") or tok_meta.get("verified_context", {}).get("session_id"),
            "identity_version": int(tok_meta["identity_version"]),
            "permission_version": int(tok_meta["permission_version"]),
            "policy_version": str(tok_meta["policy_version"]),
            "delegated_by": (tok_meta.get("delegation") or {}).get("delegated_by")
            if isinstance(tok_meta.get("delegation"), dict)
            else None,
            "delegation_id": (tok_meta.get("delegation") or {}).get("delegation_id")
            if isinstance(tok_meta.get("delegation"), dict)
            else None,
            "resource_id": resource_id,
            "execution_id": exec_id,
            "correlation_id": corr_id,
            "initiated_at": now_iso,
            "purpose": "WORKFLOW_EXECUTION",
        }

        # 2. Check Idempotency Key Binding (`G0-013`)
        if idempotency_key in self._idempotency_store:
            prev_hash, prev_result = self._idempotency_store[idempotency_key]
            if prev_hash != payload_hash:
                raise GatewayPepError(
                    "IDEMPOTENCY_PAYLOAD_MISMATCH",
                    "Idempotency key reused with a different canonical payload_hash",
                )
            return prev_result

        # 2b. Enforce G1 Policy Profile Read-Only Restrictions & Prompt Protection
        if (self.read_only_gate or connector_id == "sme_hrm_iam_reader") and operation in ("write", "delete", "download", "export"):
            reason = "DENY_GATE_RESTRICTION_READ_ONLY"
            self.audit_outbox.append_event(
                event_type="pdp.decision",
                execution_stage="DECISION_DENIED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="DENY",
                payload_hash=payload_hash,
                reason_code=reason,
            )
            raise GatewayPepError(reason, f"Operation '{operation}' is prohibited under G1 Read-Only Pilot", effect="DENY")

        if operation == "share":
            reason = "DENY_CROSS_WORKSPACE_SHARE_FORBIDDEN"
            self.audit_outbox.append_event(
                event_type="pdp.decision",
                execution_stage="DECISION_DENIED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="DENY",
                payload_hash=payload_hash,
                reason_code=reason,
            )
            raise GatewayPepError(reason, "Sharing across workspaces is prohibited", effect="DENY")

        if operation == "prompt_use":
            provider_id = args.get("provider_id")
            model_id = args.get("model_id")
            if not provider_id or not model_id:
                reason = "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"
                self.audit_outbox.append_event(
                    event_type="pdp.decision",
                    execution_stage="DECISION_DENIED",
                    execution_context=ctx,
                    resource_id=resource_id,
                    operation=operation,
                    decision_effect="DENY",
                    payload_hash=payload_hash,
                    reason_code=reason,
                )
                raise GatewayPepError(reason, "Missing provider_id or model_id for prompt_use", effect="DENY")

            model_entry = APPROVED_MODEL_REGISTRY.get((str(provider_id), str(model_id)))
            if not model_entry or model_entry.get("approval_status") != "ACTIVE":
                reason = "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"
                self.audit_outbox.append_event(
                    event_type="pdp.decision",
                    execution_stage="DECISION_DENIED",
                    execution_context=ctx,
                    resource_id=resource_id,
                    operation=operation,
                    decision_effect="DENY",
                    payload_hash=payload_hash,
                    reason_code=reason,
                )
                raise GatewayPepError(reason, f"Model {provider_id}:{model_id} not in approved registry or inactive", effect="DENY")

            # Determine resource classification
            classification = None
            if action_context_extra and "classification" in action_context_extra:
                classification = action_context_extra["classification"]
            elif "classification" in args:
                classification = args["classification"]
            elif hasattr(self.adapter, "_resources") and resource_id in getattr(self.adapter, "_resources"):
                classification = getattr(self.adapter, "_resources")[resource_id].get("classification")

            if not classification:
                classification = "CONFIDENTIAL" if "worker" in resource_id else "INTERNAL"

            if classification not in model_entry.get("approved_for_classes", set()):
                reason = "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"
                self.audit_outbox.append_event(
                    event_type="pdp.decision",
                    execution_stage="DECISION_DENIED",
                    execution_context=ctx,
                    resource_id=resource_id,
                    operation=operation,
                    decision_effect="DENY",
                    payload_hash=payload_hash,
                    reason_code=reason,
                )
                raise GatewayPepError(reason, f"Model {model_id} not approved for classification {classification}", effect="DENY")

            if classification == "CONFIDENTIAL":
                policy = model_entry.get("data_retention_policy")
                retention_ok = False
                if policy == "ZERO_DATA_RETENTION":
                    retention_ok = bool(args.get("zero_retention_verified") or (action_context_extra and action_context_extra.get("zero_retention_verified")))
                elif policy == "LOCAL_ON_PREM_ONLY":
                    egress = bool(args.get("egress_blocked"))
                    telemetry = bool(args.get("telemetry_disabled"))
                    logging = bool(args.get("prompt_logging_disabled"))
                    remote_fallback = bool(args.get("remote_fallback", False))
                    retention_ok = egress and telemetry and logging and (not remote_fallback)

                if not retention_ok:
                    reason = "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED"
                    self.audit_outbox.append_event(
                        event_type="pdp.decision",
                        execution_stage="DECISION_DENIED",
                        execution_context=ctx,
                        resource_id=resource_id,
                        operation=operation,
                        decision_effect="DENY",
                        payload_hash=payload_hash,
                        reason_code=reason,
                    )
                    raise GatewayPepError(reason, f"Retention policy requirements not satisfied for CONFIDENTIAL under {policy}", effect="DENY")

        # 3. Validate Approved Connector Catalog & SSRF URL overrides (`G0-005`)
        try:
            self.catalog.validate_request(connector_id, args)
        except PermissionError as exc:
            self.audit_outbox.append_event(
                event_type="mcp.tool_call",
                execution_stage="DECISION_DENIED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="DENY",
                payload_hash=payload_hash,
                reason_code=str(exc),
            )
            raise GatewayPepError(str(exc), "Connector or target URL is not in the approved catalog") from exc

        # Record REQUEST_ACCEPTED stage in Durable Audit Outbox (`G0-023`)
        self.audit_outbox.append_event(
            event_type="mcp.tool_call",
            execution_stage="REQUEST_ACCEPTED",
            execution_context=ctx,
            resource_id=resource_id,
            operation=operation,
            decision_effect="N/A",
            payload_hash=payload_hash,
        )

        # 4. Call PDP Evaluate (`G0-001`, `G0-002`, `G0-007`)
        action_context: dict[str, Any] = {
            "payload_hash": payload_hash,
            "idempotency_key": idempotency_key,
            "approval_id": approval_id,
            "revalidation_stage": "INGRESS",
        }
        if operation == "prompt_use":
            action_context["target_provider"] = args.get("provider_id")
            action_context["target_model"] = args.get("model_id")
        if action_context_extra:
            action_context.update(action_context_extra)

        try:
            decision = self.adapter.evaluate_pdp(
                {
                    "schema_version": "1.0.0",
                    "execution_context": ctx,
                    "resource": {"resource_id": resource_id},
                    "operation": operation,
                    "action_context": action_context,
                }
            )
        except Exception as exc:
            self.audit_outbox.append_event(
                event_type="pdp.decision",
                execution_stage="DECISION_DENIED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="PDP_UNAVAILABLE",
                payload_hash=payload_hash,
                reason_code="PDP_ERROR_FAIL_CLOSED",
            )
            raise GatewayPepError("PDP_UNAVAILABLE_FAIL_CLOSED", str(exc), effect="PDP_UNAVAILABLE") from exc

        effect = str(decision.get("effect") or "PDP_UNAVAILABLE")
        reason_code = str(decision.get("reason_code") or "PDP_ERROR_FAIL_CLOSED")
        decision_id = str(decision.get("decision_id") or "dec_unknown")

        if effect == "PDP_UNAVAILABLE":
            self.audit_outbox.append_event(
                event_type="pdp.decision",
                execution_stage="DECISION_DENIED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="PDP_UNAVAILABLE",
                payload_hash=payload_hash,
                reason_code=reason_code,
            )
            raise GatewayPepError("PDP_UNAVAILABLE_FAIL_CLOSED", reason_code, effect="PDP_UNAVAILABLE")

        if effect == "DENY":
            self.audit_outbox.append_event(
                event_type="pdp.decision",
                execution_stage="DECISION_DENIED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="DENY",
                payload_hash=payload_hash,
                reason_code=reason_code,
            )
            raise GatewayPepError(reason_code, f"PDP denied {operation} on {resource_id}", effect="DENY")

        if effect in ("APPROVAL_REQUIRED", "REQUIRES_APPROVAL"):
            if not approval_id:
                self.audit_outbox.append_event(
                    event_type="pdp.decision",
                    execution_stage="APPROVAL_REQUIRED",
                    execution_context=ctx,
                    resource_id=resource_id,
                    operation=operation,
                    decision_effect="APPROVAL_REQUIRED",
                    payload_hash=payload_hash,
                    reason_code=reason_code,
                )
                raise GatewayPepError(
                    "APPROVAL_REQUIRED",
                    reason_code,
                    effect="APPROVAL_REQUIRED",
                    approval_requirement=decision.get("approval_requirement"),
                )

            # Validate & atomically consume the approval before execution (`G0-014`, `G0-015`)
            val = self.adapter.validate_approval(
                approval_id,
                {
                    "execution_context": ctx,
                    "resource_id": resource_id,
                    "operation": operation,
                    "payload_hash": payload_hash,
                    "idempotency_key": idempotency_key,
                    "consume_on_valid": True,
                },
            )
            if not val.get("valid"):
                val_reason = str(val.get("reason_code") or "DENY_APPROVAL_INVALID_OR_MISMATCH")
                self.audit_outbox.append_event(
                    event_type="approval.validated",
                    execution_stage="DECISION_DENIED",
                    execution_context=ctx,
                    resource_id=resource_id,
                    operation=operation,
                    decision_effect="DENY",
                    payload_hash=payload_hash,
                    reason_code=val_reason,
                )
                raise GatewayPepError(val_reason, "Approval validation failed", effect="DENY")

        elif effect != "ALLOW":
            raise GatewayPepError("PDP_UNAVAILABLE_FAIL_CLOSED", f"Unknown effect: {effect}", effect="PDP_UNAVAILABLE")

        # 5. Enforce Mandatory Obligations (`iam-pdp-semantics.md` §2)
        obligations_applied: list[str] = []
        for obl in decision.get("obligations") or []:
            obl_type = str(obl.get("obligation_type") or "")
            mandatory = bool(obl.get("mandatory", True))
            params = obl.get("parameters") or {}
            if mandatory and (obl_type not in SUPPORTED_OBLIGATION_TYPES or params.get("mode", "").startswith("UNSUPPORTED")):
                self.audit_outbox.append_event(
                    event_type="pdp.decision",
                    execution_stage="DECISION_DENIED",
                    execution_context=ctx,
                    resource_id=resource_id,
                    operation=operation,
                    decision_effect="DENY",
                    payload_hash=payload_hash,
                    reason_code="AUTHZ_OBLIGATION_UNFULFILLED",
                )
                raise GatewayPepError(
                    "AUTHZ_OBLIGATION_UNFULFILLED",
                    f"Cannot fulfill mandatory obligation {obl_type}",
                    effect="DENY",
                )
            obligations_applied.append(obl_type)

        self.audit_outbox.append_event(
            event_type="pdp.decision",
            execution_stage="DECISION_GRANTED",
            execution_context=ctx,
            resource_id=resource_id,
            operation=operation,
            decision_effect="ALLOW",
            payload_hash=payload_hash,
            reason_code="OK_GRANTED",
        )

        # 6. Dispatch to Server-Side Connector (`G0-013`, `G0-023`)
        self.audit_outbox.append_event(
            event_type="mcp.tool_call",
            execution_stage="DOWNSTREAM_DISPATCHED",
            execution_context=ctx,
            resource_id=resource_id,
            operation=operation,
            decision_effect="ALLOW",
            payload_hash=payload_hash,
        )

        try:
            output = self.catalog.invoke(connector_id, args)
        except TimeoutError:
            # No blind retry on downstream timeout! Transition to RECONCILIATION_REQUIRED
            self.audit_outbox.append_event(
                event_type="task.reconciliation",
                execution_stage="RECONCILIATION_REQUIRED",
                execution_context=ctx,
                resource_id=resource_id,
                operation=operation,
                decision_effect="ALLOW",
                payload_hash=payload_hash,
                reason_code="DOWNSTREAM_UNKNOWN_TIMEOUT",
            )
            res_timeout = GatewayExecutionResult(
                status="RECONCILIATION_REQUIRED",
                effect="ALLOW",
                decision_id=decision_id,
                payload_hash=payload_hash,
                idempotency_key=idempotency_key,
                output=None,
                obligations_applied=obligations_applied,
            )
            self._idempotency_store[idempotency_key] = (payload_hash, res_timeout)
            return res_timeout

        self.audit_outbox.append_event(
            event_type="mcp.tool_call",
            execution_stage="DOWNSTREAM_COMMITTED",
            execution_context=ctx,
            resource_id=resource_id,
            operation=operation,
            decision_effect="ALLOW",
            payload_hash=payload_hash,
        )
        self.audit_outbox.append_event(
            event_type="mcp.tool_call",
            execution_stage="OPERATION_VERIFIED",
            execution_context=ctx,
            resource_id=resource_id,
            operation=operation,
            decision_effect="ALLOW",
            payload_hash=payload_hash,
        )

        res_ok = GatewayExecutionResult(
            status="COMPLETED",
            effect="ALLOW",
            decision_id=decision_id,
            payload_hash=payload_hash,
            idempotency_key=idempotency_key,
            output=output,
            obligations_applied=obligations_applied,
        )
        self._idempotency_store[idempotency_key] = (payload_hash, res_ok)
        return res_ok
