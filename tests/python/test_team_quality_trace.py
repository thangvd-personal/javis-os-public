"""Quality pilot traces remain read-only and do not invent reconciled totals."""
from _paths import ROOT
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

spec = importlib.util.spec_from_file_location('quality_trace', ROOT / 'pilot/team-multiday/trace_export.py')
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


class TraceExport(unittest.TestCase):
    def test_attributed_observations_keep_unknowns_and_strip_unrelated_content(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            db = state / 'team.sqlite3'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE teams(team_id TEXT,name TEXT,brain_id TEXT)')
                conn.execute("INSERT INTO teams VALUES('one','One','brain')")
                conn.commit()
            before = db.read_bytes()
            event = {'ts': 1, 'event_id': 'request-1', 'event_kind': 'cli_fallback',
                     'execution': {'team_id': 'one', 'task_id': 'job', 'role': 'work', 'attempt': 2,
                                   'prompt': 'never export'},
                     'in': 10, 'out': None, 'tokens_known': False,
                     'money': {'currency': 'USD', 'amount': None, 'provenance': 'unknown'},
                     'prompt': 'never export'}
            (state / 'usage-events.jsonl').write_text(json.dumps(event) + '\n' +
                json.dumps(dict(event, execution={'team_id': 'other'})) + '\nbroken\n', encoding='utf-8')
            rows = trace.export(state, 'one')
            self.assertEqual(before, db.read_bytes())
            usage = [r for r in rows if r['link'] == 'usage_event']
            self.assertEqual(1, len(usage))
            self.assertIsNone(usage[0]['tokens_out'])
            self.assertEqual('raw_observation_not_reconciled', usage[0]['coverage'])
            self.assertNotIn('never export', json.dumps(rows))
            self.assertEqual('request-1', usage[0]['event_id'])


if __name__ == '__main__':
    unittest.main()
