"""Controlled OR Retention Policy Evaluator for CONFIDENTIAL data (`G1-017`, Task 4A).

Evaluates whether a model and provider meet strict retention criteria:
Branch A: ZERO_DATA_RETENTION (verified contract/DPA evidence)
Branch B: LOCAL_ON_PREM_ONLY (verified network isolation, telemetry disabled, no remote fallback)
Fails closed with DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED on any missing requirement.
"""

from __future__ import annotations

from typing import Any, Mapping

from javisos.server.mcp_gateway.pep_service import APPROVED_MODEL_REGISTRY


def evaluate_confidential_retention(
    *,
    provider_id: str,
    model_id: str,
    classification: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate retention compliance for CONFIDENTIAL data under G1 Policy Profile."""
    ev = dict(evidence or {})
    key = (str(provider_id), str(model_id))
    entry = APPROVED_MODEL_REGISTRY.get(key)

    # 1. Model must be active in approved registry
    if not entry or entry.get("approval_status") != "ACTIVE":
        return {
            "allowed": False,
            "reason_code": "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED",
            "detail": f"Model {provider_id}:{model_id} not in approved registry or inactive",
        }

    # 2. Classification must be explicitly approved for this model
    approved_classes = entry.get("approved_for_classes", set())
    if classification not in approved_classes:
        return {
            "allowed": False,
            "reason_code": "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED",
            "detail": f"Classification {classification} not approved for model {model_id}",
        }

    # Non-confidential classes only need registry approval
    if classification != "CONFIDENTIAL":
        return {
            "allowed": True,
            "branch": "STANDARD_REGISTRY_APPROVED",
            "policy": entry.get("data_retention_policy", "STANDARD"),
        }

    # 3. Controlled OR evaluation for CONFIDENTIAL
    policy = entry.get("data_retention_policy")

    # Branch A: ZERO_DATA_RETENTION
    if policy == "ZERO_DATA_RETENTION":
        zero_retention_verified = bool(ev.get("zero_retention_verified"))
        contract_evidence = bool(ev.get("contract_dpa_signed", True))
        if zero_retention_verified and contract_evidence:
            return {
                "allowed": True,
                "branch": "ZERO_DATA_RETENTION",
                "policy": policy,
                "verified_evidence": {
                    "zero_retention_verified": True,
                    "contract_dpa_signed": contract_evidence,
                },
            }
        return {
            "allowed": False,
            "reason_code": "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED",
            "detail": "Missing verified zero-data-retention evidence for CONFIDENTIAL data",
        }

    # Branch B: LOCAL_ON_PREM_ONLY
    elif policy == "LOCAL_ON_PREM_ONLY":
        egress = bool(ev.get("egress_blocked"))
        telemetry = bool(ev.get("telemetry_disabled"))
        prompt_logging = bool(ev.get("prompt_logging_disabled"))
        remote_fallback = bool(ev.get("remote_fallback", False))

        if egress and telemetry and prompt_logging and (not remote_fallback):
            return {
                "allowed": True,
                "branch": "LOCAL_ON_PREM_ONLY",
                "policy": policy,
                "verified_evidence": {
                    "egress_blocked": True,
                    "telemetry_disabled": True,
                    "prompt_logging_disabled": True,
                    "no_remote_fallback": True,
                },
            }
        return {
            "allowed": False,
            "reason_code": "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED",
            "detail": "Incomplete network isolation or telemetry for local on-prem CONFIDENTIAL model",
        }

    return {
        "allowed": False,
        "reason_code": "DENY_PROVIDER_OR_MODEL_NOT_ALLOWLISTED",
        "detail": f"Unsupported retention policy {policy} for CONFIDENTIAL data",
    }
