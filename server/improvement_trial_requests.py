"""Closed owner API data, without authority, storage, callbacks or engine dispatch.

The HTTP caller must authenticate/authorize before using these projections. The
300-second timeout is a technical single-attempt bound, not a monetary or quality
budget. Private answers are data; this module never reports them in an error.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping

from starlette.requests import Request

import improvement_cases as IC
import improvement_store as IM
from improvement_blobs import MAX_ASSET_BYTES

MAX_BODY_BYTES = 2 * MAX_ASSET_BYTES
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
MAX_TIMEOUT_SECONDS = 300
_I64 = 2 ** 63
_CONTRACT = {"objective", "output", "criteria", "primary_metric", "unknown_conditions"}
_START = {"experiment_id", "contract_id", "case_set_id", "case_id", "base_bundle_id", "candidate_bundle_id",
          "variant", "repeat", "timeout_seconds", "expected_context_generation"}
_OPERATIONS = {
    "create_context": {"surface", "target_ids", "source_selections"},
    "revoke_context": {"expected_generation"},
    "capture_snapshot": {"source_ref"},
    "create_contract": _CONTRACT,
    "create_case_set": {"contract_id", "seed", "cases"},
    "create_bundle": {"revision_ids"},
    "start_trial": _START,
    "cancel_trial": {"expected_generation"},
    "recover_trials": set(),
    "record_owner_decision": {"criterion_id", "verdict", "output_hash", "contract_hash", "expected_generation"},
    "reevaluate": {"expected_generation"},
}


class TrialRequestError(ValueError):
    """Only these fixed codes may cross the HTTP error boundary."""
    def __init__(self, code="invalid_trial_request"):
        if code not in {"invalid_trial_request", "trial_request_too_large", "trial_request_unavailable", "trial_checker_unavailable"}:
            code = "invalid_trial_request"
        self.code = code
        self.status_code = 413 if code == "trial_request_too_large" else 400
        super().__init__(code)


def _invalid():
    raise TrialRequestError()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _finite(raw):
    value = float(raw)
    if not math.isfinite(value):
        _invalid()
    return value


def _depth(raw):
    """Reject excessive nesting before the recursive JSON decoder is invoked."""
    level, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            level += 1
            if level > MAX_JSON_DEPTH:
                _invalid()
        elif char in (93, 125):
            level -= 1


def _json_tree(value):
    nodes = 0
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _invalid()
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _invalid()
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            item.encode("utf-8", errors="strict")
        elif item is not None and type(item) not in (bool, int, float):
            _invalid()
        elif type(item) is float and not math.isfinite(item):
            _invalid()
    visit(value, 0)


def _integer(value, low=1, high=_I64 - 1):
    if type(value) is not int or not low <= value <= high:
        _invalid()


def _id(value):
    if type(value) is not str or not IC._ID.fullmatch(value):
        _invalid()


def _ids(value, maximum):
    if type(value) is not list or not 1 <= len(value) <= maximum:
        _invalid()
    for item in value:
        _id(item)
    if len(set(value)) != len(value):
        _invalid()


def _source_ref(value):
    from improvement_trial_file_sources import _relative
    IC._text(value, 256)
    _relative(value)  # Pure portable path/deny-policy validation, no source reads.


def _context(value):
    surface = value["surface"]
    IC._fields(surface, {"kind", "session_id", "language"})
    if (surface["kind"] != "dashboard" or type(surface["session_id"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", surface["session_id"])
            or type(surface["language"]) is not str):
        _invalid()
    import lang_registry
    if surface["language"] not in lang_registry.LANGS:
        _invalid()
    _ids(value["target_ids"], 20)
    selections = value["source_selections"]
    if type(selections) is not list or not 1 <= len(selections) <= 1000:
        _invalid()
    from improvement_trial_file_sources import _metadata
    seen = set()
    for entry in selections:
        IC._fields(entry, {"source_ref", "timezone", "data_period", "cutoff", "schema"})
        _source_ref(entry["source_ref"])
        if entry["source_ref"] in seen:
            _invalid()
        seen.add(entry["source_ref"])
        _metadata({key: child for key, child in entry.items() if key != "source_ref"} |
                  {"freshness_policy": "require_current_version"})


def _contract(value, registry=None):
    """Validate the public union, then reuse the real complete-contract validator."""
    projected = {key: value[key] for key in _CONTRACT}
    projected = IC._frozen(projected)
    criteria = projected["criteria"]
    if type(criteria) is not list or not 1 <= len(criteria) <= 12:
        _invalid()
    for criterion in criteria:
        IC._fields(criterion, {"id", "category", "critical", "description", "kind", "config"})
        kind, config = criterion["kind"], criterion["config"]
        if type(kind) is not str:
            _invalid()
        if kind in ("host_checker", "command_ok"):
            IC._fields(config, {"checker_id"})
            IC._text(config["checker_id"], 128)
            version, digest = "request-shape-only", "0" * 64
            if registry is not None:
                entry = registry.get(config["checker_id"])
                if not isinstance(entry, Mapping) or set(entry) != {"version", "hash", "check"} or not callable(entry["check"]):
                    raise TrialRequestError("trial_checker_unavailable")
                try:
                    IC._text(entry["version"], 128)
                    IC._digest(entry["hash"])
                except (TypeError, ValueError):
                    raise TrialRequestError("trial_checker_unavailable") from None
                version, digest = entry["version"], entry["hash"]
            criterion["config"] = {"checker_id": config["checker_id"], "version": version, "hash": digest}
        elif type(config) is dict and "path" in config:
            IC.output_path(config["path"])
    # Pin material is always host supplied. It never comes from the public JSON.
    from improvement_evaluation import EVALUATOR_PIN
    projected.update(version=1, evaluator=dict(EVALUATOR_PIN), output_hash_policy="raw_sha256")
    IC._contract(projected)
    return projected


def _cases(value):
    _id(value["contract_id"])
    _integer(value["seed"], -_I64, _I64 - 1)
    cases = value["cases"]
    if type(cases) is not list or not 1 <= len(cases) <= 1000:
        _invalid()
    ids, families = set(), set()
    for case in cases:
        IC._fields(case, {"id", "family_key", "split", "task", "snapshot_ids", "answer"})
        _id(case["id"])
        IC._text(case["family_key"], 500)
        IC._text(case["task"], MAX_ASSET_BYTES)
        if (case["id"] in ids or case["family_key"] in families
                or case["split"] not in ("tuning", "qualification", "regression")):
            _invalid()
        ids.add(case["id"])
        families.add(case["family_key"])
        _ids(case["snapshot_ids"], 100)
        if len(IM._json(case["answer"]).encode("utf-8")) > MAX_ASSET_BYTES:
            _invalid()


def validate_body(value, operation):
    """Pure closed public schema projection; authorization is the caller's job."""
    try:
        if type(operation) is not str or operation not in _OPERATIONS:
            _invalid()
        _json_tree(value)
        IC._fields(value, {"event_key"} | _OPERATIONS[operation])
        value = IC._frozen(value)
        if type(value["event_key"]) is not str or not 8 <= len(value["event_key"]) <= 160:
            _invalid()
        if "expected_generation" in value:
            _integer(value["expected_generation"])
        if operation == "create_context":
            _context(value)
        elif operation == "capture_snapshot":
            _source_ref(value["source_ref"])
        elif operation == "create_contract":
            _contract(value)
        elif operation == "create_case_set":
            _cases(value)
        elif operation == "create_bundle":
            _ids(value["revision_ids"], 100)
        elif operation == "start_trial":
            for key in ("experiment_id", "contract_id", "case_set_id", "case_id", "base_bundle_id", "candidate_bundle_id"):
                _id(value[key])
            if value["variant"] not in ("baseline", "candidate"):
                _invalid()
            _integer(value["repeat"], 1, 1_000_000)
            _integer(value["timeout_seconds"], 1, MAX_TIMEOUT_SECONDS)
            _integer(value["expected_context_generation"])
        elif operation == "record_owner_decision":
            _id(value["criterion_id"])
            if value["verdict"] not in ("pass", "fail", "unknown"):
                _invalid()
            IC._digest(value["output_hash"])
            IC._digest(value["contract_hash"])
        return value
    except TrialRequestError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, PermissionError):
        raise TrialRequestError() from None
    except (OSError, RuntimeError):
        raise TrialRequestError("trial_request_unavailable") from None


def project_contract(value, *, checker_registry):
    """Resolve only trusted registry pins; never run a checker or accept a command."""
    try:
        public = validate_body(value, "create_contract")
        if not isinstance(checker_registry, Mapping):
            raise TrialRequestError("trial_checker_unavailable")
        return _contract(public, checker_registry)
    except TrialRequestError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
        raise TrialRequestError() from None
    except (OSError, RuntimeError):
        raise TrialRequestError("trial_request_unavailable") from None


async def read_json(request):
    """Bound bytes before JSON decoding, including cached and fragmented bodies."""
    if not isinstance(request, Request):
        raise TrialRequestError()
    raw = bytearray()
    try:
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                _invalid()
            if len(length.lstrip("0")) > 10 or int(length) > MAX_BODY_BYTES:
                raise TrialRequestError("trial_request_too_large")
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                raise TrialRequestError("trial_request_too_large")
            raw.extend(chunk)
    except TrialRequestError:
        raise
    except Exception:
        # asyncio.CancelledError is a BaseException and must propagate unchanged.
        raise TrialRequestError("trial_request_unavailable") from None
    try:
        _depth(raw)
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: _invalid(), parse_float=_finite)
        return value
    except TrialRequestError:
        raise
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise TrialRequestError() from None


async def parse_body(request, operation):
    if type(operation) is not str or operation not in _OPERATIONS:
        raise TrialRequestError()
    return validate_body(await read_json(request), operation)
