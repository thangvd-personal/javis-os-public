"""Cold metadata/revocation needs no native, SQL context or prompt dependencies."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

import test_improvement_trial_host as fixtures
import improvement_store as IM
from improvement_blobs import RevisionBlobs
from resonance_store import Principal

if importlib.util.find_spec("improvement_trial_records"):
    import improvement_trial_records as records
else:
    records = None


class ContextRecordTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(records, "Dependency-free context records are missing")
        self.f = fixtures.HostContextTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.meta = self.f.create()
        self.owner = self.f.f.owner
        self.assets = self.f.f.assets

    def test_cold_existing_reader_returns_exact_metadata_without_observers_or_writes(self):
        def files():
            return {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.f.f.root.rglob("*") if p.is_file()}
        before = files()
        wrapper = IM.read_existing(self.assets.store.path, self.assets.blobs)
        with patch.object(fixtures.HOST.HostContextStore, "__init__", side_effect=AssertionError("No host wiring")), \
                patch.object(fixtures.HOST.HostContextStore, "_observe", side_effect=AssertionError("No source capture")):
            value = records.HostContextRecords(wrapper).get(self.owner, "experiment-host")
        self.assertEqual(value, self.meta)
        after = files()
        self.assertEqual(set(after), set(before))
        # SQLite readers coordinate through the already-existing SHM. Logical
        # database, WAL, encrypted blobs and all other bytes must stay unchanged.
        self.assertEqual({p: digest for p, digest in after.items() if not p.name.endswith("-shm")},
                         {p: digest for p, digest in before.items() if not p.name.endswith("-shm")})

    def test_missing_database_is_not_created_by_get_or_revoke(self):
        root = self.f.f.root / "absent"
        wrapper = IM.read_existing(root / "resonance.sqlite3", RevisionBlobs(root / "blobs"))
        self.assertIsNone(wrapper)
        self.assertFalse(root.exists())

    def test_revoke_works_without_sources_and_preserves_context_and_idempotency(self):
        pure = records.HostContextRecords(self.assets)
        with patch.object(fixtures.HOST.HostContextStore, "_observe", side_effect=AssertionError("No capture")):
            value = pure.revoke(self.owner, "experiment-host", expected_generation=1, event_key="cold-revoke-event")
            self.assertEqual(value["grant_status"], "revoked")
            self.assertEqual(value["generation"], 2)
            self.assertEqual(value["baseline_context_hash"], self.meta["baseline_context_hash"])
            self.assertEqual(pure.revoke(self.owner, "experiment-host", expected_generation=1,
                                        event_key="cold-revoke-event"), value)
            self.assertEqual(pure.get(self.owner, "experiment-host"), value)
        with self.assertRaises(IM.ConflictError):
            pure.revoke(self.owner, "experiment-host", expected_generation=1, event_key="cold-other-event")
        with self.assets.store._conn() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0], 0)

    def test_cross_brain_and_non_owner_cannot_read_or_revoke(self):
        pure = records.HostContextRecords(self.assets)
        for principal in (self.f.f.other, Principal("agent", "agent", self.owner.brain_id)):
            with self.subTest(principal=principal), self.assertRaises(PermissionError):
                pure.get(principal, "experiment-host")
            with self.subTest(principal=principal), self.assertRaises(PermissionError):
                pure.revoke(principal, "experiment-host", expected_generation=1, event_key="cold-denied-event")

    def test_corrupt_private_binding_never_returns_partial_metadata(self):
        with patch.object(self.assets.blobs, "read", return_value=b"untrusted"):
            with self.assertRaises(IM.AssetUnavailable):
                records.HostContextRecords(self.assets).get(self.owner, "experiment-host")


if __name__ == "__main__":
    unittest.main()
