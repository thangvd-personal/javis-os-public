"""Observe actual prewarmed plugin metadata without invoking any plugin callback."""
from _paths import ROOT, SERVER  # noqa: F401

import ast
import contextlib
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRATCH = Path(tempfile.mkdtemp(prefix="trial-plugins-"))
for key, child in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "BRAIN_PATH": "brain",
                   "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources"}.items():
    os.environ[key] = str(SCRATCH / child)

import plugins_host as H
import packs
import turn_context
from evidence_store import redact

if importlib.util.find_spec("improvement_trial_plugins"):
    import improvement_trial_plugins as P
else:
    P = None


class PluginProjectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, "Read-only plugin projection is missing")
        self.root = Path(tempfile.mkdtemp(dir=SCRATCH))
        self.brain = self.root / "brain"
        self.bundled = self.root / "bundled"
        self.state = self.root / "state"
        for directory in (self.brain, self.bundled, self.state):
            directory.mkdir()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for obj, key, value in ((H, "BUNDLED_DIR", self.bundled), (H, "GLOBAL_DIR", self.state / "plugins"),
                                (H, "_STATE_PATH", self.state / "plugins.json"),
                                (packs, "PACKS_DIR", self.state / "packs"), (packs, "LEDGER", self.state / "packs.json")):
            self.stack.enter_context(patch.object(obj, key, value))
        self.stack.enter_context(patch.dict(os.environ, {"JAVIS_ENABLE_USER_PLUGINS": "",
            "JAVIS_ENABLE_VAULT_PLUGINS": "", "JAVIS_DISABLE_PACKS": ""}))
        self.stack.enter_context(patch.object(H, "_cache", {}))
        self.stack.enter_context(patch.object(H, "_MANIFEST_CACHE", {}))
        self.stack.enter_context(patch.object(H, "_STATE_CACHE", {"sig": None, "data": None}))
        self.stack.enter_context(patch.object(packs, "_cache", {"sig": None, "packs": [], "connectors": {}}))
        token = turn_context.bind(turn_context.make("dashboard", la_chu=True, session_id="session-1"))
        self.addCleanup(turn_context.reset, token)
        token = H.localefmt.dat_ngon_ngu_yeu_cau("")
        self.addCleanup(H.localefmt.bo_ngon_ngu_yeu_cau, token)

    def plugin(self, slug="alpha", *, source="bundled", enabled=True, tools=("demo_ping",), code=None, extra=""):
        base = {"bundled": self.bundled, "user": self.state / "plugins", "vault": self.brain / "plugins",
                "legacy": self.brain / "Javis" / "plugins"}[source]
        path = base / slug
        path.mkdir(parents=True, exist_ok=True)
        manifest = {"name": "Alpha", "description": "base description", "enabled": enabled,
                    "tools": list(tools), "hooks": [], "min_mode": "safe"}
        (path / "plugin.yaml").write_text(json.dumps(manifest) + extra, encoding="utf-8")
        if code is None:
            code = "def ping(args, ctx):\n    return 'pong'\ndef register(ctx):\n"
            code += "".join(f"    ctx.register_tool({name!r}, 'description', ping)\n" for name in tools)
            if not tools:
                code += "    pass\n"
        (path / "plugin.py").write_text(code, encoding="utf-8")
        return path

    def warm(self):
        # Explicit setup may load trusted scratch plugin code; capture itself must never do so.
        H.plugin_tools("full", str(self.brain))

    def capture(self, settings_ui=None):
        with patch.object(H, "_load_all", side_effect=AssertionError("loader called")), \
             patch.object(H, "plugin_tools", side_effect=AssertionError("tools called")), \
             patch.object(H, "describe", side_effect=AssertionError("describe called")), \
             patch.object(H, "_iter_plugin_dirs", side_effect=AssertionError("discovery called")), \
             patch.object(H.localefmt.cfgmod, "read_settings", side_effect=AssertionError("settings read")):
            return P.PluginCapabilityProjection(host=H, packs_host=packs, redactor=redact).capture(
                self.brain, observed_session={"id": "session-1", "brain": str(self.brain), "channel": "web",
                    "engine": "cli", "model": "sonnet", "project_id": None, "pinned_provider": None,
                    "pinned_model": None, "archived": 0},
                settings_ui={"ui_lang": "vi"} if settings_ui is None else settings_ui)

    def test_real_prewarmed_metadata_matches_production_without_load_or_callbacks(self):
        self.plugin()
        self.plugin("off", enabled=False)
        self.warm()
        visible = {item["fn"] for item in H.plugin_tools("full", str(self.brain))[0]}
        expected = []
        for row in H.describe(str(self.brain)):
            tools = row["tools"]
            if row["loaded"] and tools:
                tools = [name for name in tools if name in visible]
                if not tools:
                    continue
            expected.append({key: tools if key == "tools" else row[key] for key in
                ("slug", "name", "source", "description", "enabled", "loaded", "gated", "min_mode", "tools", "hooks", "error")})
        value = self.capture()
        self.assertEqual(value["plugins"], expected)
        self.assertEqual(value["visible_tools"], sorted(visible))
        self.assertNotIn("pong", json.dumps(value))
        self.assertNotIn("complete", value)

    def test_empty_absence_observed_without_creating_anything(self):
        self.warm()
        before = sorted(str(path) for path in self.root.rglob("*"))
        value = self.capture()
        self.assertEqual(value["plugins"], [])
        self.assertTrue(any(not row["present"] for row in value["directories"]))
        self.assertEqual(before, sorted(str(path) for path in self.root.rglob("*")))

    def test_unwarmed_or_stale_cache_is_unsupported_without_loading(self):
        self.plugin()
        with self.assertRaisesRegex(ValueError, "runtime_unavailable"):
            self.capture()
        self.warm()
        (self.state / "plugins.json").write_text('{"disabled":["alpha"]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "runtime_stale"):
            self.capture()

    def test_source_same_stat_edit_disagrees_with_loaded_code(self):
        path = self.plugin()
        self.warm()
        file = path / "plugin.py"
        info = file.stat()
        file.write_text(file.read_text().replace("pong", "evil"), encoding="utf-8")
        os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "runtime_code_mismatch"):
            self.capture()

    def test_custom_visibility_never_executes_or_silently_hides(self):
        self.plugin(code="def visible(root):\n    raise AssertionError('must not run')\ndef ping(a,c):\n    return 'ok'\ndef register(ctx):\n    ctx.register_tool('demo_ping','desc',ping,visible_fn=visible)\n")
        H._load_all(str(self.brain))
        with self.assertRaisesRegex(ValueError, "unsupported_plugin_visibility"):
            self.capture()

    def test_vault_shadow_order_and_layout_drift_are_observed(self):
        self.plugin("alpha")
        self.plugin("beta", source="user", tools=("user_tool",))
        self.plugin("alpha", source="legacy", tools=("legacy_tool",))
        self.warm()
        value = self.capture()
        self.assertEqual([(row["slug"], row["source"]) for row in value["plugins"]], [("alpha", "vault"), ("beta", "user")])
        self.assertEqual(value["layout"], "Javis/plugins")
        (self.brain / "plugins").mkdir()
        with self.assertRaisesRegex(ValueError, "runtime_stale"):
            self.capture()

    def test_locale_uses_actual_request_then_nonsecret_settings(self):
        path = self.plugin()
        manifest = json.loads((path / "plugin.yaml").read_text())
        manifest.update(name_en="English", description_en="English description")
        (path / "plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        self.warm()
        self.assertEqual(self.capture({"ui_lang": "en-US"})["plugins"][0]["name"], "English")
        token = H.localefmt.dat_ngon_ngu_yeu_cau("vi")
        try:
            value = self.capture({"ui_lang": "en"})
            self.assertEqual(value["plugins"][0]["name"], "Alpha")
            self.assertEqual(value["plugins"][0]["description"], "base description")
        finally:
            H.localefmt.bo_ngon_ngu_yeu_cau(token)

    def test_pack_inventory_rejected_explicitly_including_disabled_pack(self):
        (packs.PACKS_DIR / "demo" / "plugins").mkdir(parents=True)
        packs.LEDGER.write_text('{"demo":{"enabled":false}}', encoding="utf-8")
        self.warm()
        with self.assertRaisesRegex(ValueError, "unsupported_pack_context"):
            self.capture()

    def test_duplicate_reserved_tools_use_first_visible_runtime_plugin(self):
        self.plugin("alpha", tools=("demo_ping", "javis_read_file"))
        self.plugin("beta", tools=("demo_ping", "other_tool"))
        self.warm()
        self.assertEqual(self.capture()["visible_tools"], ["demo_ping", "other_tool"])

    def test_agent_missing_or_nonowner_turn_rejected(self):
        self.warm()
        for value in (turn_context.make("dashboard", la_chu=False),
                      turn_context.make("telegram", la_chu=True),
                      turn_context.make("dashboard", la_chu=True, agent={"key": "agent-1"})):
            token = turn_context.bind(value)
            try:
                with self.assertRaisesRegex(ValueError, "unsupported_plugin_surface"):
                    self.capture()
            finally:
                turn_context.reset(token)

    def test_absent_ambient_turn_uses_observed_web_session_profile(self):
        self.plugin()
        self.warm()
        token = turn_context.bind(None)
        try:
            self.assertEqual(self.capture()["visible_tools"], ["demo_ping"])
        finally:
            turn_context.reset(token)

    def test_real_bundled_goal_owner_profile_matches_actual_main_capability_branch(self):
        actual = ROOT / "system" / "plugins"
        self.stack.enter_context(patch.object(H, "BUNDLED_DIR", actual))
        disabled = [directory.name for directory in actual.iterdir()
                    if directory.is_dir() and directory.name not in ("datetime-vn", "javis-goal")]
        H._STATE_PATH.write_text(json.dumps({"disabled": disabled}), encoding="utf-8")
        # Another enabled agent makes the OUTSIDE-turn predicate true. The
        # selected ordinary owner turn must still hide Goal without consulting it.
        import resonance_store as RS
        self.stack.enter_context(patch.object(RS, "STATE_DIR", self.state))
        self.stack.enter_context(patch.object(H.localefmt.cfgmod, "STATE_DIR", self.state))
        store = RS.GoalStore(self.state / "resonance.sqlite3")
        enabled = store.agent_set_enabled(RS.Principal("owner", "owner-1", str(self.brain)), "another-agent", True)
        self.assertTrue(enabled["enabled"])
        self.warm()
        module = sys.modules["javis_plugin_bundled_javis_goal"]
        token = turn_context.bind(None)
        try:
            self.assertTrue(module._visible(str(self.brain)))
        finally:
            turn_context.reset(token)
        tree = ast.parse((SERVER / "main.py").read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_gather_capabilities")
        namespace = {"Path": Path, "_brain_root": lambda _: str(self.brain), "plugins_host": H,
                     "_agents_dir": lambda _: self.brain / "missing-agents",
                     "_workflows_dir": lambda _: self.brain / "missing-workflows"}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SERVER / "main.py"), "exec"), namespace)
        expected = namespace["_gather_capabilities"]("unused", skills=[])["plugins"]
        before = sorted(str(path) for path in self.state.rglob("*"))
        with patch.object(RS, "GoalStore", side_effect=AssertionError("capture constructed GoalStore")):
            value = self.capture()
        self.assertEqual(value["plugins"], expected)
        self.assertNotIn("javis_goal", value["visible_tools"])
        self.assertNotIn("javis-goal", [row["slug"] for row in value["plugins"]])
        self.assertEqual(before, sorted(str(path) for path in self.state.rglob("*")))

    def test_edited_goal_callback_not_accepted_as_known_predicate(self):
        # Test a source+runtime coherent substitute: matching just name/path is insufficient.
        path = self.plugin("javis-goal", tools=("javis_goal",), code=
            "def _visible(root):\n    return True\ndef ping(a,c):\n    return 'ok'\ndef register(ctx):\n    ctx.register_tool('javis_goal','desc',ping,visible_fn=_visible)\n")
        H._load_all(str(self.brain))
        with patch.object(P, "_GOAL_FILE", path / "plugin.py"):
            with self.assertRaisesRegex(ValueError, "unsupported_plugin_visibility"):
                self.capture()

    def test_loaded_helper_edit_with_unchanged_host_signature_is_rejected(self):
        path = self.plugin(code="from . import helper\ndef ping(a,c):\n    return helper.value()\ndef register(ctx):\n    ctx.register_tool('demo_ping','desc',ping)\n")
        helper = path / "helper.py"
        helper.write_text("def value():\n    return 'old'\n", encoding="utf-8")
        self.warm()
        info = helper.stat()
        helper.write_text("def value():\n    return 'new'\n", encoding="utf-8")
        os.utime(helper, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "runtime_code_mismatch"):
            self.capture()

    def test_typed_source_hashes_include_helpers_without_source_text(self):
        path = self.plugin()
        helper = path / "unused.py"
        helper.write_bytes(b"# public helper description\r\n")
        self.warm()
        value = self.capture()
        record = next(row for row in value["sources"] if row["relative"].endswith("unused.py"))
        self.assertEqual(record["hash"], hashlib.sha256(helper.read_bytes()).hexdigest())
        self.assertNotIn("public helper description", json.dumps(value))
        helper.unlink()
        self.assertNotEqual(value, self.capture())

    def test_credentials_and_malformed_source_fail_closed(self):
        path = self.plugin()
        self.warm()
        (path / "helper.py").write_text("KEY='sk-" + "A" * 36 + "'", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "credential_bearing"):
            self.capture()
        (path / "helper.py").unlink()
        (path / "plugin.yaml").write_text("enabled: true\nenabled: false\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid_plugin_metadata"):
            self.capture()

    def test_symlink_source_rejected(self):
        path = self.plugin()
        self.warm()
        try:
            (path / "helper.py").symlink_to(path / "plugin.py")
        except OSError:
            self.skipTest("symlink unavailable")
        with self.assertRaises((ValueError, PermissionError)):
            self.capture()

    def test_membership_change_during_observation_fails(self):
        self.plugin()
        self.warm()
        original = P._Observation.directory
        count = [0]
        def mutate(observation, root, relative):
            result = original(observation, root, relative)
            count[0] += 1
            if count[0] == 1:
                self.plugin("new", enabled=False)
            return result
        with patch.object(P._Observation, "directory", mutate):
            with self.assertRaisesRegex(ValueError, "stale|changed"):
                self.capture()

    def test_all_default_bundled_capabilities_match_production(self):
        self.stack.enter_context(patch.object(H, "BUNDLED_DIR", ROOT / "system" / "plugins"))
        self.warm()
        visible = {row["fn"] for row in H.plugin_tools("full", str(self.brain))[0]}
        expected = []
        for row in H.describe(str(self.brain)):
            tools = row["tools"]
            if row["loaded"] and tools:
                tools = [name for name in tools if name in visible]
                if not tools:
                    continue
            expected.append({key: tools if key == "tools" else row[key] for key in
                ("slug", "name", "source", "description", "enabled", "loaded", "gated", "min_mode", "tools", "hooks", "error")})
        self.assertEqual(self.capture()["plugins"], expected)

    def test_state_env_enable_and_removal_keep_production_describe_labels(self):
        self.plugin("alpha", enabled=False)
        self.plugin("beta", source="user", tools=("user_tool",))
        self.plugin("removed", tools=("removed_tool",))
        H._STATE_PATH.write_text('{"enabled":["alpha"],"removed":["removed"]}', encoding="utf-8")
        os.environ["JAVIS_ENABLE_VAULT_PLUGINS"] = "off"
        self.warm()
        value = self.capture()
        rows = {row["slug"]: row for row in value["plugins"]}
        self.assertTrue(rows["alpha"]["loaded"])
        self.assertTrue(rows["beta"]["enabled"])
        self.assertFalse(rows["beta"]["loaded"])
        self.assertTrue(rows["beta"]["gated"])
        self.assertFalse(rows["removed"]["enabled"])
        os.environ["JAVIS_ENABLE_USER_PLUGINS"] = "true"
        with self.assertRaisesRegex(ValueError, "runtime_stale"):
            self.capture()
        self.warm()
        self.assertIn("user_tool", self.capture()["visible_tools"])

    def test_coherent_static_runtime_metadata_change_changes_projection(self):
        self.plugin()
        self.warm()
        first = self.capture()
        lp = H._cache[str(self.brain) + "|True"]["plugins"][0]
        lp.tools[0]["description"] = "New actual runtime description"
        self.assertNotEqual(first["runtime"], self.capture()["runtime"])
        lp.ctx.vault_root = str(self.root / "foreign")
        with self.assertRaisesRegex(ValueError, "runtime_source_mismatch"):
            self.capture()

    def test_unknown_loader_error_and_closure_are_not_silent_fallbacks(self):
        self.plugin(code="def register(ctx):\n    private = 'hidden'\n    def ping(a,c):\n        return private\n    ctx.register_tool('demo_ping','desc',ping)\n")
        self.warm()
        with self.assertRaisesRegex(ValueError, "unsupported_plugin_closure"):
            self.capture()
        H._cache[str(self.brain) + "|True"]["errors"]["alpha"] = "Private runtime details"
        with self.assertRaisesRegex(ValueError, "runtime_errors"):
            self.capture()

    def test_source_limits_and_unsafe_host_dependencies_fail_closed(self):
        self.plugin()
        self.warm()
        with patch.object(P, "MAX_PLUGIN_FILES", 1):
            with self.assertRaisesRegex(ValueError, "sources_too_large"):
                self.capture()
        with self.assertRaises(TypeError):
            P.PluginCapabilityProjection(host=object(), packs_host=packs, redactor=redact)
        async def bad_redactor(value):
            return value
        with self.assertRaises(TypeError):
            P.PluginCapabilityProjection(host=H, packs_host=packs, redactor=bad_redactor)

    def test_opaque_or_crossbrain_surface_and_extra_ui_fields_rejected(self):
        self.warm()
        collector = P.PluginCapabilityProjection(host=H, packs_host=packs, redactor=redact)
        with self.assertRaisesRegex(ValueError, "unsupported_plugin_surface"):
            collector.capture(self.brain, observed_session={"channel": "web", "brain": str(self.brain)}, settings_ui={"ui_lang": "vi"})
        with self.assertRaisesRegex(ValueError, "invalid_plugin_ui_projection"):
            self.capture({"ui_lang": "vi", "api_key": "private"})

    def test_hardlinked_code_is_rejected_without_reading_alias(self):
        path = self.plugin()
        self.warm()
        os.link(path / "plugin.py", path / "helper.py")
        with self.assertRaisesRegex(ValueError, "linked_plugin_source"):
            self.capture()

    def test_host_semantic_sources_are_typed_dependencies(self):
        self.warm()
        value = self.capture()
        names = {Path(row["root"]) / row["relative"] for row in value["sources"] if row["present"]}
        self.assertTrue({SERVER / name for name in ("plugins_host.py", "packs.py", "localefmt.py", "lang_registry.py", "turn_context.py")} <= names)

    def test_yaml_alias_metadata_rejected_without_expanding(self):
        path = self.plugin()
        self.warm()
        (path / "plugin.yaml").write_text("name: &n Alpha\ndescription: *n\nenabled: true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid_plugin_metadata"):
            self.capture()


if __name__ == "__main__":
    unittest.main()
