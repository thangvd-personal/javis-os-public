"""Real scratch workspaces and fixture capabilities never dispatch to a live environment."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="improvement-sandbox-"))
for key, name in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "OBSIDIAN_VAULT_PATH": "vault",
                  "BRAIN_PATH": "brain", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(TEST_ROOT / name)

if importlib.util.find_spec("improvement_sandbox"):
    import improvement_sandbox as SB
else:
    SB = None


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SB, "The isolated trial workspace and fixture boundary are not implemented")
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_ROOT))
        self.workspaces = SB.TrialWorkspace(self.root / "workspaces")

    def fixture(self):
        return {"snapshot_id": "is_fixture", "hash": sha("Authorized fixture\r\n"), "content": "Authorized fixture\r\n",
                "source": {"source_version": "v1", "data_period": "2026-09", "timezone": "Asia/Ho_Chi_Minh",
                           "cutoff": "2026-09-30T23:59:59+07:00", "schema": "text/plain"}}

    def request(self):
        public = self.public_acceptance()
        return {"version": 1, "brain_id": "brain-a", "bundle_hash": sha("bundle"),
                "engine_config_hash": sha("engine"), "contract_hash": sha("contract"), "grant_hash": sha("grant"),
                "baseline_context": {"content": "Keep the owner rule.", "hash": sha("Keep the owner rule."), "coverage": "complete"},
                "public_contract": {"content": public, "hash": SB._hash(public)},
                "assets": [{"id": "ir_skill", "path": "skills/report/SKILL.md", "hash": sha("Skill body"), "content": "Skill body"}],
                "case": {"id": "case-a", "task": "Report the authorized fixture", "fixtures": [self.fixture()]},
                "learning_origin": "trial"}

    def public_acceptance(self):
        return {"version": 1, "objective": "Report the authorized fixture accurately.",
                "output": {"kind": "text", "path": "answer.json", "media_type": "application/json"},
                "criteria": [{"id": "shape", "category": "mandatory", "critical": True,
                              "description": "Return JSON with the value field.", "kind": "json_keys",
                              "config": {"path": "answer.json", "keys": ["value"]}},
                             {"id": "accuracy", "category": "business", "critical": True,
                              "description": "The value accurately reflects the authorized input.", "kind": "independent_check"}]}

    def test_workspace_pins_public_acceptance_without_private_checker_material(self):
        request = self.request()
        public = self.public_acceptance()
        request["public_contract"] = {"content": public, "hash": SB._hash(public)}
        try:
            receipt = self.workspaces.prepare("public-contract", 1, request)
        except ValueError:
            receipt = None
        self.assertIsNotNone(receipt, "The workspace does not materialize a public acceptance contract")
        self.assertEqual(self.workspaces.validate(receipt)["public_contract"]["content"], public)
        for private in ("evaluator", "answer", "checker_id"):
            changed = self.request()
            leaked = self.public_acceptance()
            leaked[private] = "private-evaluator-material"
            changed["public_contract"] = {"content": leaked, "hash": SB._hash(leaked)}
            with self.subTest(private=private), self.assertRaises(ValueError):
                self.workspaces.prepare("leaked-contract", 1, changed)

    def test_public_acceptance_hash_drift_is_rejected_before_materialization(self):
        request = self.request()
        public = self.public_acceptance()
        request["public_contract"] = {"content": public, "hash": SB._hash(public)}
        public["objective"] = "Changed intent after pinning"
        with self.assertRaises(ValueError):
            self.workspaces.prepare("changed-contract", 1, request)

    def test_missing_public_acceptance_is_not_an_executable_input(self):
        request = self.request()
        request.pop("public_contract", None)
        with self.assertRaises(ValueError):
            self.workspaces.prepare("missing-contract", 1, request)

    def test_fixture_broker_uses_exact_grant_version_and_argument_identity(self):
        broker = SB.FixtureBroker([self.fixture()], sha("grant"))
        good = broker.read("fixture.read", {"snapshot_id": "is_fixture"}, "v1", sha("grant"))
        self.assertEqual(good["status"], "fixture_hit")
        self.assertEqual(good["content"], "Authorized fixture\r\n")
        good["content"] = "Caller mutation"
        self.assertEqual(broker.read("fixture.read", {"snapshot_id": "is_fixture"}, "v1", sha("grant"))["content"], "Authorized fixture\r\n")
        for capability, args, version, grant in (
                ("send_message", {"snapshot_id": "is_fixture"}, "v1", sha("grant")),
                ("fixture.read", {"snapshot_id": "../outside"}, "v1", sha("grant")),
                ("fixture.read", {"snapshot_id": "is_fixture", "url": "https://live.invalid"}, "v1", sha("grant")),
                ("fixture.read", {"snapshot_id": "is_fixture"}, "v2", sha("grant")),
                ("fixture.read", {"snapshot_id": "is_fixture"}, "v1", sha("revoked"))):
            with self.subTest(capability=capability, args=args), patch("socket.create_connection", side_effect=AssertionError("Live fallback")):
                self.assertEqual(broker.read(capability, args, version, grant)["status"], "fixture_miss")
        self.assertEqual(sum(event["status"] == "fixture_miss" for event in broker.events), 5)

    def test_fixture_digest_is_verified_before_any_trial(self):
        fixture = self.fixture()
        fixture["content"] = "Different source"
        with self.assertRaises(ValueError):
            SB.FixtureBroker([fixture], sha("grant"))

    def test_fixture_inventory_view_cannot_mutate_the_pinned_broker(self):
        broker = SB.FixtureBroker([self.fixture()], sha("grant"))
        view = broker.fixtures
        view["is_fixture"]["content"] = "Changed behind the receipt"
        view.clear()
        self.assertEqual(broker.read("fixture.read", {"snapshot_id": "is_fixture"}, "v1", sha("grant")).get("content"),
                         "Authorized fixture\r\n")
        with self.assertRaises(AttributeError):
            broker.grant_hash = sha("different-grant")

    def test_prepare_is_pinned_and_each_attempt_has_a_separate_context(self):
        first = self.workspaces.prepare("trial-a", 1, self.request())
        self.assertEqual(first, self.workspaces.prepare("trial-a", 1, self.request()))
        second = self.workspaces.prepare("trial-b", 1, self.request())
        repeat = self.workspaces.prepare("trial-a", 2, self.request())
        self.assertEqual(len({first["path"], second["path"], repeat["path"]}), 3)
        inputs = json.loads(Path(first["input_path"]).read_text(encoding="utf-8"))
        self.assertEqual(inputs["case"]["fixtures"][0]["content"], "Authorized fixture\r\n")
        self.assertNotIn("answer", inputs["case"])
        self.assertNotIn("checker", json.dumps(inputs))
        self.assertTrue(Path(first["output_root"]).is_dir())
        if os.name != "nt":
            self.assertEqual(Path(first["path"]).stat().st_mode & 0o777, 0o700)
        self.workspaces.cleanup("trial-a", 1)
        self.assertFalse(Path(first["path"]).exists())
        self.assertTrue(Path(second["path"]).exists())
        self.assertTrue(Path(repeat["path"]).exists())

    def test_answer_checker_or_secret_fields_never_get_materialized(self):
        for field, value in (("answer", {"expected": "private"}), ("evaluator_refs", ["private-checker"])):
            request = self.request()
            request["case"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.workspaces.prepare("trial-a", 1, request)
        request = self.request()
        request["api_key"] = "must-not-copy"
        with self.assertRaises(ValueError):
            self.workspaces.prepare("trial-a", 1, request)
        self.assertFalse((self.root / "workspaces").exists())

    def test_prepared_input_mutation_or_hash_drift_is_rejected(self):
        first = self.workspaces.prepare("trial-a", 1, self.request())
        changed = self.request()
        changed["case"]["task"] = "Changed task"
        with self.assertRaises(ValueError):
            self.workspaces.prepare("trial-a", 1, changed)
        path = Path(first["input_path"])
        path.chmod(0o600)
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.workspaces.validate(first)

    def test_embedded_credentials_reject_the_source_before_writing_any_inputs(self):
        credential = "Bearer syntheticCredential12345678"
        for location in ("context", "asset", "task", "fixture"):
            request = self.request()
            if location == "task":
                request["case"]["task"] = credential
            else:
                target = {"context": request["baseline_context"], "asset": request["assets"][0],
                          "fixture": request["case"]["fixtures"][0]}[location]
                target.update(content=credential, hash=sha(credential))
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, "insufficient_source"):
                self.workspaces.prepare("trial-a", 1, request)
            self.assertFalse((self.root / "workspaces").exists())

    def test_canonical_asset_path_and_content_are_verified(self):
        for path in ("../agents/owner.md", "C:relative.md", "skills/../agents/owner.md", "skills/CON/SKILL.md"):
            request = self.request()
            request["assets"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspaces.prepare("trial-a", 1, request)
        request = self.request()
        request["assets"][0]["content"] = "Unpinned instructions"
        with self.assertRaises(ValueError):
            self.workspaces.prepare("trial-a", 1, request)

    def test_linked_workspace_root_cannot_touch_an_outside_canary(self):
        outside = self.root / "outside"
        outside.mkdir()
        canary = outside / "keep.txt"
        canary.write_text("Keep outside", encoding="utf-8")
        linked = self.root / "workspaces"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            linked.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises((ValueError, RuntimeError)):
                self.workspaces.prepare("trial-a", 1, self.request())
            with self.assertRaises((ValueError, RuntimeError)):
                self.workspaces.cleanup("trial-a", 1)
            self.assertEqual(canary.read_text(), "Keep outside")
        finally:
            linked.rmdir() if os.name == "nt" else linked.unlink()

    def test_clean_worker_environment_drops_server_tokens_and_turn_authority(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "server-secret", "OPENAI_API_KEY": "other-secret",
                                     "X_JAVIS_TURN": "owner-turn", "JAVIS_STATE_DIR": "production-state",
                                     "ANTHROPIC_BASE_URL": "https://production.invalid", "BETA_TRACING_ENDPOINT": "https://logs.invalid"}):
            environment = SB.worker_environment(self.root / "worker", auth_home=self.root / "auth-home")
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "X_JAVIS_TURN", "ANTHROPIC_BASE_URL", "BETA_TRACING_ENDPOINT"):
            self.assertNotIn(name, environment)
        self.assertNotIn("server-secret", json.dumps(environment))
        self.assertEqual(environment["ENABLE_TOOL_SEARCH"], "false")
        self.assertNotEqual(environment["JAVIS_STATE_DIR"], "production-state")
        self.assertEqual(environment["HOME"], str(self.root / "auth-home"))

    def test_concurrent_prepare_has_one_pinned_workspace_receipt(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(self.workspaces.prepare, "same-trial", 1, self.request()) for _ in range(2)]
            receipts = [future.result(timeout=20) for future in futures]
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(self.workspaces.validate(receipts[0]), self.request())

    def test_cleanup_does_not_follow_an_output_link_to_another_attempt(self):
        first = self.workspaces.prepare("trial-a", 1, self.request())
        other = self.workspaces.prepare("trial-b", 1, self.request())
        canary = Path(other["output_root"]) / "keep.txt"
        canary.write_text("Other attempt", encoding="utf-8")
        linked = Path(first["output_root"]) / "external"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), other["output_root"]], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            linked.symlink_to(other["output_root"], target_is_directory=True)
        self.workspaces.cleanup("trial-a", 1)
        self.assertEqual(canary.read_text(), "Other attempt")
        self.workspaces.validate(other)


if __name__ == "__main__":
    unittest.main()
