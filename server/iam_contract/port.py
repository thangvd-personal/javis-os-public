"""Protocol and Type Definitions for Standard IAM/PDP Contract v1.0 (`IamPdpAdapterPort`)."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@runtime_checkable
class IamPdpAdapterPort(Protocol):
    """Vendor-neutral port consumed by MCP Gateway PEP and JavisOS Corporate Runtime."""

    def get_capabilities(self) -> dict[str, Any]:
        """GET /v1/capabilities"""
        ...

    def exchange_token(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/tokens/exchange"""
        ...

    def resolve_resource(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/resources/resolve"""
        ...

    def evaluate_pdp(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/pdp/evaluate"""
        ...

    def create_approval(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/approvals"""
        ...

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        """GET /v1/approvals/{approval_id}"""
        ...

    def decide_approval(self, approval_id: str, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/approvals/{approval_id}/decide"""
        ...

    def validate_approval(self, approval_id: str, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/approvals/{approval_id}/validate"""
        ...

    def check_revocation(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/revocations/check"""
        ...

    def ingest_audit_events(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """POST /v1/audit/events"""
        ...

    def verify_execution_token(
        self,
        raw_token: str,
        required_audience: str = "javisos-mcp-gateway",
    ) -> dict[str, Any]:
        """Verify execution token and return token metadata + verified_context."""
        ...
