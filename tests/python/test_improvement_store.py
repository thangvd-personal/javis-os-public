"""M0 immutable improvement history uses the real shared SQLite store, without engines."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="imp-store-"))
os.environ["JAVIS_STATE_DIR"] = str(TEST_ROOT / "state")
os.environ["BRAINS_DIR"] = str(TEST_ROOT / "brains")
os.environ["OBSIDIAN_VAULT_PATH"] = str(TEST_ROOT / "vault")

import resonance_store as RS  # noqa: E402

if importlib.util.find_spec("improvement_store"):
    import improvement_store as IM
    from improvement_blobs import RevisionBlobs
else:
    IM = None


class ImprovementStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IM, "The immutable improvement foundation is not implemented")
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_ROOT))
        self.rs = RS.GoalStore(self.root / "resonance.sqlite3")
        self.blobs = RevisionBlobs(self.root / "assets")
        self.store = IM.ImprovementStore(self.rs, self.blobs)
        self.owner = RS.Principal("owner", "owner", "brain-a")
        self.other = RS.Principal("owner", "owner", "brain-b")

    def source(self, content=b"---\nname: Report\n---\nKeep owner rules.\r\n", identity="inode-1"):
        digest = hashlib.sha256(content).hexdigest()
        return IM.SourceSnapshot("skill", "skills/report/SKILL.md", digest, digest,
                                 True, "user_protected", identity), content

    def capture(self, key="capture-0001", **kwargs):
        source, content = self.source(**kwargs)
        return self.store.capture(self.owner, source, content, event_key=key)

    def test_read_default_does_not_migrate_or_create_blobs(self):
        self.assertEqual(self.store.get_policy(self.owner), {"mode": "suggest", "version": 0})
        self.assertEqual(self.store.targets(self.owner), [])
        self.assertFalse((self.root / "assets").exists())
        with self.rs._conn() as c:
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='improvement_targets'").fetchone())

    def test_existing_metadata_reader_cannot_write_or_create_a_database(self):
        missing = self.root / "missing.sqlite3"
        self.assertIsNone(IM.read_existing(missing, self.blobs))
        self.assertFalse(missing.exists())
        self.assertIsNone(IM.read_existing(self.root / "resonance.sqlite3", self.blobs))
        first = self.capture()
        reader = IM.read_existing(self.root / "resonance.sqlite3", self.blobs)
        self.assertEqual(reader.read_revision(self.owner, first["revision_id"]), self.source()[1])
        with reader.store._conn() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden_write(value TEXT)")

    def test_capture_preserves_bytes_and_replays_same_event(self):
        first = self.capture()
        self.assertEqual(first, self.capture())
        self.assertEqual(first["revision_id"], self.capture("capture-0002")["revision_id"])
        self.assertEqual(len(self.store.revisions(self.owner, first["target_id"])), 1)
        self.assertEqual(self.store.read_revision(self.owner, first["revision_id"]), self.source()[1])
        self.assertEqual(first["generation"], 1)
        for path in (self.root / "assets").rglob("*.enc"):
            self.assertTrue(path.read_text().startswith("enc:"))
            self.assertNotIn("Keep owner rules", path.read_text())

    def test_same_event_with_different_payload_conflicts(self):
        self.capture()
        source, raw = self.source(b"Changed content")
        with self.assertRaises(IM.ConflictError):
            self.store.capture(self.owner, source, raw, event_key="capture-0001", expected_generation=1)
        self.assertEqual(len(self.store.events(self.owner)), 1)

    def test_source_refresh_requires_generation_and_keeps_history(self):
        first = self.capture()
        source, raw = self.source(b"Different bytes, same source incarnation")
        with self.assertRaises(IM.ConflictError):
            self.store.capture(self.owner, source, raw, event_key="refresh-0001")
        second = self.store.capture(self.owner, source, raw, event_key="refresh-0002", expected_generation=1)
        self.assertEqual(second["target_id"], first["target_id"])
        self.assertEqual(second["generation"], 2)
        self.assertNotEqual(second["revision_id"], first["revision_id"])
        self.assertEqual(len(self.store.revisions(self.owner, first["target_id"])), 2)
        self.assertEqual(self.store.read_revision(self.owner, first["revision_id"]), self.source()[1])

    def test_cross_brain_and_agent_cannot_read_or_mutate_assets(self):
        first = self.capture()
        self.assertEqual(self.store.targets(self.other), [])
        for p in (self.other, RS.Principal("agent", "pretend-owner", "brain-a")):
            with self.assertRaises(PermissionError):
                self.store.read_revision(p, first["revision_id"])
            with self.assertRaises(PermissionError):
                self.store.retire(p, first["target_id"], expected_generation=1, event_key="retire-other")
        with self.assertRaises(PermissionError):
            self.store.create_bundle(self.other, [first["revision_id"]], event_key="bundle-other")

    def test_retire_and_recreate_never_reuse_identity(self):
        first = self.capture()
        retired = self.store.retire(self.owner, first["target_id"], expected_generation=1, event_key="retire-0001")
        self.assertEqual(retired["status"], "retired")
        self.assertEqual(retired, self.store.retire(self.owner, first["target_id"], expected_generation=1,
                                                   event_key="retire-0001"))
        new = self.capture("capture-new", identity="inode-2")
        self.assertNotEqual(new["target_id"], first["target_id"])
        self.assertEqual(self.store.read_revision(self.owner, first["revision_id"]), self.source()[1])

    def test_replaced_source_is_a_new_target_not_an_inherited_identity(self):
        first = self.capture()
        source, raw = self.source(identity="replacement-inode")
        new = self.store.capture(self.owner, source, raw, event_key="replace-0001", expected_generation=1)
        self.assertNotEqual(first["target_id"], new["target_id"])
        self.assertEqual(len(self.store.targets(self.owner)), 1)

    def test_immutable_rows_and_bundle_same_brain_constraint(self):
        first = self.capture()
        bundle = self.store.create_bundle(self.owner, [first["revision_id"]], event_key="bundle-0001")
        self.assertEqual(bundle, self.store.create_bundle(self.owner, [first["revision_id"]], event_key="bundle-0001"))
        with self.rs._Tx(self.rs) as c:
            for table, field, value, ident in (("improvement_revisions", "content_hash", "bad", first["revision_id"]),
                                             ("improvement_bundles", "manifest_hash", "bad", bundle["bundle_id"])):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(f"UPDATE {table} SET {field}=? WHERE id=?", (value, ident))

    def test_policy_off_blocks_capture_and_mode_cannot_grant_execution(self):
        policy = self.store.set_policy(self.owner, "off", expected_version=0, event_key="policy-0001")
        self.assertEqual(policy, {"mode": "off", "version": 1})
        self.assertEqual(policy, self.store.set_policy(self.owner, "off", expected_version=0, event_key="policy-0001"))
        with self.assertRaises(PermissionError):
            self.capture()
        with self.assertRaises(ValueError):
            self.store.set_policy(self.owner, "auto_scoped", expected_version=1, event_key="policy-0002")
        with self.assertRaises(IM.ConflictError):
            self.store.set_policy(self.owner, "suggest", expected_version=0, event_key="policy-0003")
        self.assertFalse((self.root / "assets").exists())

    def test_concurrent_source_refresh_has_exactly_one_winner(self):
        first = self.capture()
        barrier = threading.Barrier(2)

        def update(index):
            wrapper = IM.ImprovementStore(self.rs, self.blobs)
            source, raw = self.source(f"change-{index}".encode())
            barrier.wait()
            try:
                return wrapper.capture(self.owner, source, raw, event_key=f"race-000{index}", expected_generation=1)
            except IM.ConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, (1, 2)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(len(self.store.revisions(self.owner, first["target_id"])), 2)

    def test_blob_corruption_or_loss_never_returns_unverified_content(self):
        first = self.capture()
        path = next((self.root / "assets").rglob("*.enc"))
        path.write_text("enc:corrupted", encoding="utf-8")
        with self.assertRaises(IM.AssetUnavailable):
            self.store.read_revision(self.owner, first["revision_id"])
        path.unlink()
        with self.assertRaises(IM.AssetUnavailable):
            self.store.read_revision(self.owner, first["revision_id"])

    def test_blob_publish_sharing_conflict_reuses_only_verified_winner(self):
        raw = b"Same authorized content from concurrent publishers.\r\n"
        digest = hashlib.sha256(raw).hexdigest()
        path = self.blobs._path(self.owner.brain_id, digest)
        winner = []

        def published_by_competitor(temporary, destination):
            self.assertEqual(Path(destination), path)
            ciphertext = Path(temporary).read_bytes()
            path.write_bytes(ciphertext)
            winner.append(ciphertext)
            raise PermissionError("Synthetic Windows sharing conflict at atomic replace")

        with patch("improvement_blobs.os.replace", side_effect=published_by_competitor):
            self.assertEqual(self.blobs.write(self.owner.brain_id, raw), digest)
        self.assertEqual(self.blobs.read(self.owner.brain_id, digest), raw)
        self.assertEqual(path.read_bytes(), winner[0])
        self.assertEqual(list(path.parent.glob(".write-*")), [])

    def test_blob_publish_conflict_never_accepts_or_overwrites_corrupt_winner(self):
        raw = b"Corrupt competitor must remain unavailable."
        path = self.blobs._path(self.owner.brain_id, hashlib.sha256(raw).hexdigest())

        def corrupt_competitor(temporary, destination):
            path.write_bytes(b"enc:corrupt-competing-history")
            raise PermissionError("Synthetic Windows sharing conflict")

        with patch("improvement_blobs.os.replace", side_effect=corrupt_competitor), self.assertRaises(IM.AssetUnavailable):
            self.blobs.write(self.owner.brain_id, raw)
        self.assertEqual(path.read_bytes(), b"enc:corrupt-competing-history")
        self.assertEqual(list(path.parent.glob(".write-*")), [])

    def test_blob_publish_permission_denial_without_winner_still_fails(self):
        raw = b"No existing content means permission denial is not success."
        path = self.blobs._path(self.owner.brain_id, hashlib.sha256(raw).hexdigest())
        with patch("improvement_blobs.os.replace", side_effect=PermissionError("Synthetic denial")), self.assertRaises(PermissionError):
            self.blobs.write(self.owner.brain_id, raw)
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob(".write-*")), [])

    def test_empty_revision_rejects_decryption_failure(self):
        first = self.capture(content=b"")
        self.assertEqual(self.store.read_revision(self.owner, first["revision_id"]), b"")
        path = next((self.root / "assets").rglob("*.enc"))
        path.write_text("enc:corrupted", encoding="ascii")
        with self.assertRaises(IM.AssetUnavailable):
            self.store.read_revision(self.owner, first["revision_id"])

    def test_migration_is_idempotent_and_future_schema_is_rejected(self):
        self.capture()
        IM.ensure(self.rs)
        IM.ensure(self.rs)
        self.assertEqual(len(self.store.events(self.owner)), 1)
        with self.rs._Tx(self.rs) as c:
            c.execute("UPDATE improvement_schema SET version=999 WHERE id=1")
        with self.assertRaises(IM.SchemaError):
            IM.ensure(self.rs)

    def test_concurrent_first_migration_serializes_its_backup(self):
        barrier = threading.Barrier(2)
        backups = []
        original = self.rs._backup_before

        def backup(*args):
            backups.append(args)
            time.sleep(0.1)
            return original(*args)

        def migrate(index):
            barrier.wait()
            try:
                IM.ensure(self.rs)
                return None
            except Exception as error:
                return type(error).__name__

        with patch.object(self.rs, "_backup_before", side_effect=backup), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(migrate, (1, 2)))
        self.assertEqual(results, [None, None])
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.store.targets(self.owner), [])

    def test_invalid_source_digest_fails_before_writing_metadata(self):
        source, raw = self.source()
        with self.assertRaises(ValueError):
            self.store.capture(self.owner, source, raw + b"tampered", event_key="tamper-0001")
        self.assertEqual(self.store.targets(self.owner), [])

    def test_failed_schema_migration_rolls_back_all_new_tables(self):
        with patch.object(IM, "_SCHEMA", (*IM._SCHEMA, "INVALID MIGRATION STATEMENT")):
            with self.assertRaises(sqlite3.OperationalError):
                IM.ensure(self.rs)
        with self.rs._conn() as connection:
            names = connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'improvement_%'").fetchall()
            self.assertEqual(names, [])
        IM.ensure(self.rs)
        self.assertEqual(self.store.targets(self.owner), [])

    def test_encryption_failure_cannot_commit_partial_asset_metadata(self):
        failing = RevisionBlobs(self.root / "failed-assets", encrypt=lambda value: "plain:" + value)
        wrapper = IM.ImprovementStore(self.rs, failing)
        source, raw = self.source()
        with self.assertRaises(IM.AssetUnavailable):
            wrapper.capture(self.owner, source, raw, event_key="encrypt-failure")
        self.assertEqual(wrapper.targets(self.owner), [])
        self.assertEqual(wrapper.events(self.owner), [])

    def test_current_revision_tracks_a_source_reverted_to_earlier_bytes(self):
        first = self.capture()
        source, raw = self.source(b"Second version")
        self.store.capture(self.owner, source, raw, event_key="second-version", expected_generation=1)
        source, raw = self.source()
        restored = self.store.capture(self.owner, source, raw, event_key="restore-source", expected_generation=2)
        self.assertEqual(restored["revision_id"], first["revision_id"])
        self.assertEqual(self.store.targets(self.owner)[0].get("current_revision_id"), first["revision_id"])
        self.assertEqual(self.store.targets(self.owner)[0]["generation"], 3)

    def test_source_kind_cannot_disagree_with_canonical_location(self):
        source, raw = self.source()
        source = IM.SourceSnapshot("agent", source.canonical_ref, source.source_hash, source.protected_hash,
                                   True, source.ownership, source.identity)
        with self.assertRaises(ValueError):
            self.store.capture(self.owner, source, raw, event_key="wrong-source-kind")

    def test_new_revision_parent_is_the_current_source_after_a_reversion(self):
        first = self.capture()
        source, raw = self.source(b"Version B")
        self.store.capture(self.owner, source, raw, event_key="parent-second", expected_generation=1)
        source, raw = self.source()
        self.store.capture(self.owner, source, raw, event_key="parent-restore", expected_generation=2)
        source, raw = self.source(b"Version C")
        current = self.store.capture(self.owner, source, raw, event_key="parent-third", expected_generation=3)
        history = {row["id"]: row for row in self.store.revisions(self.owner, first["target_id"])}
        self.assertEqual(history[current["revision_id"]]["parent_id"], first["revision_id"])

    def test_disabled_policy_rejects_new_bundle_events_but_preserves_historical_replay(self):
        revision = self.capture()
        identifiers = [revision["revision_id"]]
        original = self.store.create_bundle(self.owner, identifiers, event_key="bundle-before-disabled")
        self.store.set_policy(self.owner, "off", expected_version=0, event_key="disable-before-bundle")
        before = self.store.events(self.owner)
        self.assertEqual(self.store.create_bundle(self.owner, identifiers, event_key="bundle-before-disabled"), original)
        with self.assertRaisesRegex(PermissionError, "improvement_disabled"):
            self.store.create_bundle(self.owner, identifiers, event_key="new-bundle-while-disabled")
        self.assertEqual(self.store.events(self.owner), before)


if __name__ == "__main__":
    unittest.main()
