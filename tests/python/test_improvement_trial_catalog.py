"""Read-only real agentic inventories, never a claim of complete main context."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRATCH = Path(tempfile.mkdtemp(prefix="trial-catalog-"))
for key, child in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                   "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(SCRATCH / child)

import skill_router

if importlib.util.find_spec("improvement_trial_catalog"):
    import improvement_trial_catalog as C
else:
    C = None


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(C, "Strict read-only production catalog observation is missing")
        self.root = Path(tempfile.mkdtemp(dir=SCRATCH))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def capture(self, **kwargs):
        return C.capture_catalog(self.root, **kwargs)

    def test_absent_layout_is_recorded_without_creating_directories(self):
        value = self.capture()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(value["layout"], {"agents": "agents", "workflows": "workflows", "skills": "skills"})
        self.assertTrue(all(not item["present"] for item in value["directories"]))
        self.assertEqual(value["capabilities"], {"agents": [], "skills": [], "workflows": [], "loops": []})
        self.assertNotIn("complete", str(value))

    def test_canonical_disabled_and_mirror_precedence_match_production(self):
        self.write("skills/.disabled/alpha/SKILL.md", "---\nname: Disabled\ndescription: Canonical wins\n---\nprivate body")
        self.write(".claude/skills/alpha/SKILL.md", "---\nname: Wrong\n---\nFallback body")
        self.write(".agents/beta/SKILL.md", "---\nname_en: English\ndescription_en: English trigger\n---\nLegacy body")
        value = self.capture(language="en")
        expected = [{key: item[key] for key in ("slug", "name", "description", "group", "enabled")}
                    for item in skill_router.list_skills(self.root, "en")]
        self.assertEqual(value["capabilities"]["skills"], expected)
        self.assertEqual(len([item for item in value["files"] if item["present"]]), 3)
        self.assertEqual([item["relative"] for item in value["files"] if not item["present"]],
                         ["Javis/loop-state.json", "Javis/skill-usage.json"])
        self.assertNotIn("private body", str(value))
        self.assertNotIn("Fallback body", str(value))

    def test_new_membership_and_legacy_to_canonical_layout_are_observed(self):
        self.write("Javis/agents/old.md", "---\nname: Legacy\n---\nbody")
        old = self.capture()
        self.assertEqual(old["layout"]["agents"], "Javis/agents")
        self.write("agents/new.md", "---\nname: Canonical\n---\nbody")
        current = self.capture()
        self.assertEqual(current["layout"]["agents"], "agents")
        self.assertEqual([item["name"] for item in current["capabilities"]["agents"]], ["Canonical"])
        self.assertNotEqual(old, current)

    def test_metadata_and_body_hash_drift_are_both_retained_without_body_injection(self):
        path = self.write("agents/a.md", "---\nname: A\nrole: Helper\ntags: [One, Two]\n---\nSECRET EVALUATOR ANSWER")
        first = self.capture()
        self.assertEqual(first["capabilities"]["agents"][0]["tags"], ["One", "Two"])
        self.assertNotIn("SECRET EVALUATOR ANSWER", str(first))
        path.write_text("---\nname: A\nrole: Helper\ntags: [One, Two]\n---\nchanged body", encoding="utf-8")
        second = self.capture()
        self.assertEqual(first["capabilities"], second["capabilities"])
        self.assertNotEqual(first["files"], second["files"])

    def test_workflow_and_loop_projection_match_current_normalization(self):
        self.write("workflows/w.md", "---\nname: W\nstatus: on\nsteps:\n  - agent: a\n  - task: x\n---\nprivate")
        self.write("Javis/loops/daily.md", "---\nname: D\nenabled: false\ninterval_min: 1\nmode: invalid\ngoal: invalid\n---\nprivate")
        self.write("Javis/loop-state.json", '{"daily":{"auto_paused_reason":"owner paused"}}')
        value = self.capture()
        self.assertEqual(value["capabilities"]["workflows"][0], {
            "slug": "w", "name": "W", "status": "active", "description": "", "group": "Chung",
            "agents": ["a", None], "n_steps": 2})
        self.assertEqual(value["capabilities"]["loops"][0], {
            "slug": "daily", "name": "D", "enabled": False, "mode": "full", "interval_min": 5,
            "goal": "custom", "paused": True})

    def test_router_priority_uses_captured_sidecar_without_live_reads(self):
        for slug in ("a", "b"):
            self.write(f"skills/{slug}/SKILL.md", f"---\nname: {slug}\ndescription: trigger\n---\nbody")
        self.write("Javis/skill-usage.json", '{"b":{"pinned":true,"use_count":2,"last_used_at":10}}')
        value = self.capture()
        self.assertEqual([item["slug"] for item in value["router"]["visible"]], ["b", "a"])
        self.assertEqual(value["router"]["hidden_count"], 0)
        self.assertNotIn("read_usage", str(value))

    def test_malformed_or_duplicate_json_is_rejected_instead_of_empty_fallback(self):
        for text in ('{"a":1,"a":2}', '{bad', '{"a":NaN}', '[]'):
            with self.subTest(text=text):
                self.write("Javis/skill-usage.json", text)
                with self.assertRaises(ValueError):
                    self.capture()

    def test_credential_source_and_invalid_frontmatter_fail_without_secret_in_error(self):
        secret = "sk-" + "a" * 40
        path = self.write("agents/a.md", "---\nname: A\n---\napi_key=" + secret)
        with self.assertRaises(ValueError) as raised:
            self.capture()
        self.assertNotIn(secret, str(raised.exception))
        path.write_text("---\nname: [broken\n---\nbody", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.capture()

    def test_same_bytes_replacement_changes_file_identity(self):
        path = self.write("skills/a/SKILL.md", "---\nname: A\n---\nbody")
        first = self.capture()
        replacement = path.with_suffix(".new")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        self.assertNotEqual(first["files"], self.capture()["files"])

    def test_mid_scan_membership_change_cannot_return_a_mixed_catalog(self):
        self.write("agents/a.md", "---\nname: A\n---\nbody")
        original = C._read
        changed = []

        def mutate(source):
            value = original(source)
            if not changed:
                changed.append(True)
                self.write("agents/b.md", "---\nname: B\n---\nbody")
            return value

        with patch.object(C, "_read", side_effect=mutate), self.assertRaisesRegex(ValueError, "changed"):
            self.capture()

    def test_resource_limits_reject_instead_of_truncating(self):
        self.write("agents/a.md", "---\nname: A\n---\nbody")
        self.write("agents/b.md", "---\nname: B\n---\nbody")
        with patch.object(C, "MAX_CATALOG_FILES", 1), self.assertRaisesRegex(ValueError, "large"):
            self.capture()

    def test_hard_linked_catalog_file_is_not_an_authorized_brain_source(self):
        outside = self.root.parent / (self.root.name + "-private.md")
        outside.write_text("---\nname: Private\n---\nprivate", encoding="utf-8")
        (self.root / "agents").mkdir()
        os.link(outside, self.root / "agents/a.md")
        with self.assertRaisesRegex(ValueError, "linked"):
            self.capture()

    def test_yaml_aliases_are_rejected_before_expanding_projection(self):
        self.write("agents/a.md", "---\nname: A\nx: &base [a, b, c]\nskills: [*base, *base]\n---\nbody")
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            self.capture()

    def test_ordinary_yaml_dates_match_production_loop_normalization(self):
        import self_improve

        self.write("Javis/loops/daily.md", "---\nname: Daily\nenabled: true\nupdated: 2026-10-22\ncreated: 2026-10-22T12:00:00Z\n---\nprivate")
        value = self.capture()
        normalized = self_improve.LoopFeature._norm_loop(None, {"name": "Daily", "enabled": True,
            "updated": __import__("datetime").date(2026, 10, 22)}, "private", "daily")
        expected = {key: normalized[key] for key in ("slug", "name", "enabled", "mode", "interval_min", "goal")}
        expected["paused"] = False
        self.assertEqual(value["capabilities"]["loops"][0], expected)
        record = next(item for item in value["files"] if item["relative"] == "Javis/loops/daily.md")
        self.assertEqual(record["projection"]["updated"], "2026-10-22")
        self.assertEqual(record["projection"]["created"], "2026-10-22T12:00:00+00:00")

    def test_native_filename_case_lookup_matches_real_production_discovery(self):
        self.write("agents/helper.MD", "---\nname: Upper suffix\n---\nprivate")
        self.write("skills/demo/skill.md", "---\nname: Lower file\ndescription: Trigger\n---\nprivate")
        value = self.capture()
        self.assertEqual([item["slug"] for item in value["capabilities"]["agents"]],
                         [path.stem for path in sorted((self.root / "agents").glob("*.md"))])
        self.assertEqual([item["slug"] for item in value["capabilities"]["skills"]],
                         [item["slug"] for item in skill_router.list_skills(self.root)])
        if (self.root / "skills/demo/SKILL.md").is_file():
            self.assertTrue(any(item["relative"] == "skills/demo/skill.md" for item in value["files"]))


if __name__ == "__main__":
    unittest.main()
