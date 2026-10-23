"""Body proposals preserve raw bytes and discovery metadata without semantic claims."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import importlib.util
import unittest

from improvement_blobs import MAX_ASSET_BYTES

if importlib.util.find_spec("improvement_skill_patch"):
    import improvement_skill_patch as PATCH
else:
    PATCH = None


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class SkillPatchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PATCH, "Byte-preserving skill body patch validation is absent")
        self.base = b'---\r\nname: Report\r\ndescription: Original routing\r\ndisabled: false\r\n---\r\nOwner rule.\r\nOld step.\r\nKeep suffix.\r\n'
        self.start = self.base.index(b"Old step.")
        self.end = self.start + len(b"Old step.")

    def patch(self, base=None, **changes):
        base = self.base if base is None else base
        return {"version": 1, "target_id": "it-test", "base_revision_id": "ir-test", "before_hash": digest(base),
            "operation": "replace_body_region", "start": self.start, "end": self.end, "replacement": "Verify the subtotal.",
            "hypothesis": "A subtotal check catches omitted rows.", "failure_signature": "Missing row in total.",
            "expected_behavior": "Every authorized row contributes once.", "possible_regressions": ["Duplicate subtotal."],
            "evidence_refs": [], "angle": "execution", **changes}

    def test_preserves_exact_prefix_suffix_frontmatter_crlf_and_non_ascii(self):
        replacement = "Kiểm tổng, đừng đoán.\r\nGiữ nguồn."
        result = PATCH.validate_body_patch(self.base, self.patch(replacement=replacement), None)
        expected = self.base[:self.start] + replacement.encode("utf-8") + self.base[self.end:]
        self.assertEqual(result["content"], expected)
        self.assertEqual(result["content_hash"], digest(expected))
        self.assertEqual(result["byte_size"], len(expected))
        self.assertEqual(result["edit_status"], "awaiting_exact_diff")
        self.assertEqual(result["semantic_status"], "unreviewed")
        self.assertNotIn("safe", result)
        layout = PATCH.body_layout(self.base)
        self.assertEqual(layout["body_start"], self.base.index(b"Owner rule."))
        self.assertEqual(layout["prefix_hash"], digest(self.base[:layout["body_start"]]))

    def test_region_must_be_explicit_host_selected_and_contains_entire_replacement(self):
        region = {"start": self.start, "end": self.end, "before_hash": digest(self.base)}
        result = PATCH.validate_body_patch(self.base, self.patch(), region)
        self.assertEqual(result["edit_status"], "within_owner_region")
        self.assertEqual(result["semantic_status"], "unreviewed")
        for invalid in ({**region, "start": self.start + 1}, {**region, "end": self.end - 1},
                        {**region, "before_hash": "a" * 64}, {**region, "start": True},
                        {**region, "end": len(self.base) + 1}, {**region, "extra": "caller authority"}):
            with self.subTest(region=invalid), self.assertRaises(ValueError):
                PATCH.validate_body_patch(self.base, self.patch(), invalid)

    def test_keep_base_is_valid_without_inventing_an_improvement(self):
        result = PATCH.validate_body_patch(self.base, self.patch(replacement="Old step."), None)
        self.assertEqual(result["content"], self.base)
        self.assertTrue(result["keep_base"])
        self.assertEqual(result["semantic_status"], "unreviewed")

    def test_rejects_frontmatter_description_or_metadata_edit(self):
        for token in (b"name", b"Original routing", b"disabled", b"---"):
            start = self.base.index(token)
            with self.subTest(token=token), self.assertRaises(ValueError):
                PATCH.validate_body_patch(self.base, self.patch(start=start, end=start + len(token)), None)

    def test_rejects_stale_digest_closed_fields_boolean_offsets_and_wrong_surfaces(self):
        for changes in ({"before_hash": "b" * 64}, {"version": True}, {"start": True}, {"end": False},
                        {"start": -1}, {"end": len(self.base) + 1}, {"end": self.start - 1},
                        {"operation": "replace_file"}, {"path": "helpers/a.py"}, {"model": "other"},
                        {"target_id": "../target"}, {"base_revision_id": []}, {"angle": "routing"},
                        {"replacement": 123}, {"replacement": "bad\x00text"}, {"replacement": "\ud800"}):
            with self.subTest(changes=repr(changes)), self.assertRaises(ValueError):
                PATCH.validate_body_patch(self.base, self.patch(**changes), None)

    def test_rejects_split_utf8_codepoint_and_preserves_bom_plain_text(self):
        base = "\ufeffOriginal routing\r\nĐếm đầy đủ.\r\nSuffix".encode("utf-8")
        start = base.index("Đ".encode())
        patch = self.patch(base, start=start, end=start + len("Đếm đầy đủ.".encode()), replacement="Đối chiếu.")
        result = PATCH.validate_body_patch(base, patch, None)
        self.assertTrue(result["content"].startswith(b"\xef\xbb\xbfOriginal routing\r\n"))
        with self.assertRaises(ValueError):
            PATCH.validate_body_patch(base, {**patch, "start": start + 1}, None)
        with self.assertRaises(ValueError):
            PATCH.validate_body_patch(base, {**patch, "end": start + 1}, None)

    def test_rejects_ambiguous_yaml_layout_without_rewriting_it(self):
        examples = (
            b"---\nname: X\nNo closing delimiter",
            b"---\nname: X\n---not-close\n---\nBody",
            b"---\nname: X\nname: Y\n---\nBody",
            b"---\n- sequence\n---\nBody",
            b"---\nname: [broken\n---\nBody",
            b"\xef\xbb\xbf---\nname: X\n---\nBody",
            b"---\nname: &name X\ndescription: *name\n---\nBody",
        )
        for base in examples:
            with self.subTest(base=base), self.assertRaises(ValueError):
                PATCH.body_layout(base)

    def test_freezes_implicit_routing_description_without_blocking_later_body(self):
        for base in (b"Original routing\nOld step.\n", b"\n  Original routing\nOld step.\n",
                     b"---\nname: X\n---\n\nOriginal routing\nOld step.\n"):
            first = base.index(b"Original routing")
            with self.subTest(base=base), self.assertRaises(ValueError):
                PATCH.validate_body_patch(base, self.patch(base, start=first, end=first + 3), None)
            start = base.index(b"Old step.")
            result = PATCH.validate_body_patch(base, self.patch(base, start=start, end=start + 9), None)
            self.assertEqual(result["content"][:start], base[:start])

    def test_empty_implicit_description_cannot_become_a_new_route(self):
        for base in (b"", b"\n  \n", b"---\nname: X\n---\n"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                PATCH.validate_body_patch(base, self.patch(base, start=len(base), end=len(base)), None)

    def test_known_dependency_reference_inventory_cannot_change(self):
        for reference in ("scripts/new.py", "../helpers/leak.sh", ".claude/skills/new/SKILL.md",
                          "skills/other/SKILL.md", "references/new.md", "C:\\private\\script.py", "helpers\\new.txt"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                PATCH.validate_body_patch(self.base, self.patch(replacement="Run " + reference), None)
        base = self.base.replace(b"Old step.", b"Use scripts/existing.py once.")
        patch = self.patch(base, end=self.start + len(b"Use scripts/existing.py once."), replacement="First validate. Use scripts/existing.py once.")
        result = PATCH.validate_body_patch(base, patch, None)
        self.assertEqual(result["reference_inventory"], ["scripts/existing.py"])
        self.assertEqual(result["dependency_status"], "unresolved")

    def test_bounded_output_and_manifest_values(self):
        for changes in ({"replacement": "x" * MAX_ASSET_BYTES}, {"hypothesis": " "},
                        {"expected_behavior": "x" * 4001}, {"possible_regressions": []},
                        {"possible_regressions": [""]}, {"possible_regressions": ["a"] * 17},
                        {"evidence_refs": [{"kind": "qualification", "id": "case-2"}]},
                        {"evidence_refs": [{"kind": "tuning_trial", "id": "a", "path": "secret"}]},
                        {"evidence_refs": [{"kind": "owner_request", "id": "../file"}]}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                PATCH.validate_body_patch(self.base, self.patch(**changes), None)
        valid = self.patch(evidence_refs=[{"kind": "owner_request", "id": "owner-event-1"}])
        original = copy.deepcopy(valid)
        PATCH.validate_body_patch(self.base, valid, None)
        self.assertEqual(valid, original)


if __name__ == "__main__":
    unittest.main()
