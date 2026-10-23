"""Owner metadata routes are lazy and isolated from engines, live brains and server startup."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="imp-api-"))
for key, value in {"JAVIS_STATE_DIR": TEST_ROOT / "state", "BRAINS_DIR": TEST_ROOT / "brains",
                   "OBSIDIAN_VAULT_PATH": TEST_ROOT / "vault", "BRAIN_PATH": TEST_ROOT / "brain",
                   "SOURCES_PATH": TEST_ROOT / "sources"}.items():
    os.environ[key] = str(value)
(TEST_ROOT / "brains" / "Brain Default").mkdir(parents=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import resonance_store as RS  # noqa: E402
from improvement_blobs import RevisionBlobs  # noqa: E402
from improvement_store import ImprovementStore, read_existing  # noqa: E402

if importlib.util.find_spec("improvement_api"):
    import improvement_api as IA
else:
    IA = None


class ImprovementApiTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(IA, "Owner improvement routes are not implemented")
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_ROOT))
        self.brain = self.root / "brain"
        self.brain.mkdir()
        self.other = self.root / "other"
        self.other.mkdir()
        path = self.brain / "skills" / "report" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: Report\n---\nOwner body\n", encoding="utf-8")
        self.database = self.root / "resonance.sqlite3"
        self.wrapper = None
        self.store_calls = 0

        def read_store():
            return read_existing(self.database, RevisionBlobs(self.root / "assets"))

        def write_store():
            self.store_calls += 1
            if self.wrapper is None:
                self.wrapper = ImprovementStore(RS.GoalStore(self.database), RevisionBlobs(self.root / "assets"))
            return self.wrapper

        def resolve_brain(value):
            if value == "brain":
                return str(self.brain)
            if value == "other":
                return str(self.other)
            raise ValueError("unknown_brain")

        app = FastAPI()
        IA.register(app, IA.ImprovementApiDeps(read_store, write_store, resolve_brain))
        self.client = TestClient(app)

    def capture(self, **updates):
        return self.client.post("/improvement-targets/capture", json={
            "kind": "skill", "slug": "report", "event_key": "capture-api-0001", **updates})

    def test_get_default_and_targets_creates_no_state(self):
        response = self.client.get("/improvement-policy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["policy"], {"mode": "suggest", "version": 0})
        self.assertFalse(response.json()["capabilities"]["trial"])
        self.assertFalse(response.json()["capabilities"]["activation"])
        self.assertEqual(self.client.get("/improvement-targets").json()["targets"], [])
        self.assertEqual(self.store_calls, 0)
        self.assertFalse(self.database.exists())

    def test_capture_history_and_repeated_request_are_safe(self):
        response = self.capture()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["capture"]
        self.assertEqual(result, self.capture().json()["capture"])
        target_id = result["target_id"]
        response = self.client.get(f"/improvement-targets/{target_id}/revisions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["revisions"]), 1)
        self.assertNotIn("Owner body", response.text)
        self.assertNotIn(".enc", response.text)
        cross_brain = self.client.get(f"/improvement-targets/{target_id}/revisions", params={"brain": "other"})
        self.assertEqual(cross_brain.status_code, 404)

    def test_payload_cannot_claim_actor_or_protected_source_fields(self):
        for extra in ({"actor": "owner"}, {"source_hash": "fake"}, {"brain": "other"}, {"ownership": "user_managed"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.capture(**extra).status_code, 422)
        self.assertEqual(self.store_calls, 0)

    def test_off_and_unsupported_policy_do_not_run_or_capture(self):
        response = self.client.put("/improvement-policy", json={"mode": "off", "expected_version": 0,
                                                               "event_key": "policy-off-0001"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.capture().status_code, 403)
        self.assertEqual(self.client.put("/improvement-policy", json={"mode": "trial", "expected_version": 1,
                         "event_key": "policy-trial-0001"}).status_code, 422)
        self.assertEqual(self.client.get("/improvement-targets").json()["targets"], [])

    def test_stale_generation_and_unknown_brain_do_not_mutate_default(self):
        first = self.capture().json()["capture"]
        stale = self.client.post(f"/improvement-targets/{first['target_id']}/retire",
                                 json={"expected_generation": 42, "event_key": "retire-stale-0001"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get("/improvement-policy", params={"brain": "missing"}).status_code, 404)
        self.assertEqual(self.client.put("/improvement-policy", params={"brain": "missing"},
                         json={"mode": "off", "expected_version": 0, "event_key": "wrong-brain-0001"}).status_code, 404)
        self.assertEqual(self.client.get("/improvement-policy").json()["policy"]["mode"], "suggest")

    def test_retire_is_explicit_and_idempotent(self):
        first = self.capture().json()["capture"]
        url = f"/improvement-targets/{first['target_id']}/retire"
        payload = {"expected_generation": 1, "event_key": "retire-api-0001"}
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), self.client.post(url, json=payload).json())
        self.assertEqual(self.client.get("/improvement-targets").json()["targets"], [])


class MainSecurityTests(unittest.TestCase):
    def test_installed_status_reads_never_initialize_the_runtime_store(self):
        import main
        state = Path(tempfile.mkdtemp(prefix="installed-", dir=TEST_ROOT))
        wrapper = ImprovementStore(RS.GoalStore(state / "resonance.sqlite3"), RevisionBlobs(state / "improvement"))
        principal = RS.Principal("owner", "owner", str((TEST_ROOT / "brains" / "Brain Default").resolve()))
        wrapper.set_policy(principal, "off", expected_version=0, event_key="installed-policy")
        with patch.object(main.cfgmod, "STATE_DIR", state), \
             patch.object(main, "_resonance_store", side_effect=AssertionError("Status initialized runtime store")), \
             patch.object(main.cfgmod, "gate_active", return_value=True), \
             patch.object(main.cfgmod, "valid_session", return_value=True):
            client = TestClient(main.app, base_url="http://testserver")
            response = client.get("/improvement-policy", cookies={"javis_session": "owner-cookie"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["policy"], {"mode": "off", "version": 1})
            self.assertEqual(client.get("/improvement-targets", cookies={"javis_session": "owner-cookie"}).json()["targets"], [])

    def test_real_status_does_not_migrate_an_existing_legacy_database(self):
        self.assertIsNotNone(IA)
        import main
        state = Path(tempfile.mkdtemp(prefix="legacy-", dir=TEST_ROOT))
        database = state / "resonance.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE legacy_marker(value TEXT)")
        with patch.object(main.cfgmod, "STATE_DIR", state), patch.object(RS, "STATE_DIR", state), \
             patch.object(main, "_RESONANCE_STORE", None), \
             patch.object(main.cfgmod, "gate_active", return_value=True), \
             patch.object(main.cfgmod, "valid_session", return_value=True):
            client = TestClient(main.app, base_url="http://testserver")
            response = client.get("/improvement-policy", cookies={"javis_session": "owner-cookie"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["policy"], {"mode": "suggest", "version": 0})
        with sqlite3.connect(database) as connection:
            tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertEqual(tables, ["legacy_marker"], "A metadata status read must not run GoalStore migrations")

    def test_real_main_keeps_auth_and_csrf_gates_on_new_routes(self):
        self.assertIsNotNone(IA, "Improvement routes must exist before checking main wiring")
        import main
        client = TestClient(main.app, base_url="http://testserver")
        with patch.object(main.cfgmod, "gate_active", return_value=True), \
             patch.object(main.cfgmod, "valid_session", side_effect=lambda value: value == "owner-cookie"), \
             patch.object(main, "_token_ok", return_value=False):
            response = client.get("/improvement-policy")
            self.assertEqual(response.status_code, 401)
            response = client.put("/improvement-policy", headers={"origin": "https://foreign.example"},
                                  cookies={"javis_session": "owner-cookie"},
                                  json={"mode": "off", "expected_version": 0, "event_key": "csrf-rejected-0001"})
            self.assertEqual(response.status_code, 403)
            response = client.get("/improvement-policy", cookies={"javis_session": "owner-cookie"})
            self.assertEqual(response.status_code, 200, response.text)
            response = client.get("/improvement-policy", params={"brain": str(TEST_ROOT / "missing")},
                                  cookies={"javis_session": "owner-cookie"})
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
