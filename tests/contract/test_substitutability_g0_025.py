"""Host-side Contract Substitutability & Zero-Django-Coupling Verification (`G0-025`)."""

from __future__ import annotations

import ast
import json
import urllib.request
from pathlib import Path

import pytest


def test_zero_django_coupling_in_javisos_consumer_code():
    """Verify no file under javisos/server/ imports django, rest_framework, or sme_btos modules."""
    javis_server_dir = Path(__file__).resolve().parents[2] / "server"
    forbidden_prefixes = ("django", "rest_framework", "apps.platform", "apps.enterprise")
    checked_files = 0
    for py_file in javis_server_dir.rglob("*.py"):
        checked_files += 1
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"Forbidden import '{alias.name}' in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), (
                    f"Forbidden import '{node.module}' in {py_file}"
                )
    assert checked_files >= 8


def test_live_sme_btos_container_and_mock_adapter_capabilities_equivalence():
    """Verify live sme_backend Docker container on localhost:8000 and InMemoryMockIamPdpAdapter expose matching v1.0 contract capabilities."""
    from javisos.server.iam_contract.mock_adapter import InMemoryMockIamPdpAdapter

    mock_caps = InMemoryMockIamPdpAdapter().get_capabilities()
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/iam-pdp-contract/v1/capabilities",
        headers={
            "X-Service-Client-Id": "javisos-mcp-gateway",
            "X-Service-Token": "dev-gateway-service-secret",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        sme_caps = json.loads(resp.read().decode("utf-8"))

    assert mock_caps["contract_version"] == sme_caps["contract_version"] == "1.0.0"
    assert mock_caps["supported_features"] == sme_caps["supported_features"]
    gap_ids = {g["gap_id"] for g in sme_caps["declared_gaps"]}
    assert {"GAP-SME-001", "GAP-SME-004"}.issubset(gap_ids)
