"""Dependency evidence follows each declaring document's exact granted identity."""
from _paths import ROOT, SERVER  # noqa: F401
import tempfile
import unittest
from pathlib import Path

from improvement_trial_context import ContextSource, capture_context
from improvement_dependency_evidence import dependency_evidence


class DependencyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.main = 'skills/report/SKILL.md'

    def evidence(self, files, body=b'Use references/checks.md\n'):
        sources = []
        for identifier, relative, text in files:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            sources.append(ContextSource(identifier, self.root, relative))
        capture = capture_context(sources, surface={'kind': 'synthetic-dependency-identity'}, complete_plan=True)
        return dependency_evidence(capture, self.main, body, body, candidate_root=self.root)

    def test_missing_nested_sibling_cannot_bind_an_unrelated_root_file(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', '[child](child.md)\n'),
            ('unrelated-child', 'child.md', 'Unrelated granted root file.\n')])
        self.assertEqual(value['verdict'], 'unknown')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks'])
        self.assertEqual(value['unresolved'][0]['reference'], 'child.md')

    def test_missing_nested_sibling_cannot_fall_back_to_candidate_directory(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', '[child](child.md)\n'),
            ('candidate-child', 'skills/report/child.md', 'Not the declared sibling.\n')])
        self.assertEqual(value['verdict'], 'unknown')
        self.assertNotIn('candidate-child', [node['ref'] for node in value['nodes']])

    def test_parent_relative_link_normalizes_to_its_actual_granted_file(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', '[notes](../notes.md)\n'),
            ('notes', 'skills/report/notes.md', 'The exact declared parent file.\n')])
        self.assertEqual(value['verdict'], 'pass')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks', 'notes'])
        self.assertEqual(value['unresolved'], [])

    def test_explicit_root_relative_plain_reference_keeps_its_identity(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', 'Authorized checks.\n')],
            body=b'Use skills/report/references/checks.md\n')
        self.assertEqual(value['verdict'], 'pass')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks'])

    def test_real_sibling_and_unrelated_same_name_are_not_ambiguous(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', '[child](child.md)\n'),
            ('sibling', 'skills/report/references/child.md', 'Declared sibling.\n'),
            ('root-child', 'child.md', 'Unrelated file.\n')])
        self.assertEqual(value['verdict'], 'pass')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks', 'sibling'])

    def test_plain_and_markdown_same_spelling_preserve_both_declared_identities(self):
        value = self.evidence([('markdown-target', 'skills/report/skills/shared/checks.md', 'Only the Markdown target is granted.\n')],
            body=b'Use skills/shared/checks.md.\nSee [other](skills/shared/checks.md).\n')
        self.assertEqual(value['verdict'], 'unknown')
        self.assertTrue(value['unresolved'])

    def test_plain_external_uri_cannot_certify_a_local_granted_suffix(self):
        value = self.evidence([('local-suffix', 'skills/shared/checks.md', 'Not the remote document.\n')],
            body=b'See https://example.invalid/skills/shared/checks.md\n')
        self.assertEqual(value['verdict'], 'unknown')
        self.assertEqual(value['nodes'], [])

    def test_markdown_external_uri_cannot_certify_a_local_granted_suffix(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', '[child](https://example.invalid/skills/shared/checks.md)\n'),
            ('local-suffix', 'skills/shared/checks.md', 'Not the remote document.\n')])
        self.assertEqual(value['verdict'], 'unknown')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks'])

    def test_encoded_markdown_name_resolves_the_actual_granted_file(self):
        value = self.evidence([('checks', 'skills/report/references/checks.md', '[child](child%20notes.md)\n'),
            ('child', 'skills/report/references/child notes.md', 'The exact decoded target.\n')])
        self.assertEqual(value['verdict'], 'pass')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks', 'child'])

    def test_a_relative_reference_cannot_adopt_the_same_path_from_a_foreign_root(self):
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory)
            path = foreign / 'skills/report/references/checks.md'
            path.parent.mkdir(parents=True)
            path.write_text('Granted context, but not the candidate root.\n')
            capture = capture_context([ContextSource('foreign', foreign, 'skills/report/references/checks.md')],
                surface={'kind': 'synthetic-dependency-identity'}, complete_plan=True)
            body = b'Use references/checks.md\n'
            value = dependency_evidence(capture, self.main, body, body, candidate_root=self.root)
            self.assertEqual(value['verdict'], 'unknown')
            self.assertEqual(value['nodes'], [])

    def test_relative_traversal_cannot_escape_its_declaring_root(self):
        brain = self.root / 'brain'
        path = brain / 'skills/report/references/checks.md'
        path.parent.mkdir(parents=True)
        path.write_text('[outside](../../../../outside.md)\n')
        (self.root / 'outside.md').write_text('An explicitly granted separate context root.\n')
        capture = capture_context([ContextSource('checks', brain, 'skills/report/references/checks.md'),
            ContextSource('outside', self.root, 'outside.md')], surface={'kind': 'synthetic-dependency-identity'}, complete_plan=True)
        body = b'Use references/checks.md\n'
        value = dependency_evidence(capture, self.main, body, body, candidate_root=brain)
        self.assertEqual(value['verdict'], 'unknown')
        self.assertEqual([node['ref'] for node in value['nodes']], ['checks'])


if __name__ == '__main__':
    unittest.main()
