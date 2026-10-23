"""Closed private semantic source records, distinct from the rendered model baseline.

Only trusted collectors create these records. Validation checks representation and pins;
it cannot prove a production surface complete or turn arbitrary JSON into source authority.
Collectors must re-observe actual files/SQL/inventories before the host grants or uses them.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from evidence_store import redact
from improvement_blobs import MAX_ASSET_BYTES
from improvement_cases import _digest, _fields, _frozen, output_path
from improvement_store import _hash, _json
from improvement_trial_context import _ref

MAX_SEMANTIC_SOURCES = 1000
MAX_SOURCE_PROJECTIONS_BYTES = 6 * MAX_ASSET_BYTES
_FIELDS = {"kind", "ref", "locator", "role", "required", "present", "identity", "hash", "projection", "projection_hash"}
_ROLES = {"file": {"protected", "discovery", "authority"}, "sql": {"protected", "authority"},
          "inventory": {"discovery", "authority"}, "runtime": {"volatile"}, "renderer": {"authority"}}


def _sources(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SEMANTIC_SOURCES:
        raise ValueError("invalid_semantic_sources")
    refs, paths, size = set(), set(), 0
    for item in value:
        _fields(item, _FIELDS)
        ref = _ref(item["ref"])
        kind, role, locator = item["kind"], item["role"], item["locator"]
        if (not isinstance(kind, str) or kind not in _ROLES or not isinstance(role, str) or role not in _ROLES[kind]
                or type(item["required"]) is not bool or type(item["present"]) is not bool
                or not isinstance(locator, dict) or not locator or len(_json(locator).encode("utf-8")) > 16384):
            raise ValueError("invalid_semantic_source")
        if ref.casefold() in refs:
            raise ValueError("duplicate_semantic_ref")
        refs.add(ref.casefold())
        if kind == "file":
            _fields(locator, {"root", "relative"})
            root = locator["root"]
            if not isinstance(root, str) or not Path(root).is_absolute() or ".." in Path(root).parts:
                raise ValueError("invalid_semantic_file_root")
            relative = output_path(locator["relative"])
            path = str(Path(root) / relative).casefold()
            if path in paths:
                raise ValueError("duplicate_semantic_file")
            paths.add(path)
        if item["present"]:
            for key in ("identity", "hash", "projection_hash"):
                _digest(item[key])
            projection = item["projection"]
            if kind == "file" and role == "discovery" and not isinstance(projection, dict):
                raise ValueError("file_discovery_metadata_required")
            if item["projection_hash"] != _hash(projection):
                raise ValueError("semantic_projection_hash_mismatch")
            if kind != "file" and item["hash"] != item["projection_hash"]:
                raise ValueError("semantic_source_hash_mismatch")
            if role == "protected":
                if not isinstance(projection, str):
                    raise ValueError("protected_semantic_text_required")
                if kind == "file" and item["hash"] != hashlib.sha256(projection.encode("utf-8")).hexdigest():
                    raise ValueError("protected_source_hash_mismatch")
        elif (item["required"] or any(item[key] is not None for key in ("identity", "hash", "projection", "projection_hash"))):
            raise ValueError("invalid_absent_semantic_source")
        size += len(_json(item).encode("utf-8"))
        if size > MAX_SOURCE_PROJECTIONS_BYTES:
            raise ValueError("insufficient_source: semantic_sources_too_large")
    return value


def _plan(surface, sources):
    return _hash({"version": 2, "surface": surface, "sources": [
        {key: item[key] for key in ("kind", "ref", "locator", "role", "required")} for item in sources]})


def validate_capture(value):
    """Check a private typed capture without reading sources or granting authority."""
    value = _frozen(value)
    _fields(value, {"version", "surface", "plan_hash", "sources", "baseline_context"})
    if type(value["version"]) is not int or value["version"] != 2:
        raise ValueError("invalid_semantic_context_version")
    surface = value["surface"]
    if not isinstance(surface, dict) or not surface or len(_json(surface).encode("utf-8")) > 16384:
        raise ValueError("invalid_semantic_surface")
    sources = _sources(value["sources"])
    if value["plan_hash"] != _plan(surface, sources):
        raise ValueError("semantic_plan_hash_mismatch")
    context = value["baseline_context"]
    _fields(context, {"content", "hash", "coverage"})
    content = context["content"]
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_ASSET_BYTES:
        raise ValueError("insufficient_source: semantic_context_empty_or_too_large")
    if context["hash"] != hashlib.sha256(content.encode("utf-8")).hexdigest():
        raise ValueError("semantic_context_hash_mismatch")
    if not isinstance(context["coverage"], str) or context["coverage"] not in {"complete", "unknown"}:
        raise ValueError("invalid_semantic_context_coverage")
    for item in sources:
        if item["present"] and item["role"] == "protected" and item["projection"] not in content:
            raise ValueError("insufficient_source: protected_text_not_rendered")
    if redact(value) != value:
        raise ValueError("insufficient_source: credential_bearing_semantic_context")
    return value


def capture_semantics(*, surface, sources, content, complete_plan):
    """Freeze host observations and an explicitly rendered baseline; never append payloads."""
    if type(complete_plan) is not bool:
        raise ValueError("invalid_semantic_completeness")
    if not isinstance(content, str):
        raise ValueError("invalid_semantic_context_content")
    sources, surface = _frozen(sources), _frozen(surface)
    _sources(sources)
    return validate_capture({"version": 2, "surface": surface, "plan_hash": _plan(surface, sources), "sources": sources,
        "baseline_context": {"content": content, "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                             "coverage": "complete" if complete_plan else "unknown"}})
