"""Typed source pins retain private inputs separately from rendered trial context."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix="trial-semantics-"))
for key, child in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                   "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(SCRATCH / child)

from improvement_store import _hash

if importlib.util.find_spec("improvement_trial_semantics"):
    import improvement_trial_semantics as S
else:
    S = None


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(S, "Typed host context source records are missing")
        self.surface = {"kind": "synthetic-dashboard", "session_id": "session-1", "language": "en"}
        self.raw = " Full owner rule\r\n" * 300
        self.owner = {"kind": "file", "ref": "owner-profile", "locator": {"root": str(SCRATCH), "relative": "profile.md"},
            "role": "protected", "required": True, "present": True, "identity": _hash("owner-incarnation"),
            "hash": hashlib.sha256(self.raw.encode()).hexdigest(), "projection": self.raw, "projection_hash": _hash(self.raw)}
        self.sql = {"kind": "sql", "ref": "session-membership", "locator": {"database": str(SCRATCH / "sessions.sqlite3"), "scope": "session-1"},
            "role": "authority", "required": True, "present": True, "identity": _hash("database-incarnation"),
            "hash": _hash({"files": [], "links": []}), "projection": {"files": [], "links": []}, "projection_hash": _hash({"files": [], "links": []})}

    def capture(self, sources=None, **changes):
        return S.capture_semantics(surface=self.surface, sources=sources or [self.owner, self.sql],
            content=changes.pop("content", self.raw + "Rendered source list."), complete_plan=changes.pop("complete_plan", True), **changes)

    def test_typed_sources_and_exact_protected_bytes_are_preserved(self):
        value = self.capture()
        self.assertEqual(value["version"], 2)
        self.assertEqual(value["sources"], [self.owner, self.sql])
        self.assertIn(self.raw, value["baseline_context"]["content"])
        self.assertEqual(S.validate_capture(value), value)
        self.assertEqual(value["baseline_context"]["coverage"], "complete")

    def test_rendered_metadata_does_not_implicitly_append_source_payloads(self):
        source = copy.deepcopy(self.sql)
        source["projection"] = {"internal_permission_projection": "private evidence, not model input"}
        source["hash"] = source["projection_hash"] = _hash(source["projection"])
        value = self.capture([self.owner, source])
        self.assertNotIn("private evidence", value["baseline_context"]["content"])
        self.assertIn("private evidence", str(value["sources"]))

    def test_absent_optional_and_new_source_membership_change_the_snapshot(self):
        missing = {**self.owner, "ref": "optional-rule", "locator": {"root": str(SCRATCH), "relative": "optional.md"},
                   "required": False, "present": False, "identity": None, "hash": None, "projection": None, "projection_hash": None}
        first = self.capture()
        second = self.capture([self.owner, self.sql, missing])
        self.assertNotEqual(first["plan_hash"], second["plan_hash"])
        self.assertEqual(first["baseline_context"], second["baseline_context"])
        missing["required"] = True
        with self.assertRaises(ValueError):
            self.capture([self.owner, missing])

    def test_observation_change_preserves_plan_but_changes_capture(self):
        first = self.capture()
        sql = copy.deepcopy(self.sql)
        sql["projection"]["files"] = ["newly-pinned.md"]
        sql["hash"] = sql["projection_hash"] = _hash(sql["projection"])
        second = self.capture([self.owner, sql])
        self.assertEqual(first["plan_hash"], second["plan_hash"])
        self.assertNotEqual(first, second)

    def test_rehashed_or_truncated_protected_source_cannot_keep_old_raw_hash(self):
        source = copy.deepcopy(self.owner)
        source["projection"] = source["projection"][:100]
        source["projection_hash"] = _hash(source["projection"])
        with self.assertRaises(ValueError):
            self.capture([source])
        with self.assertRaisesRegex(ValueError, "protected_text_not_rendered"):
            self.capture(content="Owner text was silently omitted")

    def test_closed_fields_role_pairings_and_plan_are_validated(self):
        for change in ({"extra": True}, {"role": "volatile"}, {"kind": "unknown"}, {"projection_hash": "a" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.capture([{**self.owner, **change}])
        value = self.capture()
        value["plan_hash"] = "a" * 64
        with self.assertRaises(ValueError):
            S.validate_capture(value)

    def test_file_locator_is_portable_and_duplicate_ref_or_alias_rejected(self):
        for relative in ("../out.md", "C:drive.md", "CON", "a:stream"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.capture([{**self.owner, "locator": {"root": str(SCRATCH), "relative": relative}}])
        with self.assertRaises(ValueError):
            self.capture([self.owner, {**self.owner, "ref": "OWNER-PROFILE"}])
        with self.assertRaises(ValueError):
            self.capture([self.owner, {**self.owner, "ref": "alias", "locator": {**self.owner["locator"], "relative": "PROFILE.md"}}])

    def test_unknown_coverage_is_not_upgraded_and_credentials_reject(self):
        value = self.capture(complete_plan=False)
        self.assertEqual(value["baseline_context"]["coverage"], "unknown")
        with self.assertRaises(ValueError):
            self.capture(content="api_key=sk-" + "a" * 40)
        source = copy.deepcopy(self.sql)
        source["projection"] = {"api_key": "sensitive-value"}
        source["hash"] = source["projection_hash"] = _hash(source["projection"])
        with self.assertRaises(ValueError):
            self.capture([source])

    def test_oversize_prompt_or_source_is_rejected_instead_of_cut(self):
        with self.assertRaisesRegex(ValueError, "large"):
            self.capture(content="x" * (1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.capture(complete_plan=1)

    def test_protected_sql_text_and_frozen_runtime_have_distinct_roles(self):
        rules = " Full project rules\r\n"
        sql = {**self.sql, "ref": "project-rules", "role": "protected", "projection": rules,
               "hash": _hash(rules), "projection_hash": _hash(rules)}
        clock = {"clock": "2026-10-23T11:00:00+07:00", "usage": {"in": 10, "out": 5}}
        runtime = {**self.sql, "kind": "runtime", "ref": "clock-usage", "role": "volatile",
                   "locator": {"name": "frozen_clock_and_usage"}, "projection": clock,
                   "hash": _hash(clock), "projection_hash": _hash(clock)}
        value = self.capture([self.owner, sql, runtime], content=self.raw + rules + "Rendered frozen clock")
        self.assertEqual(S.validate_capture(value), value)
        value["sources"][-1]["hash"] = "a" * 64
        with self.assertRaises(ValueError):
            S.validate_capture(value)

    def test_file_discovery_requires_metadata_instead_of_raw_target_text(self):
        source = {**self.owner, "role": "discovery"}
        with self.assertRaisesRegex(ValueError, "discovery_metadata"):
            self.capture([source])


if __name__ == "__main__":
    unittest.main()
