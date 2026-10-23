"""Actual scratch brain files; no connector, model, shell or production credential access."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from resonance_store import Principal
import improvement_store as IM

if importlib.util.find_spec("improvement_trial_file_sources"):
    import improvement_trial_file_sources as files
else:
    files = None


class BrainFileFixtureTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(files, "Concrete owner brain file source provider is not implemented")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "brain"
        self.root.mkdir()
        (self.root / "reports").mkdir()
        self.path = self.root / "reports" / "input.txt"
        self.raw = "Owner selected UTF-8 input\r\nDữ liệu nguyên vẹn.\r\n".encode()
        self.path.write_bytes(self.raw)
        self.ref = "brain-file:reports/input.txt"
        self.owner = Principal("owner", "owner", str(self.root))
        self.metadata = {"timezone": "Asia/Ho_Chi_Minh", "data_period": "2026-09",
                         "cutoff": "2026-09-30T23:59:59+07:00", "schema": "text/plain",
                         "freshness_policy": "require_current_version"}
        self.provider = files.BrainFileFixtures({self.ref: self.metadata})

    def observe(self, *, provider=None, owner=None, refs=None, frozen=None):
        return (provider or self.provider)(owner or self.owner, [self.ref] if refs is None else refs,
                                           frozen_inventory=frozen)

    def test_inventory_is_only_metadata_and_current_content_is_lossless(self):
        inventory = self.observe()
        self.assertEqual(len(inventory), 1)
        entry = inventory[0]
        digest = hashlib.sha256(self.raw).hexdigest()
        self.assertEqual(entry["source_version"], digest)
        self.assertEqual((entry["source_hash"], entry["fixture_hash"]), (digest, digest))
        self.assertEqual(entry["redaction"], "none")
        self.assertIs(entry["sufficient_source"], True)
        self.assertEqual(len(entry["identity"]), 64)
        self.assertEqual(len(entry["permission_id"]), 64)
        self.assertIs(type(entry["permission_version"]), int)
        self.assertGreater(entry["permission_version"], 0)
        self.assertNotIn(self.raw.decode(), str(inventory))
        capture = self.provider.capture(self.owner, self.ref, expected_entry=entry)
        self.assertEqual(set(capture), {"raw", "fixture", "manifest"})
        self.assertEqual(capture["raw"], self.raw)
        self.assertEqual(capture["fixture"], self.raw)
        self.assertNotIn("grant_hash", capture["manifest"])
        self.assertNotIn("captured_at", capture["manifest"])
        self.assertEqual(capture["manifest"]["freshness"], "current")
        self.assertEqual(capture["manifest"]["source_version"], digest)

    def test_frozen_revalidation_needs_no_initial_metadata_and_never_resnapshots(self):
        inventory = self.observe()
        fresh_provider = files.BrainFileFixtures()
        self.assertEqual(self.observe(provider=fresh_provider, frozen=inventory), inventory)
        self.assertEqual(fresh_provider.capture(self.owner, self.ref, expected_entry=inventory[0])["raw"], self.raw)
        self.path.write_bytes(b"owner edited current input")
        with self.assertRaises(IM.ConflictError):
            self.observe(provider=fresh_provider, frozen=inventory)
        with self.assertRaises(IM.ConflictError):
            fresh_provider.capture(self.owner, self.ref, expected_entry=inventory[0])

    def test_same_content_file_replacement_and_root_replacement_invalidate_identity(self):
        frozen = self.observe()
        alternate = self.path.with_name("replacement.txt")
        alternate.write_bytes(self.raw)
        os.replace(alternate, self.path)
        with self.assertRaises(IM.ConflictError):
            self.observe(frozen=frozen)
        frozen = self.observe()
        archived = self.root.with_name("old-brain")
        self.root.rename(archived)
        self.root.mkdir()
        (self.root / "reports").mkdir()
        self.path.write_bytes(self.raw)
        with self.assertRaises(IM.ConflictError):
            self.observe(frozen=frozen)

    def test_owner_principal_and_absolute_canonical_brain_root_are_required(self):
        invalid = (Principal("agent", "solver", str(self.root)), Principal("owner", "", str(self.root)),
                   Principal("owner", "owner", "relative/brain"), Principal("owner", "owner", str(self.path)),
                   Principal("owner", "owner", str(self.root / "missing")),
                   SimpleNamespace(kind="owner", id="owner", brain_id=str(self.root), by="owner:owner"))
        for principal in invalid:
            with self.subTest(principal=principal), self.assertRaises((PermissionError, ValueError)):
                self.observe(owner=principal)

    def test_foreign_brain_and_different_owner_cannot_reuse_a_frozen_grant(self):
        frozen = self.observe()
        other = self.root.with_name("other-brain")
        (other / "reports").mkdir(parents=True)
        (other / "reports" / "input.txt").write_bytes(self.raw)
        for owner in (Principal("owner", "other-owner", str(self.root)), Principal("owner", "owner", str(other))):
            with self.assertRaises(IM.ConflictError):
                self.observe(owner=owner, frozen=frozen)

    def test_no_root_hash_identity_or_permission_can_be_supplied_as_initial_metadata(self):
        for key in ("root", "source_hash", "identity", "permission_id", "permission_version", "grant_hash"):
            with self.assertRaises(ValueError):
                files.BrainFileFixtures({self.ref: {**self.metadata, key: "client-assertion"}})

    def test_snapshot_version_is_explicitly_unsupported_and_metadata_must_be_aware(self):
        for key, value in (("freshness_policy", "snapshot_version"), ("timezone", "invalid/timezone"),
                           ("cutoff", "2026-09-30"), ("schema", "")):
            with self.assertRaises(ValueError):
                files.BrainFileFixtures({self.ref: {**self.metadata, key: value}})

    def test_unsafe_hidden_and_secret_paths_are_denied_before_file_read(self):
        paths = ("../outside.txt", "/outside.txt", "reports\\input.txt", "reports/../input.txt", "C:/input.txt",
                 "reports//input.txt", ".git/config", ".claude/settings.json", ".agents/SKILL.md", "state/settings.json",
                 ".env", "reports/.env.local", "credentials.json", "settings.json", "private.pem", "auth/token.json",
                 "reports/CON.txt", "reports/input.txt:stream")
        for path in paths:
            ref = "brain-file:" + path
            with self.subTest(path=path), self.assertRaises((PermissionError, ValueError)):
                provider = files.BrainFileFixtures({ref: self.metadata})
                provider(self.owner, [ref])
        for ref in ("file:reports/input.txt", self.ref + "?token=synthetic", "brain-file:"):
            with self.assertRaises((PermissionError, ValueError)):
                files.BrainFileFixtures({ref: self.metadata})(self.owner, [ref])

    def test_missing_tombstoned_parent_and_unreadable_file_invalidate_frozen_inventory(self):
        frozen = self.observe()
        parent = self.path.parent
        parent.rename(parent.with_name("tombstone"))
        with self.assertRaises((PermissionError, ValueError)):
            self.observe(frozen=frozen)
        parent.with_name("tombstone").rename(parent)
        with patch.object(files, "_read_stable", side_effect=PermissionError("synthetic denial")):
            with self.assertRaises(PermissionError):
                self.observe(frozen=frozen)

    def test_mode_change_and_acl_projection_change_revoke_the_frozen_permission(self):
        frozen = self.observe()
        mode = self.path.stat().st_mode
        try:
            self.path.chmod(mode & ~0o222)
            with self.assertRaises(IM.ConflictError):
                self.observe(frozen=frozen)
        finally:
            self.path.chmod(mode)
        frozen = self.observe()
        original = files._acl_hash

        def acl(path):
            return "e" * 64 if path == self.path else original(path)

        with patch.object(files, "_acl_hash", side_effect=acl):
            with self.assertRaises(IM.ConflictError):
                self.observe(frozen=frozen)
        with patch.object(files, "_acl_hash", side_effect=OSError("synthetic ACL read failure")):
            with self.assertRaises(PermissionError):
                self.observe(frozen=frozen)

    def test_link_or_junction_on_root_parent_or_file_is_rejected(self):
        original = files.Path.lstat

        class Reparse:
            st_file_attributes = 0x400

            def __init__(self, value):
                self.value = value

            def __getattr__(self, name):
                return getattr(self.value, name)

        for target in (self.root, self.path.parent, self.path):
            def lstat(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                return Reparse(info) if path == target else info

            with patch.object(files.Path, "lstat", lstat):
                with self.assertRaises((PermissionError, ValueError)):
                    self.observe()

    def test_hard_linked_input_is_denied_even_with_a_safe_relative_name(self):
        linked = self.path.with_name("linked.txt")
        try:
            os.link(self.path, linked)
        except OSError as error:
            self.skipTest("Scratch filesystem cannot create a hard link: " + type(error).__name__)
        with self.assertRaises(PermissionError):
            self.observe()

    def test_edit_after_bounded_read_cannot_return_stale_inventory_or_capture(self):
        frozen = self.observe()
        original = files._read_stable

        def edited(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            self.path.write_bytes(b"changed after read completed")
            return result

        with patch.object(files, "_read_stable", side_effect=edited):
            with self.assertRaises(IM.ConflictError):
                self.observe(frozen=frozen)
        self.path.write_bytes(self.raw)
        frozen = self.observe()
        with patch.object(files, "_read_stable", side_effect=edited):
            with self.assertRaises(IM.ConflictError):
                self.provider.capture(self.owner, self.ref, expected_entry=frozen[0])

    def test_later_capture_cannot_hide_an_earlier_members_current_content_or_acl_drift(self):
        other_ref = "brain-file:reports/second.txt"
        other_path = self.path.with_name("second.txt")
        other_path.write_bytes(b"second authorized source")
        provider = files.BrainFileFixtures({self.ref: self.metadata, other_ref: self.metadata})
        refs = [self.ref, other_ref]
        frozen = provider(self.owner, refs)
        original_capture, original_acl = provider._capture, files._acl_hash
        revoked = False

        def acl(path):
            return "e" * 64 if revoked and path == self.path else original_acl(path)

        for drift in ("current_content", "acl"):
            self.path.write_bytes(self.raw)
            revoked = False
            frozen = provider(self.owner, refs)

            def capture(principal, ref, metadata):
                nonlocal revoked
                result = original_capture(principal, ref, metadata)
                if ref == other_ref:
                    if drift == "current_content":
                        self.path.write_bytes(b"earlier member changed during later capture")
                    else:
                        revoked = True
                return result

            with self.subTest(drift=drift), patch.object(provider, "_capture", side_effect=capture), \
                 patch.object(files, "_acl_hash", side_effect=acl):
                with self.assertRaises(IM.ConflictError):
                    provider(self.owner, refs, frozen_inventory=frozen)

    def test_root_replacement_after_later_capture_cannot_return_a_frozen_inventory(self):
        other_ref = "brain-file:reports/second.txt"
        other_path = self.path.with_name("second.txt")
        other_path.write_bytes(b"second authorized source")
        provider = files.BrainFileFixtures({self.ref: self.metadata, other_ref: self.metadata})
        refs = [self.ref, other_ref]
        frozen = provider(self.owner, refs)
        original_capture = provider._capture
        replaced = False

        def capture(principal, ref, metadata):
            nonlocal replaced
            result = original_capture(principal, ref, metadata)
            if ref == other_ref and not replaced:
                replaced = True
                self.root.rename(self.root.with_name("replaced-brain"))
                (self.root / "reports").mkdir(parents=True)
                self.path.write_bytes(self.raw)
                other_path.write_bytes(b"second authorized source")
            return result

        with patch.object(provider, "_capture", side_effect=capture):
            with self.assertRaises(IM.ConflictError):
                provider(self.owner, refs, frozen_inventory=frozen)
        self.assertTrue(replaced)

    def test_credentials_binary_invalid_utf8_and_oversized_input_are_rejected_without_echo(self):
        credential = "api_key=sk-" + "x" * 40
        for content in (credential.encode(), b"\xff\xfe", b"NUL\x00binary", b"x" * (files.MAX_ASSET_BYTES + 1)):
            self.path.write_bytes(content)
            with self.assertRaises((PermissionError, ValueError)) as error:
                self.observe()
            self.assertNotIn(credential, str(error.exception))

    def test_capture_rejects_any_changed_expected_entry_and_does_not_return_unpinned_bytes(self):
        entry = self.observe()[0]
        for key, value in (("source_ref", "brain-file:other.txt"), ("source_hash", "f" * 64),
                           ("identity", "e" * 64), ("permission_id", "a" * 64), ("permission_version", entry["permission_version"] + 1),
                           ("schema", "other-schema"), ("redaction", "changed"), ("freshness_policy", "snapshot_version")):
            expected = {**entry, key: value}
            with self.subTest(field=key), self.assertRaises((IM.ConflictError, ValueError, PermissionError)):
                self.provider.capture(self.owner, self.ref, expected_entry=expected)

    def test_initial_metadata_is_detached_and_frozen_scope_must_match_refs_exactly(self):
        self.metadata["schema"] = "caller-mutated"
        self.assertEqual(self.observe()[0]["schema"], "text/plain")
        frozen = self.observe()
        for refs, inventory in (([], frozen), ([self.ref, self.ref], frozen), ([self.ref], []),
                                (["brain-file:other.txt"], frozen), ([self.ref], [*frozen, {**frozen[0], "identity": "f" * 64}])):
            with self.assertRaises((ValueError, PermissionError)):
                self.observe(refs=refs, frozen=inventory)
        with self.assertRaises(PermissionError):
            self.observe(provider=files.BrainFileFixtures())

    def test_observation_and_capture_open_files_read_only(self):
        original = files.os.open
        forbidden = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

        def opening(path, flags, *args, **kwargs):
            self.assertEqual(flags & forbidden, 0)
            return original(path, flags, *args, **kwargs)

        before = self.path.read_bytes()
        with patch.object(files.os, "open", side_effect=opening):
            entry = self.observe()[0]
            self.provider.capture(self.owner, self.ref, expected_entry=entry)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
