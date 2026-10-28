"""Pure typed baseline preparation data; no candidate or execution grant."""
import math

from improvement_experiment_protocol import definition, _id, _digest, _freeze
from improvement_store import _hash
from improvement_store import _json
from improvement_execution_limits import MAX_PREPARATION_TRIALS

PINS = {"experiment_id", "base_bundle_hash", "contract_hash", "case_set_hash", "engine_config_hash",
        "grant_hash", "scope_hash", "policy_version"}


def preparation_schedule(value, case_ids, case_seed):
    value = definition(value)
    if (type(case_ids) is not list or not 1 <= len(case_ids) <= 1000
            or any(type(item) is not str for item in case_ids) or len(set(case_ids)) != len(case_ids)
            or type(case_seed) is not int):
        raise ValueError("invalid_preparation_schedule")
    for identifier in case_ids:
        _id(identifier)
    seed = _hash(["javis-preparation-schedule-v1", _hash(value), case_seed])
    ordered = sorted(case_ids, key=lambda identifier: (_hash([seed, identifier]), identifier))
    return {"version": 1, "experiment_id": value["experiment_id"], "definition_hash": _hash(value),
            "context_id": value["context_id"], "case_set_id": value["case_set_id"],
            "case_set_hash": value["case_set_hash"], "repeat_policy": value["repeat_policy"], "schedule_seed": seed,
            "cells": [{"case_id": identifier, "repeat": repeat} for identifier in ordered
                      for repeat in range(1, value["repeat_policy"]["repeats"] + 1)]}


def validate_preparation_request_v3(value):
    """Closed shape only; frozen SQL/blob lineage and live source checks are separate."""
    request = _freeze(value)
    if type(request) is not dict or set(request) != {
            "version", "pins", "case_set_id", "case_id", "variant", "repeat", "engine_config",
            "baseline_context_hash", "target_generations", "deadline_at", "preparation"}:
        raise ValueError("invalid_preparation_request")
    if (type(request["version"]) is not int or request["version"] != 3 or request["variant"] != "baseline"
            or type(request["repeat"]) is not int or request["repeat"] not in (1, 2)):
        raise ValueError("invalid_preparation_request")
    for key in ("case_set_id", "case_id"):
        _id(request[key])
    _digest(request["baseline_context_hash"])
    pins = request["pins"]
    if type(pins) is not dict or set(pins) != PINS:
        raise ValueError("invalid_preparation_pins")
    _id(pins["experiment_id"])
    for key in PINS - {"experiment_id", "policy_version"}:
        _digest(pins[key])
    if type(pins["policy_version"]) is not int or not 0 <= pins["policy_version"] < 2 ** 63:
        raise ValueError("invalid_preparation_policy")
    lineage = request["preparation"]
    if type(lineage) is not dict or set(lineage) != {
            "version", "context_id", "definition_hash", "preparation_id", "schedule_hash", "cell_position"}:
        raise ValueError("invalid_preparation_lineage")
    if (type(lineage["version"]) is not int or lineage["version"] != 1
            or type(lineage["cell_position"]) is not int or not 0 <= lineage["cell_position"] < MAX_PREPARATION_TRIALS):
        raise ValueError("invalid_preparation_lineage")
    for key in ("context_id", "preparation_id"):
        _id(lineage[key])
    for key in ("definition_hash", "schedule_hash"):
        _digest(lineage[key])
    engine = request["engine_config"]
    if type(engine) is not dict or set(engine) != {
            "version", "provider", "engine_id", "model", "reasoning", "cli_path", "cli_hash",
            "cli_version", "sdk_version", "profile_version", "profile_hash"}:
        raise ValueError("invalid_preparation_engine")
    if (type(engine["version"]) is not int or engine["version"] != 1
            or any(type(child) is not str or not 1 <= len(child) <= 4096 for key, child in engine.items() if key != "version")):
        raise ValueError("invalid_preparation_engine")
    for key in ("cli_hash", "profile_hash"):
        _digest(engine[key])
    if (engine["provider"] != "anthropic-cli" or engine["engine_id"] != "claude-sdk"
            or _hash(engine) != pins["engine_config_hash"]):
        raise ValueError("unsupported_preparation_engine")
    targets = request["target_generations"]
    if type(targets) is not dict or len(targets) != 1:
        raise ValueError("invalid_preparation_targets")
    for identifier, generation in targets.items():
        _id(identifier)
        if type(generation) is not int or not 1 <= generation < 2 ** 63:
            raise ValueError("invalid_preparation_targets")
    deadline = request["deadline_at"]
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("invalid_preparation_deadline")
    if len(_json(request).encode("utf-8")) > 65536:
        raise ValueError("preparation_request_too_large")
    return request


def logical_key_v3(value):
    request = validate_preparation_request_v3(value)
    return _hash(["improvement-trial-v3", request["pins"]["experiment_id"], request["preparation"],
                  request["case_set_id"], request["case_id"], request["repeat"], "baseline"])
