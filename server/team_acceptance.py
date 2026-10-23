"""Product acceptance (nghiệm thu) for Team jobs (0.209.0).

Design: docs/superpowers/specs/2026-10-17-team-duyet-muc-tieu-nghiem-thu-design.md, part 3.

Before this version a Team job was `done` as soon as one step produced a text: nothing checked that the product the
job promised exists, has what it should have, or still is what was checked. Now the owner can attach acceptance
criteria to a job; the runner checks them on the product itself before closing the job.

Criteria (owner-set, bound to the job; every check records the job revision it ran on):
    file_exists    {"path"}                                     the file is there (path inside the brain)
    file_contains  {"path", "min_chars", "must_contain": [...]} enough text, the required phrases
    json_keys      {"path", "keys": [...]}                      valid JSON object with those top-level keys
    command_ok     {"command"}                                  a check command exits 0 (run_command at `auto`)
    owner_confirm  {"path"?}                                    the owner confirms; bound to the file's sha256

Each check gives pass | fail | unknown with evidence (path, sha256, size, mtime, detail). A model's opinion is never a
verdict here. The job closes `done` only when every criterion passes under the owner's policy:
- all objective criteria pass and no owner_confirm -> done;
- any fail -> a bounded fix loop (the member who produced the result gets a fix step with the failed checks), past
  `fix_rounds` the job stops `acceptance_failed`;
- unknown, or owner_confirm -> the job waits for the owner (`acceptance_review`), who confirms (binding the product
  hashes) or sends it back with a note.
History is kept per round. The reward signal (pulse) is written once per (job, revision), only from a passed objective
acceptance or the owner's confirmation, never from an agent's words. Editing a checked product afterwards shows the
acceptance as stale (sha256 no longer matches).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Optional
import localefmt
import team_migration

KINDS = ("file_exists", "file_contains", "json_keys", "command_ok", "owner_confirm")
VERDICTS = ("pass", "fail", "unknown")
DEFAULT_FIX_ROUNDS = 2
MAX_CRITERIA = 12
READ_MAX = 2_000_000
HASH_MAX = 200_000_000   # bigger products are recorded by size and mtime only (no sha256, verdict at most unknown)

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_acceptance(
  task_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, criteria_json TEXT NOT NULL, fix_rounds INTEGER NOT NULL DEFAULT 2,
  rounds_used INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', revision INTEGER,
  confirmed_by TEXT NOT NULL DEFAULT '', confirmed_at REAL, confirmed_hashes_json TEXT NOT NULL DEFAULT '{}',
  set_by TEXT NOT NULL DEFAULT 'owner', result_msg_id INTEGER, message_id INTEGER, created_at REAL NOT NULL,
  updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS team_acceptance_checks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, revision INTEGER NOT NULL, round INTEGER NOT NULL,
  idx INTEGER NOT NULL, kind TEXT NOT NULL, verdict TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
  at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS team_acceptance_checks_task ON team_acceptance_checks(task_id, id);
"""


class AcceptanceError(ValueError):
    pass


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _rel_ok(p: str) -> bool:
    raw = str(p or '').strip()
    win, posix = PureWindowsPath(raw), PurePosixPath(raw)
    return bool(raw) and '\x00' not in raw and not win.drive and not win.root and not posix.root and \
        '..' not in win.parts and '..' not in posix.parts


def digest(value: Any) -> str:
    return hashlib.sha256(_j(value).encode('utf-8')).hexdigest()


def clean_fix_rounds(value):
    try:
        return max(0, min(5, int(DEFAULT_FIX_ROUNDS if value in (None, '') else value)))
    except (TypeError, ValueError, OverflowError):
        raise AcceptanceError(localefmt.chu('Số vòng sửa không hợp lệ', 'Invalid fix round count'))


def clean_outputs(value):
    if not isinstance(value, list) or len(value) > MAX_CRITERIA:
        raise AcceptanceError(localefmt.chu('Danh sách sản phẩm không hợp lệ', 'Invalid output list'))
    out = []
    for item in value:
        if not isinstance(item, dict) or (item.get('target') != 'message' and not _rel_ok(item.get('path'))):
            raise AcceptanceError(localefmt.chu('Đường dẫn sản phẩm không hợp lệ', 'Invalid product path'))
        out.append({k: item[k] for k in ('target', 'path', 'sha256', 'version', 'required', 'label') if k in item})
    return out


def clean_criteria(raw: Any) -> list:
    if not isinstance(raw, list) or not raw:
        raise AcceptanceError("cần ít nhất một tiêu chí")
    out = []
    for c in raw[:MAX_CRITERIA]:
        if not isinstance(c, dict) or c.get("kind") not in KINDS:
            raise AcceptanceError(f"tiêu chí không hợp lệ: {str(c)[:80]}")
        k = c["kind"]
        item = {"kind": k, "label": str(c.get("label") or "").strip()[:200]}
        if k == 'owner_confirm':
            target = c.get('target') or ('file' if c.get('path') else 'message')
            if target not in ('file', 'message') or (target == 'file' and not c.get('path')):
                raise AcceptanceError(localefmt.chu('Loại sản phẩm không hợp lệ', 'Invalid confirmation target'))
            item['target'] = target
        if k == 'command_ok' and c.get('checker_sha256'):
            item['checker_sha256'] = str(c['checker_sha256'])
        if k == 'command_ok' and c.get('requires_isolated_checker'):
            item['requires_isolated_checker'] = True
        if k in ("file_exists", "file_contains", "json_keys") or (k == "owner_confirm" and c.get("path")):
            if not _rel_ok(c.get("path")):
                raise AcceptanceError("đường dẫn sản phẩm phải nằm trong bộ não (tương đối, không '..')")
            item["path"] = str(c["path"]).strip()[:400]
        if k == "file_contains":
            try:
                item["min_chars"] = max(0, int(c.get("min_chars") or 0))
            except (TypeError, ValueError):
                raise AcceptanceError("min_chars phải là số")
            item["must_contain"] = [str(x)[:200] for x in (c.get("must_contain") or []) if str(x or "").strip()][:10]
        if k == "json_keys":
            item["keys"] = [str(x)[:100] for x in (c.get("keys") or []) if str(x or "").strip()][:20]
            if not item["keys"]:
                raise AcceptanceError("json_keys cần danh sách khoá")
        if k == "command_ok":
            item["command"] = str(c.get("command") or "").strip()[:400]
            if not item["command"]:
                raise AcceptanceError("command_ok cần lệnh")
        out.append(item)
    return out


def sha256_file(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def resolve_in(root: Path, rel: str) -> Optional[Path]:
    """The product path inside `root`, symlinks resolved; None when it leaves the root."""
    if not _rel_ok(rel):
        return None
    try:
        base = Path(root).resolve()
        p = (base / rel).resolve()
        p.relative_to(base)
        return p
    except (ValueError, OSError):
        return None


def _file_evidence(root: Path, rel: str, since: float = 0.0) -> tuple:
    """(evidence, content bytes or None). The bytes are read once and the hash is of those same bytes (review 0.209.0),
    so the evidence describes exactly what was checked. Over HASH_MAX only size and mtime are kept."""
    p = resolve_in(root, rel)
    if p is None:
        return {"path": rel, "error": "ngoài bộ não"}, None
    if not p.is_file():
        return {"path": rel, "exists": False}, None
    try:
        st = p.stat()
    except OSError:
        return {'path': rel, 'error': localefmt.chu('Không đọc được', 'Cannot read product')}, None
    ev = {"path": rel, "exists": True, "size": st.st_size, "mtime": st.st_mtime}
    if since and st.st_mtime < since:
        ev["older_than_job"] = True
    if st.st_size > HASH_MAX:
        ev["too_big"] = True
        return ev, None
    try:
        data = p.read_bytes()
    except OSError:
        return dict(ev, error="không đọc được"), None
    ev["sha256"] = hashlib.sha256(data).hexdigest()
    return ev, data


def _script_sha(root: Path, command: str) -> dict:
    """The hash of a script file a check command runs (python x.py, node y.js...), so the evidence says WHICH checker
    passed: an agent can write files in the brain, a changed checker is visible (review 0.209.0)."""
    import shlex
    try:
        parts = shlex.split(command)
    except ValueError:
        return {}
    for a in parts[1:]:
        p = resolve_in(root, a) if _rel_ok(a) else None
        if p is not None and p.is_file():
            return {"script": a, "script_sha256": sha256_file(p)}
    return {}


def check_one(c: dict, root: Path, run_command: Optional[Callable] = None, since: float = 0.0) -> tuple:
    """(verdict, evidence) for one criterion on the product as it is now. `since`: the job's start; a product file not
    touched since then did not come from this job, so it is only "unknown" (the owner decides)."""
    k = c["kind"]
    if k == "command_ok":
        if c.get('requires_isolated_checker'):
            return 'unknown', dict(_script_sha(root, c['command']), command=c['command'],
                reason='checker_isolation_unavailable', detail=localefmt.chu(
                    'Chưa có môi trường kiểm độc lập bảo vệ bộ kiểm và dữ liệu mẫu; cần chủ xem kết quả',
                    'An isolated checker and fixture environment is unavailable; owner review is required'))
        if run_command is None:
            return "unknown", {"command": c["command"], "detail": "máy chủ không chạy được lệnh kiểm tra"}
        sc = _script_sha(root, c["command"])
        if c.get('checker_sha256') and sc.get('script_sha256') != c['checker_sha256']:
            return 'unknown', dict(sc, detail=localefmt.chu('Bộ kiểm đã thay đổi', 'Checker changed'))
        res = run_command(c["command"], root) or {}
        if c.get('checker_sha256') and _script_sha(root, c['command']).get('script_sha256') != c['checker_sha256']:
            return 'unknown', dict(sc, detail=localefmt.chu('Bộ kiểm đã thay đổi', 'Checker changed'))
        if not res.get("ran"):
            return "unknown", dict(sc, command=c["command"], detail=str(res.get("error") or "lệnh không chạy")[:300])
        code = int(res.get("exit") or 0)
        return ("pass" if code == 0 else "fail"), dict(sc, command=c["command"], exit=code,
                                                       output=str(res.get("output") or "")[-600:])
    if k == "owner_confirm":
        if not c.get("path"):
            return "unknown", {"detail": "chờ chủ nhóm xác nhận"}
        ev, _ = _file_evidence(root, c["path"], since)
        if ev.get("error") or not ev.get("exists"):
            return "fail", dict(ev, detail="chưa có file để xác nhận")
        return "unknown", dict(ev, detail="chờ chủ nhóm xác nhận")
    ev, data = _file_evidence(root, c["path"], since)
    if ev.get("error"):
        return "fail", ev
    if not ev.get("exists"):
        return "fail", dict(ev, detail="chưa có file")
    if ev.get("older_than_job"):
        return "unknown", dict(ev, detail="file có từ trước khi giao việc, không phải sản phẩm của việc này")
    if k == "file_exists":
        return ("unknown", dict(ev, detail="file quá lớn để băm")) if ev.get("too_big") else ("pass", ev)
    if data is None or ev["size"] > READ_MAX:
        return "unknown", dict(ev, detail="file quá lớn để đọc kiểm")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown", dict(ev, detail="không đọc được nội dung dạng chữ")
    if k == "file_contains":
        miss = [m for m in c.get("must_contain") or [] if m.lower() not in text.lower()]
        short = len(text.strip()) < int(c.get("min_chars") or 0)
        if miss or short:
            why = []
            if short:
                why.append(f"chỉ có {len(text.strip())} ký tự, cần {c.get('min_chars')}")
            if miss:
                why.append("thiếu: " + ", ".join(miss))
            return "fail", dict(ev, detail="; ".join(why))
        return "pass", dict(ev, chars=len(text.strip()))
    if k == "json_keys":
        try:
            data = json.loads(text)
        except ValueError as ex:
            return "fail", dict(ev, detail=f"không phải JSON hợp lệ: {str(ex)[:120]}")
        if not isinstance(data, dict):
            return "fail", dict(ev, detail="JSON không phải object")
        miss = [x for x in c["keys"] if x not in data]
        return ("fail", dict(ev, detail="thiếu khoá: " + ", ".join(miss))) if miss else ("pass", ev)
    return "unknown", ev


class AcceptanceStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        team_migration.snapshot_once(self.path, 'team_acceptance', 'contract_version', suffix='.pre-0.283.0')
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)
            columns = {r[1] for r in c.execute('PRAGMA table_info(team_acceptance)')}
            additions = {'contract_version': 'INTEGER NOT NULL DEFAULT 1', 'contract_hash': "TEXT NOT NULL DEFAULT ''",
                'origin': "TEXT NOT NULL DEFAULT 'owner'", 'template_id': "TEXT NOT NULL DEFAULT ''",
                'template_version': 'INTEGER', 'job_revision': 'INTEGER NOT NULL DEFAULT 1',
                'expected_outputs_json': "TEXT NOT NULL DEFAULT '[]'", 'source_refs_json': "TEXT NOT NULL DEFAULT '[]'",
                'source_snapshot_hash': "TEXT NOT NULL DEFAULT ''", 'output_manifest_json': "TEXT NOT NULL DEFAULT '[]'",
                'output_manifest_hash': "TEXT NOT NULL DEFAULT ''"}
            for name, declaration in additions.items():
                if name not in columns:
                    c.execute(f'ALTER TABLE team_acceptance ADD COLUMN {name} {declaration}')
            check_columns = {r[1] for r in c.execute('PRAGMA table_info(team_acceptance_checks)')}
            for name, declaration in {'contract_version': 'INTEGER NOT NULL DEFAULT 1',
                'output_manifest_hash': "TEXT NOT NULL DEFAULT ''", 'source_snapshot_hash': "TEXT NOT NULL DEFAULT ''"}.items():
                if name not in check_columns:
                    c.execute(f'ALTER TABLE team_acceptance_checks ADD COLUMN {name} {declaration}')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS team_acceptance_versions(
                  task_id TEXT NOT NULL, contract_version INTEGER NOT NULL, snapshot TEXT NOT NULL,
                  PRIMARY KEY(task_id,contract_version));
                CREATE TABLE IF NOT EXISTS team_acceptance_decisions(
                  task_id TEXT NOT NULL, decision_key TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT NOT NULL,
                  PRIMARY KEY(task_id,decision_key));
                CREATE TABLE IF NOT EXISTS team_acceptance_intents(
                  task_id TEXT NOT NULL, decision_key TEXT NOT NULL, payload_hash TEXT NOT NULL, body TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending', team_applied INTEGER NOT NULL DEFAULT 0,
                  signal_claimed INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                  PRIMARY KEY(task_id,decision_key));
                CREATE UNIQUE INDEX IF NOT EXISTS team_acceptance_one_intent ON team_acceptance_intents(task_id)
                  WHERE status='pending';
            ''')

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    @staticmethod
    def _row(r) -> Optional[dict]:
        if r is None:
            return None
        d = dict(r)
        d["criteria"] = json.loads(d.pop("criteria_json") or "[]")
        d["confirmed_hashes"] = json.loads(d.pop("confirmed_hashes_json") or "{}")
        for key in ('expected_outputs', 'source_refs', 'output_manifest'):
            d[key] = json.loads(d.pop(key + '_json') or '[]')
        return d

    def get(self, task_id: str) -> Optional[dict]:
        with closing(self._conn()) as c:
            return self._row(c.execute("SELECT * FROM team_acceptance WHERE task_id=?", (task_id,)).fetchone())

    def restore_contract(self, task_id):
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT snapshot FROM team_acceptance_versions WHERE task_id=? ORDER BY contract_version DESC LIMIT 1',
                            (task_id,)).fetchone()
            if row is None:
                return None
            snapshot = json.loads(row['snapshot'])
            for key in ('criteria', 'confirmed_hashes', 'expected_outputs', 'source_refs', 'output_manifest'):
                snapshot[key + '_json'] = _j(snapshot.pop(key))
            columns = list(snapshot)
            c.execute('INSERT OR IGNORE INTO team_acceptance(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')',
                      tuple(snapshot.values()))
            c.commit()
        return self.get(task_id)

    def put(self, task_id: str, team_id: str, criteria: Any, fix_rounds: Any = None, by: str = "owner", *,
            origin='owner', template_id='', template_version=None, job_revision=1, expected_outputs=None,
            source_refs=None, only_if_absent=False) -> dict:
        crit = clean_criteria(criteria)
        rounds = clean_fix_rounds(fix_rounds)
        outputs, sources = clean_outputs(expected_outputs or []), clean_outputs(source_refs or [])
        now = self.clock()
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            old = self._row(c.execute('SELECT * FROM team_acceptance WHERE task_id=?', (task_id,)).fetchone())
            if old and only_if_absent:
                return old
            version = int((old or {}).get('contract_version') or 0) + 1
            if old:
                c.execute('INSERT OR IGNORE INTO team_acceptance_versions VALUES(?,?,?)',
                          (task_id, old['contract_version'], _j(old)))
            c.execute("INSERT INTO team_acceptance(task_id, team_id, criteria_json, fix_rounds, status, set_by, created_at,"
                      " updated_at) VALUES(?,?,?,?, 'pending', ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
                      "criteria_json=excluded.criteria_json, fix_rounds=excluded.fix_rounds, status='pending', "
                      "rounds_used=0, revision=NULL, confirmed_by='', confirmed_at=NULL, confirmed_hashes_json='{}', "
                      "set_by=excluded.set_by, updated_at=excluded.updated_at",
                      (task_id, team_id, _j(crit), rounds, by, now, now))
            c.execute('UPDATE team_acceptance SET contract_version=?, contract_hash=?, origin=?, template_id=?, '
                      'template_version=?, job_revision=?, expected_outputs_json=?, source_refs_json=?, '
                      "source_snapshot_hash=?, output_manifest_json='[]', output_manifest_hash='', result_msg_id=NULL WHERE task_id=?",
                      (version, digest([crit, outputs, sources, rounds]), origin, template_id, template_version,
                       job_revision, _j(outputs), _j(sources), digest(sources), task_id))
            snapshot = self._row(c.execute('SELECT * FROM team_acceptance WHERE task_id=?', (task_id,)).fetchone())
            c.execute('INSERT OR REPLACE INTO team_acceptance_versions VALUES(?,?,?)', (task_id, version, _j(snapshot)))
            c.commit()
        return self.get(task_id)

    def set_state(self, task_id: str, **fields) -> None:
        allowed = {"status", "rounds_used", "revision", "confirmed_by", "confirmed_at", "confirmed_hashes", "result_msg_id",
                   "message_id", 'output_manifest', 'output_manifest_hash', 'job_revision'}
        if set(fields) - allowed:
            raise AcceptanceError("trường không đổi được")
        cols = {(k + '_json' if k in ('confirmed_hashes', 'output_manifest') else k): (_j(v) if k in ('confirmed_hashes', 'output_manifest') else v)
                for k, v in fields.items()}
        with closing(self._conn()) as c:
            c.execute("UPDATE team_acceptance SET " + ", ".join(f"{k}=?" for k in cols) + ", updated_at=? WHERE task_id=?",
                      (*cols.values(), self.clock(), task_id))

    def decision(self, task_id, key, payload_hash):
        with closing(self._conn()) as c:
            row = c.execute('SELECT payload_hash,result FROM team_acceptance_decisions WHERE task_id=? AND decision_key=?',
                            (task_id, key)).fetchone()
        if row:
            return json.loads(row['result']) if row['payload_hash'] == payload_hash else {'ok': False, 'error': 'decision_key_conflict'}
        return None

    def save_decision(self, task_id, key, payload_hash, result):
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO team_acceptance_decisions VALUES(?,?,?,?)', (task_id, key, payload_hash, _j(result)))
            c.execute("UPDATE team_acceptance_intents SET status='complete' WHERE task_id=? AND decision_key=? AND payload_hash=?",
                      (task_id, key, payload_hash))
            c.commit()

    def begin_decision(self, task_id, key, payload_hash, body):
        with closing(self._conn()) as c:
            c.execute('INSERT INTO team_acceptance_intents(task_id,decision_key,payload_hash,body,created_at) VALUES(?,?,?,?,?)',
                      (task_id, key, payload_hash, _j(body), self.clock()))

    def pending_decisions(self, task_id=None):
        with closing(self._conn()) as c:
            rows = c.execute("SELECT * FROM team_acceptance_intents WHERE status='pending'" +
                (' AND task_id=?' if task_id else '') + ' ORDER BY created_at,task_id', (task_id,) if task_id else ()).fetchall()
        return [dict(row, body=json.loads(row['body'])) for row in rows]

    def claim_decision_signal(self, task_id, key):
        with closing(self._conn()) as c:
            return c.execute('UPDATE team_acceptance_intents SET signal_claimed=1 WHERE task_id=? AND decision_key=? '
                             'AND signal_claimed=0', (task_id, key)).rowcount == 1

    def apply_rejection(self, intent):
        """Prepare exactly one repair before the other database may make the job runnable."""
        body, task_id, key = intent['body'], intent['task_id'], intent['decision_key']
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT team_applied FROM team_acceptance_intents WHERE task_id=? AND decision_key=?',
                            (task_id, key)).fetchone()
            if row['team_applied']:
                return
            repair = body.get('repair')
            if repair:
                if body.get('old_step_id'):
                    c.execute("UPDATE team_steps SET status='continued',note='acceptance_rejected',updated_at=? WHERE step_id=?",
                              (self.clock(), body['old_step_id']))
                if body.get('cap_blocked'):
                    cap_record = {'task_id': task_id, 'cap': 'task_steps', 'specs': [repair], 'continues': None,
                                  'decision_key': key}
                    c.execute('INSERT INTO team_events(team_id,kind,by,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)',
                              (body['team_id'], 'cap_blocked', 'host', 'null', _j(cap_record), self.clock()))
                else:
                    seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM team_steps WHERE task_id=?', (task_id,)).fetchone()[0]
                    c.execute('INSERT OR IGNORE INTO team_steps(step_id,task_id,team_id,seq,kind,agent_key,job_revision,brief,'
                              'input_refs_json,parent_step,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (body['repair_id'], task_id, body['team_id'], seq, repair['kind'], repair['agent'], body['revision'],
                               repair['brief'][:8000], _j(repair['inputs']), repair.get('parent'), 'queued', self.clock(), self.clock()))
            c.execute("UPDATE team_acceptance SET status='fixing',updated_at=? WHERE task_id=?", (self.clock(), task_id))
            c.execute('UPDATE team_acceptance_intents SET team_applied=1 WHERE task_id=? AND decision_key=?', (task_id, key))
            c.commit()

    def record(self, task_id: str, revision: int, rnd: int, results: list) -> None:
        now = self.clock()
        with closing(self._conn()) as c:
            acc = self.get(task_id) or {}
            manifest_hash = digest(output_manifest(results))
            for i, (crit, verdict, ev) in enumerate(results):
                c.execute("INSERT INTO team_acceptance_checks(task_id, revision, round, idx, kind, verdict, evidence_json,"
                          " at, contract_version, output_manifest_hash, source_snapshot_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (task_id, int(revision), int(rnd), i, crit["kind"], verdict, _j(ev), now,
                           acc.get('contract_version', 1), manifest_hash, acc.get('source_snapshot_hash', '')))

    def history(self, task_id: str) -> list:
        with closing(self._conn()) as c:
            return [dict(r, evidence=json.loads(r["evidence_json"])) for r in c.execute(
                "SELECT * FROM team_acceptance_checks "
                "WHERE task_id=? ORDER BY id", (task_id,)).fetchall()]


def run_checks(acc: dict, root: Path, run_command: Optional[Callable] = None, since: float = 0.0, message=None) -> list:
    results = []
    for c in acc['criteria']:
        if c['kind'] == 'owner_confirm' and c.get('target') == 'message':
            ev = message_evidence(message)
            results.append((c, 'unknown', ev))
        else:
            results.append((c,) + check_one(c, root, run_command, since))
    for output in acc.get('expected_outputs') or []:
        if output.get('target') == 'message':
            ev = message_evidence(message)
            results.append(({'kind': 'expected_message'}, 'pass' if ev.get('sha256') else 'unknown', ev))
            continue
        c = dict(output, kind='file_exists')
        results.append((c,) + check_one(c, root, run_command, since))
    for source in acc.get('source_refs') or []:
        if source.get('path') and source.get('sha256'):
            ev, _ = _file_evidence(root, source['path'])
            verdict = 'pass' if ev.get('sha256') == source['sha256'] else 'unknown'
            results.append(({'kind': 'source_snapshot', 'path': source['path']}, verdict,
                            dict(ev, sha256=source['sha256'], detail=localefmt.chu('Nguồn đã thay đổi', 'Source snapshot changed'))
                            if verdict == 'unknown' else ev))
    return results


def message_evidence(message):
    if not message:
        return {'target': 'message', 'error': localefmt.chu('Thiếu tin kết quả đã lưu', 'Stored result message is missing')}
    return {'target': 'message', 'message_id': message['id'],
            'sha256': hashlib.sha256(str(message.get('content') or '').encode('utf-8')).hexdigest()}


def output_manifest(results):
    seen = {}
    for _c, _v, ev in results:
        if ev.get('path'):
            key = 'file:' + ev['path']
            seen[key] = {'target': 'file', 'path': ev['path'], 'sha256': ev.get('sha256')}
        elif ev.get('target') == 'message':
            seen['message'] = {k: ev.get(k) for k in ('target', 'message_id', 'sha256')}
        if ev.get('script'):
            seen['checker:' + ev['script']] = {'target': 'file', 'path': ev['script'], 'sha256': ev.get('script_sha256')}
    return [seen[k] for k in sorted(seen)]


def manifest_changes(manifest, root, message=None):
    out = []
    for item in manifest or []:
        if item.get('target') == 'message':
            current = message_evidence(message)
            if current.get('sha256') != item.get('sha256') or current.get('message_id') != item.get('message_id') or not item.get('sha256'):
                out.append('message')
        else:
            p = resolve_in(root, item.get('path', ''))
            if not item.get('sha256') or p is None or not p.is_file() or sha256_file(p) != item['sha256']:
                out.append(item.get('path', ''))
    return out


def summarize(results: list) -> str:
    """'pass' (every criterion), 'fail' (any objective failure), 'review' (unknown or owner_confirm left)."""
    verdicts = [v for _c, v, _e in results]
    if "fail" in verdicts:
        return "fail"
    if "unknown" in verdicts:
        return "review"
    return "pass"


def stale(acc: Optional[dict], history: list, root: Path) -> list:
    """Checked product files that changed since the last passing round or the owner's confirmation (sha256)."""
    if not acc or acc.get("status") not in ("passed", "accepted"):
        return []
    hashes = dict(acc.get("confirmed_hashes") or {})
    seen = {}
    last_round = max([h["round"] for h in history] or [0])
    for h in history:
        ev = h["evidence"]
        if h["round"] == last_round and ev.get("path"):
            seen[ev["path"]] = ev
            if not acc.get("confirmed_hashes") and ev.get("sha256"):
                hashes[ev["path"]] = ev["sha256"]
    out = []
    for rel, sha in hashes.items():
        p = resolve_in(root, rel)
        if p is None or not p.is_file():
            out.append(rel)
            continue
        st = p.stat()
        if st.st_size > HASH_MAX or sha256_file(p) != sha:
            out.append(rel)
    return out


def view(acc: Optional[dict], history: Optional[list] = None, root: Optional[Path] = None) -> Optional[dict]:
    if not acc:
        return None
    hist = history or []
    last_round = max([h["round"] for h in hist] or [0])
    return {"task_id": acc["task_id"], "criteria": acc["criteria"], "fix_rounds": acc["fix_rounds"],
            "rounds_used": acc["rounds_used"], "status": acc["status"], "revision": acc["revision"],
            "confirmed_by": acc["confirmed_by"], "confirmed_at": acc["confirmed_at"], "message_id": acc.get("message_id"),
            "last": [{"idx": h["idx"], "kind": h["kind"], "verdict": h["verdict"], "evidence": h["evidence"]}
                     for h in hist if h["round"] == last_round],
            "rounds": last_round,
            "stale": stale(acc, hist, root) if root is not None else []}
