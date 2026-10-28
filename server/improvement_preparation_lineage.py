"""Actual preparation SQL/blob lineage, without fake arms or host/runner imports."""
from __future__ import annotations

import json

import improvement_store as IM
from improvement_blobs import AssetUnavailable
from improvement_experiment_lineage import _row, _same
from improvement_experiment_protocol import definition
from improvement_preparation_protocol import PINS, preparation_schedule, validate_preparation_request_v3


def current_preparation_fences(connection, brain, proof):
    """Final SQL refresh after slow observations; never decrypt or regrant."""
    from improvement_evaluation import EVALUATOR_PIN
    frozen = proof["definition"]
    experiment = _row(connection, "improvement_experiments", brain, frozen["experiment_id"])
    if experiment["phase"] != "preparing":
        raise PermissionError("preparation_execution_unavailable")
    if experiment["definition_hash"] != IM._hash(frozen) or frozen["evaluator"] != EVALUATOR_PIN:
        raise IM.ConflictError("preparation_definition_changed")
    if connection.execute("SELECT 1 FROM improvement_experiment_selections WHERE experiment_id=? AND brain_id=?",
                          (experiment["id"], brain)).fetchone():
        raise PermissionError("preparation_experiment_sealed")
    grant = connection.execute("SELECT * FROM improvement_trial_context_grants WHERE context_id=? AND brain_id=?",
                               (frozen["context_id"], brain)).fetchone()
    policy = IM.ImprovementStore._policy(connection, brain)
    if grant is None or grant["status"] != "active" or policy["mode"] == "off":
        raise PermissionError("preparation_authority_revoked")
    if grant["generation"] != frozen["context_generation"] or policy["version"] != frozen["policy_version"]:
        raise IM.ConflictError("preparation_authority_changed")
    target = _row(connection, "improvement_targets", brain, frozen["target_id"])
    if (target["status"] != "live" or target["generation"] != frozen["base_generation"]
            or target["current_revision_id"] != frozen["base_revision_id"] or target["source_hash"] != frozen["base_hash"]):
        raise IM.ConflictError("preparation_base_changed")
    # This check must survive the final source observation, not just blob loading.
    if connection.execute("SELECT 1 FROM improvement_case_families t JOIN improvement_case_families h "
        "ON h.brain_id=t.brain_id AND h.fingerprint=t.fingerprint WHERE t.brain_id=? AND t.case_set_id=? "
        "AND t.case_id=? AND h.split IN ('qualification','regression') LIMIT 1",
        (brain, frozen["case_set_id"], proof["cell"]["case_id"])).fetchone():
        raise PermissionError("preparation_holdout_alias")


def load_preparation_lineage(connection, brain, value, *, blobs, current, require_link, trial_id=None):
    if type(current) is not bool or type(require_link) is not bool or (trial_id is not None and not require_link):
        raise ValueError("explicit_preparation_lineage_mode_required")
    request = validate_preparation_request_v3(value)
    try:
        return _load(connection, brain, request, blobs=blobs, current=current, require_link=require_link, trial_id=trial_id)
    except (IM.ConflictError, PermissionError):
        raise
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError) as error:
        raise AssetUnavailable("preparation_lineage_corrupt") from error


def _load(connection, brain, request, *, blobs, current, require_link, trial_id):
    pins, lineage = request["pins"], request["preparation"]
    experiment = _row(connection, "improvement_experiments", brain, pins["experiment_id"])
    frozen = definition(json.loads(experiment["definition_json"]))
    if IM._hash(frozen) != experiment["definition_hash"]:
        raise IM.ConflictError("preparation_definition_changed")
    _same({"id": frozen["experiment_id"], "context_id": frozen["context_id"], "target_id": frozen["target_id"]}, experiment)
    _same({key: frozen[key] for key in PINS}, pins)
    _same({"context_id": frozen["context_id"], "definition_hash": experiment["definition_hash"]}, lineage)
    if (request["case_set_id"] != frozen["case_set_id"] or request["baseline_context_hash"] != frozen["baseline_context_hash"]
            or request["target_generations"] != {frozen["target_id"]: frozen["base_generation"]}):
        raise IM.ConflictError("preparation_request_changed")
    context = _row(connection, "improvement_trial_contexts", brain, frozen["context_id"])
    _same({key: frozen[key] for key in ("experiment_id", "binding_hash", "engine_config_hash", "grant_hash", "scope_hash", "policy_version")}, context)
    if context["context_hash"] != frozen["baseline_context_hash"] or json.loads(context["target_generations_json"]) != request["target_generations"]:
        raise IM.ConflictError("preparation_context_changed")
    bundle = _row(connection, "improvement_bundles", brain, frozen["base_bundle_id"])
    revision = _row(connection, "improvement_revisions", brain, frozen["base_revision_id"])
    manifest = json.loads(bundle["manifest_json"])
    if (bundle["manifest_hash"] != frozen["base_bundle_hash"] or IM._hash(manifest) != frozen["base_bundle_hash"]
            or manifest != [{key: revision[key] for key in ("id", "target_id", "manifest_hash", "content_hash")}]
            or revision["target_id"] != frozen["target_id"] or revision["content_hash"] != frozen["base_hash"]
            or IM._hash(json.loads(revision["manifest_json"])) != revision["manifest_hash"]):
        raise IM.ConflictError("preparation_baseline_changed")
    contract = _row(connection, "improvement_contracts", brain, frozen["contract_id"])
    contract_value = json.loads(contract["contract_json"])
    if (contract["manifest_hash"] != frozen["contract_hash"] or IM._hash(contract_value) != frozen["contract_hash"]
            or contract_value["evaluator"] != frozen["evaluator"] or contract_value["primary_metric"] != {
                "criterion_id": frozen["endpoint"]["criterion_id"], "direction": "higher", "aggregation": "all_pass"}):
        raise IM.ConflictError("preparation_contract_changed")
    case_set = _row(connection, "improvement_case_sets", brain, frozen["case_set_id"])
    manifest = json.loads(case_set["manifest_json"])
    if case_set["manifest_hash"] != frozen["case_set_hash"] or IM._hash(manifest) != frozen["case_set_hash"]:
        raise IM.ConflictError("preparation_case_set_changed")
    associated = [json.loads(row[0]) for row in connection.execute(
        "SELECT result_json FROM improvement_events WHERE brain_id=? AND operation='owner_trial_case_set' ORDER BY id", (brain,))]
    associated = [item for item in associated if item.get("case_set_id") == frozen["case_set_id"]]
    if not associated or any(item != associated[0] for item in associated):
        raise PermissionError("preparation_case_set_unassociated")
    _same({key: frozen[key] for key in ("context_id", "experiment_id", "contract_id", "contract_hash", "case_set_id", "grant_hash")}, associated[0])
    if associated[0]["manifest_hash"] != frozen["case_set_hash"]:
        raise IM.ConflictError("preparation_case_set_changed")
    rows = [json.loads(row[0]) for row in connection.execute(
        "SELECT case_json FROM improvement_cases WHERE brain_id=? AND case_set_id=?", (brain, frozen["case_set_id"]))]
    if sorted(rows, key=lambda item: item["id"]) != sorted(manifest["cases"], key=lambda item: item["id"]):
        raise IM.ConflictError("preparation_case_set_changed")
    preparation = _row(connection, "improvement_preparations", brain, lineage["preparation_id"])
    _same({"experiment_id": experiment["id"], "context_id": frozen["context_id"], "definition_hash": experiment["definition_hash"],
           "schedule_hash": lineage["schedule_hash"]}, preparation)
    expected = preparation_schedule(frozen, [case["id"] for case in rows if case["split"] == "tuning"], manifest["seed"])
    raw = blobs.read(brain, preparation["schedule_blob_hash"])
    if (type(raw) is not bytes or len(raw) > 1048576 or preparation["schedule_blob_hash"] != preparation["schedule_hash"]
            or raw != IM._json(expected).encode("utf-8") or IM._hash(expected) != preparation["schedule_hash"]
            or preparation["cell_count"] != len(expected["cells"])):
        raise IM.ConflictError("preparation_schedule_changed")
    position = lineage["cell_position"]
    if position >= preparation["cell_count"]:
        raise IM.ConflictError("preparation_cell_changed")
    cell = expected["cells"][position] | {"variant": "baseline"}
    _same({key: request[key] for key in ("case_id", "repeat", "variant")}, cell)
    proof = {"definition": frozen, "preparation": dict(preparation), "cell": cell}
    if current:
        current_preparation_fences(connection, brain, proof)
    if require_link:
        link = connection.execute("SELECT * FROM improvement_preparation_trial_links WHERE preparation_id=? AND brain_id=? AND cell_position=?",
                                  (preparation["id"], brain, position)).fetchone()
        if link is None:
            raise PermissionError("preparation_trial_link_unavailable")
        trial = _row(connection, "improvement_trials", brain, link["trial_id"])
        _same({"experiment_id": experiment["id"], "request_hash": IM._hash(request), "case_id": cell["case_id"], "repeat": cell["repeat"]}, link)
        if (trial["request_hash"] != link["request_hash"] or trial["request_json"] != IM._json(request)
                or (trial_id is not None and trial["id"] != trial_id)):
            raise IM.ConflictError("preparation_trial_link_changed")
    return proof
