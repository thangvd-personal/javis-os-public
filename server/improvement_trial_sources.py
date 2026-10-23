"""Read-only membership checks between immutable cases and a host-observed inventory.

This primitive does not establish provider permission. The host must independently
observe permission/version freshness, bind the canonical inventory into its grant,
and revalidate it at execution boundaries. Caller-provided hashes are not authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import improvement_cases as IC
import improvement_store as IM
from evidence_store import redact
from improvement_blobs import AssetUnavailable, MAX_ASSET_BYTES

_MATCH_FIELDS = {"source_ref", "source_version", "source_hash", "fixture_hash", "timezone", "data_period",
                 "cutoff", "schema", "redaction", "sufficient_source"}
_FIELDS = _MATCH_FIELDS | {"identity", "permission_id", "permission_version", "freshness_policy"}
MAX_INVENTORY_ENTRIES = 1000


def validate_inventory(value):
    """Canonicalize only closed, sufficient snapshot metadata; never credentials or grants."""
    if not isinstance(value, list) or len(value) > MAX_INVENTORY_ENTRIES:
        raise ValueError("invalid_trial_source_inventory")
    for entry in value:
        IC._fields(entry, _FIELDS)
    entries = IC._frozen(value)
    if redact(entries) != entries:
        raise ValueError("credential_bearing_trial_source_inventory")
    for entry in entries:
        for key in _FIELDS - {"permission_version", "sufficient_source"}:
            IC._text(entry[key])
        for key in ("identity", "source_hash", "fixture_hash"):
            IC._digest(entry[key])
        if (type(entry["permission_version"]) is not int or entry["permission_version"] <= 0
                or entry["sufficient_source"] is not True
                or entry["freshness_policy"] not in {"snapshot_version", "require_current_version"}):
            raise ValueError("invalid_trial_source_permission")
        try:
            ZoneInfo(entry["timezone"])
            if datetime.fromisoformat(entry["cutoff"]).utcoffset() is None:
                raise ValueError("timezone_required")
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("invalid_source_time") from error
    encoded = [IM._json(entry) for entry in entries]
    if len(encoded) != len(set(encoded)):
        raise ValueError("duplicate_trial_source_inventory")
    return [entry for _, entry in sorted(zip(encoded, entries), key=lambda item: item[0])]


def _manifest(row):
    """Recheck immutable row integrity rather than trusting status/hash columns alone."""
    manifest = json.loads(row["manifest_json"])
    IC._fields(manifest, IC._SNAPSHOT_KEYS)
    if (type(manifest["version"]) is not int or manifest["version"] != 1
            or manifest["sufficient_source"] is not True or manifest["freshness"] != "current"
            or row["status"] != "sufficient"):
        raise PermissionError("trial_source_insufficient")
    for key in IC._SNAPSHOT_KEYS - {"version", "sufficient_source"}:
        IC._text(manifest[key])
    IC._digest(manifest["grant_hash"])
    for key in ("source_hash", "fixture_hash"):
        IC._digest(row[key])
    try:
        ZoneInfo(manifest["timezone"])
        for key in ("captured_at", "cutoff"):
            if datetime.fromisoformat(manifest[key]).utcoffset() is None:
                raise ValueError("timezone_required")
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("invalid_source_time") from error
    pinned = {**manifest, "source_hash": row["source_hash"], "fixture_hash": row["fixture_hash"], "status": row["status"]}
    if IM._hash(pinned) != row["manifest_hash"]:
        raise IM.ConflictError("trial_source_manifest_changed")
    return manifest


def _exclude_baseline_body(raw, baseline):
    """Basic exact full-body exclusion only; no partial or semantic taint claim."""
    if raw and raw in baseline:
        raise PermissionError("trial_source_context_overlap")


def authorize_case(connection, brain, request, inventory, *, blobs, baseline_content=None):
    """Prove exact granted snapshot membership and bytes; return no case or content.

    No qualification reservation, exposure, solver projection or answer read occurs.
    ``blobs`` is a required host dependency that verifies encrypted storage itself.
    ``baseline_content`` is optional trusted host content; when supplied, reject
    any complete nonempty source/fixture body embedded verbatim in that baseline.
    """
    if not callable(getattr(blobs, "read", None)):
        raise ValueError("trial_source_blobs_required")
    if baseline_content is not None and not isinstance(baseline_content, str):
        raise ValueError("invalid_trial_source_baseline")
    baseline = baseline_content.encode("utf-8") if baseline_content is not None else None
    inventory = validate_inventory(inventory)
    request = IC._frozen(request)
    if not isinstance(request, dict) or not isinstance(request.get("pins"), dict):
        raise ValueError("invalid_trial_source_request")
    IC._text(brain)
    for key in ("case_set_id", "case_id"):
        IC._text(request.get(key), 128)
    for key in ("case_set_hash", "grant_hash"):
        IC._digest(request["pins"].get(key))
    case_set = IC.CaseStore._row(connection, "improvement_case_sets", brain, request["case_set_id"])
    manifest = json.loads(case_set["manifest_json"])
    if case_set["manifest_hash"] != request["pins"]["case_set_hash"] or IM._hash(manifest) != case_set["manifest_hash"]:
        raise IM.ConflictError("trial_source_case_set_changed")
    # _case is a read-only scoped lookup and does not use its CaseStore instance.
    case = IC.CaseStore._case(None, connection, brain, request["case_set_id"], request["case_id"])
    matches = [item for item in manifest.get("cases", []) if item.get("id") == request["case_id"]]
    if len(matches) != 1 or matches[0] != case:
        raise IM.ConflictError("trial_source_case_changed")
    refs = case.get("snapshot_ids")
    if (not isinstance(refs, list) or not refs or len(refs) > 100
            or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs)):
        raise IM.ConflictError("trial_source_case_changed")
    for snapshot_id in refs:
        snapshot = IC.CaseStore._row(connection, "improvement_snapshots", brain, snapshot_id)
        snapshot_manifest = _manifest(snapshot)
        if snapshot_manifest["grant_hash"] != request["pins"]["grant_hash"]:
            raise PermissionError("trial_source_grant_mismatch")
        metadata = {key: snapshot[key] if key in {"source_hash", "fixture_hash"} else snapshot_manifest[key]
                    for key in _MATCH_FIELDS}
        if sum(all(entry[key] == value for key, value in metadata.items()) for entry in inventory) != 1:
            raise PermissionError("trial_source_not_granted")
        for digest in {snapshot["source_hash"], snapshot["fixture_hash"]}:
            raw = blobs.read(brain, digest)
            if not isinstance(raw, bytes) or len(raw) > MAX_ASSET_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                raise AssetUnavailable("trial_source_bytes_changed")
            if baseline is not None:
                _exclude_baseline_body(raw, baseline)
