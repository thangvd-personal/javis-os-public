"""RFC 8785 (JSON Canonicalization Scheme - JCS) & SHA-256 Payload Hash Utilities."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any, Mapping


def _normalize_jcs_value(value: Any) -> Any:
    """Normalize numbers and nested containers according to RFC 8785 rules."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        fval = float(value)
        if math.isnan(fval) or math.isinf(fval):
            raise ValueError("JCS does not permit NaN or Infinity in JSON numbers")
        if fval == 0.0:
            return 0
        if fval.is_integer():
            return int(fval)
        return fval
    if isinstance(value, (list, tuple)):
        return [_normalize_jcs_value(item) for item in value]
    if isinstance(value, Mapping):
        # Sort keys by UTF-16 code units per RFC 8785 Section 3.2.3
        sorted_items = sorted(
            value.items(),
            key=lambda kv: str(kv[0]).encode("utf-16-be"),
        )
        return {str(k): _normalize_jcs_value(v) for k, v in sorted_items}
    raise TypeError(f"Unsupported type for JCS canonicalization: {type(value).__name__}")


def canonicalize_jcs(value: Any) -> bytes:
    """Serialize a JSON-compatible value into deterministic RFC 8785 UTF-8 bytes."""
    normalized = _normalize_jcs_value(value)
    if isinstance(normalized, Mapping):
        parts = [
            f"{json.dumps(k, ensure_ascii=False, separators=(',', ':'))}:{canonicalize_jcs(v).decode('utf-8')}"
            for k, v in normalized.items()
        ]
        return ("{" + ",".join(parts) + "}").encode("utf-8")
    if isinstance(normalized, list):
        parts = [canonicalize_jcs(item).decode("utf-8") for item in normalized]
        return ("[" + ",".join(parts) + "]").encode("utf-8")
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compute_jcs_sha256(value: Any) -> str:
    """Compute sha256:<64-hex> digest over RFC 8785 canonical JSON."""
    digest = hashlib.sha256(canonicalize_jcs(value)).hexdigest()
    return f"sha256:{digest}"


def compute_action_payload_hash(
    resource_id: str,
    operation: str,
    arguments: Mapping[str, Any] | None = None,
) -> str:
    """Compute canonical payload_hash over (resource_id, operation, normalized_arguments)."""
    envelope = {
        "arguments": dict(arguments or {}),
        "operation": str(operation),
        "resource_id": str(resource_id),
    }
    return compute_jcs_sha256(envelope)
