"""Stored context metadata and durable revocation without live source dependencies.

Readers may use ImprovementStore.read_existing; neither operation migrates a
schema, initializes an ordinary source, probes native authentication or queries.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import closing

import improvement_store as IM
from improvement_blobs import MAX_ASSET_BYTES, AssetUnavailable
from resonance_store import Principal

MAX_BINDING_BYTES = 8 * MAX_ASSET_BYTES


def _brain(principal):
    if not isinstance(principal, Principal):
        raise PermissionError("owner_required")
    return IM._brain(principal)


class HostContextRecords:
    def __init__(self, assets):
        if type(assets) is not IM.ImprovementStore:
            raise TypeError("concrete_trial_records_required")
        self.assets, self.store, self.blobs = assets, assets.store, assets.blobs

    @staticmethod
    def _row(connection, brain, experiment):
        row = connection.execute("SELECT * FROM improvement_trial_contexts WHERE brain_id=? AND experiment_id=?",
                                 (brain, experiment)).fetchone()
        if row is None:
            raise PermissionError("trial_context_unavailable")
        return row

    @staticmethod
    def _grant(connection, row):
        grant = connection.execute("SELECT * FROM improvement_trial_context_grants WHERE context_id=? AND brain_id=?",
                                   (row["id"], row["brain_id"])).fetchone()
        if grant is None:
            raise PermissionError("trial_context_unavailable")
        return grant

    def _decode(self, row):
        try:
            hashes = json.loads(row["blob_hashes_json"])
            if not isinstance(hashes, list) or not 1 <= len(hashes) <= MAX_BINDING_BYTES // MAX_ASSET_BYTES:
                raise AssetUnavailable("invalid_trial_context_chunks")
            raw = b"".join(self.blobs.read(row["brain_id"], digest) for digest in hashes)
            if len(raw) > MAX_BINDING_BYTES or hashlib.sha256(raw).hexdigest() != row["binding_hash"]:
                raise AssetUnavailable("trial_context_hash_mismatch")
            binding = json.loads(raw)
            if (IM._hash(binding["capture"]["baseline_context"]) != row["context_hash"]
                    or IM._hash(binding["engine_config"]) != row["engine_config_hash"]
                    or IM._hash(binding["grant"]) != row["grant_hash"]
                    or IM._hash(binding["scope"]) != row["scope_hash"]):
                raise AssetUnavailable("trial_context_reference_mismatch")
            return binding
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise AssetUnavailable("trial_context_unavailable") from error

    def _public(self, connection, row):
        grant, binding = self._grant(connection, row), self._decode(row)
        return {"context_id": row["id"], "experiment_id": row["experiment_id"],
                "baseline_context_hash": row["context_hash"], "scope_hash": row["scope_hash"], "grant_hash": row["grant_hash"],
                "engine_config_hash": row["engine_config_hash"], "engine_config": binding["engine_config"],
                "target_generations": json.loads(row["target_generations_json"]), "policy_version": row["policy_version"],
                "grant_status": grant["status"], "generation": grant["generation"]}

    def get(self, principal, experiment_id):
        brain = _brain(principal)
        if IM._schema_version(self.store) < 3:
            raise PermissionError("trial_context_unavailable")
        with closing(self.store._conn()) as connection:
            return self._public(connection, self._row(connection, brain, experiment_id))

    def revoke(self, principal, experiment_id, *, expected_generation, event_key):
        brain = _brain(principal)
        if IM._schema_version(self.store) < 3:
            raise PermissionError("trial_context_unavailable")
        payload = {"experiment_id": experiment_id, "expected_generation": expected_generation}
        with self.store._Tx(self.store) as connection:
            replay = self.assets._replay(connection, brain, event_key, "trial_context_revoke", payload)
            if replay is not None:
                return replay
            row = self._row(connection, brain, experiment_id)
            grant = self._grant(connection, row)
            if type(expected_generation) is not int or grant["generation"] != expected_generation or grant["status"] != "active":
                raise IM.ConflictError("trial_context_grant_stale")
            connection.execute("UPDATE improvement_trial_context_grants SET status='revoked',generation=generation+1,revoked_at=? WHERE context_id=?",
                               (time.time(), row["id"]))
            result = self._public(connection, row)
            self.assets._event(connection, principal, event_key, "trial_context_revoke", payload, result)
            return result
