"""An unisolated command cannot automatically certify a Quality job."""
from _paths import SERVER  # noqa: F401
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import team_acceptance
import team_runner


class CheckerIsolation(unittest.TestCase):
    def test_quality_checker_requires_isolation_before_any_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'checker.py').write_text('import mutable_fixture', encoding='utf-8')
            runner = object.__new__(team_runner.TeamRunner)
            runner.brain_root = lambda team: root
            runner.quality = SimpleNamespace(policy=lambda tid: {'enabled': True})
            criterion = runner._pin_checkers({'team_id': 'quality'}, [
                {'kind': 'command_ok', 'command': 'python checker.py'}])[0]
            calls = []
            verdict, evidence = team_acceptance.check_one(criterion, root,
                lambda *args: calls.append(args) or {'ran': True, 'exit': 0})
            self.assertEqual('unknown', verdict)
            self.assertEqual('checker_isolation_unavailable', evidence.get('reason'))
            self.assertFalse(calls, 'mutable imports/fixtures cannot produce a trusted pass')
            runner.quality = SimpleNamespace(policy=lambda tid: {'enabled': False})
            preserved = runner._pin_checkers({'team_id': 'quality'}, [criterion])[0]
            self.assertTrue(preserved['requires_isolated_checker'])

    def test_legacy_command_keeps_existing_behavior(self):
        with tempfile.TemporaryDirectory() as folder:
            verdict, _ = team_acceptance.check_one({'kind': 'command_ok', 'command': 'echo ok'}, Path(folder),
                lambda *args: {'ran': True, 'exit': 0})
            self.assertEqual('pass', verdict)


if __name__ == '__main__':
    unittest.main()
