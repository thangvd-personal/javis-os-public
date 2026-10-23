"""Host context plans capture exact protected bytes without healing or writing sources."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="improvement-trial-context-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(TEST_ROOT / name)

from improvement_store import _hash  # noqa: E402

if importlib.util.find_spec("improvement_trial_context"):
    import improvement_trial_context as IC
else:
    IC = None


def sha(content):
    return hashlib.sha256(content).hexdigest()


class TrialContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IC, "Read-only host context capture is not implemented")
        self.root = Path(tempfile.mkdtemp(prefix="brain-", dir=TEST_ROOT))
        self.owner = self.root / "CLAUDE.md"
        self.owner_raw = "Keep the owner's rule exactly.\r\nKhông sửa luật chủ.\r\n".encode("utf-8")
        self.owner.write_bytes(self.owner_raw)
        self.memory = self.root / "memory" / "MEMORY.md"
        self.memory.parent.mkdir()
        self.memory.write_bytes(b"Frozen shared memory.\n")
        self.surface = {"kind": "agent_dm", "agent_key": "agent-1", "channel": "web", "language": "vi"}

    def source(self, ref="owner-rules", relative="CLAUDE.md", **changes):
        return IC.ContextSource(ref, changes.pop("root", self.root), relative, **changes)

    def capture(self, sources=None, **changes):
        return IC.capture_context(sources or [self.source()], surface=changes.pop("surface", self.surface),
                                  complete_plan=changes.pop("complete_plan", True), **changes)

    def test_capture_preserves_full_utf8_crlf_and_returns_closed_metadata(self):
        sources = [self.source(), self.source("shared-memory", "memory/MEMORY.md")]
        result = self.capture(sources)
        self.assertEqual(set(result), {"version", "surface", "plan_hash", "sources", "baseline_context"})
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["surface"], self.surface)
        context = result["baseline_context"]
        self.assertEqual(context["coverage"], "complete")
        self.assertEqual(context["hash"], sha(context["content"].encode("utf-8")))
        self.assertIn(self.owner_raw, context["content"].encode("utf-8"))
        self.assertLess(context["content"].index("owner-rules"), context["content"].index("shared-memory"))
        first = result["sources"][0]
        self.assertEqual(set(first), {"ref", "root", "relative", "required", "present", "identity", "hash"})
        self.assertEqual(first["hash"], sha(self.owner_raw))
        self.assertEqual(first["root"], str(self.root.resolve()))
        self.assertTrue(first["required"])
        self.assertTrue(first["present"])
        self.assertIsNotNone(first["identity"])
        self.assertNotIn("Keep the owner's rule", str(result["sources"]))
        self.assertEqual(self.owner.read_bytes(), self.owner_raw)
        self.assertFalse((self.root / ".javis").exists())

    def test_same_plan_and_bytes_replay_deterministically_without_mutating_plan(self):
        sources = [self.source(), self.source("shared-memory", "memory/MEMORY.md")]
        surface = copy.deepcopy(self.surface)
        first = self.capture(sources, surface=surface)
        second = self.capture(sources, surface=surface)
        self.assertEqual(first, second)
        self.assertEqual(surface, self.surface)
        with self.assertRaises((AttributeError, TypeError)):
            sources[0].relative = "memory/MEMORY.md"

    def test_incomplete_host_plan_never_infers_complete_from_present_files(self):
        result = self.capture(complete_plan=False)
        self.assertEqual(result["baseline_context"]["coverage"], "unknown")
        self.assertNotEqual(result["plan_hash"], self.capture()["plan_hash"])

    def test_absent_optional_source_is_explicit_and_required_absence_is_insufficient(self):
        missing = self.source("optional-note", "memory/OPTIONAL.md", required=False)
        result = self.capture([self.source(), missing])
        self.assertEqual(result["baseline_context"]["coverage"], "complete")
        record = result["sources"][1]
        self.assertFalse(record["present"])
        self.assertIsNone(record["identity"])
        self.assertIsNone(record["hash"])
        with self.assertRaisesRegex(ValueError, "insufficient_source"):
            self.capture([self.source("missing-rule", "MISSING.md")])

    def test_order_surface_and_required_flag_are_part_of_the_host_plan_pin(self):
        sources = [self.source(), self.source("shared-memory", "memory/MEMORY.md")]
        original = self.capture(sources)
        for changed in (self.capture(list(reversed(sources))), self.capture(sources, surface={**self.surface, "language": "en"}),
                        self.capture([self.source(required=False), sources[1]])):
            self.assertNotEqual(changed["plan_hash"], original["plan_hash"])
        self.assertNotEqual(self.capture(list(reversed(sources)))["baseline_context"]["hash"], original["baseline_context"]["hash"])

    def test_excluded_mutable_target_refs_are_never_duplicated_into_context(self):
        with self.assertRaises(ValueError):
            self.capture(excluded_refs=["owner-rules"])
        first = self.capture(excluded_refs=["mutable-skill"])
        second = self.capture()
        self.assertNotEqual(first["plan_hash"], second["plan_hash"])

    def test_excluded_canonical_path_cannot_be_hidden_behind_a_source_ref_alias(self):
        for excluded in ("CLAUDE.md", "claude.MD"):
            with self.subTest(excluded=excluded), self.assertRaisesRegex(ValueError, "excluded_context_source"):
                self.capture([self.source("innocent-alias")], excluded_refs=[excluded])

    def test_duplicate_refs_and_portable_path_aliases_are_rejected(self):
        for sources in ([self.source(), self.source("owner-rules", "memory/MEMORY.md")],
                        [self.source(), self.source("other-ref", "CLAUDE.md")],
                        [self.source(), self.source("other-ref", "claude.md")]):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                self.capture(sources)

    def test_same_size_timestamp_preserved_edit_changes_the_source_hash(self):
        first = self.capture()
        stamp = self.owner.stat()
        self.owner.write_bytes(self.owner_raw.replace(b"Keep", b"KEEP"))
        os.utime(self.owner, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        second = self.capture()
        self.assertEqual(first["plan_hash"], second["plan_hash"])
        self.assertNotEqual(first["sources"][0]["hash"], second["sources"][0]["hash"])
        self.assertNotEqual(first["baseline_context"]["hash"], second["baseline_context"]["hash"])

    def test_replacing_source_with_same_bytes_changes_identity(self):
        first = self.capture()
        replacement = self.root / "replacement.md"
        replacement.write_bytes(self.owner_raw)
        replacement.replace(self.owner)
        second = self.capture()
        self.assertEqual(first["sources"][0]["hash"], second["sources"][0]["hash"])
        self.assertNotEqual(first["sources"][0]["identity"], second["sources"][0]["identity"])

    def test_portable_traversal_devices_absolute_and_ambiguous_paths_are_rejected(self):
        for relative in ("../CLAUDE.md", "/CLAUDE.md", "C:CLAUDE.md", "C:/CLAUDE.md", "//host/share", "memory\\MEMORY.md",
                         "memory/./MEMORY.md", "memory//MEMORY.md", "memory/CON.txt", "memory/trailing. "):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.capture([self.source(relative=relative)])

    def test_root_must_be_an_existing_absolute_directory(self):
        for root in (Path("relative-brain"), self.root / "missing", self.owner):
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.capture([self.source(root=root, required=False)])

    def test_linked_root_or_parent_is_rejected(self):
        outside = Path(tempfile.mkdtemp(prefix="outside-", dir=TEST_ROOT))
        (outside / "CLAUDE.md").write_bytes(self.owner_raw)
        linked = self.root / "linked"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, "Windows junction test must execute")
        else:
            linked.symlink_to(outside, target_is_directory=True)
        for source in (self.source(root=linked), self.source(relative="linked/CLAUDE.md")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.capture([source])

    def test_invalid_utf8_oversize_and_credential_data_fail_without_truncation(self):
        for raw in (b"\xff", b"x" * (1024 * 1024 + 1), b"Authorization: Bearer syntheticcredential123456789"):
            self.owner.write_bytes(raw)
            with self.subTest(size=len(raw)), self.assertRaisesRegex(ValueError, "insufficient_source"):
                self.capture()

    def test_total_rendered_bytes_include_headers_and_all_sources(self):
        self.owner.write_bytes(b"a" * (600 * 1024))
        self.memory.write_bytes(b"b" * (600 * 1024))
        with self.assertRaisesRegex(ValueError, "insufficient_source"):
            self.capture([self.source(), self.source("shared-memory", "memory/MEMORY.md")])
        self.owner.write_bytes(b"a" * (1024 * 1024))
        with self.assertRaisesRegex(ValueError, "insufficient_source"):
            self.capture()

    def test_malformed_plan_flags_unknown_fields_and_empty_sources_are_rejected(self):
        invalid = [[], {}, [dict(ref="owner-rules", root=str(self.root), relative="CLAUDE.md", required=True, coverage="complete")],
                   [self.source(required="true")], [self.source(ref="owner\nforged-header")]]
        for sources in invalid:
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                IC.capture_context(sources, surface=self.surface, complete_plan=True)
        for flag in (None, 1, "true"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.capture(complete_plan=flag)
        for surface in ({}, [], {"key": float("nan")}, {"api_key": "credential"}):
            with self.subTest(surface=surface), self.assertRaises(ValueError):
                self.capture(surface=surface)

    def test_capture_is_not_permitted_to_write_or_heal_sources(self):
        real_open = IC.os.open

        def read_only(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return real_open(path, flags, *args, **kwargs)

        with patch.object(IC.os, "open", side_effect=read_only):
            result = self.capture()
        self.assertEqual(result["baseline_context"]["coverage"], "complete")
        self.assertEqual(self.owner.read_bytes(), self.owner_raw)

    def test_unknown_dataclass_fields_are_not_silently_ignored(self):
        @dataclass(frozen=True)
        class ClaimedCompleteSource(IC.ContextSource):
            coverage: str = "complete"

        with self.assertRaises(ValueError):
            self.capture([ClaimedCompleteSource("owner-rules", self.root, "CLAUDE.md")])

    def test_broken_junction_or_symlink_is_not_an_absent_optional_source(self):
        linked = self.root / "broken"
        missing_target = TEST_ROOT / "missing-junction-target"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(missing_target)], capture_output=True)
            self.assertEqual(result.returncode, 0, "Broken junction protection must be exercised")
        else:
            linked.symlink_to(missing_target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "insufficient_source"):
            self.capture([self.source("optional", "broken/CLAUDE.md", required=False)])

    def test_earlier_source_changes_while_another_source_is_read_invalidate_capture(self):
        real_open, edited = IC.os.open, False

        def concurrent_edit(path, *args, **kwargs):
            nonlocal edited
            if Path(path) == self.memory and not edited:
                self.owner.write_bytes(self.owner_raw.replace(b"Keep", b"KEEP"))
                edited = True
            return real_open(path, *args, **kwargs)

        with patch.object(IC.os, "open", side_effect=concurrent_edit), self.assertRaisesRegex(ValueError, "insufficient_source"):
            self.capture([self.source(), self.source("shared-memory", "memory/MEMORY.md")])

    def test_optional_file_disappearing_after_stat_is_an_unstable_read(self):
        real_open = IC.os.open

        def remove_before_open(path, *args, **kwargs):
            if Path(path) == self.owner:
                self.owner.unlink()
            return real_open(path, *args, **kwargs)

        with patch.object(IC.os, "open", side_effect=remove_before_open), self.assertRaisesRegex(ValueError, "insufficient_source"):
            self.capture([self.source(required=False)])

    def test_hard_link_aliases_cannot_duplicate_the_same_owner_rules(self):
        linked = self.root / "alias.md"
        os.link(self.owner, linked)
        with self.assertRaises(ValueError):
            self.capture([self.source(), self.source("alias-ref", "alias.md")])


if __name__ == "__main__":
    unittest.main()
