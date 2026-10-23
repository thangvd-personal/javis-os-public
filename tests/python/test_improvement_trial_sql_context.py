"""Actual SQLite projections stay read-only, scoped and internally consistent."""
from _paths import ROOT, SERVER  # noqa: F401

import ast
import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRATCH = Path(tempfile.mkdtemp(prefix="trial-sql-context-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(SCRATCH / name)

from evidence_store import redact  # noqa: E402
from resonance_store import Principal  # noqa: E402

if importlib.util.find_spec("improvement_trial_sql_context"):
    import improvement_trial_sql_context as S
else:
    S = None


def schema_literal(filename, name):
    tree = ast.parse((SERVER / filename).read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))


class SqlContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(S, "Strict read-only SQL context is not implemented")
        self.root = Path(tempfile.mkdtemp(prefix="fixture-", dir=SCRATCH))
        self.brain = self.root / "brain"
        self.other = self.root / "other-brain"
        self.brain.mkdir()
        self.other.mkdir()
        self.principal = Principal("owner", "owner-1", str(self.brain.resolve()))
        self.sessions = self.root / "sessions.db"
        self.workflows = self.root / "workflows.db"
        self.writer = sqlite3.connect(self.sessions, isolation_level=None)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.writer.executescript(schema_literal("sessions.py", "_SCHEMA_SQL"))
        for field in ("project_id", "pinned_provider", "pinned_model"):
            self.writer.execute(f"ALTER TABLE sessions ADD COLUMN {field} TEXT")
        self.writer.execute("ALTER TABLE projects ADD COLUMN instructions TEXT")
        self.writer.execute("INSERT INTO sessions(id,brain,engine,model,channel,project_id,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", ("session-1", str(self.brain), "cli", "sonnet", "web", "project-1", 1, 1))
        self.writer.execute("INSERT INTO projects(id,brain,name,instructions,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                            ("project-1", str(self.brain), "Owner Project", " Preserve this owner rule.\r\n", 1, 1))
        self.workflow_writer = sqlite3.connect(self.workflows, isolation_level=None)
        self.addCleanup(self.workflow_writer.close)
        self.workflow_writer.execute("PRAGMA journal_mode=WAL")
        self.workflow_writer.execute("PRAGMA wal_autocheckpoint=0")
        self.workflow_writer.executescript(schema_literal("workflow_runs.py", "_SCHEMA"))
        self.session_lock = threading.RLock()
        self.workflow_lock = threading.RLock()

    def collector(self, **changes):
        session_db = changes.pop("session_db", self.sessions)
        workflow_db = changes.pop("workflow_db", self.workflows)
        sources = {}
        if hasattr(S, "ExistingSQLiteSource"):
            if session_db == self.sessions:
                sources[self.sessions] = S.ExistingSQLiteSource(self.sessions, self.writer, self.session_lock)
            if workflow_db == self.workflows:
                sources[self.workflows] = S.ExistingSQLiteSource(self.workflows, self.workflow_writer, self.workflow_lock)
        if sources or "existing_sources" in changes:
            changes.setdefault("existing_sources", sources)
        return S.SQLiteContextProjection(session_db, workflow_db,
                                         principal_type=changes.pop("principal_type", Principal),
                                         redactor=changes.pop("redactor", redact), **changes)

    def capture(self, **changes):
        collector = changes.pop("collector", None) or self.collector()
        return collector(changes.pop("principal", self.principal), changes.pop("session_id", "session-1"),
                         changes.pop("project_id", "project-1"), **changes)

    def add_file(self, identifier="file-1", *, path="notes/price.md", pinned=0, added_at=1, session=False):
        table, column = ("session_files", "session_id") if session else ("project_files", "project_id")
        parent = "session-1" if session else "project-1"
        self.writer.execute(f"INSERT INTO {table}(id,{column},path,name,pinned,added_at) VALUES(?,?,?,?,?,?)",
                            (identifier, parent, path, "Price", pinned, added_at))

    def test_exact_projection_preserves_full_instructions_and_omits_private_history(self):
        text = " \r\n" + "Owner rule. " * 500 + "TAIL\r\n "
        self.writer.execute("UPDATE projects SET instructions=?", (text,))
        self.writer.execute("INSERT INTO messages(session_id,role,content,ts) VALUES(?,?,?,?)",
                            ("session-1", "user", "PRIVATE HISTORY Authorization: Bearer syntheticcredential0000", 1))
        self.writer.execute("UPDATE sessions SET pinned_provider=?,pinned_model=?", ("anthropic-cli", "sonnet-pinned"))
        self.add_file()
        self.add_file("session-file", path="notes/session.md", session=True)
        self.writer.execute("INSERT INTO project_links VALUES(?,?,?,?,?,?)", ("link-1", "project-1", "https://example.org", "Reference", 1, 2))
        result = self.capture()
        self.assertEqual(set(result), {"version", "session", "project", "project_files", "project_links", "session_files", "session_links", "latest_workflow"})
        self.assertEqual(result["version"], 1)
        self.assertEqual(set(result["session"]), {"id", "brain", "channel", "engine", "model", "project_id", "pinned_provider", "pinned_model", "archived"})
        self.assertEqual(result["session"]["pinned_model"], "sonnet-pinned")
        self.assertEqual(result["project"], {"id": "project-1", "brain": str(self.brain), "name": "Owner Project", "instructions": text})
        self.assertEqual(result["project_files"], [{"id": "file-1", "path": "notes/price.md", "name": "Price", "pinned": 0, "added_at": 1.0}])
        self.assertEqual(result["session_files"][0]["path"], "notes/session.md")
        self.assertEqual(result["project_links"][0]["url"], "https://example.org")
        self.assertIsNone(result["latest_workflow"])
        self.assertNotIn("PRIVATE HISTORY", str(result))

    def test_wal_uncheckpointed_data_is_visible_without_immutable_connection(self):
        self.assertTrue(Path(str(self.sessions) + "-wal").is_file())
        self.assertEqual(self.capture()["session"]["id"], "session-1")
        self.writer.execute("UPDATE projects SET instructions='WAL NEW RULE'")
        self.assertEqual(self.capture()["project"]["instructions"], "WAL NEW RULE")

    def test_unguarded_wal_requires_concrete_source_and_never_attempts_a_new_connection(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        with patch.object(S.sqlite3, "connect", side_effect=AssertionError("unexpected source connect")):
            with self.assertRaisesRegex(ValueError, "sql_context_lifetime_required"):
                self.capture(collector=self.collector(existing_sources={}))

    def test_unmatched_delete_journal_source_is_rejected_without_a_new_connection(self):
        clone = self.root / "unmatched-delete.db"
        connection = sqlite3.connect(clone)
        try:
            self.writer.backup(connection)
            connection.execute("PRAGMA journal_mode=DELETE")
        finally:
            connection.close()
        self.assertEqual(clone.read_bytes()[18:20], b"\x01\x01")
        before = {path.name for path in self.root.iterdir()}
        with patch.object(S.sqlite3, "connect", side_effect=AssertionError("unexpected source connect")):
            with self.assertRaises(Exception) as error:
                self.capture(collector=self.collector(session_db=clone, workflow_db=None, existing_sources={}))
        self.assertIsInstance(error.exception, ValueError)
        self.assertIn("sql_context_lifetime_required", str(error.exception))
        self.assertEqual(before, {path.name for path in self.root.iterdir()})

    def test_nonempty_rollback_journal_is_rejected_before_native_sql_reads(self):
        journal = self.root / "sessions.db-journal"
        journal.write_bytes(b"unresolved hot rollback journal")
        before = journal.read_bytes()
        statements = []
        self.writer.set_trace_callback(statements.append)
        with self.assertRaisesRegex(ValueError, "sql_context_hot_journal"):
            self.capture()
        self.assertEqual(statements, [])
        self.assertEqual(journal.read_bytes(), before)

    def test_concrete_sources_make_no_new_connections_and_preserve_host_settings(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        row_factory = lambda cursor, values: {column[0]: value for column, value in zip(cursor.description, values)}
        self.writer.row_factory = row_factory
        operations = []
        self.writer.set_authorizer(lambda *arguments: operations.append(arguments[0]) or sqlite3.SQLITE_OK)
        progress = []
        self.writer.set_progress_handler(lambda: progress.append(True) or 0, 1)
        before = self.writer.execute("PRAGMA query_only").fetchone()["query_only"]
        with patch.object(S.sqlite3, "connect", side_effect=AssertionError("unexpected source connect")):
            result = self.capture()
        self.assertEqual(result["session"]["id"], "session-1")
        self.assertIs(self.writer.row_factory, row_factory)
        self.assertEqual(self.writer.execute("PRAGMA query_only").fetchone()["query_only"], before)
        self.assertFalse(self.writer.in_transaction)
        self.writer.execute("UPDATE sessions SET title='host mutation still permitted'")
        self.assertIn(sqlite3.SQLITE_UPDATE, operations)
        self.assertTrue(progress)

    def test_concrete_source_lock_prevents_cooperative_host_close_until_capture_finishes(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        connection = sqlite3.connect(self.sessions, isolation_level=None, check_same_thread=False)
        connection.execute("SELECT id FROM sessions").fetchall()
        self.addCleanup(connection.close)
        lock, attempted, closed = threading.Lock(), threading.Event(), threading.Event()
        threads = []

        def host_close():
            attempted.set()
            with lock:
                connection.close()
                closed.set()

        def observe_locked_capture(payload):
            thread = threading.Thread(target=host_close)
            threads.append(thread)
            thread.start()
            self.assertTrue(attempted.wait(1))
            self.assertFalse(closed.wait(0.05), "Capture failed to retain the host close/mutation lock")
            return redact(payload)

        source = S.ExistingSQLiteSource(self.sessions, connection, lock)
        result = self.capture(collector=self.collector(workflow_db=None, existing_sources={self.sessions: source}, redactor=observe_locked_capture))
        self.assertEqual(result["session"]["id"], "session-1")
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertTrue(closed.is_set())

    def test_concrete_source_rejects_wrong_database_closed_connection_and_preexisting_transaction(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        wrong = S.ExistingSQLiteSource(self.sessions, self.workflow_writer, self.session_lock)
        with self.assertRaisesRegex(ValueError, "sql_context_connection_mismatch"):
            self.capture(collector=self.collector(workflow_db=None, existing_sources={self.sessions: wrong}))
        self.writer.execute("BEGIN")
        try:
            with self.assertRaisesRegex(ValueError, "sql_context_transaction_active"):
                self.capture()
            self.assertTrue(self.writer.in_transaction, "Collector rolled back a transaction it did not own")
        finally:
            self.writer.rollback()
        connection = sqlite3.connect(self.sessions, isolation_level=None)
        connection.close()
        closed = S.ExistingSQLiteSource(self.sessions, connection, self.session_lock)
        with self.assertRaises(ValueError):
            self.capture(collector=self.collector(workflow_db=None, existing_sources={self.sessions: closed}))

    def test_same_physical_source_is_reused_once_for_both_context_projections(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        self.writer.executescript(schema_literal("workflow_runs.py", "_SCHEMA"))
        statements = []
        self.writer.set_trace_callback(statements.append)
        result = self.capture(collector=self.collector(workflow_db=self.sessions))
        self.assertEqual(result["session"]["id"], "session-1")
        self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
        self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)

    def test_existing_connection_temp_tables_cannot_shadow_authorized_main_tables(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        self.writer.execute("CREATE TEMP TABLE sessions AS SELECT * FROM main.sessions")
        self.writer.execute("UPDATE temp.sessions SET model='TEMP POISON'")
        self.assertEqual(self.capture()["session"]["model"], "sonnet")

    def test_existing_source_requires_native_connection_and_native_shared_lock(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        for connection, lock in ((object(), self.session_lock), (self.writer, object())):
            with self.subTest(connection=connection, lock=lock), self.assertRaises(ValueError):
                S.ExistingSQLiteSource(self.sessions, connection, lock)

    def test_wal_copy_without_shm_is_rejected_without_creating_reader_artifacts(self):
        clone = self.root / "missing-shm.db"
        clone.write_bytes(self.sessions.read_bytes())
        wal = self.root / "missing-shm.db-wal"
        wal.write_bytes(Path(str(self.sessions) + "-wal").read_bytes())
        self.assertFalse(Path(str(clone) + "-shm").exists())
        before = {path.name for path in self.root.iterdir()}
        contents = {path: path.read_bytes() for path in (clone, wal)}
        rejected = False
        try:
            self.capture(collector=self.collector(session_db=clone, workflow_db=None))
        except ValueError:
            rejected = True
        self.assertEqual(before, {path.name for path in self.root.iterdir()})
        self.assertEqual(contents, {path: path.read_bytes() for path in contents})
        self.assertTrue(rejected, "Incomplete WAL sidecars must fail before SQLite opens")

    def test_closed_wal_mode_database_without_both_sidecars_is_rejected_without_creation(self):
        clone = self.root / "closed-wal.db"
        connection = sqlite3.connect(clone, isolation_level=None)
        try:
            self.writer.backup(connection)
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        finally:
            connection.close()
        self.assertEqual(clone.read_bytes()[18:20], b"\x02\x02")
        self.assertFalse(Path(str(clone) + "-wal").exists())
        self.assertFalse(Path(str(clone) + "-shm").exists())
        before = {path.name for path in self.root.iterdir()}
        contents = clone.read_bytes()
        rejected = False
        try:
            self.capture(collector=self.collector(session_db=clone, workflow_db=None))
        except ValueError:
            rejected = True
        self.assertEqual(before, {path.name for path in self.root.iterdir()})
        self.assertEqual(contents, clone.read_bytes())
        self.assertTrue(rejected, "Persisted WAL mode needs both existing sidecars")

    def test_asset_order_uses_pinned_added_at_then_rowid_and_replay_is_stable(self):
        self.add_file("older-pinned", path="a.md", pinned=1, added_at=1)
        self.add_file("same-time-first", path="b.md", pinned=0, added_at=9)
        self.add_file("same-time-second", path="c.md", pinned=0, added_at=9)
        self.assertEqual([row["id"] for row in self.capture()["project_files"]], ["older-pinned", "same-time-second", "same-time-first"])
        self.assertEqual(self.capture(), self.capture())

    def test_membership_reorder_rules_and_engine_pin_changes_are_semantic(self):
        self.add_file()
        original = self.capture()
        self.add_file("new", path="new.md", added_at=3)
        changed = self.capture()
        self.assertNotEqual(original, changed)
        self.writer.execute("UPDATE project_files SET pinned=1 WHERE id='file-1'")
        reordered = self.capture()
        self.assertNotEqual(changed, reordered)
        self.assertEqual(reordered["project_files"][0]["id"], "file-1")
        self.writer.execute("UPDATE projects SET instructions='Changed owner rule'")
        self.assertNotEqual(reordered, self.capture())
        previous = self.capture()
        self.writer.execute("UPDATE sessions SET pinned_model='other-model'")
        self.assertNotEqual(previous, self.capture())

    def test_unrelated_message_and_session_bookkeeping_changes_do_not_invalidate(self):
        original = self.capture()
        self.writer.execute("INSERT INTO messages(session_id,role,content,ts) VALUES('session-1','assistant','PRIVATE',2)")
        self.writer.execute("UPDATE sessions SET title='New title',msg_count=99,updated_at=999,compact_summary='PRIVATE'")
        self.assertEqual(original, self.capture())

    def test_missing_session_project_mismatch_and_project_cross_brain_fail_closed(self):
        for arguments in ({"session_id": "missing"}, {"project_id": ""}, {"project_id": "another-project"}):
            with self.subTest(arguments=arguments), self.assertRaises((PermissionError, ValueError)):
                self.capture(**arguments)
        self.writer.execute("UPDATE projects SET brain=?", (str(self.other),))
        with self.assertRaises(PermissionError):
            self.capture()

    def test_only_real_owner_with_existing_canonical_root_may_read(self):
        fake = SimpleNamespace(kind="owner", id="owner-1", brain_id=str(self.brain))
        for principal in (fake, Principal("agent", "owner-1", str(self.brain)), Principal("owner", "", str(self.brain)),
                          Principal("owner", "owner-1", "brain"), Principal("owner", "owner-1", str(self.root / "missing")),
                          Principal("owner", "owner-1", str(self.other))):
            with self.subTest(principal=principal), self.assertRaises((PermissionError, ValueError)):
                self.capture(principal=principal)

    def test_legacy_brain_alias_needs_explicit_independently_canonicalized_mapping(self):
        self.writer.execute("UPDATE sessions SET brain='brain'")
        self.writer.execute("UPDATE projects SET brain='brain'")
        with self.assertRaises(PermissionError):
            self.capture()
        collector = self.collector(default_brain_aliases={"brain": self.brain.resolve()})
        self.assertEqual(self.capture(collector=collector)["session"]["brain"], "brain")
        with self.assertRaises((PermissionError, ValueError)):
            self.capture(collector=self.collector(default_brain_aliases={"brain": self.other.resolve()}))
        with self.assertRaises(ValueError):
            self.collector(default_brain_aliases={"brain": Path("relative")})

    def test_no_project_is_explicit_and_requires_matching_empty_request(self):
        self.writer.execute("UPDATE sessions SET project_id=NULL")
        result = self.capture(project_id="")
        self.assertIsNone(result["project"])
        self.assertEqual(result["project_files"], [])
        self.assertEqual(result["project_links"], [])
        with self.assertRaises((PermissionError, ValueError)):
            self.capture()

    def test_non_dashboard_archived_and_unknown_surface_columns_are_unsupported(self):
        for channel in ("agent:writer", "agent:javis-core", "coding:phien", "team:one", "workflow:one", "telegram:one", "adaptive", "dashboard", ""):
            self.writer.execute("UPDATE sessions SET channel=?", (channel,))
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                self.capture()
        self.writer.execute("UPDATE sessions SET channel='web',archived=1")
        with self.assertRaises(ValueError):
            self.capture()
        self.writer.execute("UPDATE sessions SET archived=0")
        self.writer.execute("ALTER TABLE sessions ADD COLUMN pinned_folder TEXT")
        self.writer.execute("UPDATE sessions SET pinned_folder='elsewhere'")
        with self.assertRaises(ValueError):
            self.capture()

    def test_latest_workflow_uses_only_own_brain_and_production_rowid_tiebreak(self):
        for identifier, brain, name, started in (("older", self.brain, "Older", 1), ("first", self.brain, "First", 9),
                                                  ("second", self.brain, "Second", 9), ("other", self.other, "PRIVATE OTHER", 999)):
            self.workflow_writer.execute("INSERT INTO workflow_runs(id,brain,slug,name,started_at,status,input,output) VALUES(?,?,?,?,?,?,?,?)",
                                         (identifier, str(brain), "flow", name, started, "done", "PRIVATE INPUT", "PRIVATE OUTPUT"))
        result = self.capture()
        self.assertEqual(result["latest_workflow"], {"id": "second", "brain": str(self.brain), "name": "Second", "started_at": 9.0, "status": "done"})
        self.assertNotIn("PRIVATE", str(result))
        self.assertIsNone(self.capture(collector=self.collector(workflow_db=None))["latest_workflow"])

    def test_missing_schema_and_views_never_become_empty_success_or_migrate(self):
        self.writer.execute("ALTER TABLE projects RENAME TO projects_actual")
        self.writer.execute("CREATE VIEW projects AS SELECT * FROM projects_actual")
        with self.assertRaises(ValueError):
            self.capture()
        self.writer.execute("DROP VIEW projects")
        self.writer.execute("ALTER TABLE projects_actual RENAME TO projects")
        self.writer.execute("DROP TABLE session_links")
        with self.assertRaises(ValueError):
            self.capture()
        self.assertIsNone(self.writer.execute("SELECT name FROM sqlite_master WHERE name='session_links'").fetchone())
        self.workflow_writer.execute("DROP TABLE workflow_runs")
        with self.assertRaises(ValueError):
            self.capture()

    def test_missing_and_corrupt_database_do_not_create_repair_or_backup_files(self):
        missing = self.root / "not-created.db"
        before = set(self.root.iterdir())
        with self.assertRaises(ValueError):
            self.capture(collector=self.collector(session_db=missing))
        self.assertEqual(before, set(self.root.iterdir()))
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not an SQLite database")
        before = set(self.root.iterdir())
        with self.assertRaises(ValueError):
            self.capture(collector=self.collector(session_db=corrupt))
        self.assertEqual(corrupt.read_bytes(), b"not an SQLite database")
        self.assertEqual(before, set(self.root.iterdir()))

    def test_path_authority_rejects_relative_paths_and_real_junctions(self):
        with self.assertRaises(ValueError):
            self.collector(session_db=Path("relative.db"))
        linked = self.root / "linked"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(self.root)], capture_output=True)
            self.assertEqual(result.returncode, 0)
        else:
            linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.capture(collector=self.collector(session_db=linked / "sessions.db"))

    def test_dangling_sqlite_sidecar_junction_is_rejected_before_sqlite_opens(self):
        clone = self.root / "clone.db"
        with sqlite3.connect(clone) as connection:
            self.writer.backup(connection)
            connection.execute("PRAGMA journal_mode=DELETE")
        source_connection = sqlite3.connect(clone, isolation_level=None)
        self.addCleanup(source_connection.close)
        source_connection.execute("SELECT id FROM sessions").fetchall()
        sidecar = self.root / "clone.db-wal"
        absent_target = self.root / "absent-sidecar-target"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(sidecar), str(absent_target)], capture_output=True)
            self.assertEqual(result.returncode, 0)
        else:
            sidecar.symlink_to(absent_target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "linked_sql_context_path"):
            source = S.ExistingSQLiteSource(clone, source_connection, threading.RLock())
            self.capture(collector=self.collector(session_db=clone, workflow_db=None, existing_sources={clone: source}))

    def test_bad_types_nonfinite_numbers_and_credentials_are_not_returned(self):
        self.add_file()
        for instructions in (sqlite3.Binary(b"binary"), "Authorization: Bearer syntheticcredential123456789"):
            self.writer.execute("UPDATE projects SET instructions=?", (instructions,))
            with self.subTest(instructions=instructions), self.assertRaises(ValueError):
                self.capture()
        self.writer.execute("UPDATE projects SET instructions='safe'")
        for value in (2, "true"):
            self.writer.execute("UPDATE project_files SET pinned=?", (value,))
            with self.subTest(pinned=value), self.assertRaises(ValueError):
                self.capture()
        self.writer.execute("UPDATE project_files SET pinned=0,added_at=?", (float("inf"),))
        with self.assertRaises(ValueError):
            self.capture()

    def test_numeric_primary_key_is_not_accepted_as_a_string_session_identity(self):
        self.writer.execute("DROP TABLE sessions")
        self.writer.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY,brain TEXT,channel TEXT,engine TEXT,model TEXT,"
                            "project_id TEXT,pinned_provider TEXT,pinned_model TEXT,archived INTEGER)")
        self.writer.execute("INSERT INTO sessions VALUES(1,?,'web',NULL,NULL,NULL,NULL,NULL,0)", (str(self.brain),))
        with self.assertRaises(ValueError):
            self.capture(session_id="1", project_id="")

    def test_filesystem_permission_error_does_not_echo_private_source_paths(self):
        real_stat = S._checked_stat

        def denied_database(path, **kwargs):
            if path == self.sessions:
                raise PermissionError("PRIVATE SOURCE PATH")
            return real_stat(path, **kwargs)

        with patch.object(S, "_checked_stat", side_effect=denied_database):
            with self.assertRaises(Exception) as error:
                self.capture()
        self.assertIsInstance(error.exception, ValueError)
        self.assertNotIn("PRIVATE SOURCE PATH", str(error.exception))

    def test_duplicate_membership_and_duplicate_session_rows_are_ambiguous(self):
        self.add_file()
        self.add_file("alias", path="notes/price.md")
        with self.assertRaises(ValueError):
            self.capture()
        self.writer.execute("DELETE FROM project_files")
        self.writer.execute("CREATE TABLE duplicate_sessions AS SELECT * FROM sessions")
        self.writer.execute("INSERT INTO duplicate_sessions SELECT * FROM sessions")
        self.writer.execute("DROP TABLE sessions")
        self.writer.execute("ALTER TABLE duplicate_sessions RENAME TO sessions")
        with self.assertRaises(ValueError):
            self.capture()

    def test_inventory_and_payload_limits_fail_instead_of_truncating(self):
        for i in range(3):
            self.add_file(f"file-{i}", path=f"notes/{i}.md")
        with patch.object(S, "MAX_CONTEXT_ROWS", 2), self.assertRaises(ValueError):
            self.capture()
        self.writer.execute("UPDATE projects SET instructions=?", ("x" * 5000,))
        with patch.object(S, "MAX_CONTEXT_BYTES", 1024), self.assertRaises(ValueError):
            self.capture()

    def test_inplace_redaction_cannot_rewrite_owner_instructions_and_claim_capture_success(self):
        def rewriting_redactor(payload):
            payload["project"]["instructions"] = "[REDACTED]"
            return payload

        with self.assertRaisesRegex(ValueError, "credential_bearing_sql_context"):
            self.capture(collector=self.collector(redactor=rewriting_redactor))

    def test_read_transaction_sees_one_snapshot_during_concurrent_membership_and_rule_edits(self):
        self.assertTrue(hasattr(S, "ExistingSQLiteSource"), "Concrete host source retention is missing")
        before = self.capture()
        writer, changed = self.writer, False

        class ObservedCursor(sqlite3.Cursor):
            def execute(cursor, sql, *args, **kwargs):
                nonlocal changed
                result = super().execute(sql, *args, **kwargs)
                if "FROM main.sessions WHERE id" in sql and not changed:
                    writer.execute("BEGIN IMMEDIATE")
                    writer.execute("UPDATE projects SET instructions='new concurrent rule'")
                    writer.execute("INSERT INTO project_files VALUES('new','project-1','new.md','New',1,99)")
                    writer.execute("COMMIT")
                    changed = True
                return result

        class ObservedConnection(sqlite3.Connection):
            def cursor(connection, *args, **kwargs):
                return super().cursor(*args, **kwargs, factory=ObservedCursor)

        connection = sqlite3.connect(self.sessions, isolation_level=None, factory=ObservedConnection)
        self.addCleanup(connection.close)
        connection.execute("SELECT id FROM sessions").fetchall()
        source = S.ExistingSQLiteSource(self.sessions, connection, threading.RLock())
        with patch.object(S.sqlite3, "connect", side_effect=AssertionError("unexpected source connect")):
            consistent = self.capture(collector=self.collector(workflow_db=None, existing_sources={self.sessions: source}))
        self.assertTrue(changed)
        self.assertEqual(consistent, before)
        after = self.capture()
        self.assertEqual(after["project"]["instructions"], "new concurrent rule")
        self.assertEqual(after["project_files"][0]["id"], "new")

    def test_existing_source_reads_preserve_directory_database_and_wal_contents(self):
        snapshots = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in self.root.iterdir() if path.name.endswith((".db", "-wal"))}
        before = {path.name for path in self.root.iterdir()}
        with patch.object(S.sqlite3, "connect", side_effect=AssertionError("unexpected source connect")):
            self.capture()
        self.assertEqual(before, {path.name for path in self.root.iterdir()})
        self.assertEqual(snapshots, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in snapshots})

    def test_import_and_dependency_validation_do_not_load_config_or_main(self):
        script = ("import sys; sys.path.insert(0, " + repr(str(SERVER)) + "); "
                  "import improvement_trial_sql_context; "
                  "assert 'config' not in sys.modules; assert 'main' not in sys.modules; "
                  "assert 'sessions' not in sys.modules; assert 'workflow_runs' not in sys.modules")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        for invalid in (None, Principal("owner", "one", str(self.brain)), 1):
            with self.subTest(principal_type=invalid), self.assertRaises(ValueError):
                self.collector(principal_type=invalid)
        for invalid in (None, 1):
            with self.subTest(redactor=invalid), self.assertRaises(ValueError):
                self.collector(redactor=invalid)
        async def async_redactor(value):
            return value
        with self.assertRaises(ValueError):
            self.collector(redactor=async_redactor)


if __name__ == "__main__":
    unittest.main()
