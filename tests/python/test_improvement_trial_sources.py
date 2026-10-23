"""Synthetic host inventories against real immutable case SQLite; no provider access."""
from _paths import ROOT, SERVER  # noqa: F401

import base64
import copy
import hashlib
import importlib.util
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import test_improvement_cases as fixtures
import improvement_cases as IC
import improvement_store as IM
from improvement_blobs import AssetUnavailable, RevisionBlobs

if importlib.util.find_spec("improvement_trial_sources"):
    import improvement_trial_sources as sources
else:
    sources = None


class SourceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sources, "Host source authorization primitive is not implemented")
        self.f = fixtures.ImprovementCaseTests()
        self.f.setUp()
        # Synthetic envelope codec avoids any real key-store access; not crypto evidence.
        self.blobs = RevisionBlobs(self.f.root / "synthetic-sealed",
            encrypt=lambda value: "enc:" + base64.b64encode(value.encode()).decode(),
            decrypt=lambda value: base64.b64decode(value[4:]).decode())
        self.f.assets.blobs = self.f.cases.blobs = self.blobs
        self.case_set, self.cases = self.f.case_set()
        self.request = {"case_set_id": self.case_set["case_set_id"], "case_id": "case-1",
                        "pins": {"case_set_hash": self.case_set["manifest_hash"], "grant_hash": fixtures.digest("grant-a")}}
        self.inventory = [self.entry(case["snapshot_ids"][0], index) for index, case in enumerate(self.cases)]

    def entry(self, snapshot_id, index=0):
        snapshot = self.f.cases.get_snapshot(self.f.owner, snapshot_id)
        return {**{key: snapshot["manifest"][key] for key in
            ("source_ref", "source_version", "timezone", "data_period", "cutoff", "schema", "redaction", "sufficient_source")},
            "identity": fixtures.digest("host-source-incarnation-" + str(index)),
            "source_hash": snapshot["source_hash"], "fixture_hash": snapshot["fixture_hash"],
            "permission_id": "host-authorized-source-" + str(index), "permission_version": 1,
            "freshness_policy": "snapshot_version"}

    def authorize(self, *, brain=None, request=None, inventory=None, blobs=None, baseline_content=None):
        with closing(self.f.rs._conn()) as connection:
            return sources.authorize_case(connection, brain or self.f.owner.brain_id,
                self.request if request is None else request, self.inventory if inventory is None else inventory,
                blobs=self.blobs if blobs is None else blobs, baseline_content=baseline_content)

    def test_inventory_is_closed_canonical_sorted_and_detached_from_caller(self):
        value = list(reversed(self.inventory))
        expected = sorted(copy.deepcopy(value), key=IM._json)
        result = sources.validate_inventory(value)
        self.assertEqual(result, expected)
        self.assertEqual(sources.validate_inventory(result), result)
        value[0]["source_ref"] = "caller changed metadata"
        self.assertEqual(result, expected)
        self.assertEqual(sources.validate_inventory([]), [])

    def test_inventory_rejects_missing_extra_circular_and_credential_fields(self):
        for field in self.inventory[0]:
            value = copy.deepcopy(self.inventory[:1])
            value[0].pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                sources.validate_inventory(value)
        for field in ("snapshot_id", "manifest_hash", "grant_hash", "credentials", "access_token"):
            value = copy.deepcopy(self.inventory[:1])
            value[0][field] = "synthetic-never-output-this"
            with self.subTest(extra=field), self.assertRaises(ValueError):
                sources.validate_inventory(value)

    def test_credential_bearing_metadata_is_rejected_without_echoing_the_credential(self):
        credential = "api_key=sk-" + "x" * 40
        for field in ("source_ref", "permission_id"):
            value = copy.deepcopy(self.inventory[:1])
            value[0][field] = "synthetic-host-ref?" + credential
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as error:
                    sources.validate_inventory(value)
                self.assertNotIn(credential, str(error.exception))

    def test_inventory_rejects_invalid_hashes_permission_and_snapshot_metadata(self):
        changes = [(key, value) for key in ("identity", "source_hash", "fixture_hash")
                   for value in ("", "A" * 64, "g" * 64, False, None)]
        changes += [("permission_id", value) for value in ("", " ", None, 3)]
        changes += [("permission_version", value) for value in (True, False, 0, -1, 1.0, "1", None)]
        changes += [("sufficient_source", value) for value in (False, 1, "true", None)]
        changes += [("freshness_policy", value) for value in ("current", "", None, 1)]
        changes += [("timezone", "not/a/timezone"), ("cutoff", "2026-09-01"), ("cutoff", "yesterday"),
                    ("source_ref", ""), ("source_version", ""), ("schema", {}), ("redaction", ""), ("data_period", "")]
        for key, changed in changes:
            value = copy.deepcopy(self.inventory[:1])
            value[0][key] = changed
            with self.subTest(field=key, value=changed), self.assertRaises(ValueError):
                sources.validate_inventory(value)

    def test_inventory_rejects_nonlists_nonfinite_values_and_duplicate_entries(self):
        for value in (None, {}, "[]", [1], [self.inventory[0], self.inventory[0]]):
            with self.assertRaises(ValueError):
                sources.validate_inventory(value)
        value = copy.deepcopy(self.inventory[:1])
        value[0]["permission_version"] = float("nan")
        with self.assertRaises(ValueError):
            sources.validate_inventory(value)

    def test_exact_matching_sources_authorize_without_returning_content(self):
        with patch.object(self.blobs, "read", wraps=self.blobs.read) as read:
            self.assertIsNone(self.authorize())
        self.assertEqual({call.args[1] for call in read.call_args_list},
                         {self.inventory[0]["source_hash"], self.inventory[0]["fixture_hash"]})
        self.assertTrue(all(call.args[0] == self.f.owner.brain_id for call in read.call_args_list))

    def test_both_freshness_policies_match_capture_without_claiming_live_permission(self):
        for policy in ("snapshot_version", "require_current_version"):
            inventory = copy.deepcopy(self.inventory)
            inventory[0]["freshness_policy"] = policy
            self.assertIsNone(self.authorize(inventory=inventory))

    def test_foreign_brain_missing_case_and_wrong_case_set_hash_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.authorize(brain=self.f.other.brain_id)
        for field, value in (("case_id", "absent-case"), ("case_set_id", "absent-case-set")):
            request = copy.deepcopy(self.request)
            request[field] = value
            with self.assertRaises(PermissionError):
                self.authorize(request=request)
        request = copy.deepcopy(self.request)
        request["pins"]["case_set_hash"] = "f" * 64
        with self.assertRaises(IM.ConflictError):
            self.authorize(request=request)

    def test_same_grant_does_not_authorize_different_source_fixture_ref_version_or_redaction(self):
        changes = {"source_ref": "another-source", "source_version": "v2", "source_hash": "a" * 64,
                   "fixture_hash": "b" * 64, "timezone": "UTC", "data_period": "2026-10",
                   "cutoff": "2026-10-01T00:00:00+00:00", "schema": "other-schema", "redaction": "other-view"}
        for key, value in changes.items():
            inventory = copy.deepcopy(self.inventory)
            inventory[0][key] = value
            with self.subTest(field=key), self.assertRaises(PermissionError):
                self.authorize(inventory=inventory)
        with self.assertRaises(PermissionError):
            self.authorize(inventory=[])

    def test_another_valid_case_with_same_grant_requires_its_own_exact_membership(self):
        request = {**self.request, "case_id": "case-2"}
        with self.assertRaises(PermissionError):
            self.authorize(request=request, inventory=self.inventory[:1])

    def test_ambiguous_matching_permissions_are_rejected(self):
        extra = {**self.inventory[0], "permission_id": "other-valid-host-permission", "permission_version": 2,
                 "identity": "e" * 64}
        with self.assertRaises(PermissionError):
            self.authorize(inventory=[*self.inventory, extra])

    def test_snapshot_grant_must_match_request_even_when_inventory_matches(self):
        request = copy.deepcopy(self.request)
        request["pins"]["grant_hash"] = "d" * 64
        with self.assertRaises(PermissionError):
            self.authorize(request=request)

    def test_redacted_source_requires_both_raw_and_fixture_blobs(self):
        snapshot = self.f.snapshot(9, raw=b"synthetic raw CRLF\r\n", fixture=b"synthetic redacted\r\n", redaction="host-reviewed")
        case = {**self.cases[0], "id": "redacted", "snapshot_ids": [snapshot["snapshot_id"]]}
        case_set = self.f.cases.put_case_set(self.f.owner, [case], 43, event_key="redacted-set")
        request = {"case_set_id": case_set["case_set_id"], "case_id": "redacted",
                   "pins": {**self.request["pins"], "case_set_hash": case_set["manifest_hash"]}}
        inventory = [self.entry(snapshot["snapshot_id"])]
        with patch.object(self.blobs, "read", wraps=self.blobs.read) as read:
            self.authorize(request=request, inventory=inventory)
        self.assertEqual({call.args[1] for call in read.call_args_list}, {snapshot["source_hash"], snapshot["fixture_hash"]})
        self.blobs._path(self.f.owner.brain_id, snapshot["source_hash"]).unlink()
        with self.assertRaises(AssetUnavailable):
            self.authorize(request=request, inventory=inventory)

    def test_exact_nonempty_raw_or_redacted_body_inside_common_baseline_is_rejected(self):
        raw, fixture = b"private raw source\r\n", b"redacted solver fixture\r\n"
        snapshot = self.f.snapshot(12, raw=raw, fixture=fixture, redaction="host-reviewed")
        case = {**self.cases[0], "id": "embedded", "snapshot_ids": [snapshot["snapshot_id"]]}
        case_set = self.f.cases.put_case_set(self.f.owner, [case], 43, event_key="embedded-fixture-set")
        request = {"case_set_id": case_set["case_set_id"], "case_id": "embedded",
                   "pins": {**self.request["pins"], "case_set_hash": case_set["manifest_hash"]}}
        inventory = [self.entry(snapshot["snapshot_id"])]
        for content in (raw, fixture):
            with self.assertRaisesRegex(PermissionError, "context_overlap"):
                self.authorize(request=request, inventory=inventory, baseline_content="Owner prefix\n" + content.decode() + "Owner suffix\n")
        # Deliberately an exact full-body byte comparison, with no partial or
        # semantic matching claim; CRLF normalization is not equivalent bytes.
        self.assertIsNone(self.authorize(request=request, inventory=inventory,
            baseline_content=raw.decode().replace("\r\n", "\n") + fixture.decode().replace("\r\n", "\n")))

    def test_empty_body_does_not_match_every_common_baseline(self):
        snapshot = self.f.snapshot(13, raw=b"", fixture=b"")
        case = {**self.cases[0], "id": "empty", "snapshot_ids": [snapshot["snapshot_id"]]}
        case_set = self.f.cases.put_case_set(self.f.owner, [case], 43, event_key="empty-fixture-set")
        request = {"case_set_id": case_set["case_set_id"], "case_id": "empty",
                   "pins": {**self.request["pins"], "case_set_hash": case_set["manifest_hash"]}}
        self.assertIsNone(self.authorize(request=request, inventory=[self.entry(snapshot["snapshot_id"])],
                                         baseline_content="Nonempty protected common baseline"))

    def test_corrupted_or_missing_fixture_and_incorrect_host_blob_bytes_are_rejected(self):
        path = self.blobs._path(self.f.owner.brain_id, self.inventory[0]["fixture_hash"])
        path.write_text("enc:corrupt", encoding="ascii")
        with self.assertRaises(AssetUnavailable):
            self.authorize()
        with patch.object(self.blobs, "read", return_value=b"wrong synthetic bytes"):
            with self.assertRaises(AssetUnavailable):
                self.authorize()

    def test_qualification_check_reads_no_answer_or_solver_projection_and_writes_nothing(self):
        request = {**self.request, "case_id": "case-2"}
        with closing(self.f.rs._conn()) as connection:
            before = connection.total_changes
            tables = ("improvement_case_exposures", "improvement_qualification", "improvement_qualification_claims", "improvement_events")
            counts = {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables}
            writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
            connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK)
            with patch.object(IC.CaseStore, "solver_case", side_effect=AssertionError("No solver projection")), \
                 patch.object(IC.CaseStore, "evaluator_case", side_effect=AssertionError("No answers")), \
                 patch.object(self.blobs, "read", wraps=self.blobs.read) as read:
                self.assertIsNone(sources.authorize_case(connection, self.f.owner.brain_id, request, self.inventory,
                    blobs=self.blobs, baseline_content="Protected baseline without full fixture bodies"))
            connection.set_authorizer(None)
            self.assertEqual(connection.total_changes, before)
            self.assertEqual(counts, {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables})
            case = IC.CaseStore._case(None, connection, self.f.owner.brain_id, request["case_set_id"], request["case_id"])
            self.assertNotIn(case["answer_hash"], {call.args[1] for call in read.call_args_list})

    def test_corrupt_snapshot_status_manifest_hash_and_case_set_identity_are_rejected(self):
        original = IC.CaseStore._row
        for field, changed in (("status", "insufficient_source"), ("manifest_hash", "f" * 64)):
            def row(connection, table, brain, identifier):
                value = original(connection, table, brain, identifier)
                return {**dict(value), field: changed} if table == "improvement_snapshots" else value

            with patch.object(IC.CaseStore, "_row", side_effect=row):
                with self.assertRaises((PermissionError, IM.ConflictError)):
                    self.authorize()
        def case_set_row(connection, table, brain, identifier):
            value = original(connection, table, brain, identifier)
            return {**dict(value), "manifest_json": "{}"} if table == "improvement_case_sets" else value

        with patch.object(IC.CaseStore, "_row", side_effect=case_set_row):
            with self.assertRaises(IM.ConflictError):
                self.authorize()

    def test_required_blob_dependency_has_no_default_or_silent_bypass(self):
        with closing(self.f.rs._conn()) as connection:
            with self.assertRaises(TypeError):
                sources.authorize_case(connection, self.f.owner.brain_id, self.request, self.inventory)
            with self.assertRaises(ValueError):
                sources.authorize_case(connection, self.f.owner.brain_id, self.request, self.inventory, blobs=None)


if __name__ == "__main__":
    unittest.main()
