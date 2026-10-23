"""Opt-in quality policy and immutable owner template versions for Team jobs."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import localefmt
import team_acceptance
import team_migration


class QualityError(ValueError):
    def __init__(self, code, vi, en):
        self.code = code
        super().__init__(localefmt.chu(vi, en))


class QualityStore:
    def __init__(self, path, clock=time.time, changed=None):
        self.path, self.clock, self.changed = Path(path), clock, changed
        self.path.parent.mkdir(parents=True, exist_ok=True)
        team_migration.snapshot_before_table(self.path, 'teams', 'team_quality_policy', '.pre-0.283.0')
        with closing(self._conn()) as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS team_quality_policy(
                    team_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS team_quality_templates(
                    team_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(team_id, version));
                CREATE TABLE IF NOT EXISTS team_quality_policy_history(
                    id INTEGER PRIMARY KEY, team_id TEXT NOT NULL, body TEXT NOT NULL, at REAL NOT NULL);
            ''')

    def _conn(self):
        c = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def policy(self, team_id):
        with closing(self._conn()) as c:
            row = c.execute('SELECT body FROM team_quality_policy WHERE team_id=?', (team_id,)).fetchone()
        if row:
            out = json.loads(row['body'])
            out.setdefault('limit_migration_selected', True)
            return out
        return {'enabled': False, 'cost_behavior': 'observe_only', 'limit_migration': 'retain',
                'reference_usd': None, 'limit_migration_selected': False}

    def set_policy(self, team_id, body):
        if not isinstance(body, dict):
            raise QualityError('invalid_policy', 'Chính sách không hợp lệ', 'Invalid quality policy')
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM team_quality_policy WHERE team_id=?', (team_id,)).fetchone()
            old = json.loads(row['body']) if row else self.policy(team_id)
            out = dict(old)
            if 'enabled' in body and not isinstance(body['enabled'], bool):
                raise QualityError('invalid_enabled', 'Trạng thái phải là bật hoặc tắt', 'Enabled must be a boolean')
            if body.get('enabled') and not old['enabled'] and 'limit_migration' not in body:
                raise QualityError('limit_migration_required', 'Chọn giữ hoặc chuyển các giới hạn cũ một lần',
                                   'Choose whether to retain or observe legacy limits once')
            if body.get('cost_behavior', 'observe_only') != 'observe_only':
                raise QualityError('invalid_cost_behavior', 'Chi phí chỉ dùng để theo dõi', 'Cost behavior must be observe_only')
            if body.get('limit_migration', out['limit_migration']) not in ('retain', 'observe'):
                raise QualityError('invalid_limit_migration', 'Cách chuyển giới hạn không hợp lệ', 'Invalid limit migration')
            if 'reference_usd' in body:
                v = body['reference_usd']
                try:
                    valid = v is None or (not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0)
                except OverflowError:
                    valid = False
                if not valid:
                    raise QualityError('invalid_reference', 'Mức tham khảo phải là số hữu hạn không âm',
                                       'Reference must be a finite non-negative number')
            out.update({k: body[k] for k in ('enabled', 'limit_migration', 'reference_usd') if k in body})
            out['limit_migration_selected'] = bool(old.get('limit_migration_selected') or 'limit_migration' in body)
            if out['enabled'] and (not old['enabled'] or out['limit_migration'] != old['limit_migration']):
                out['limit_migrated_at'] = self.clock()
                events_exist = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_events'").fetchone()
                out['limit_migrated_event'] = c.execute('SELECT COALESCE(MAX(id),0) FROM team_events WHERE team_id=?',
                    (team_id,)).fetchone()[0] if events_exist else 0
            out['cost_behavior'] = 'observe_only'
            payload = json.dumps(out, sort_keys=True)
            c.execute('INSERT INTO team_quality_policy VALUES(?,?,?) ON CONFLICT(team_id) DO UPDATE SET '
                      'body=excluded.body, updated_at=excluded.updated_at', (team_id, payload, self.clock()))
            c.execute('INSERT INTO team_quality_policy_history(team_id,body,at) VALUES(?,?,?)',
                      (team_id, payload, self.clock()))
            c.commit()
        if self.changed:
            self.changed(team_id, out)
        return out

    def template(self, team_id):
        with closing(self._conn()) as c:
            row = c.execute('SELECT version,body FROM team_quality_templates WHERE team_id=? ORDER BY version DESC LIMIT 1',
                            (team_id,)).fetchone()
        return dict(json.loads(row['body']), version=row['version'], template_id=team_id) if row else None

    def set_template(self, team_id, body):
        if not isinstance(body, dict) or isinstance(body.get('expected_version'), bool) or not isinstance(body.get('expected_version'), int):
            raise QualityError('expected_version_required', 'Cần phiên bản mẫu đang xem', 'Expected template version is required')
        out = {'criteria': team_acceptance.clean_criteria(body.get('criteria')),
               'expected_outputs': team_acceptance.clean_outputs(body.get('expected_outputs', [])),
               'source_refs': team_acceptance.clean_outputs(body.get('source_refs', [])),
               'fix_rounds': team_acceptance.clean_fix_rounds(body.get('fix_rounds'))}
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            version = c.execute('SELECT COALESCE(MAX(version),0) FROM team_quality_templates WHERE team_id=?',
                                (team_id,)).fetchone()[0]
            if body['expected_version'] != version:
                raise QualityError('version_conflict', 'Mẫu đã thay đổi, hãy tải lại', 'Template changed, reload it')
            c.execute('INSERT INTO team_quality_templates VALUES(?,?,?,?)',
                      (team_id, version + 1, json.dumps(out, ensure_ascii=False), self.clock()))
            c.commit()
        return self.template(team_id)
