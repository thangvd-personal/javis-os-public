"""Owner proposals use real canonical files, ACLs, encrypted blobs and M1 context grants."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import json
import sqlite3
import stat
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

import improvement_assets as IA
import improvement_store as IM
from improvement_candidate_records import CandidateRecords
import test_improvement_host_snapshots as fixtures

if importlib.util.find_spec("improvement_skill_candidates"):
    from improvement_skill_candidates import SkillCandidates
else:
    SkillCandidates = None


class SkillCandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SkillCandidates, "Owner skill candidate staging is absent")
        self.f = fixtures.HostSnapshotTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.service = SkillCandidates(self.f.assets, pack_state_dir=self.f.root / "packs")
        self.owner = self.f.owner
        self.raw = self.f.skill.read_bytes()
        self.start = self.raw.index(b"Retain owner rules.")
        self.end = len(self.raw)
        self.records = CandidateRecords(self.f.assets)
        self.original_targets = self.f.assets.targets(self.owner)
        self.original_revisions = self.f.assets.revisions(self.owner, self.f.target["target_id"])
        self.addCleanup(self.unchanged)

    def unchanged(self):
        self.assertEqual(self.f.assets.targets(self.owner), self.original_targets)
        self.assertEqual(self.f.assets.revisions(self.owner, self.f.target["target_id"]), self.original_revisions)
        self.assertEqual(self.f.count("agent_calls"), 0)
        self.assertEqual(self.f.count("improvement_trials"), 0)
        self.assertFalse((self.f.brain / ".claude").exists())

    def patch(self, **changes):
        return {"version": 1, "target_id": self.f.target["target_id"], "base_revision_id": self.f.target["revision_id"],
            "before_hash": hashlib.sha256(self.raw).hexdigest(), "operation": "replace_body_region",
            "start": self.start, "end": self.end, "replacement": "Retain owner rules.\nCheck every subtotal.\n",
            "hypothesis": "Checking subtotals catches omitted rows.", "failure_signature": "Missing row in total.",
            "expected_behavior": "All authorized rows counted exactly once.", "possible_regressions": ["Duplicate subtotal."],
            "evidence_refs": [], "angle": "execution", **changes}

    def stage(self, key="owner-stage-candidate", **changes):
        return self.service.stage(self.owner, "experiment-owner", self.patch(**changes), event_key=key)

    def region(self, key="owner-declare-region"):
        return self.service.declare_region(self.owner, "experiment-owner", self.f.target["revision_id"], self.start, self.end,
                                          expected_target_generation=1, event_key=key)

    def test_no_ownership_map_stages_diff_only_with_host_owner_provenance(self):
        result = self.stage()
        self.assertIsNone(result["edit_authority"])
        self.assertEqual(result["semantic_status"], "unreviewed")
        self.assertFalse(result["keep_base"])
        self.assertEqual(self.f.skill.read_bytes(), self.raw)
        self.assertEqual(len(self.records.list(self.owner, "experiment-owner")), 1)
        content = self.f.blobs.read(self.owner.brain_id, result["content_hash"])
        self.assertEqual(content, self.raw[:self.start] + self.patch()["replacement"].encode())
        provenance = json.loads(self.f.blobs.read(self.owner.brain_id, result["provenance_hash"]))
        self.assertEqual(provenance["context_id"], self.f.binding["context_id"])
        self.assertEqual(provenance["source_authority_hash"], self.service.observe(self.owner, self.original_targets[0])[0]["source_authority_hash"])
        self.assertEqual(provenance["semantic_status"], "unreviewed")
        with closing(self.f.database._conn()) as connection:
            event = connection.execute("SELECT result_json FROM improvement_events WHERE brain_id=? AND event_key=?",
                (self.owner.brain_id, provenance["owner_request_event"])).fetchone()
            self.assertEqual(json.loads(event[0])["context_id"], self.f.binding["context_id"])
        self.assertNotIn("Retain owner", json.dumps(result))
        self.assertTrue(all(path.read_bytes().startswith(b"enc:") for path in (self.f.root / "assets").rglob("*.enc")))

    def test_explicit_region_and_exact_diff_are_edit_authority_only(self):
        region = self.region()
        result = self.stage()
        self.assertEqual(result["edit_authority"]["authority_id"], region["authority_id"])
        self.assertEqual(result["edit_authority"]["kind"], "editable_region")
        decision = self.service.authorize_diff(self.owner, result["candidate_id"], expected_generation=1,
            before_hash=result["base_hash"], after_hash=result["content_hash"], event_key="owner-authorize-exact-diff")
        self.assertEqual(decision["kind"], "exact_diff")
        self.assertEqual(decision["status"], "active")
        self.assertEqual(self.records.get(self.owner, result["candidate_id"])["semantic_status"], "unreviewed")

    def test_same_event_replays_without_read_and_cannot_revive_revoked_authority(self):
        region = self.region()
        result = self.stage()
        self.records.revoke_authority(self.owner, region["authority_id"], expected_generation=1, event_key="owner-revoke-region")
        self.f.host.revoke(self.owner, "experiment-owner", expected_generation=1, event_key="owner-revoke-context")
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="owner-switch-off")
        with patch.object(IA, "capture_source", side_effect=AssertionError("Historical replay cannot recapture")):
            self.assertEqual(self.stage(), result)
            self.assertEqual(self.region(), region)
        current = self.records.get(self.owner, result["candidate_id"])
        self.assertEqual(current["edit_authority"]["status"], "revoked")
        self.assertEqual(current["context_grant"]["status"], "revoked")
        with self.assertRaises(PermissionError):
            self.stage("new-event-after-off")

    def test_exact_hash_stale_generation_and_foreign_brain_cannot_authorize(self):
        result = self.stage()
        options = {"expected_generation": 1, "before_hash": result["base_hash"], "after_hash": result["content_hash"],
                   "event_key": "invalid-exact-decision"}
        for change in ({"expected_generation": True}, {"expected_generation": 2}, {"after_hash": "a" * 64},
                       {"before_hash": "b" * 64}):
            with self.subTest(change=change), self.assertRaises((ValueError, IM.ConflictError)):
                self.service.authorize_diff(self.owner, result["candidate_id"], **{**options, **change})
        with self.assertRaises(PermissionError):
            self.service.authorize_diff(self.f.other, result["candidate_id"], **options)
        self.assertIsNone(self.records.get(self.owner, result["candidate_id"])["edit_authority"])

    def test_keep_base_is_preserved_and_duplicate_event_is_exact(self):
        result = self.stage(replacement=self.raw[self.start:self.end].decode())
        self.assertTrue(result["keep_base"])
        self.assertEqual(result["content_hash"], result["base_hash"])
        self.assertEqual(self.stage(replacement=self.raw[self.start:self.end].decode()), result)
        with self.assertRaises(IM.ConflictError):
            self.stage(replacement="Different request.")

    def test_source_edit_during_blob_publication_rejects_all_metadata(self):
        before = self.f.count("improvement_events")
        original, changed = self.f.blobs.write, []

        def publish(*args):
            result = original(*args)
            if not changed:
                changed.append(1)
                self.f.skill.write_bytes(self.raw + b"Owner added a new rule.\n")
            return result

        with patch.object(self.f.blobs, "write", side_effect=publish), self.assertRaises((IM.ConflictError, IA.SourceError)):
            self.stage()
        self.assertEqual(self.records.list(self.owner, "experiment-owner"), [])
        self.assertEqual(self.f.count("improvement_events"), before)
        self.assertEqual(self.f.skill.read_bytes(), self.raw + b"Owner added a new rule.\n")

    def test_current_permissions_change_during_publication_rejects(self):
        original = self.f.blobs.write
        mode, changed = self.f.skill.stat().st_mode, []

        def publish(*args):
            result = original(*args)
            if not changed:
                changed.append(1)
                self.f.skill.chmod(mode & ~stat.S_IWUSR)
            return result

        try:
            with patch.object(self.f.blobs, "write", side_effect=publish), self.assertRaises((IM.ConflictError, PermissionError, IA.SourceError)):
                self.stage()
            self.assertEqual(self.records.list(self.owner, "experiment-owner"), [])
        finally:
            self.f.skill.chmod(mode)

    def test_third_slot_concurrent_requests_have_one_remaining_winner(self):
        self.stage()
        gate = threading.Barrier(2)

        def stage(index):
            gate.wait()
            try:
                return self.stage("parallel-candidate-" + str(index), replacement=f"Retain owner rules.\nCheck row {index}.\n")
            except IM.ConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(stage, (2, 3)))
        self.assertEqual(sum(value is not None for value in values), 1)
        self.assertEqual([row["ordinal"] for row in self.records.list(self.owner, "experiment-owner")], [1, 2])
        self.assertEqual(self.f.count("improvement_skill_candidates"), 2)

    def test_sensitive_invalid_provenance_and_metadata_edits_reject_without_events(self):
        before = self.f.count("improvement_events")
        for index, change in enumerate((
            {"replacement": "api_key=sk-" + "a" * 40},
            {"evidence_refs": [{"kind": "owner_request", "id": "snapshot-test-context"}]},
            {"evidence_refs": [{"kind": "tuning_trial", "id": "missing-trial"}]},
            {"start": self.raw.index(b"description"), "end": self.raw.index(b"description") + 4},
            {"replacement": "Run scripts/ungranted.py"}, {"target_id": "unknown-target"},
        )):
            with self.subTest(change=list(change)), self.assertRaises((ValueError, PermissionError, IM.ConflictError)):
                self.stage("invalid-stage-" + str(index), **change)
        self.assertEqual(self.f.count("improvement_events"), before)
        self.assertEqual(self.records.list(self.owner, "experiment-owner"), [])

    def test_disabled_or_replaced_canonical_never_becomes_candidate_authority(self):
        disabled = self.f.skill.parent.parent / ".disabled" / "report"
        disabled.parent.mkdir()
        self.f.skill.parent.rename(disabled)
        try:
            with self.assertRaises((IA.SourceError, PermissionError, IM.ConflictError)):
                self.stage()
        finally:
            disabled.rename(self.f.skill.parent)
        old = self.f.skill.with_name("old.txt")
        self.f.skill.rename(old)
        self.f.skill.write_bytes(self.raw)
        with self.assertRaises((IA.SourceError, PermissionError, IM.ConflictError)):
            self.stage("stage-replaced-same-bytes")

    def test_exact_diff_cannot_reauthorize_a_candidate_under_changed_source_permissions(self):
        result = self.stage()
        mode = self.f.skill.stat().st_mode
        try:
            self.f.skill.chmod(mode & ~stat.S_IWUSR)
            with self.assertRaises((IM.ConflictError, PermissionError, IA.SourceError)):
                self.service.authorize_diff(self.owner, result["candidate_id"], expected_generation=1,
                    before_hash=result["base_hash"], after_hash=result["content_hash"], event_key="authorize-after-mode-change")
            self.assertIsNone(self.records.get(self.owner, result["candidate_id"])["edit_authority"])
        finally:
            self.f.skill.chmod(mode)

    def test_corrupt_provenance_cannot_be_authorized_even_when_after_hash_matches(self):
        result = self.stage()
        self.f.blobs._path(self.owner.brain_id, result["provenance_hash"]).write_text("enc:broken", encoding="ascii")
        from improvement_blobs import AssetUnavailable
        with self.assertRaises(AssetUnavailable):
            self.service.authorize_diff(self.owner, result["candidate_id"], expected_generation=1,
                before_hash=result["base_hash"], after_hash=result["content_hash"], event_key="authorize-corrupt-provenance")
        self.assertIsNone(self.records.get(self.owner, result["candidate_id"])["edit_authority"])

    def test_owner_request_reference_resolves_to_same_host_context(self):
        first = self.stage()
        provenance = json.loads(self.f.blobs.read(self.owner.brain_id, first["provenance_hash"]))
        second = self.stage("owner-stage-second", evidence_refs=[{"kind": "owner_request", "id": provenance["owner_request_event"]}])
        saved = json.loads(self.f.blobs.read(self.owner.brain_id, second["provenance_hash"]))
        self.assertEqual(saved["evidence_refs"], [{"kind": "owner_request", "id": provenance["owner_request_event"]}])

    def test_authorization_replay_does_not_restore_revoked_diff_or_read_private_blobs(self):
        candidate = self.stage()
        options = {"expected_generation": 1, "before_hash": candidate["base_hash"], "after_hash": candidate["content_hash"],
                   "event_key": "owner-approve-once"}
        decision = self.service.authorize_diff(self.owner, candidate["candidate_id"], **options)
        self.records.revoke_authority(self.owner, decision["authority_id"], expected_generation=1, event_key="owner-revoke-exact-diff")
        self.f.host.revoke(self.owner, "experiment-owner", expected_generation=1, event_key="revoke-before-diff-replay")
        with patch.object(self.f.blobs, "read", side_effect=AssertionError("Historical replay has no private I/O")):
            self.assertEqual(self.service.authorize_diff(self.owner, candidate["candidate_id"], **options), decision)
            self.assertEqual(self.records.get(self.owner, candidate["candidate_id"])["edit_authority"]["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
