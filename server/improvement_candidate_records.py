"""Cold owner-scoped authoring metadata; never loads bodies, settings or engines."""
from __future__ import annotations

import json
import time
from contextlib import closing

import improvement_cases as IC
import improvement_store as IM
from improvement_trial_records import _brain


def identifier(value):
    if type(value) is not str or not IC._ID.fullmatch(value):
        raise ValueError("invalid_candidate_identifier")
    return value


class CandidateRecords:
    def __init__(self, assets):
        if type(assets) is not IM.ImprovementStore:
            raise TypeError("concrete_candidate_records_required")
        self.assets, self.store, self.blobs = assets, assets.store, assets.blobs

    @staticmethod
    def _row(connection, brain, candidate_id):
        row = connection.execute("SELECT * FROM improvement_skill_candidates WHERE id=? AND brain_id=?",
                                 (candidate_id, brain)).fetchone()
        if row is None:
            raise PermissionError("candidate_unavailable")
        return row

    @staticmethod
    def _authority(connection, brain, authority_id):
        row = connection.execute("SELECT a.*,g.generation,g.status,g.revoked_at FROM improvement_candidate_authorities a "
            "JOIN improvement_candidate_authority_grants g ON g.authority_id=a.id AND g.brain_id=a.brain_id "
            "WHERE a.id=? AND a.brain_id=?", (authority_id, brain)).fetchone()
        if row is None:
            raise PermissionError("candidate_authority_unavailable")
        return row

    @staticmethod
    def _authority_public(row):
        return {"authority_id": row["id"], "kind": row["kind"], "generation": row["generation"],
                "status": row["status"], "start": row["start"], "end": row["end"],
                "before_hash": row["before_hash"], "after_hash": row["after_hash"]}

    def _public(self, connection, row):
        fields = ("context_id", "experiment_id", "target_id", "base_revision_id", "base_hash", "base_generation",
                  "ordinal", "content_hash", "byte_size", "patch_hash", "provenance_hash", "semantic_status", "created_at")
        result = {key: row[key] for key in fields}
        result["candidate_id"] = row["id"]
        result["keep_base"] = row["content_hash"] == row["base_hash"]
        authorities = connection.execute("SELECT a.*,g.generation,g.status,g.revoked_at "
            "FROM improvement_candidate_authorities a JOIN improvement_candidate_authority_grants g "
            "ON g.authority_id=a.id AND g.brain_id=a.brain_id WHERE a.brain_id=? AND (a.id=? OR a.candidate_id=?) "
            "ORDER BY CASE g.status WHEN 'active' THEN 0 ELSE 1 END,a.created_at DESC,a.id DESC",
            (row["brain_id"], row["authority_id"], row["id"])).fetchall()
        result["edit_authority"] = self._authority_public(authorities[0]) if authorities else None
        grant = connection.execute("SELECT generation,status FROM improvement_trial_context_grants "
            "WHERE context_id=? AND brain_id=?", (row["context_id"], row["brain_id"])).fetchone()
        result["context_grant"] = dict(grant) if grant is not None else {"generation": None, "status": "unavailable"}
        return result

    def get(self, principal, candidate_id):
        brain = _brain(principal)
        identifier(candidate_id)
        if IM._schema_version(self.store) < 4:
            raise PermissionError("candidate_unavailable")
        with closing(self.store._conn()) as connection:
            connection.execute("BEGIN")
            return self._public(connection, self._row(connection, brain, candidate_id))

    def list(self, principal, experiment_id):
        brain = _brain(principal)
        identifier(experiment_id)
        if IM._schema_version(self.store) < 4:
            return []
        with closing(self.store._conn()) as connection:
            connection.execute("BEGIN")
            return [self._public(connection, row) for row in connection.execute(
                "SELECT * FROM improvement_skill_candidates WHERE brain_id=? AND experiment_id=? ORDER BY ordinal",
                (brain, experiment_id))]

    @staticmethod
    def _objectives(connection, brain, context):
        """Only public objective metadata from the existing immutable association."""
        results = {}
        for row in connection.execute("SELECT result_json FROM improvement_events WHERE brain_id=? "
                                      "AND operation='owner_trial_case_set' ORDER BY id", (brain,)):
            value = json.loads(row[0])
            if value.get("context_id") != context["id"] or value.get("experiment_id") != context["experiment_id"]:
                continue
            contract = connection.execute("SELECT contract_json,manifest_hash FROM improvement_contracts WHERE brain_id=? AND id=?",
                                          (brain, value.get("contract_id"))).fetchone()
            if contract is None or contract["manifest_hash"] != value.get("contract_hash") or context["grant_hash"] != value.get("grant_hash"):
                raise IM.ConflictError("candidate_context_association_changed")
            manifest = json.loads(contract["contract_json"])
            if IM._hash(manifest) != contract["manifest_hash"]:
                raise IM.ConflictError("candidate_context_association_changed")
            results[value["contract_id"]] = {"contract_id": value["contract_id"], "objective": manifest["objective"]}
        return list(results.values())

    def contexts(self, principal, canonical_ref, *, cursor=None, limit=50):
        brain = _brain(principal)
        if (type(canonical_ref) is not str or not IM._SOURCE.fullmatch(canonical_ref) or not canonical_ref.startswith("skills/")
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("invalid_candidate_context_request")
        if cursor is not None:
            identifier(cursor)
        if IM._schema_version(self.store) < 3:
            return {"items": [], "next_cursor": None}
        with closing(self.store._conn()) as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT c.*,g.generation AS grant_generation,g.status AS grant_status,"
                "t.id AS target_id,t.generation AS current_generation FROM improvement_trial_contexts c "
                "JOIN improvement_trial_context_grants g ON g.context_id=c.id AND g.brain_id=c.brain_id "
                "JOIN improvement_targets t ON t.brain_id=c.brain_id AND t.canonical_ref=? AND t.kind='skill' AND t.status='live' "
                "WHERE c.brain_id=? AND c.id>? AND EXISTS(SELECT 1 FROM json_each(c.target_generations_json) WHERE key=t.id) "
                "ORDER BY c.id LIMIT ?", (canonical_ref, brain, cursor or "", limit + 1)).fetchall()
            items = [{"context_id": row["id"], "experiment_id": row["experiment_id"], "target_id": row["target_id"],
                      "created_at": row["created_at"], "grant_status": row["grant_status"], "generation": row["grant_generation"],
                      "base_generation": json.loads(row["target_generations_json"])[row["target_id"]],
                      "current_generation": row["current_generation"], "objectives": self._objectives(connection, brain, row)}
                     for row in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["context_id"] if len(rows) > limit else None}

    def revoke_authority(self, principal, authority_id, *, expected_generation, event_key):
        brain = _brain(principal)
        identifier(authority_id)
        if type(expected_generation) is not int or expected_generation < 1:
            raise ValueError("invalid_candidate_generation")
        if IM._schema_version(self.store) < 4:
            raise PermissionError("candidate_authority_unavailable")
        payload = {"authority_id": authority_id, "expected_generation": expected_generation}
        with self.store._Tx(self.store) as connection:
            replay = self.assets._replay(connection, brain, event_key, "candidate_authority_revoke", payload)
            if replay is not None:
                return replay
            row = self._authority(connection, brain, authority_id)
            if row["generation"] != expected_generation or row["status"] != "active":
                raise IM.ConflictError("candidate_authority_stale")
            connection.execute("UPDATE improvement_candidate_authority_grants SET status='revoked',generation=generation+1,revoked_at=? "
                               "WHERE authority_id=? AND brain_id=?", (time.time(), authority_id, brain))
            result = self._authority_public(self._authority(connection, brain, authority_id))
            self.assets._event(connection, principal, event_key, "candidate_authority_revoke", payload, result)
            return result
