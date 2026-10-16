"""Approved Connector Catalog for Server-Side MCP Gateway (`G0-005`, `G0-011`).

Keeps enterprise connector credentials strictly server-side and rejects unapproved
connectors or client-supplied URL overrides (anti-SSRF / anti-open-proxy).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

FORBIDDEN_URL_OVERRIDE_KEYS = frozenset(
    {
        "_target_url",
        "target_url",
        "endpoint_url",
        "base_url",
        "override_url",
        "webhook_url",
        "proxy_url",
    }
)


class ApprovedConnectorCatalog:
    """Server-side registry of approved enterprise connectors and their credentials."""

    def __init__(self) -> None:
        self._connectors: dict[str, tuple[str, Callable[[Mapping[str, Any], str], Any]]] = {}

    def register_connector(
        self,
        *,
        connector_id: str,
        server_credential: str,
        handler: Callable[[Mapping[str, Any], str], Any],
    ) -> None:
        if not connector_id or not server_credential:
            raise ValueError("connector_id and server_credential are required")
        self._connectors[connector_id] = (server_credential, handler)

    def validate_request(self, connector_id: str, arguments: Mapping[str, Any]) -> None:
        if connector_id not in self._connectors:
            raise PermissionError("CONNECTOR_NOT_IN_APPROVED_CATALOG")
        for key in arguments:
            if str(key).lower() in FORBIDDEN_URL_OVERRIDE_KEYS:
                raise PermissionError("CONNECTOR_NOT_IN_APPROVED_CATALOG")

    def invoke(self, connector_id: str, arguments: Mapping[str, Any]) -> Any:
        self.validate_request(connector_id, arguments)
        credential, handler = self._connectors[connector_id]
        raw_result = handler(dict(arguments), credential)
        # Never leak raw server_credential in downstream output
        if isinstance(raw_result, dict):
            sanitized = {k: v for k, v in raw_result.items() if v != credential}
            return sanitized
        return raw_result
