"""Test-only fixtures and downstream recorders for G2 readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter


CANARY_A = "CANARY-G2-WORKSPACE-A-7D2F"
CANARY_B = "CANARY-G2-WORKSPACE-B-91AC"


@dataclass(frozen=True)
class SameTenantWorkspaceFixture:
    adapter: InMemoryMockIamPdpAdapter
    principal_a: dict[str, Any]
    principal_b: dict[str, Any]
    execution_token_a: str
    execution_token_b: str
    resource_a: str
    resource_b: str
    canary_a: str = CANARY_A
    canary_b: str = CANARY_B


def build_same_tenant_workspace_fixture() -> SameTenantWorkspaceFixture:
    adapter = InMemoryMockIamPdpAdapter(adapter_id="g2-readiness-test-double")
    adapter.seed_principal(
        principal_id="g2-user-a",
        tenant_id="g2-tenant",
        workspace_id="g2-workspace-a",
        session_token="g2-session-a",
        permissions={("g2-resource-a", "read"), ("g2-resource-a", "prompt_use")},
    )
    adapter.seed_principal(
        principal_id="g2-user-b",
        tenant_id="g2-tenant",
        workspace_id="g2-workspace-b",
        session_token="g2-session-b",
        permissions={("g2-resource-b", "read"), ("g2-resource-b", "prompt_use")},
    )
    adapter.seed_resource(
        resource_id="g2-resource-a",
        tenant_id="g2-tenant",
        workspace_id="g2-workspace-a",
        resource_type="document",
        classification="INTERNAL",
    )
    adapter.seed_resource(
        resource_id="g2-resource-b",
        tenant_id="g2-tenant",
        workspace_id="g2-workspace-b",
        resource_type="document",
        classification="INTERNAL",
    )

    exchange = {
        "subject_token_type": "SESSION_TOKEN",
        "requested_audience": "javisos-mcp-gateway",
        "device_id": "g2-test-device",
        "device_posture": "COMPLIANT",
    }
    token_a = adapter.exchange_token(
        {**exchange, "subject_token": "g2-session-a", "requested_workspace_id": "g2-workspace-a"}
    )["issued_token"]
    token_b = adapter.exchange_token(
        {**exchange, "subject_token": "g2-session-b", "requested_workspace_id": "g2-workspace-b"}
    )["issued_token"]

    return SameTenantWorkspaceFixture(
        adapter=adapter,
        principal_a=adapter.principals["g2-user-a"],
        principal_b=adapter.principals["g2-user-b"],
        execution_token_a=token_a,
        execution_token_b=token_b,
        resource_a="g2-resource-a",
        resource_b="g2-resource-b",
    )


class RecordingDownstream:
    """Connector fake retaining only explicit canary markers and argument names."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, arguments: Mapping[str, Any], credential: str) -> dict[str, Any]:
        self.calls.append(
            {
                "argument_keys": tuple(sorted(str(key) for key in arguments)),
                "marker": arguments.get("marker"),
                "credential_received": bool(credential),
            }
        )
        return {"ok": True, "marker": arguments.get("marker")}


class RecordingProvider(RecordingDownstream):
    """Provider boundary fake with the same redacted recording contract."""
