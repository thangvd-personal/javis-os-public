"""Synthetic host callbacks bind real encrypted context/grant records, without engines."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import importlib.util
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_improvement_trials as fixtures
import improvement_store as IM

if importlib.util.find_spec("improvement_trial_host"):
    import improvement_trial_host as HOST
else:
    HOST = None


class HostContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(HOST, "Host-owned context/grant bindings are not implemented")
        self.f = fixtures.TrialStoreTests()
        self.f.setUp()
        self.target_ids = [item["id"] for item in self.f.assets.targets(self.f.owner)]
        text = "Complete synthetic owner context\r\n"
        self.capture = {"version": 1, "surface": {"kind": "synthetic", "session_id": ""},
            "plan_hash": fixtures.fixtures.digest("host-complete-plan"),
            "sources": [{"ref": "owner", "root": str(self.f.root), "relative": "owner.md", "required": True,
                         "present": True, "identity": fixtures.fixtures.digest("source-incarnation"),
                         "hash": fixtures.fixtures.digest(text)}],
            "baseline_context": {"content": text, "hash": fixtures.fixtures.digest(text), "coverage": "complete"}}
        self.engine = copy.deepcopy(self.f.request["engine_config"])
        self.fixture_content = {f"host-source:{index}": f"Authorized host fixture {index}".encode() for index in range(1, 4)}
        self.fixture_inventory = [{"source_ref": ref, "source_version": "v1", "identity": fixtures.fixtures.digest(ref),
            "source_hash": fixtures.fixtures.digest(raw.decode()), "fixture_hash": fixtures.fixtures.digest(raw.decode()),
            "timezone": "Asia/Ho_Chi_Minh", "data_period": "2026-09", "cutoff": "2026-09-30T23:59:59+07:00",
            "schema": "text/plain", "redaction": "none", "sufficient_source": True,
            "permission_id": "owner-explicit-synthetic-source", "permission_version": 1,
            "freshness_policy": "snapshot_version"} for ref, raw in self.fixture_content.items()]
        self.case_sets = {}
        self.observe_count = 0
        self.host = HOST.HostContextStore(self.f.assets, context_capture=self.context_capture,
            selected_engine=lambda: copy.deepcopy(self.engine), target_observer=self.observe_targets,
            fixture_observer=lambda p, refs, **kw: copy.deepcopy(self.fixture_inventory))

    def context_capture(self, principal, surface, targets):
        self.assertEqual(principal, self.f.owner)
        self.assertEqual(surface, self.capture["surface"])
        self.assertEqual([item["id"] for item in targets], self.target_ids)
        return copy.deepcopy(self.capture)

    def observe_targets(self, principal, identifiers):
        self.observe_count += 1
        with self.f.rs._conn() as connection:
            return [{key: row[key] for key in HOST.TARGET_FIELDS} for row in connection.execute(
                "SELECT * FROM improvement_targets WHERE brain_id=? ORDER BY id", (principal.brain_id,))
                if row["id"] in identifiers]

    def create(self, key="create-host-context", **changes):
        return self.host.create(self.f.owner, "experiment-host", self.capture["surface"], self.target_ids,
                                source_refs=list(self.fixture_content), event_key=key, **changes)

    def request(self, metadata):
        request = copy.deepcopy(self.f.request)
        if metadata["grant_hash"] not in self.case_sets:
            items = []
            for index, entry in enumerate(self.fixture_inventory, 1):
                manifest = {key: entry[key] for key in ("source_ref", "source_version", "timezone", "data_period", "cutoff", "schema", "redaction", "sufficient_source")}
                manifest.update(version=1, grant_hash=metadata["grant_hash"], captured_at="2026-10-22T10:00:00+07:00", freshness="current")
                raw = self.fixture_content[entry["source_ref"]]
                snapshot = self.f.cases.put_snapshot(self.f.owner, manifest, raw, raw, event_key="host-snapshot-" + str(index))
                items.append({"id": "host-case-" + str(index), "family_key": "host-family-" + str(index),
                    "split": ("tuning", "qualification", "regression")[index - 1], "task": "Report authorized fixture",
                    "snapshot_ids": [snapshot["snapshot_id"]], "answer": {"expected": "private-host-answer"}, "evaluator_refs": []})
            self.case_sets[metadata["grant_hash"]] = self.f.cases.put_case_set(self.f.owner, items, 47, event_key="host-bound-case-set")
        case_set = self.case_sets[metadata["grant_hash"]]
        request.update(case_set_id=case_set["case_set_id"], case_id="host-case-1")
        request["pins"]["case_set_hash"] = case_set["manifest_hash"]
        request["pins"].update({key: metadata[key] for key in ("experiment_id", "scope_hash", "grant_hash", "engine_config_hash", "policy_version")})
        request.update(baseline_context_hash=metadata["baseline_context_hash"],
                       target_generations=metadata["target_generations"], engine_config=copy.deepcopy(self.engine))
        return request

    def test_binding_is_encrypted_immutable_scoped_and_replay_does_not_recapture(self):
        meta = self.create()
        before = self.observe_count
        self.assertEqual(self.create(), meta)
        self.assertEqual(self.observe_count, before)
        request = self.request(meta)
        self.assertEqual(self.host.context_loader(self.f.owner, request), self.capture["baseline_context"])
        self.assertNotIn("Complete synthetic", json.dumps(meta))
        with self.f.rs._conn() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM improvement_trial_contexts")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0], 0)
        with self.assertRaises(PermissionError):
            self.host.get(self.f.other, "experiment-host")

    def test_live_guard_binds_frozen_context_selection_grant_scope_and_generations(self):
        meta = self.create()
        request = self.request(meta)
        expected = {key: meta[key] for key in ("baseline_context_hash", "scope_hash", "grant_hash", "engine_config_hash", "target_generations")}
        self.assertEqual(self.host.live_guard(self.f.owner, request), expected)
        for change in ({"baseline_context_hash": "f" * 64}, {"target_generations": {self.target_ids[0]: 999}},
                       {"pins": {**request["pins"], "grant_hash": "f" * 64}}):
            with self.subTest(change=change), self.assertRaises(IM.ConflictError):
                self.host.live_guard(self.f.owner, {**request, **change})
        self.engine["reasoning"] = "high"
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)

    def test_source_change_or_replacement_invalidates_authority_without_mutating_frozen_context(self):
        meta = self.create()
        request = self.request(meta)
        original = self.host.context_loader(self.f.owner, request)
        self.capture["sources"][0]["identity"] = "f" * 64
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)
        self.assertEqual(self.host.context_loader(self.f.owner, request), original)

    def test_revocation_is_cas_idempotent_and_cannot_be_reenabled_by_replay(self):
        meta = self.create()
        options = {"expected_generation": 1, "event_key": "revoke-host-source-grant"}
        revoked = self.host.revoke(self.f.owner, "experiment-host", **options)
        self.assertEqual(revoked["grant_status"], "revoked")
        self.assertEqual(self.host.revoke(self.f.owner, "experiment-host", **options), revoked)
        self.create()
        self.assertEqual(self.host.get(self.f.owner, "experiment-host")["grant_status"], "revoked")
        with self.assertRaises(PermissionError):
            self.host.context_loader(self.f.owner, self.request(meta))
        with self.assertRaises(PermissionError):
            self.host.live_guard(self.f.owner, self.request(meta))

    def test_incomplete_context_and_credentials_never_create_an_authority_record(self):
        self.capture["baseline_context"]["coverage"] = "unknown"
        with self.assertRaises((ValueError, PermissionError)):
            self.create()
        self.capture["baseline_context"].update(content="api_key=sk-" + "a" * 40, coverage="complete")
        self.capture["baseline_context"]["hash"] = fixtures.fixtures.digest(self.capture["baseline_context"]["content"])
        with self.assertRaises(ValueError):
            self.create()
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_contexts").fetchone()[0], 0)

    def test_mutation_during_capture_cannot_commit_mixed_authority(self):
        original = self.host.context_capture
        calls = []

        def changing(*args):
            observed = original(*args)
            calls.append(1)
            if len(calls) > 1:
                observed["sources"][0]["identity"] = "f" * 64
            return observed

        self.host.context_capture = changing
        with self.assertRaises(IM.ConflictError):
            self.create()
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_contexts").fetchone()[0], 0)

    def test_policy_or_target_generation_drift_blocks_existing_binding(self):
        meta = self.create()
        request = self.request(meta)
        with self.f.rs._conn() as connection:
            connection.execute("UPDATE improvement_targets SET generation=generation+1 WHERE id=?", (self.target_ids[0],))
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)
        self.f.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="host-policy-off")
        with self.assertRaises(PermissionError):
            self.host.context_loader(self.f.owner, request)
        self.assertEqual(self.host.get(self.f.owner, "experiment-host")["experiment_id"], "experiment-host")

    def test_concurrent_logical_creations_share_one_context_and_never_regrant(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.create, ("create-context-parallel-a", "create-context-parallel-b")))
        self.assertEqual(results[0], results[1])
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_trial_contexts").fetchone()[0], 1)
        self.host.revoke(self.f.owner, "experiment-host", expected_generation=1, event_key="revoke-logical-context")
        self.assertEqual(self.create("new-event-same-context")["grant_status"], "revoked")

    def test_target_change_during_context_read_cannot_escape_the_live_guard(self):
        meta = self.create()
        original_capture, original_observer = self.host.context_capture, self.host.target_observer
        changed = []

        def capture_then_change_target(*args):
            result = original_capture(*args)
            changed.append(1)
            return result

        def live_target(*args):
            result = original_observer(*args)
            if changed:
                result[0]["source_hash"] = "f" * 64
            return result

        self.host.context_capture, self.host.target_observer = capture_then_change_target, live_target
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, self.request(meta))

    def test_target_database_change_during_source_read_is_rechecked(self):
        meta = self.create()
        original = self.host.context_capture

        def changed_generation(*args):
            result = original(*args)
            with self.f.rs._conn() as connection:
                connection.execute("UPDATE improvement_targets SET generation=generation+1 WHERE id=?", (self.target_ids[0],))
            return result

        self.host.context_capture = changed_generation
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, self.request(meta))

    def test_real_source_capture_detects_optional_appearance_and_owner_edit(self):
        from improvement_trial_context import ContextSource, capture_context
        owner_file = self.f.root / "owner.md"
        owner_file.write_bytes(b"Full owner rules\r\n")
        sources = [ContextSource("owner", self.f.root, "owner.md"),
                   ContextSource("optional-profile", self.f.root, "profile.md", required=False)]
        self.host.context_capture = lambda p, s, t: capture_context(sources, surface=s, complete_plan=True,
            excluded_refs=[target["canonical_ref"] for target in t])
        meta = self.create()
        request = self.request(meta)
        self.host.live_guard(self.f.owner, request)
        frozen = self.host.context_loader(self.f.owner, request)
        self.assertIn("Full owner rules\r\n", frozen["content"])
        (self.f.root / "profile.md").write_bytes(b"New profile")
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)
        (self.f.root / "profile.md").unlink()
        owner_file.write_bytes(b"Updated owner rules\r\n")
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)
        self.assertEqual(self.host.context_loader(self.f.owner, request), frozen)

    def test_large_encoded_context_chunks_reassemble_and_missing_chunk_fails_closed(self):
        text = "Full owner rules" + "\n" * 600_000
        self.capture["baseline_context"].update(content=text, hash=fixtures.fixtures.digest(text))
        self.capture["sources"][0]["hash"] = fixtures.fixtures.digest(text)
        meta = self.create()
        with self.f.rs._conn() as connection:
            chunks = json.loads(connection.execute("SELECT blob_hashes_json FROM improvement_trial_contexts").fetchone()[0])
        self.assertGreater(len(chunks), 1)
        self.assertEqual(self.host.context_loader(self.f.owner, self.request(meta))["content"], text)
        self.f.assets.blobs._path(self.f.owner.brain_id, chunks[-1]).unlink()
        with self.assertRaises(IM.AssetUnavailable):
            self.host.context_loader(self.f.owner, self.request(meta))

    def test_revoke_event_failure_rolls_back_source_grant_status(self):
        self.create()
        with self.f.rs._conn() as connection:
            connection.execute("CREATE TRIGGER revoke_event_failure BEFORE INSERT ON improvement_events "
                "WHEN NEW.operation='trial_context_revoke' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.host.revoke(self.f.owner, "experiment-host", expected_generation=1, event_key="revoke-host-event-failure")
        self.assertEqual(self.host.get(self.f.owner, "experiment-host")["grant_status"], "active")

    def test_existing_context_identity_cannot_be_redefined_by_a_new_surface(self):
        self.create()
        self.capture["surface"]["session_id"] = "another-session"
        with self.assertRaises(IM.ConflictError):
            self.create("new-context-surface")

    def test_retired_target_cannot_receive_a_new_source_grant(self):
        target = self.f.assets.targets(self.f.owner)[0]
        self.f.assets.retire(self.f.owner, target["id"], expected_generation=target["generation"], event_key="retire-before-context")
        with self.assertRaises((PermissionError, IM.ConflictError)):
            self.create()

    def test_same_grant_hash_cannot_authorize_an_unlisted_case_snapshot(self):
        meta = self.create()
        request = self.request(meta)
        manifest = {key: self.fixture_inventory[0][key] for key in ("source_ref", "source_version", "timezone", "data_period", "cutoff", "schema", "redaction", "sufficient_source")}
        manifest.update(version=1, source_ref="unlisted-host-source", grant_hash=meta["grant_hash"],
                        captured_at="2026-10-22T10:00:00+07:00", freshness="current")
        forged = self.f.cases.put_snapshot(self.f.owner, manifest, b"Not in the authorized inventory", b"Not in the authorized inventory",
                                            event_key="forged-known-grant-snapshot")
        with self.f.rs._conn() as connection:
            cases = [json.loads(row[0]) for row in connection.execute("SELECT case_json FROM improvement_cases WHERE case_set_id=? ORDER BY id",
                                                                     (request["case_set_id"],))]
        items = []
        for case in cases:
            item = {key: case[key] for key in ("id", "family_key", "split", "task", "snapshot_ids", "evaluator_refs")}
            item["answer"] = {"expected": "private-host-answer"}
            if case["id"] == request["case_id"]:
                item["snapshot_ids"] = [forged["snapshot_id"]]
            items.append(item)
        forged_set = self.f.cases.put_case_set(self.f.owner, items, 47, event_key="forged-known-grant-case-set")
        request["case_set_id"] = forged_set["case_set_id"]
        request["pins"]["case_set_hash"] = forged_set["manifest_hash"]
        with self.assertRaisesRegex(PermissionError, "trial_source_not_granted"):
            self.host.live_guard(self.f.owner, request)

    def test_fixture_permission_revocation_blocks_intact_snapshot_without_exposure(self):
        meta = self.create()
        request = self.request(meta)
        self.fixture_inventory[0]["permission_version"] += 1
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)
        with self.f.rs._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_case_exposures").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM improvement_qualification").fetchone()[0], 0)

    def test_invalid_fixture_references_are_rejected_before_any_capture(self):
        with self.assertRaises(ValueError):
            self.host.create(self.f.owner, "bad-source-ref", self.capture["surface"], self.target_ids,
                             source_refs=[{}], event_key="invalid-fixture-reference")
        self.assertEqual(self.observe_count, 0)

    def test_case_source_content_cannot_be_copied_into_the_common_context(self):
        self.fixture_inventory[0]["source_hash"] = self.capture["sources"][0]["hash"]
        self.fixture_inventory[0]["fixture_hash"] = self.capture["sources"][0]["hash"]
        with self.assertRaises(ValueError):
            self.create()

    def typed_capture(self, *, discovery_target=False):
        from improvement_trial_semantics import capture_semantics

        legacy = self.capture["sources"][0]
        text = self.capture["baseline_context"]["content"]
        sources = [{"kind": "file", "ref": legacy["ref"], "locator": {"root": legacy["root"], "relative": legacy["relative"]},
            "role": "protected", "required": True, "present": True, "identity": legacy["identity"], "hash": legacy["hash"],
            "projection": text, "projection_hash": IM._hash(text)}]
        if discovery_target:
            target = self.observe_targets(self.f.owner, self.target_ids)[0]
            projection = {"name": "Pinned target discovery metadata"}
            sources.append({"kind": "file", "ref": "target-discovery", "locator": {"root": str(self.f.root), "relative": target["canonical_ref"]},
                "role": "discovery", "required": True, "present": True, "identity": IM._hash(target["identity"]), "hash": target["source_hash"],
                "projection": projection, "projection_hash": IM._hash(projection)})
        projection = {"files": [], "project_instructions": "Private semantic input already rendered separately"}
        sources.append({"kind": "sql", "ref": "session-membership", "locator": {"database": str(self.f.root / "sessions.sqlite3"), "scope": "synthetic"},
            "role": "authority", "required": True, "present": True, "identity": IM._hash("synthetic-database-incarnation"),
            "hash": IM._hash(projection), "projection": projection, "projection_hash": IM._hash(projection)})
        return capture_semantics(surface=self.capture["surface"], sources=sources, content=text, complete_plan=True)

    def test_typed_file_and_sql_sources_stay_private_and_bind_real_history(self):
        self.capture = self.typed_capture()
        meta = self.create()
        request = self.request(meta)
        self.assertEqual(self.host.context_loader(self.f.owner, request), self.capture["baseline_context"])
        self.assertNotIn("Private semantic", json.dumps(meta))
        self.host.live_guard(self.f.owner, request)
        projection = self.capture["sources"][-1]["projection"]
        projection["files"] = ["newly-added.md"]
        self.capture["sources"][-1]["hash"] = self.capture["sources"][-1]["projection_hash"] = IM._hash(projection)
        with self.assertRaises(IM.ConflictError):
            self.host.live_guard(self.f.owner, request)

    def test_target_discovery_metadata_does_not_duplicate_target_body(self):
        self.capture = self.typed_capture(discovery_target=True)
        meta = self.create()
        self.host.live_guard(self.f.owner, self.request(meta))
        target_source = self.capture["sources"][1]
        target_source.update(role="protected", projection=self.capture["baseline_context"]["content"],
                             projection_hash=IM._hash(self.capture["baseline_context"]["content"]),
                             hash=self.capture["baseline_context"]["hash"])
        from improvement_trial_semantics import capture_semantics
        self.capture = capture_semantics(surface=self.capture["surface"], sources=self.capture["sources"],
            content=self.capture["baseline_context"]["content"], complete_plan=True)
        with self.assertRaisesRegex(ValueError, "trial_target_context_overlap"):
            self.host.create(self.f.owner, "typed-target-second-experiment", self.capture["surface"], self.target_ids,
                             source_refs=list(self.fixture_content), event_key="typed-target-overlap")

    def test_host_revalidation_receives_frozen_capture_without_refreshing_volatile_inputs(self):
        frozen = self.typed_capture()
        observations = []
        owner = self.f.owner

        class Capture:
            def __call__(capture_self, principal, surface, targets):
                observations.append("initial")
                return copy.deepcopy(frozen)

            def revalidate(capture_self, principal, surface, targets, *, frozen_capture):
                self.assertEqual(principal, owner)
                self.assertEqual(frozen_capture, frozen)
                observations.append("revalidated")
                return copy.deepcopy(frozen_capture)

        self.host.context_capture = Capture()
        meta = self.create()
        self.host.live_guard(owner, self.request(meta))
        # Frozen revalidation now brackets slow target-blob reads in both the
        # writer and live observer; volatile initial capture is still used once.
        self.assertEqual(observations, ["initial", "revalidated", "revalidated", "revalidated", "revalidated"])

    def test_protected_sql_text_cannot_copy_an_authorized_fixture_into_baseline(self):
        from improvement_trial_semantics import capture_semantics

        self.capture = self.typed_capture()
        fixture = self.fixture_content[next(iter(self.fixture_content))].decode("utf-8")
        sql = self.capture["sources"][-1]
        sql.update(role="protected", projection=fixture, hash=IM._hash(fixture), projection_hash=IM._hash(fixture))
        self.capture = capture_semantics(surface=self.capture["surface"], sources=self.capture["sources"],
            content=self.capture["baseline_context"]["content"] + fixture, complete_plan=True)
        with self.assertRaisesRegex(ValueError, "trial_case_context_overlap"):
            self.create()

    def test_discovery_metadata_does_not_authorize_full_target_source_in_baseline(self):
        from improvement_trial_semantics import capture_semantics

        self.capture = self.typed_capture(discovery_target=True)
        target = self.observe_targets(self.f.owner, self.target_ids)[0]
        raw = self.f.assets.blobs.read(self.f.owner.brain_id, target["source_hash"]).decode("utf-8")
        self.capture = capture_semantics(surface=self.capture["surface"], sources=self.capture["sources"],
            content=self.capture["baseline_context"]["content"] + raw, complete_plan=True)
        with self.assertRaisesRegex(ValueError, "trial_target_context_overlap"):
            self.create()


if __name__ == "__main__":
    unittest.main()
