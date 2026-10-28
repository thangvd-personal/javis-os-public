"""Immutable preparation metadata; actual source observation/admission is separate."""
from __future__ import annotations

import json
import secrets
import time
from contextlib import closing

import improvement_cases as IC
import improvement_store as IM
from improvement_experiments import ExperimentRecords, _identifier, _generation
from improvement_experiment_protocol import definition
from improvement_preparation_protocol import preparation_schedule
from improvement_trial_records import _brain


class PreparationRecords:
    def __init__(self, assets):
        if type(assets) is not IM.ImprovementStore:
            raise TypeError("concrete_preparation_store_required")
        self.assets, self.store, self.blobs = assets, assets.store, assets.blobs
        self.experiments = ExperimentRecords(assets)

    @staticmethod
    def _public(row):
        return {"preparation_id": row["id"], **{key: row[key] for key in
                ("experiment_id", "context_id", "definition_hash", "schedule_hash", "cell_count", "created_at")}}

    def get(self, principal, identifier):
        brain = _brain(principal)
        _identifier(identifier)
        if IM._schema_version(self.store) < 6:
            raise PermissionError("preparation_unavailable")
        with closing(self.store._conn()) as connection:
            row = connection.execute("SELECT * FROM improvement_preparations WHERE id=? AND brain_id=?",
                                     (identifier, brain)).fetchone()
            if row is None:
                raise PermissionError("preparation_unavailable")
            return self._public(row)

    def list(self, principal, experiment_id):
        brain = _brain(principal)
        _identifier(experiment_id)
        if IM._schema_version(self.store) < 6:
            return []
        with closing(self.store._conn()) as connection:
            self.experiments._row(connection, brain, experiment_id)
            return [self._public(row) for row in connection.execute(
                "SELECT * FROM improvement_preparations WHERE experiment_id=? AND brain_id=? ORDER BY id", (experiment_id, brain))]

    def _freeze_in(self, connection, principal, schedule, blob_hash, *, expected_generation, event_key):
        """Metadata CAS only; the concrete host must observe sources before admission."""
        IC.CaseStore(self.assets)._own_transaction(connection)
        brain = _brain(principal)
        _generation(expected_generation)
        schedule = IC._frozen(schedule)
        if type(schedule) is not dict or "experiment_id" not in schedule:
            raise ValueError("invalid_preparation_schedule")
        _identifier(schedule["experiment_id"])
        IC._digest(blob_hash)
        if IM._hash(schedule) != blob_hash:
            raise IM.ConflictError("preparation_schedule_changed")
        payload = {"schedule": schedule, "blob_hash": blob_hash, "expected_generation": expected_generation}
        replay = self.assets._replay(connection, brain, event_key, "preparation_freeze", payload)
        if replay is not None:
            return replay
        row = self.experiments._row(connection, brain, schedule["experiment_id"])
        self.experiments._public(row)
        if row["generation"] != expected_generation or row["phase"] != "proposed":
            raise IM.ConflictError("experiment_state_stale")
        value = definition(json.loads(row["definition_json"]))
        self.experiments._bound_in(connection, brain, value, value["context_generation"])
        case_set = IC.CaseStore._row(connection, "improvement_case_sets", brain, value["case_set_id"])
        manifest = json.loads(case_set["manifest_json"])
        actual = [json.loads(item[0]) for item in connection.execute(
            "SELECT case_json FROM improvement_cases WHERE brain_id=? AND case_set_id=?", (brain, value["case_set_id"]))]
        if (IM._hash(manifest) != value["case_set_hash"]
                or sorted(actual, key=lambda item: item["id"]) != sorted(manifest["cases"], key=lambda item: item["id"])
                or schedule != preparation_schedule(value, [item["id"] for item in actual if item["split"] == "tuning"], manifest["seed"])):
            raise IM.ConflictError("preparation_schedule_changed")
        if connection.execute("SELECT 1 FROM improvement_experiment_arms WHERE experiment_id=? AND brain_id=?",
                              (row["id"], brain)).fetchone():
            raise IM.ConflictError("preparation_after_arm_binding")
        identifier, stamp = "ip_" + secrets.token_hex(12), time.time()
        connection.execute("INSERT INTO improvement_preparations VALUES(?,?,?,?,?,?,?,?,?)", (
            identifier, brain, row["id"], row["context_id"], row["definition_hash"], blob_hash, blob_hash,
            len(schedule["cells"]), stamp))
        connection.execute("UPDATE improvement_experiments SET phase='preparing',generation=generation+1,updated_at=? "
                           "WHERE id=? AND brain_id=?", (stamp, row["id"], brain))
        saved = connection.execute("SELECT * FROM improvement_preparations WHERE id=? AND brain_id=?", (identifier, brain)).fetchone()
        result = {**self._public(saved), "generation": expected_generation + 1}
        self.assets._event(connection, principal, event_key, "preparation_freeze", payload, result)
        return result
