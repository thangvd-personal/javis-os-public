"""Canonical source capture is read-only, byte-exact and conservative about ownership."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="imp-assets-"))
os.environ["JAVIS_STATE_DIR"] = str(TEST_ROOT / "state")
os.environ["BRAINS_DIR"] = str(TEST_ROOT / "brains")
os.environ["OBSIDIAN_VAULT_PATH"] = str(TEST_ROOT / "vault")

if importlib.util.find_spec("improvement_assets"):
    import improvement_assets as IA
else:
    IA = None


class ImprovementAssetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IA, "Canonical guarded capture is not implemented")
        self.root = Path(tempfile.mkdtemp(prefix="brain-", dir=TEST_ROOT))
        self.pack_state = TEST_ROOT / "packs-state"
        self.path = self.root / "skills" / "report" / "SKILL.md"
        self.path.parent.mkdir(parents=True)
        self.raw = b"---\r\nname: report\r\ndescription: Make a report\r\n---\r\nOwner content.\r\n"
        self.path.write_bytes(self.raw)

    def capture(self, kind="skill", slug="report"):
        self.assertIn("agent_identity", inspect.signature(IA.capture_source).parameters)
        return IA.capture_source(self.root, kind, slug, pack_state_dir=self.pack_state,
                                 agent_identity=lambda: "host-agent-editor")

    def test_capture_does_not_normalize_or_write_owner_content(self):
        source, raw = self.capture()
        self.assertEqual(raw, self.raw)
        self.assertEqual(source.source_hash, hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(source.protected_hash, source.source_hash)
        self.assertEqual(source.ownership, "user_protected")
        self.assertEqual(source.canonical_ref, "skills/report/SKILL.md")
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse((self.root / ".javis").exists())

    def test_canonical_disabled_shadows_enabled_legacy_mirror(self):
        disabled = self.root / "skills" / ".disabled" / "report"
        disabled.parent.mkdir(parents=True)
        self.path.parent.rename(disabled)
        mirror = self.root / ".claude" / "skills" / "report" / "SKILL.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_bytes(self.raw)
        with self.assertRaises(IA.SourceError):
            self.capture()

    def test_legacy_only_skill_is_not_treated_as_user_canonical(self):
        self.path.unlink()
        mirror = self.root / ".claude" / "skills" / "report" / "SKILL.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_bytes(self.raw)
        with self.assertRaises(IA.SourceError):
            self.capture()

    def test_invalid_slugs_and_windows_path_forms_are_rejected(self):
        for slug in ("../report", "report/file", "report\\file", "C:report", "\\report", "//host/share", "CON", "Report"):
            with self.subTest(slug=slug), self.assertRaises(IA.SourceError):
                self.capture(slug=slug)

    def test_system_and_locked_sources_are_blocked(self):
        with patch("system_sync.is_system_skill", return_value=True):
            with self.assertRaises(IA.SourceError):
                self.capture()
        self.path.write_bytes(self.raw.replace(b"name: report", b"name: report\r\nlearn_lock: true"))
        with self.assertRaises(IA.SourceError):
            self.capture()

    def test_pack_managed_source_is_blocked_even_after_owner_edit(self):
        self.pack_state.mkdir(parents=True, exist_ok=True)
        state_file = self.pack_state / f"pack-{self.root.name}.json"
        state_file.write_text(json.dumps({"brain": str(self.root), "items": {"skills/report": {
            "duong": str(self.path.parent), "hash": "previous-version", "loai": "skills", "slug": "report"}}}))
        with self.assertRaises(IA.SourceError):
            self.capture()
        state_file.unlink()

    def test_same_size_timestamp_preserved_edit_has_new_hash(self):
        first, _ = self.capture()
        original_stat = self.path.stat()
        self.path.write_bytes(self.raw.replace(b"Owner", b"Other"))
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        second, _ = self.capture()
        self.assertNotEqual(first.source_hash, second.source_hash)

    def test_oversized_and_invalid_utf8_sources_are_not_truncated(self):
        self.path.write_bytes(b"x" * (IA.MAX_ASSET_BYTES + 1))
        with self.assertRaises(IA.SourceError):
            self.capture()
        self.path.write_bytes(b"\xffnot-utf8")
        with self.assertRaises(IA.SourceError):
            self.capture()

    def test_agent_capture_uses_only_canonical_file(self):
        agent = self.root / "agents" / "editor.md"
        agent.parent.mkdir()
        agent.write_bytes(b"---\nname: Editor\n---\nKeep all owner rules.\n")
        source, content = self.capture("agent", "editor")
        self.assertEqual(source.kind, "agent")
        self.assertEqual(source.agent_key, "host-agent-editor")
        self.assertEqual(source.canonical_ref, "agents/editor.md")
        self.assertEqual(content, agent.read_bytes())

    def test_linked_source_directory_is_blocked(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_bytes(self.raw)
        link = self.root / "skills" / "linked"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, "A junction is required to verify Windows reparse protection")
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(IA.SourceError):
            self.capture(slug="linked")


if __name__ == "__main__":
    unittest.main()
