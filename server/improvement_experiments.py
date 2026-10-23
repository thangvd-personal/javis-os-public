"""Owner experiment metadata without engine, source capture or automatic recovery."""
from __future__ import annotations

import json
import time
from contextlib import closing

import improvement_cases as IC
import improvement_store as IM
from improvement_trial_records import _brain
from improvement_experiment_protocol import definition as _definition
from improvement_experiment_schema import TERMINAL_PHASES

_BLOCKERS = {"source_unavailable", "runtime_unavailable", "quota_unavailable", "candidate_unavailable",
             "cleanup_unavailable", "reviewer_unavailable"}
_STOP_REASONS = {"owner_requested", "source_changed", "policy_changed", "candidate_rejected", "evidence_incomplete", "keep_base"}


def _generation(value):
    if type(value) is not int or not 1 <= value < 2 ** 63:
        raise ValueError("invalid_experiment_generation")


def _identifier(value):
    if type(value) is not str or not IC._ID.fullmatch(value):
        raise ValueError("invalid_experiment_identifier")
    return value


class ExperimentRecords:
    def __init__(self, assets):
        if type(assets) is not IM.ImprovementStore:
            raise TypeError("concrete_experiment_store_required")
        self.assets, self.store, self.blobs = assets, assets.store, assets.blobs

    @staticmethod
    def _row(connection, brain, identifier):
        row = connection.execute("SELECT * FROM improvement_experiments WHERE id=? AND brain_id=?",
                                 (identifier, brain)).fetchone()
        if row is None:
            raise PermissionError("experiment_unavailable")
        return row

    def _available(self):
        if IM._schema_version(self.store) < 5:
            raise PermissionError("experiment_unavailable")

    @staticmethod
    def _public(row):
        definition = _definition(json.loads(row["definition_json"]))
        if (IM._hash(definition) != row["definition_hash"] or definition["experiment_id"] != row["id"]
                or definition["context_id"] != row["context_id"] or definition["target_id"] != row["target_id"]):
            raise IM.ConflictError("experiment_definition_changed")
        return {"experiment_id": row["id"], "context_id": row["context_id"], "target_id": row["target_id"],
                "definition_hash": row["definition_hash"], "phase": row["phase"], "generation": row["generation"],
                "verdict": row["verdict"], "resume_from": row["resume_from"], "blocker_hash": row["blocker_hash"],
                "stop_kind": row["stop_kind"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                "authority_status": "not_checked"}

    def _bound_in(self, connection, brain, value, expected_context_generation):
        """Current SQL authority only; Plans must also observe real sources before commit."""
        from improvement_trial_records import HostContextRecords
        from improvement_trial_associations import _association
        from improvement_evaluation import EVALUATOR_PIN
        context = HostContextRecords._row(connection, brain, value["experiment_id"])
        grant = HostContextRecords._grant(connection, context)
        if grant["status"] != "active":
            raise PermissionError("trial_source_grant_revoked")
        if (context["id"] != value["context_id"] or grant["generation"] != expected_context_generation
                or grant["generation"] != value["context_generation"]
                or json.loads(context["target_generations_json"]) != {value["target_id"]: value["base_generation"]}
                or context["context_hash"] != value["baseline_context_hash"]
                or any(context[key] != value[key] for key in ("binding_hash", "engine_config_hash", "grant_hash", "scope_hash", "policy_version"))):
            raise IM.ConflictError("experiment_context_changed")
        policy = self.assets._policy(connection, brain)
        if policy["mode"] == "off":
            raise PermissionError("improvement_disabled")
        if policy["version"] != value["policy_version"]:
            raise IM.ConflictError("experiment_policy_changed")
        target = self.assets._target(connection, brain, value["target_id"])
        if (target["kind"] != "skill" or target["status"] != "live" or target["generation"] != value["base_generation"]
                or target["current_revision_id"] != value["base_revision_id"] or target["source_hash"] != value["base_hash"]
                or target["ownership"] not in ("user_managed", "user_protected")):
            raise IM.ConflictError("experiment_base_changed")
        revision = IC.CaseStore._row(connection, "improvement_revisions", brain, value["base_revision_id"])
        bundle = IC.CaseStore._row(connection, "improvement_bundles", brain, value["base_bundle_id"])
        manifest = json.loads(bundle["manifest_json"])
        if (revision["target_id"] != target["id"] or revision["content_hash"] != value["base_hash"]
                or IM._hash(json.loads(revision["manifest_json"])) != revision["manifest_hash"]
                or bundle["manifest_hash"] != value["base_bundle_hash"] or IM._hash(manifest) != bundle["manifest_hash"]
                or manifest != [{key: revision[key] for key in ("id", "target_id", "manifest_hash", "content_hash")}]):
            raise IM.ConflictError("experiment_base_changed")
        contract = IC.CaseStore._row(connection, "improvement_contracts", brain, value["contract_id"])
        contract_value = json.loads(contract["contract_json"])
        IC._contract(contract_value)
        metric = contract_value["primary_metric"]
        if (contract["manifest_hash"] != value["contract_hash"] or IM._hash(contract_value) != contract["manifest_hash"]
                or contract_value["evaluator"] != value["evaluator"] or value["evaluator"] != EVALUATOR_PIN
                or metric != {"criterion_id": value["endpoint"]["criterion_id"], "direction": "higher", "aggregation": "all_pass"}):
            raise IM.ConflictError("experiment_contract_changed")
        case_set = IC.CaseStore._row(connection, "improvement_case_sets", brain, value["case_set_id"])
        if (case_set["manifest_hash"] != value["case_set_hash"]
                or IM._hash(json.loads(case_set["manifest_json"])) != case_set["manifest_hash"]):
            raise IM.ConflictError("experiment_case_set_changed")
        association = _association(connection, brain, value["case_set_id"])
        if association is None or any(association.get(key) != value[key] for key in
                ("context_id", "experiment_id", "contract_id", "contract_hash", "grant_hash")):
            raise PermissionError("experiment_case_set_unassociated")
        if association["manifest_hash"] != value["case_set_hash"]:
            raise IM.ConflictError("experiment_case_set_changed")

    def create(self, principal, definition, *, expected_context_generation, event_key):
        """Persist proposed metadata; this method grants no source, trial or model access."""
        brain = _brain(principal)
        value = _definition(definition)
        _generation(expected_context_generation)
        IM.ensure(self.store)
        with self.store._Tx(self.store) as connection:
            return self._create_in(connection, principal, value, expected_context_generation, event_key)

    def _create_in(self, connection, principal, value, expected_context_generation, event_key):
        """Use an existing concrete writer; never open a nested transaction."""
        IC.CaseStore(self.assets)._own_transaction(connection)
        brain = _brain(principal)
        value = _definition(value)
        _generation(expected_context_generation)
        payload = {"definition": value, "expected_context_generation": expected_context_generation}
        replay = self.assets._replay(connection, brain, event_key, "experiment_create", payload)
        if replay is not None:
            return replay
        self._bound_in(connection, brain, value, expected_context_generation)
        existing = connection.execute("SELECT * FROM improvement_experiments WHERE id=? AND brain_id=?",
                                      (value["experiment_id"], brain)).fetchone()
        if existing is not None:
            if existing["definition_hash"] != IM._hash(value):
                raise IM.ConflictError("experiment_identity_conflict")
            result = self._public(existing)
        else:
            if connection.execute("SELECT 1 FROM improvement_experiments WHERE brain_id=? "
                    "AND phase NOT IN ('completed','rejected','inconclusive','cancelled','stale')", (brain,)).fetchone():
                raise IM.ConflictError("experiment_brain_busy")
            now = time.time()
            connection.execute("INSERT INTO improvement_experiments(id,brain_id,context_id,target_id,definition_hash,definition_json,"
                "phase,generation,created_at,updated_at) VALUES(?,?,?,?,?,?,'proposed',1,?,?)", (
                value["experiment_id"], brain, value["context_id"], value["target_id"], IM._hash(value), IM._json(value), now, now))
            result = self._public(self._row(connection, brain, value["experiment_id"]))
        self.assets._event(connection, principal, event_key, "experiment_create", payload, result)
        return result

    def block(self, principal, experiment_id, blocker, *, expected_generation, event_key):
        """Record a host-observed blocker; opening status never repeats the preflight."""
        brain = _brain(principal)
        _identifier(experiment_id)
        _generation(expected_generation)
        blocker = IC._frozen(blocker)
        IC._fields(blocker, {"code", "fingerprint"})
        if type(blocker["code"]) is not str or blocker["code"] not in _BLOCKERS:
            raise ValueError("invalid_experiment_blocker")
        IC._digest(blocker["fingerprint"])
        self._available()
        payload = {"experiment_id": experiment_id, "blocker": blocker, "expected_generation": expected_generation}
        with self.store._Tx(self.store) as connection:
            replay = self.assets._replay(connection, brain, event_key, "experiment_block", payload)
            if replay is not None:
                return replay
            row = self._row(connection, brain, experiment_id)
            if row["generation"] != expected_generation or row["phase"] in (*TERMINAL_PHASES, "stopping"):
                raise IM.ConflictError("experiment_state_stale")
            connection.execute("UPDATE improvement_experiments SET phase='blocked',generation=generation+1,"
                "resume_from=?,blocker_json=?,blocker_hash=?,updated_at=? WHERE id=? AND brain_id=?", (
                row["resume_from"] if row["phase"] == "blocked" else row["phase"], IM._json(blocker), IM._hash(blocker),
                time.time(), experiment_id, brain))
            result = self._public(self._row(connection, brain, experiment_id))
            self.assets._event(connection, principal, event_key, "experiment_block", payload, result)
            return result

    def resume(self, principal, experiment_id, *, observed_fingerprint, expected_generation, event_key):
        """Metadata transition after host preflight; fingerprint is never an HTTP authority input."""
        brain = _brain(principal)
        _identifier(experiment_id)
        _generation(expected_generation)
        IC._digest(observed_fingerprint)
        self._available()
        with self.store._Tx(self.store) as connection:
            return self._resume_in(connection, principal, experiment_id, observed_fingerprint=observed_fingerprint,
                expected_generation=expected_generation, event_key=event_key)

    def _resume_in(self, connection, principal, experiment_id, *, observed_fingerprint, expected_generation, event_key):
        IC.CaseStore(self.assets)._own_transaction(connection)
        brain = _brain(principal)
        payload = {"experiment_id": experiment_id, "observed_fingerprint": observed_fingerprint, "expected_generation": expected_generation}
        replay = self.assets._replay(connection, brain, event_key, "experiment_resume", payload)
        if replay is not None:
            return replay
        row = self._row(connection, brain, experiment_id)
        if row["generation"] != expected_generation or row["phase"] != "blocked":
            raise IM.ConflictError("experiment_state_stale")
        blocker = json.loads(row["blocker_json"])
        if IM._hash(blocker) != row["blocker_hash"]:
            raise IM.ConflictError("experiment_blocker_changed")
        if observed_fingerprint == blocker["fingerprint"]:
            raise IM.ConflictError("experiment_blocker_unchanged")
        value = _definition(json.loads(row["definition_json"]))
        self._bound_in(connection, brain, value, value["context_generation"])
        connection.execute("UPDATE improvement_experiments SET phase=resume_from,generation=generation+1,"
            "resume_from=NULL,blocker_json=NULL,blocker_hash=NULL,updated_at=? WHERE id=? AND brain_id=?",
            (time.time(), experiment_id, brain))
        result = self._public(self._row(connection, brain, experiment_id))
        self.assets._event(connection, principal, event_key, "experiment_resume", payload, result)
        return result

    def begin_stop(self, principal, experiment_id, *, kind, reason, expected_generation, event_key):
        """Fence future dispatch; Task6 must drain actual ownership before finalizing."""
        brain = _brain(principal)
        _identifier(experiment_id)
        _generation(expected_generation)
        if type(kind) is not str or kind not in TERMINAL_PHASES or type(reason) is not str or reason not in _STOP_REASONS:
            raise ValueError("invalid_experiment_stop")
        self._available()
        payload = {"experiment_id": experiment_id, "kind": kind, "reason": reason, "expected_generation": expected_generation}
        with self.store._Tx(self.store) as connection:
            replay = self.assets._replay(connection, brain, event_key, "experiment_stop", payload)
            if replay is not None:
                return replay
            row = self._row(connection, brain, experiment_id)
            if row["generation"] != expected_generation or row["phase"] in (*TERMINAL_PHASES, "stopping"):
                raise IM.ConflictError("experiment_state_stale")
            connection.execute("UPDATE improvement_experiments SET phase='stopping',generation=generation+1,stop_kind=?,"
                "stop_event_key=?,stop_reason=?,updated_at=? WHERE id=? AND brain_id=?", (
                kind, event_key, reason, time.time(), experiment_id, brain))
            result = self._public(self._row(connection, brain, experiment_id))
            self.assets._event(connection, principal, event_key, "experiment_stop", payload, result)
            return result

    def get(self, principal, experiment_id):
        brain = _brain(principal)
        _identifier(experiment_id)
        if IM._schema_version(self.store) < 5:
            raise PermissionError("experiment_unavailable")
        with closing(self.store._conn()) as connection:
            return self._public(self._row(connection, brain, experiment_id))

    def list(self, principal, *, cursor=None, limit=50):
        brain = _brain(principal)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_experiment_limit")
        if cursor is not None:
            _identifier(cursor)
        if IM._schema_version(self.store) < 5:
            return {"items": [], "next_cursor": None}
        with closing(self.store._conn()) as connection:
            rows = connection.execute("SELECT * FROM improvement_experiments WHERE brain_id=? AND id>? ORDER BY id LIMIT ?",
                                      (brain, cursor or "", limit + 1)).fetchall()
            items = [self._public(row) for row in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["experiment_id"] if len(rows) > limit else None}
