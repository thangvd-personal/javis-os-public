"""JSON Schema Draft 2020-12 Validator for Standard IAM/PDP Contract v1.0."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACT_SCHEMAS_DIR = (
    Path(__file__).resolve().parents[2]
    / "brains"
    / "SME-BTOS"
    / "documents"
    / "contract"
    / "schemas"
)


class ContractSchemaError(ValueError):
    """Raised when a contract request or response payload violates JSON Schema v1.0."""


@lru_cache(maxsize=1)
def _load_schema_registry() -> tuple[dict[str, dict[str, Any]], Registry]:
    schemas_by_name: dict[str, dict[str, Any]] = {}
    resources: list[tuple[str, Resource]] = []

    for schema_path in sorted(CONTRACT_SCHEMAS_DIR.glob("*.schema.json")):
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
        schemas_by_name[schema_path.name] = raw
        res = Resource.from_contents(raw)
        if "$id" in raw:
            resources.append((raw["$id"], res))
        resources.append((f"./{schema_path.name}", res))
        resources.append((schema_path.name, res))

    registry = Registry().with_resources(resources)
    return schemas_by_name, registry


def validate_contract_payload(schema_name: str, payload: Mapping[str, Any]) -> None:
    """Validate payload against a named schema in `documents/contract/schemas/`."""
    schemas_by_name, registry = _load_schema_registry()
    if schema_name not in schemas_by_name:
        raise KeyError(f"Unknown contract schema: {schema_name}")
    schema = schemas_by_name[schema_name]
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.path) or "<root>"
        raise ContractSchemaError(f"{schema_name} validation failed at {loc}: {first.message}")
