"""Exact owner-private review with current source authority, separate from status.

No model, native authentication, ambient prompt builder or source-capture Host is
constructed. Stored artifacts are data; reading never accepts a product or grant.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing

import improvement_cases as IC
import improvement_store as IM
from improvement_blobs import AssetUnavailable, MAX_ASSET_BYTES
from improvement_candidate_records import CandidateRecords, identifier
from improvement_skill_candidates import SkillCandidates
from improvement_skill_patch import body_layout
from improvement_trial_associations import _association
from improvement_trial_file_sources import BrainFileFixtures
from improvement_trial_host import TARGET_FIELDS
from improvement_trial_products import TrialProducts
from improvement_trial_records import HostContextRecords, _brain
from improvement_trial_sources import authorize_case, _manifest, validate_inventory
from improvement_trials import TrialStore, _request

MAX_PRIVATE_REVIEW_BYTES = 8 * MAX_ASSET_BYTES


def _text(raw):
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise AssetUnavailable("private_review_utf8_required") from error


def _exact(raw):
    return {"text": _text(raw), "hash": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw)}


class PrivateReview:
    def __init__(self, assets, *, pack_state_dir, fixture_observer=None):
        self.records = CandidateRecords(assets)
        self.assets, self.store, self.blobs = assets, assets.store, assets.blobs
        self.skills = SkillCandidates(assets, pack_state_dir=pack_state_dir)
        self.fixtures = BrainFileFixtures() if fixture_observer is None else fixture_observer
        if type(self.fixtures) is not BrainFileFixtures:
            raise TypeError("concrete_private_review_fixtures_required")

    def baseline(self, principal, experiment_id, target_id):
        brain = _brain(principal)
        identifier(experiment_id)
        identifier(target_id)

        def state(connection):
            target = self.assets._target(connection, brain, target_id)
            return self.skills._state(connection, principal, experiment_id, target_id,
                target["current_revision_id"], _private_read=True)

        with closing(self.store._conn()) as connection:
            connection.execute("BEGIN")
            before = state(connection)
        observed, raw = self.skills.observe(principal, before["target"])
        layout = body_layout(raw)
        if self.skills.observe(principal, before["target"])[0] != observed:
            raise IM.ConflictError("private_review_authority_changed")
        with closing(self.store._conn()) as connection:
            connection.execute("BEGIN")
            if state(connection) != before:
                raise IM.ConflictError("private_review_authority_changed")
        target = before["target"]
        return {"target_id": target_id, "base_revision_id": target["current_revision_id"],
            "base_generation": target["generation"], "baseline": _exact(raw),
            "editable_start": layout["editable_start"]}

    def _candidate_state(self, connection, principal, candidate_id):
        row = self.records._row(connection, _brain(principal), candidate_id)
        # Policy controls new improvement work. It does not erase an existing
        # read grant, and never creates one: active context, exact source and
        # original native permission pins are still mandatory here.
        state = self.skills._state(connection, principal, row["experiment_id"], row["target_id"],
            row["base_revision_id"], row["base_hash"], _private_read=True)
        return row, state

    def candidate(self, principal, candidate_id):
        _brain(principal)
        identifier(candidate_id)
        if IM._schema_version(self.store) < 4:
            raise PermissionError("candidate_unavailable")
        from cryptography.fernet import InvalidToken

        try:
            with closing(self.store._conn()) as connection:
                connection.execute("BEGIN")
                row, state = self._candidate_state(connection, principal, candidate_id)
            observed, before = self.skills.observe(principal, state["target"])
            with closing(self.store._conn()) as connection:
                connection.execute("BEGIN")
                current, current_state = self._candidate_state(connection, principal, candidate_id)
                if dict(current) != dict(row) or current_state != state:
                    raise IM.ConflictError("private_review_authority_changed")
                proposal = self.skills.read_proposal(connection, principal, row, state, observed, before)
            if self.skills.observe(principal, state["target"])[0] != observed:
                raise IM.ConflictError("private_review_authority_changed")
            with closing(self.store._conn()) as connection:
                connection.execute("BEGIN")
                current, current_state = self._candidate_state(connection, principal, candidate_id)
                if dict(current) != dict(row) or current_state != state:
                    raise IM.ConflictError("private_review_authority_changed")
                metadata = self.records._public(connection, current)
            patch = proposal["patch"]
            return {"candidate_id": candidate_id, "before": _exact(proposal["before"]), "after": _exact(proposal["after"]),
                "region": {"start": patch["start"], "end": patch["end"]}, "semantic_status": "unreviewed",
                "hypothesis": patch["hypothesis"], "failure_signature": patch["failure_signature"],
                "expected_behavior": patch["expected_behavior"], "possible_regressions": patch["possible_regressions"],
                "evidence_refs": proposal["provenance"]["evidence_refs"], "metadata": metadata}
        except InvalidToken as error:
            raise AssetUnavailable("private_review_blob_unavailable") from error

    def _artifact_state(self, connection, principal, trial_id, attempt):
        brain = _brain(principal)
        trial = TrialStore._row(connection, brain, trial_id)
        request = _request(json.loads(trial["request_json"]))
        if IM._hash(request) != trial["request_hash"]:
            raise AssetUnavailable("private_review_trial_changed")
        if request.get("version") == 3:
            from improvement_preparation_lineage import load_preparation_lineage
            load_preparation_lineage(connection, brain, request, blobs=self.blobs,
                                     current=False, require_link=True, trial_id=trial_id)
        if request.get("version") == 2:
            from improvement_experiment_lineage import load_lineage
            load_lineage(connection, brain, request, blobs=self.blobs, current=False, require_link=True, trial_id=trial_id)
        context = HostContextRecords._row(connection, brain, request["pins"]["experiment_id"])
        grant = HostContextRecords._grant(connection, context)
        if grant["status"] != "active":
            raise PermissionError("trial_source_grant_revoked")
        if (any(request["pins"][key] != context[key] for key in ("scope_hash", "grant_hash", "engine_config_hash", "policy_version"))
                or request["baseline_context_hash"] != context["context_hash"]
                or request["target_generations"] != json.loads(context["target_generations_json"])):
            raise IM.ConflictError("private_review_context_changed")
        association = _association(connection, brain, request["case_set_id"])
        if (association is None or association["context_id"] != context["id"]
                or association["experiment_id"] != context["experiment_id"] or association["grant_hash"] != context["grant_hash"]
                or association["contract_hash"] != request["pins"]["contract_hash"]
                or association["manifest_hash"] != request["pins"]["case_set_hash"]):
            raise PermissionError("private_review_case_unassociated")
        contract = IC.CaseStore._row(connection, "improvement_contracts", brain, association["contract_id"])
        contract_value = json.loads(contract["contract_json"])
        if contract["manifest_hash"] != association["contract_hash"] or IM._hash(contract_value) != contract["manifest_hash"]:
            raise AssetUnavailable("private_review_contract_changed")
        case = IC.CaseStore._case(None, connection, brain, request["case_set_id"], request["case_id"])
        if case["split"] == "qualification":
            qualification = IC.CaseStore._row(connection, "improvement_qualification", brain, request.get("qualification_id"))
            if (qualification["status"] != "consumed" or qualification["pins_hash"] != IM._hash(request["pins"])
                    or qualification["case_set_id"] != request["case_set_id"]):
                raise PermissionError("private_review_qualification_unavailable")
        artifact = connection.execute("SELECT * FROM improvement_trial_artifacts WHERE trial_id=? AND brain_id=? AND attempt=?",
                                      (trial_id, brain, attempt)).fetchone()
        if artifact is None:
            raise PermissionError("trial_product_unavailable")
        if artifact["path"] != contract_value["output"]["path"]:
            raise AssetUnavailable("private_review_artifact_changed")
        snapshots = []
        for snapshot_id in case["snapshot_ids"]:
            snapshot = IC.CaseStore._row(connection, "improvement_snapshots", brain, snapshot_id)
            _manifest(snapshot)
            snapshots.append(dict(snapshot))
        targets = []
        for target_id, generation in sorted(request["target_generations"].items()):
            target = self.assets._target(connection, brain, target_id)
            if target["kind"] != "skill" or target["status"] != "live" or target["generation"] != generation:
                raise IM.ConflictError("private_review_target_changed")
            targets.append(dict(target))
        return {"trial_id": trial_id, "request_hash": trial["request_hash"], "request": request,
            "context": dict(context), "grant": dict(grant), "association": association,
            "contract": contract_value, "case": case, "artifact": dict(artifact), "snapshots": snapshots, "targets": targets}

    def _source_observation(self, principal, state, binding):
        refs = {json.loads(row["manifest_json"])["source_ref"] for row in state["snapshots"]}
        inventory = validate_inventory([entry for entry in binding["fixture_inventory"] if entry["source_ref"] in refs])
        if {entry["source_ref"] for entry in inventory} != refs:
            raise PermissionError("private_review_fixture_unavailable")
        self.fixtures(principal, sorted(refs), frozen_inventory=inventory)
        targets = [{key: row[key] for key in TARGET_FIELDS} for row in state["targets"]]
        if targets != sorted(binding["targets"], key=lambda row: row["id"]):
            raise IM.ConflictError("private_review_target_changed")
        observed = {row["id"]: self.skills.observe(principal, row)[0] for row in state["targets"]}
        return observed

    def artifact(self, principal, trial_id, attempt):
        brain = _brain(principal)
        identifier(trial_id)
        if type(attempt) is not int or not 1 <= attempt < 2 ** 63:
            raise ValueError("invalid_trial_attempt")
        if IM._schema_version(self.store) < 3:
            raise PermissionError("trial_product_unavailable")
        from cryptography.fernet import InvalidToken

        try:
            with closing(self.store._conn()) as connection:
                connection.execute("BEGIN")
                state = self._artifact_state(connection, principal, trial_id, attempt)
                binding = HostContextRecords(self.assets)._decode(state["context"])
                authorize_case(connection, brain, state["request"], binding["fixture_inventory"], blobs=self.blobs,
                               baseline_content=binding["capture"]["baseline_context"]["content"])
            observed = self._source_observation(principal, state, binding)
            # Reuse the existing exact encrypted-product reader, with its hash/size checks.
            raw = TrialProducts(TrialStore(self.assets)).artifact(principal, trial_id, attempt)
            if hashlib.sha256(raw).hexdigest() != state["artifact"]["content_hash"] or len(raw) != state["artifact"]["byte_size"]:
                raise AssetUnavailable("private_review_artifact_changed")
            facts, total = [], len(raw)
            for snapshot in state["snapshots"]:
                manifest = json.loads(snapshot["manifest_json"])
                content = self.blobs.read(brain, snapshot["fixture_hash"])
                total += len(content)
                if total > MAX_PRIVATE_REVIEW_BYTES:
                    raise AssetUnavailable("private_review_too_large")
                facts.append({key: manifest[key] for key in ("source_ref", "source_version", "timezone", "data_period", "cutoff", "schema")} |
                             {"fixture_hash": snapshot["fixture_hash"], "byte_size": len(content), "text": _text(content)})
            if self._source_observation(principal, state, binding) != observed:
                raise IM.ConflictError("private_review_authority_changed")
            with closing(self.store._conn()) as connection:
                connection.execute("BEGIN")
                if self._artifact_state(connection, principal, trial_id, attempt) != state:
                    raise IM.ConflictError("private_review_authority_changed")
            return {"trial_id": trial_id, "attempt": attempt, "artifact": _exact(raw), "task": state["case"]["task"],
                    "contract": IC.public_contract(state["contract"]), "fixtures": facts}
        except InvalidToken as error:
            raise AssetUnavailable("private_review_blob_unavailable") from error
