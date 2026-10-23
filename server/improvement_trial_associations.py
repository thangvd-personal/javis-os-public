"""Atomic owner case imports bound to an actual host context and immutable contract.

Association events are immutable history, not live source permission. Reads do not
warm sources or migrate stores. Dispatch must separately revalidate host authority.
"""
from __future__ import annotations

import json
from contextlib import closing

import improvement_cases as IC
import improvement_store as IM
from improvement_evaluation import EVALUATOR_PIN
from improvement_trial_file_sources import BrainFileFixtures
from improvement_trial_host import HostContextStore, _brain
from improvement_trial_sources import authorize_case

_OPERATION = "owner_trial_case_set"
_FIELDS = {"context_id", "experiment_id", "contract_id", "contract_hash", "case_set_id", "manifest_hash", "counts", "grant_hash"}
_CASE_FIELDS = {"id", "family_key", "split", "task", "snapshot_ids", "answer"}


def _association(connection, brain, case_set_id):
    result = None
    for row in connection.execute("SELECT result_json FROM improvement_events WHERE brain_id=? AND operation=? ORDER BY id",
                                  (brain, _OPERATION)):
        value = json.loads(row[0])
        IC._fields(value, _FIELDS)
        if value["case_set_id"] == case_set_id:
            if result is not None and value != result:
                raise IM.ConflictError("trial_case_set_association_conflict")
            result = value
    return result


def read_association(assets, principal, case_set_id):
    """Historical metadata only; no private answers, source capture or current grant claim."""
    brain = _brain(principal)
    if not isinstance(case_set_id, str) or not IC._ID.fullmatch(case_set_id):
        raise ValueError("invalid_trial_case_set_id")
    if IM._schema_version(assets.store) < 3:
        raise PermissionError("trial_case_set_unassociated")
    with closing(assets.store._conn()) as connection:
        result = _association(connection, brain, case_set_id)
        if result is None:
            raise PermissionError("trial_case_set_unassociated")
        case_set = IC.CaseStore._row(connection, "improvement_case_sets", brain, case_set_id)
        contract = IC.CaseStore._row(connection, "improvement_contracts", brain, result["contract_id"])
        context = HostContextStore._row(connection, brain, result["experiment_id"])
        if (case_set["manifest_hash"] != result["manifest_hash"]
                or IM._hash(json.loads(case_set["manifest_json"])) != case_set["manifest_hash"]
                or contract["manifest_hash"] != result["contract_hash"]
                or IM._hash(json.loads(contract["contract_json"])) != contract["manifest_hash"]
                or context["id"] != result["context_id"] or context["grant_hash"] != result["grant_hash"]):
            raise IM.ConflictError("trial_case_set_association_changed")
        return result


class TrialCaseAssociations:
    def __init__(self, host, *, checker_registry=None):
        if type(host) is not HostContextStore or type(host.fixture_observer) is not BrainFileFixtures:
            raise TypeError("concrete_trial_association_host_required")
        self.host, self.assets, self.store = host, host.assets, host.store
        self.cases = IC.CaseStore(self.assets)
        self.registry = {} if checker_registry is None else checker_registry
        if type(self.registry) is not dict:
            raise ValueError("invalid_trial_checker_registry")

    def get(self, principal, case_set_id):
        return read_association(self.assets, principal, case_set_id)

    def _contract(self, connection, brain, contract_id):
        row = self.cases._row(connection, "improvement_contracts", brain, contract_id)
        contract = json.loads(row["contract_json"])
        IC._contract(contract)
        if IM._hash(contract) != row["manifest_hash"]:
            raise IM.ConflictError("trial_contract_changed")
        if contract["evaluator"] != EVALUATOR_PIN:
            raise PermissionError("trial_contract_evaluator_unavailable")
        refs = set()
        for criterion in contract["criteria"]:
            if criterion["kind"] in {"host_checker", "command_ok"}:
                pin = criterion["config"]
                checker = self.registry.get(pin["checker_id"])
                if (type(checker) is not dict or set(checker) != {"version", "hash", "check"}
                        or checker["version"] != pin["version"] or checker["hash"] != pin["hash"]
                        or not callable(checker["check"])):
                    raise PermissionError("trial_contract_checker_unavailable")
                refs.add(pin["checker_id"])
        return row["manifest_hash"], sorted(refs)

    def _authority(self, connection, principal, experiment_id, contract_id):
        row = self.host._row(connection, _brain(principal), experiment_id)
        binding = self.host._decode(row)
        row, binding = self.host._snapshot_binding(connection, principal, experiment_id, binding["scope"]["source_refs"][0])
        contract_hash, refs = self._contract(connection, row["brain_id"], contract_id)
        return row, binding, contract_hash, refs

    def create(self, principal, experiment_id, contract_id, cases, seed, *, event_key):
        brain = _brain(principal)
        for identifier in (experiment_id, contract_id):
            if not isinstance(identifier, str) or not IC._ID.fullmatch(identifier):
                raise ValueError("invalid_trial_association_request")
        cases = IC._frozen(cases)
        if not isinstance(cases, list) or not 1 <= len(cases) <= 1000 or type(seed) is not int:
            raise ValueError("invalid_trial_cases")
        for case in cases:
            IC._fields(case, _CASE_FIELDS)
        if IM._schema_version(self.store) < 3:
            raise PermissionError("trial_context_unavailable")
        payload = {"experiment_id": experiment_id, "contract_id": contract_id, "seed": seed, "cases_hash": IM._hash(cases)}
        with closing(self.store._conn()) as connection:
            self.host._row(connection, brain, experiment_id)
            replay = self.assets._replay(connection, brain, event_key, _OPERATION, payload)
            if replay is not None:
                return replay
            row, binding, contract_hash, refs = self._authority(connection, principal, experiment_id, contract_id)
        self.host._snapshot_observe(principal, binding)
        inner_key = "owner-cases-" + IM._hash([experiment_id, contract_id, event_key])
        with self.store._Tx(self.store) as connection:
            replay = self.assets._replay(connection, brain, event_key, _OPERATION, payload)
            if replay is not None:
                return replay
            current, current_binding, current_contract, current_refs = self._authority(connection, principal, experiment_id, contract_id)
            if (current["id"] != row["id"] or current_binding != binding
                    or current_contract != contract_hash or current_refs != refs):
                raise IM.ConflictError("trial_case_set_authority_changed")
            result, created = self.cases._put_case_set_in(connection, principal,
                [{**case, "evaluator_refs": refs} for case in cases], seed, event_key=inner_key)
            associated = _association(connection, brain, result["case_set_id"])
            metadata = {**result, "context_id": row["id"], "experiment_id": experiment_id,
                        "contract_id": contract_id, "contract_hash": contract_hash, "grant_hash": row["grant_hash"]}
            if not created and associated is None:
                raise PermissionError("trial_case_set_unassociated")
            if associated is not None and associated != metadata:
                raise IM.ConflictError("trial_case_set_association_conflict")
            for case in cases:
                authorize_case(connection, brain, {"case_set_id": result["case_set_id"], "case_id": case["id"],
                    "pins": {"case_set_hash": result["manifest_hash"], "grant_hash": row["grant_hash"]}},
                    binding["fixture_inventory"], blobs=self.host.blobs,
                    baseline_content=binding["capture"]["baseline_context"]["content"])
            self.host._snapshot_observe(principal, binding)
            final, final_binding, final_contract, final_refs = self._authority(connection, principal, experiment_id, contract_id)
            if (final["id"] != row["id"] or final_binding != binding
                    or final_contract != contract_hash or final_refs != refs):
                raise IM.ConflictError("trial_case_set_authority_changed")
            self.assets._event(connection, principal, event_key, _OPERATION, payload, metadata)
            return metadata
