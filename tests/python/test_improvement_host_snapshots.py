"""Host snapshots use real owner files, encrypted blobs and the shared SQLite transaction."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="hs-"))
for key, name in {"JAVIS_STATE_DIR": "boot-state", "BRAIN_PATH": "boot-brain", "BRAINS_DIR": "boot-brains",
                  "OBSIDIAN_VAULT_PATH": "boot-vault", "SOURCES_PATH": "boot-sources",
                  "JAVIS_SESSIONS_DB": "boot-sessions.sqlite3"}.items():
    os.environ[key] = str(TEST_ROOT / name)

from cryptography.fernet import Fernet
from resonance_store import GoalStore, Principal
from improvement_assets import capture_source
from improvement_blobs import RevisionBlobs
from improvement_cases import CaseStore
from improvement_trial_context import ContextSource, capture_context
from improvement_trial_file_sources import BrainFileFixtures
from improvement_trial_host import HostContextStore, TARGET_FIELDS
import improvement_store as IM


class HostSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(HostContextStore, "capture_snapshot", None)),
                        "Host-private source snapshot capture is not implemented")
        self.directory = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.brain = self.root / "brain"
        self.brain.mkdir()
        self.owner = Principal("owner", "owner", str(self.brain))
        other = self.root / "other-brain"
        other.mkdir()
        self.other = Principal("owner", "owner", str(other))
        self.database = GoalStore(self.root / "state" / "resonance.sqlite3")
        self.codec = Fernet(Fernet.generate_key())
        self.blobs = RevisionBlobs(self.root / "assets",
            encrypt=lambda text: "enc:" + self.codec.encrypt(text.encode()).decode(),
            decrypt=lambda text: self.codec.decrypt(text.removeprefix("enc:").encode()).decode())
        self.assets = IM.ImprovementStore(self.database, self.blobs)
        self.cases = CaseStore(self.assets)
        self.skill = self.brain / "skills" / "report" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_bytes(b"---\nname: Owner report\ndescription: Summarize an authorized fixture.\n---\nRetain owner rules.\n")
        source, raw = capture_source(self.brain, "skill", "report", pack_state_dir=self.root / "packs")
        self.target = self.assets.capture(self.owner, source, raw, event_key="snapshot-test-target")
        self.protected = self.brain / "owner.md"
        self.protected.write_bytes(b"Synthetic complete owner instructions.\r\n")
        self.path = self.brain / "reports" / "source.txt"
        self.path.parent.mkdir()
        self.raw = "Private selected fixture.\r\nNguyên bản.\r\n".encode()
        self.path.write_bytes(self.raw)
        self.ref = "brain-file:reports/source.txt"
        self.metadata = {"timezone": "UTC", "data_period": "synthetic-period", "cutoff": "2026-10-22T00:00:00+00:00",
                         "schema": "text/plain", "freshness_policy": "require_current_version"}
        self.provider = BrainFileFixtures({self.ref: self.metadata})
        self.surface = {"kind": "synthetic-owner-test", "session_id": ""}
        self.engine = {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk", "model": "synthetic-model",
            "reasoning": "medium", "cli_path": str(self.root / "never-executed.exe"), "cli_hash": "a" * 64,
            "cli_version": "synthetic", "sdk_version": "synthetic", "profile_version": "synthetic", "profile_hash": "b" * 64}
        self.host = HostContextStore(self.assets, context_capture=self.context_capture,
            selected_engine=lambda: copy.deepcopy(self.engine), target_observer=self.targets, fixture_observer=self.provider)
        self.binding = self.host.create(self.owner, "experiment-owner", self.surface, [self.target["target_id"]],
                                       source_refs=[self.ref], event_key="snapshot-test-context")
        self.addCleanup(patch.stopall)
        patch("subprocess.run", side_effect=AssertionError("No native/auth/model calls permitted")).start()
        patch("subprocess.Popen", side_effect=AssertionError("No native/auth/model calls permitted")).start()

    def context_capture(self, principal, surface, targets):
        return capture_context([ContextSource("owner", Path(principal.brain_id), "owner.md")],
                               surface=surface, complete_plan=True)

    def targets(self, principal, identifiers):
        rows = self.assets.targets(principal)
        result = []
        for row in rows:
            if row["id"] in identifiers:
                source, _ = capture_source(Path(principal.brain_id), "skill", "report", pack_state_dir=self.root / "packs")
                row.update({key: getattr(source, key) for key in ("kind", "canonical_ref", "identity", "source_hash", "protected_hash", "ownership")})
                result.append({key: row[key] for key in TARGET_FIELDS})
        return sorted(result, key=lambda row: row["id"])

    def capture(self, key="host-snapshot-create", **kwargs):
        return self.host.capture_snapshot(self.owner, "experiment-owner", self.ref, event_key=key, **kwargs)

    def count(self, table):
        with closing(self.database._conn()) as connection:
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_real_private_snapshot_preserves_raw_and_binds_host_grant_without_exposure(self):
        result = self.capture()
        snapshot = self.cases.get_snapshot(self.owner, result["snapshot_id"])
        self.assertEqual(snapshot["manifest"]["grant_hash"], self.binding["grant_hash"])
        self.assertEqual(snapshot["manifest"]["source_ref"], self.ref)
        self.assertEqual(snapshot["manifest"]["source_version"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(datetime.fromisoformat(snapshot["manifest"]["captured_at"]).utcoffset().total_seconds(), 0)
        self.assertEqual(self.blobs.read(self.owner.brain_id, snapshot["source_hash"]), self.raw)
        self.assertEqual(self.blobs.read(self.owner.brain_id, snapshot["fixture_hash"]), self.raw)
        self.assertNotIn(self.raw.decode(), json.dumps(result))
        with closing(self.database._conn()) as connection:
            self.assertNotIn(self.raw.decode(), str(connection.execute("SELECT * FROM improvement_snapshots").fetchall()))
        self.assertTrue(all(path.read_bytes().startswith(b"enc:") for path in (self.root / "assets").rglob("*.enc")))
        for table in ("improvement_qualification", "improvement_trials", "agent_calls", "goals"):
            self.assertEqual(self.count(table), 0)
        self.assertFalse((self.root / "state" / "team.sqlite3").exists())

    def embedded_binding(self):
        self.protected.write_bytes(b"Owner prefix before selected body.\r\n" + self.raw + b"Owner suffix after selected body.\r\n")
        return self.host.create(self.owner, "experiment-embedded", self.surface, [self.target["target_id"]],
                                source_refs=[self.ref], event_key="embedded-fixture-context")

    def test_entire_fixture_embedded_in_larger_protected_context_is_rejected_before_publication(self):
        self.embedded_binding()
        events = self.count("improvement_events")
        with patch.object(self.blobs, "write", side_effect=AssertionError("Overlap must fail before blob publication")):
            with self.assertRaisesRegex(PermissionError, "context_overlap"):
                self.host.capture_snapshot(self.owner, "experiment-embedded", self.ref, event_key="embedded-fixture-snapshot")
        self.assertEqual(self.count("improvement_events"), events)
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_manual_component_snapshot_cannot_bypass_host_query_guard_overlap_check(self):
        binding = self.embedded_binding()
        inventory = self.provider(self.owner, [self.ref])
        captured = self.provider.capture(self.owner, self.ref, expected_entry=inventory[0])
        manifest = {**captured["manifest"], "grant_hash": binding["grant_hash"], "captured_at": "2026-10-23T00:00:00+00:00"}
        snapshot = self.cases.put_snapshot(self.owner, manifest, captured["raw"], captured["fixture"], event_key="manual-embedded-snapshot")
        case_set = self.cases.put_case_set(self.owner, [{"id": "private-holdout", "family_key": "private-family", "split": "qualification",
            "task": "Private holdout task", "snapshot_ids": [snapshot["snapshot_id"]], "answer": {"expected": "private answer"},
            "evaluator_refs": []}], 47, event_key="manual-embedded-case-set")
        request = {"case_set_id": case_set["case_set_id"], "case_id": "private-holdout",
            "pins": {**{key: binding[key] for key in ("experiment_id", "scope_hash", "grant_hash", "engine_config_hash", "policy_version")},
                     "case_set_hash": case_set["manifest_hash"]}, "baseline_context_hash": binding["baseline_context_hash"],
            "target_generations": binding["target_generations"], "engine_config": self.engine}
        before = self.count("improvement_case_exposures"), self.count("improvement_qualification"), self.count("improvement_events")
        for guard in (self.host.context_loader, self.host.live_guard):
            with self.assertRaisesRegex(PermissionError, "context_overlap"):
                guard(self.owner, request)
        self.assertEqual((self.count("improvement_case_exposures"), self.count("improvement_qualification"), self.count("improvement_events")), before)
        self.assertEqual(self.count("agent_calls"), 0)

    def test_replay_is_historical_metadata_without_recapture_or_grant_reactivation(self):
        result = self.capture()
        before = self.count("improvement_events")
        self.path.unlink()
        self.host.revoke(self.owner, "experiment-owner", expected_generation=1, event_key="snapshot-grant-revoke")
        with patch.object(BrainFileFixtures, "capture", side_effect=AssertionError("Replay cannot read raw source")):
            self.assertEqual(self.capture(), result)
        self.assertEqual(self.count("improvement_events"), before + 1)
        self.assertEqual(self.host.get(self.owner, "experiment-owner")["grant_status"], "revoked")
        with self.assertRaises(PermissionError):
            self.capture("host-snapshot-new-event")

    def test_closed_signature_cross_brain_and_unlisted_sources_are_denied(self):
        with self.assertRaises(TypeError):
            self.capture(raw=self.raw)
        with self.assertRaises(PermissionError):
            self.host.capture_snapshot(self.other, "experiment-owner", self.ref, event_key="other-brain-snapshot")
        with self.assertRaises(PermissionError):
            self.host.capture_snapshot(self.owner, "experiment-owner", "brain-file:reports/unlisted.txt", event_key="unlisted-source-event")
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_generic_provider_cannot_claim_the_host_snapshot_boundary(self):
        self.host.fixture_observer = lambda *args, **kwargs: []
        with self.assertRaises((TypeError, ValueError, PermissionError)):
            self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_provider_capture_output_must_match_the_exact_bound_inventory(self):
        original = BrainFileFixtures.capture
        changes = ({"raw": b"different unbound source"}, {"fixture": b"different unbound fixture"},
                   {"manifest": {"source_version": "unlisted-version"}}, {"manifest": {"schema": "other-schema"}})
        for index, change in enumerate(changes):
            def alter(provider, principal, source_ref, **kwargs):
                value = original(provider, principal, source_ref, **kwargs)
                if "manifest" in change:
                    value["manifest"].update(change["manifest"])
                else:
                    value.update(change)
                return value

            with patch.object(BrainFileFixtures, "capture", alter), self.assertRaises(IM.ConflictError):
                self.capture("mismatched-provider-capture-" + str(index))
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_invalid_public_snapshot_still_validates_before_schema_creation(self):
        cold = GoalStore(self.root / "cold.sqlite3")
        cases = CaseStore(IM.ImprovementStore(cold, self.blobs))
        self.assertEqual(IM._schema_version(cold), 0)
        with self.assertRaises(ValueError):
            cases.put_snapshot(self.owner, {}, self.raw, self.raw, event_key="invalid-cold-snapshot")
        self.assertEqual(IM._schema_version(cold), 0)

    def test_private_case_helper_requires_active_own_database_transaction(self):
        snapshot = self.cases.get_snapshot(self.owner, self.capture()["snapshot_id"])
        with closing(self.database._conn()) as connection:
            with self.assertRaisesRegex(ValueError, "transaction_required"):
                self.cases._put_snapshot_in(connection, self.owner, snapshot["manifest"], self.raw, self.raw, event_key="inactive-helper-test")
        foreign = GoalStore(self.root / "foreign.sqlite3")
        with foreign._Tx(foreign) as connection:
            with self.assertRaisesRegex(ValueError, "transaction_mismatch"):
                self.cases._put_snapshot_in(connection, self.owner, snapshot["manifest"], self.raw, self.raw, event_key="foreign-helper-test")

    def test_cold_host_snapshot_does_not_migrate_or_create_context(self):
        cold = GoalStore(self.root / "cold-host.sqlite3")
        assets = IM.ImprovementStore(cold, self.blobs)
        host = HostContextStore(assets, context_capture=self.context_capture, selected_engine=lambda: self.engine,
                                target_observer=self.targets, fixture_observer=self.provider)
        with self.assertRaises(PermissionError):
            host.capture_snapshot(self.owner, "experiment-owner", self.ref, event_key="cold-host-snapshot")
        self.assertEqual(IM._schema_version(cold), 0)

    def test_off_policy_prevents_new_snapshot(self):
        self.assets.set_policy(self.owner, "off", expected_version=0, event_key="snapshot-policy-off")
        with self.assertRaises(PermissionError):
            self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_target_database_drift_prevents_new_snapshot(self):
        with closing(self.database._conn()) as connection:
            connection.execute("UPDATE improvement_targets SET generation=generation+1 WHERE id=?", (self.target["target_id"],))
        with self.assertRaises(IM.ConflictError):
            self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def assert_blocked_without_snapshot(self):
        with self.assertRaises((ValueError, PermissionError, IM.ConflictError)):
            self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_current_target_file_is_revalidated(self):
        self.skill.write_bytes(b"owner changed target")
        self.assert_blocked_without_snapshot()

    def test_current_protected_context_is_revalidated(self):
        self.protected.write_bytes(b"owner changed context")
        self.assert_blocked_without_snapshot()

    def test_current_selected_configuration_is_revalidated(self):
        self.engine.update(reasoning="high")
        self.assert_blocked_without_snapshot()

    def test_current_selected_fixture_is_revalidated(self):
        self.path.write_bytes(b"owner changed selected fixture")
        self.assert_blocked_without_snapshot()

    def test_retired_target_blocks_snapshot(self):
        self.assets.retire(self.owner, self.target["target_id"], expected_generation=1, event_key="retire-for-snapshot")
        self.assert_blocked_without_snapshot()

    def test_actual_permission_change_blocks_snapshot(self):
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.addCleanup(self.path.chmod, mode)
        self.path.chmod(mode ^ stat.S_IWUSR)
        self.assert_blocked_without_snapshot()

    @unittest.skipUnless(sys.platform == "win32", "Native DACL mutation case is Windows-specific")
    def test_actual_native_dacl_control_change_blocks_snapshot(self):
        import ctypes
        from ctypes import wintypes
        library = ctypes.WinDLL("advapi32", use_last_error=True)
        get = library.GetFileSecurityW
        get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get.restype = wintypes.BOOL
        set_control = library.SetSecurityDescriptorControl
        set_control.argtypes = [wintypes.LPVOID, wintypes.WORD, wintypes.WORD]
        set_control.restype = wintypes.BOOL
        write = library.SetFileSecurityW
        write.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        write.restype = wintypes.BOOL
        needed = wintypes.DWORD()
        get(str(self.path), 7, None, 0, ctypes.byref(needed))
        descriptor = ctypes.create_string_buffer(needed.value)
        self.assertTrue(get(str(self.path), 7, descriptor, needed.value, ctypes.byref(needed)))
        # Protect the scratch file's existing DACL, retaining the same access
        # entries. Only this newly owned fixture is changed, never an ancestor.
        self.assertTrue(set_control(descriptor, 0x1000, 0x1000))
        self.assertTrue(write(str(self.path), 0x80000004, descriptor))
        self.assert_blocked_without_snapshot()

    def test_revocation_between_provider_capture_and_writer_lock_blocks_snapshot(self):
        original = BrainFileFixtures.capture

        def revoke(provider, principal, source_ref, **kwargs):
            captured = original(provider, principal, source_ref, **kwargs)
            self.host.revoke(self.owner, "experiment-owner", expected_generation=1, event_key="during-capture-revoke")
            return captured

        with patch.object(BrainFileFixtures, "capture", revoke):
            with self.assertRaises(PermissionError):
                self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)

    def test_source_edit_during_blob_publication_rolls_back_snapshot_and_events(self):
        original = self.blobs.write

        def edit(brain, raw):
            result = original(brain, raw)
            if raw == self.raw:
                self.path.write_bytes(b"source changed while encrypted blob was published")
            return result

        events = self.count("improvement_events")
        with patch.object(self.blobs, "write", side_effect=edit):
            with self.assertRaises(IM.ConflictError):
                self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)
        self.assertEqual(self.count("improvement_events"), events)

    def test_host_event_failure_rolls_back_inner_snapshot_and_inner_event(self):
        original = self.assets._event

        def fail(connection, principal, key, operation, payload, result):
            if operation == "trial_snapshot_capture":
                raise OSError("synthetic event storage unavailable")
            return original(connection, principal, key, operation, payload, result)

        events = self.count("improvement_events")
        with patch.object(self.assets, "_event", side_effect=fail):
            with self.assertRaises(OSError):
                self.capture()
        self.assertEqual(self.count("improvement_snapshots"), 0)
        self.assertEqual(self.count("improvement_events"), events)
        self.assertEqual(self.capture()["status"], "sufficient")

    def test_concurrent_same_event_commits_one_snapshot_and_identical_timestamp(self):
        barrier = threading.Barrier(2)
        original = BrainFileFixtures.capture

        def simultaneous(provider, principal, source_ref, **kwargs):
            value = original(provider, principal, source_ref, **kwargs)
            barrier.wait(timeout=5)
            return value

        with patch.object(BrainFileFixtures, "capture", simultaneous), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.capture(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count("improvement_snapshots"), 1)
        self.assertEqual(self.capture(), results[0])

    def test_revocation_waits_for_atomic_snapshot_commit_and_never_reenables_grant(self):
        published, attempted = threading.Event(), threading.Event()
        original = self.blobs.write

        def during_writer(brain, raw):
            value = original(brain, raw)
            if raw == self.raw and not published.is_set():
                published.set()
                self.assertTrue(attempted.wait(5))
            return value

        def revoke():
            self.assertTrue(published.wait(5))
            attempted.set()
            return self.host.revoke(self.owner, "experiment-owner", expected_generation=1, event_key="concurrent-revoke")

        with patch.object(self.blobs, "write", side_effect=during_writer), ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(revoke)
            snapshot = self.capture()
            revoked = future.result(timeout=5)
        self.assertEqual(revoked["grant_status"], "revoked")
        self.assertEqual(self.count("improvement_snapshots"), 1)
        self.assertEqual(self.capture(), snapshot)  # Historical replay never regrants.
        with closing(self.database._conn()) as connection:
            order = [row[0] for row in connection.execute("SELECT operation FROM improvement_events ORDER BY id")]
        self.assertLess(order.index("trial_snapshot_capture"), order.index("trial_context_revoke"))

    def test_same_event_cannot_capture_a_different_experiment_or_source(self):
        self.capture()
        with self.assertRaises(IM.ConflictError):
            self.host.capture_snapshot(self.owner, "experiment-owner", "brain-file:reports/different.txt", event_key="host-snapshot-create")
        self.assertEqual(self.count("improvement_snapshots"), 1)


if __name__ == "__main__":
    unittest.main()
