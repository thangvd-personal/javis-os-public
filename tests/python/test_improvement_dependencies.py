"""Read only actual captured dependency bytes; never execute model-selected paths."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import improvement_store as IM
from improvement_trial_context import ContextSource, capture_context


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / 'skills/report/references').mkdir(parents=True)
        self.first = self.root / 'skills/report/references/checks.md'
        self.first.write_text('Check each authorized subtotal.\n', encoding='utf-8')
        self.capture = capture_context([ContextSource('owner-checks', self.root, 'skills/report/references/checks.md')],
                                       surface={'kind': 'dependency-test'}, complete_plan=True)

    def resolve(self, before=b'Use references/checks.md\n', after=b'Use references/checks.md\nThen check totals.\n', capture=None):
        self.assertIsNotNone(importlib.util.find_spec('improvement_dependency_evidence'), 'Actual dependency evidence is absent')
        from improvement_dependency_evidence import dependency_evidence
        return dependency_evidence(capture or self.capture, 'skills/report/SKILL.md', before, after, candidate_root=self.root)

    def test_declared_document_reads_exact_granted_bytes_and_keeps_runtime_coverage_explicit(self):
        result = self.resolve()
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual(result['coverage'], 'declared_reference_graph')
        self.assertEqual(result['nodes'][0]['hash'], hashlib.sha256(self.first.read_bytes()).hexdigest())
        self.assertEqual(result['nodes'][0]['content'].encode(), self.first.read_bytes())
        self.assertNotEqual(result['runtime'], 'qualified')

    def test_file_existence_is_not_a_dependency_grant_and_new_refs_do_not_expand_scope(self):
        with patch('improvement_trial_context._read', side_effect=AssertionError('Ungrant cannot open any file')):
            result = self.resolve(before=b'Use references/ungranted.md\n', after=b'Use references/ungranted.md\n')
        self.assertEqual(result['verdict'], 'unknown')
        self.assertTrue(result['unresolved'])
        with self.assertRaises(ValueError):
            self.resolve(after=b'Use references/checks.md and scripts/new.py\n')

    def test_changed_dependency_after_capture_never_uses_stale_or_missing_fallback(self):
        self.first.write_text('Changed owner dependency.\n', encoding='utf-8')
        with self.assertRaises(IM.ConflictError):
            self.resolve()

    def test_captured_executable_is_data_and_never_runtime_qualification(self):
        script = self.root / 'skills/report/scripts/validate.py'
        script.parent.mkdir()
        script.write_text('raise AssertionError("A dependency must never run here")\n', encoding='utf-8')
        captured = capture_context([ContextSource('script', self.root, 'skills/report/scripts/validate.py')],
                                   surface={'kind': 'dependency-test'}, complete_plan=True)
        result = self.resolve(before=b'Use scripts/validate.py\n', after=b'Use scripts/validate.py\n', capture=captured)
        self.assertEqual(result['verdict'], 'unknown')
        self.assertEqual(result['runtime'], 'unsupported_executable_dependency')
        self.assertEqual(result['nodes'][0]['content'].encode(), script.read_bytes())

    def test_transitive_document_refs_are_granted_individually_and_cycles_stay_unknown(self):
        child = self.first.with_name('child.md')
        self.first.write_text('See [child](child.md).\n', encoding='utf-8')
        child.write_text('Check the subtotal.\n', encoding='utf-8')
        sources = [ContextSource('checks', self.root, 'skills/report/references/checks.md'),
                   ContextSource('child', self.root, 'skills/report/references/child.md')]
        captured = capture_context(sources, surface={'kind': 'dependency-test'}, complete_plan=True)
        result = self.resolve(capture=captured)
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual({node['ref'] for node in result['nodes']}, {'checks', 'child'})
        child.write_text('See [parent](checks.md).\n', encoding='utf-8')
        captured = capture_context(sources, surface={'kind': 'dependency-test'}, complete_plan=True)
        result = self.resolve(capture=captured)
        self.assertEqual(result['verdict'], 'unknown')
        self.assertTrue(any(item['reason'] == 'dependency_cycle' for item in result['unresolved']))

    def test_discovery_or_metadata_projection_does_not_grant_unprojected_file_body(self):
        from improvement_trial_semantics import capture_semantics
        original = self.capture['sources'][0]
        projection = {'description': 'Metadata only.'}
        source = {'kind': 'file', 'ref': 'metadata-only', 'locator': {'root': str(self.root), 'relative': original['relative']},
            'role': 'authority', 'required': True, 'present': True, 'identity': original['identity'], 'hash': original['hash'],
            'projection': projection, 'projection_hash': IM._hash(projection)}
        captured = capture_semantics(surface={'kind': 'dependency-test'}, sources=[source], content='Known metadata only.', complete_plan=True)
        with patch('improvement_trial_context._read', side_effect=AssertionError('Metadata grant cannot read the whole file')):
            result = self.resolve(capture=captured)
        self.assertEqual(result['verdict'], 'unknown')
        self.assertEqual(result['nodes'], [])


if __name__ == '__main__':
    unittest.main()
