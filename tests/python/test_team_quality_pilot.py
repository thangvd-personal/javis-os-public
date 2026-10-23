"""Paired pilot preparation is reproducible and never invents business results."""
from _paths import ROOT, SERVER  # noqa: F401
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE = ROOT / 'pilot' / 'team-multiday' / 'paired_evaluation.py'


def fake(fn):
    fn.is_fake = True
    return fn


class PairedPilot(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), 'the paired pilot harness exists')
        spec = importlib.util.spec_from_file_location('paired_evaluation', MODULE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.out = Path(tempfile.mkdtemp(prefix='javis-paired-'))

    def test_twenty_pairs_and_forty_isolated_dry_runs(self):
        manifest = self.module.synthetic_manifest()
        self.assertEqual(len(manifest['pairs']), 20)
        self.assertEqual(sum(p['workflow'] == 'content' for p in manifest['pairs']), 10)
        calls = []
        @fake
        def execute(request):
            calls.append(request)
            return {'text': 'Synthetic rehearsal only.', 'status': 'draft', 'usage': None}
        summary = self.module.run_pairs(manifest, execute, self.out, dry_run=True)
        self.assertEqual(len(calls), 40)
        self.assertEqual(len({c['workspace'] for c in calls}), 40)
        for pair in manifest['pairs']:
            arms = [c for c in calls if c['pair_id'] == pair['pair_id']]
            self.assertEqual(arms[0]['input_hash'], arms[1]['input_hash'])
            self.assertEqual(arms[0]['settings'], arms[1]['settings'])
        self.assertEqual(summary['business_status'], 'not_run')
        self.assertIsNone(summary['quality_improvement'])
        self.assertIsNone(summary['cash_cost_per_accepted_result'])
        self.assertEqual(summary['accepted_results'], 0)
        self.assertEqual(summary['execution_records'], 40)
        self.module.run_pairs(manifest, execute, self.out, dry_run=True)
        self.assertEqual(len(calls), 40, 'restarting never repeats an already recorded execution')

    def test_failures_and_unknown_usage_remain_in_report(self):
        manifest = self.module.synthetic_manifest()
        @fake
        def execute(request):
            if request['arm'] == 'team':
                raise RuntimeError('provider unavailable')
            return {'text': 'draft', 'status': 'draft', 'usage': None}
        report = self.module.run_pairs(manifest, execute, self.out, dry_run=True)
        self.assertEqual(report['failed_executions'], 20)
        self.assertEqual(report['unknown_usage_executions'], 40)
        self.assertEqual(report['execution_records'], 40)
        self.assertIsNone(report['quality_improvement'])

    def test_synthetic_input_cannot_be_reported_as_real(self):
        with self.assertRaises(ValueError):
            self.module.run_pairs(self.module.synthetic_manifest(), lambda r: {}, self.out, dry_run=False)

    def test_changed_input_cannot_silently_reuse_old_record(self):
        manifest = self.module.synthetic_manifest()
        self.module.run_pairs(manifest, fake(lambda r: {'text': 'x', 'usage': None}), self.out, dry_run=True)
        manifest['pairs'][0]['input']['prompt'] = 'A new requirement'
        with self.assertRaises(ValueError):
            self.module.run_pairs(manifest, fake(lambda r: {}), self.out, dry_run=True)

    def test_pair_identifiers_cannot_escape_workspaces(self):
        manifest = self.module.synthetic_manifest()
        manifest['pairs'][0]['pair_id'] = '../escape'
        with self.assertRaises(ValueError):
            self.module.run_pairs(manifest, fake(lambda r: {}), self.out, dry_run=True)


if __name__ == '__main__':
    unittest.main()
