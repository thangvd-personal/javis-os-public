"""Read-only synthetic settings/binary fixtures; no CLI, authentication or model calls."""
from _paths import ROOT, SERVER  # noqa: F401

import copy
import ast
import contextlib
import io
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_ROOT = Path(tempfile.mkdtemp(prefix="trial-selection-"))
for key, child in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                   "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources",
                   "JAVIS_SESSIONS_DB": "state/sessions.sqlite3"}.items():
    os.environ[key] = str(TEST_ROOT / child)

import improvement_claude as native

if importlib.util.find_spec("improvement_trial_selection"):
    import improvement_trial_selection as selection
else:
    selection = None


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(selection, "Strict host selection reader is not implemented")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = self.root / "settings.json"
        self.binary = self.root / "synthetic-claude.exe"
        self.binary.write_bytes(b"synthetic inert runtime; never executable")
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.config = {"model": {"main": {"provider": "anthropic-cli", "model": "claude-opus-5-5"},
                                  "reasoning": "medium", "fast": False, "claude_auth": "subscription"}}
        self.write()
        self.addCleanup(patch.stopall)
        patch.object(native, "VERIFIED_NATIVE_RUNTIMES", {(sys.platform, "synthetic-version", self.digest, "synthetic-sdk")}).start()
        patch.object(selection.importlib.metadata, "version", return_value="synthetic-sdk").start()
        patch.object(native, "profile_hash", return_value="a" * 64).start()
        patch("subprocess.run", side_effect=AssertionError("No binary or authentication execution permitted")).start()
        patch("config.read_settings", side_effect=AssertionError("No config fallback/decryption permitted")).start()

    def write(self, value=None):
        self.settings.write_text(json.dumps(self.config if value is None else value), encoding="utf-8")

    def expected(self):
        return {"provider": "anthropic-cli", "model": "claude-opus-5-5", "reasoning": "medium",
                "auth_mode": "subscription", "fast": False}

    def pin(self):
        return selection.create_pin(self.settings, self.binary)

    def test_selected_main_wins_over_explicit_different_legacy(self):
        self.config["model"].update(engine="openrouter", claude_model="other-legacy-model")
        self.write()
        self.assertEqual(selection.read_selection(self.settings), self.expected())

    def test_explicit_legacy_cli_is_preserved_without_default_model(self):
        for main in (None, {}, {"provider": "", "model": ""}):
            self.config["model"].update(main=main, engine="cli", claude_model="explicit-legacy-alias")
            self.write()
            self.assertEqual(selection.read_selection(self.settings)["model"], "explicit-legacy-alias")
        for fields in ({"engine": "cli"}, {"engine": "cli", "claude_model": ""},
                       {"claude_model": "explicit-but-no-engine"}, {"engine": "openrouter", "claude_model": "model"}):
            self.write({"model": fields})
            with self.assertRaises(ValueError):
                selection.read_selection(self.settings)

    def test_partial_main_selection_cannot_fall_back(self):
        for main in ({"provider": "anthropic-cli", "model": ""}, {"provider": "", "model": "stray-model"},
                     {"provider": 3, "model": "model"}, [], "anthropic-cli"):
            self.config["model"].update(main=main, engine="cli", claude_model="fallback-model")
            self.write()
            with self.assertRaises(ValueError):
                selection.read_selection(self.settings)

    def test_only_documented_missing_defaults_are_applied(self):
        for key in ("reasoning", "fast", "claude_auth"):
            self.config["model"].pop(key)
        self.write()
        self.assertEqual(selection.read_selection(self.settings), {**self.expected(), "reasoning": "off"})
        with self.assertRaises(ValueError):
            self.pin()

    def test_semantic_hash_ignores_unrelated_synthetic_secrets_but_detects_selection_drift(self):
        first = selection.selection_hash(selection.read_selection(self.settings))
        first_pin = self.pin()
        self.config["model"]["anthropic_api_key"] = "synthetic-unrelated-secret-a"
        self.config["auth"] = {"token": "synthetic-unrelated-secret-b"}
        self.write()
        self.assertEqual(first, selection.selection_hash(selection.read_selection(self.settings)))
        self.assertEqual(first_pin, self.pin())
        for key, value in (("reasoning", "high"), ("fast", True), ("claude_auth", "api_key")):
            changed = copy.deepcopy(self.config)
            changed["model"][key] = value
            self.write(changed)
            self.assertNotEqual(first, selection.selection_hash(selection.read_selection(self.settings)))
        changed = copy.deepcopy(self.config)
        changed["model"]["main"]["model"] = "different-explicit-model"
        self.write(changed)
        self.assertNotEqual(first, selection.selection_hash(selection.read_selection(self.settings)))

    def test_closed_runtime_pin_uses_allowlisted_identity_without_executing_binary(self):
        pin = self.pin()
        self.assertEqual(pin, {"version": 1, "provider": "anthropic-cli", "engine_id": "claude-sdk",
            "model": "claude-opus-5-5", "reasoning": "medium", "cli_path": str(self.binary),
            "cli_hash": self.digest, "cli_version": "synthetic-version", "sdk_version": "synthetic-sdk",
            "profile_version": native.PROFILE_VERSION, "profile_hash": "a" * 64})
        self.assertEqual(native.ClaudeTrialAdapter(pin).pin, pin)
        before = self.settings.read_bytes(), self.binary.read_bytes()
        self.pin()
        self.assertEqual(before, (self.settings.read_bytes(), self.binary.read_bytes()))

    def test_unsupported_options_are_preserved_then_rejected_without_mapping(self):
        cases = (("reasoning", "off"), ("reasoning", "ultra"), ("reasoning", "Medium"),
                 ("reasoning", " medium "), ("fast", True), ("claude_auth", "api_key"))
        for key, value in cases:
            changed = copy.deepcopy(self.config)
            changed["model"][key] = value
            self.write(changed)
            self.assertEqual(selection.read_selection(self.settings)["auth_mode" if key == "claude_auth" else key], value)
            with self.assertRaises(ValueError):
                self.pin()
        changed = copy.deepcopy(self.config)
        changed["model"]["main"]["provider"] = "openai"
        self.write(changed)
        self.assertEqual(selection.read_selection(self.settings)["provider"], "openai")
        with self.assertRaises(ValueError):
            self.pin()

    def test_all_supported_reasoning_levels_are_kept_exact(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self.config["model"]["reasoning"] = effort
            self.write()
            self.assertEqual(self.pin()["reasoning"], effort)

    def test_missing_malformed_duplicate_nonfinite_or_oversized_input_never_becomes_defaults(self):
        cases = (b"", b"{", b"[]", b"null", b"{}", b'{"model":{},"model":{}}',
                 b'{"model":{"main":{"provider":"anthropic-cli","provider":"openai","model":"x"}}}',
                 b'{"unrelated":NaN}', b'{"unrelated":Infinity}', b'{"unrelated":1e9999}', b"\xff")
        for content in cases:
            self.settings.write_bytes(content)
            with self.assertRaises(ValueError):
                selection.read_selection(self.settings)
        self.settings.write_bytes(b" " * (selection.MAX_SETTINGS_BYTES + 1))
        with self.assertRaises(ValueError):
            selection.read_selection(self.settings)
        self.settings.unlink()
        with self.assertRaises(ValueError):
            selection.read_selection(self.settings)

    def test_invalid_relevant_types_and_unknown_auth_are_rejected(self):
        for key, values in (("reasoning", (None, False, 3, [], {})), ("fast", (None, 0, 1, "false", [])),
                            ("claude_auth", (None, "", "unexpected", [], 1))):
            for value in values:
                changed = copy.deepcopy(self.config)
                changed["model"][key] = value
                self.write(changed)
                with self.assertRaises(ValueError):
                    selection.read_selection(self.settings)

    def test_source_replacement_or_host_sharing_error_during_read_is_rejected(self):
        original = selection.os.fstat
        calls = 0

        def replace_after_read(fd):
            nonlocal calls
            result = original(fd)
            calls += 1
            if calls == 2:
                alternate = self.root / "alternate.json"
                alternate.write_text(json.dumps(self.config), encoding="utf-8")
                os.replace(alternate, self.settings)
            return result

        with patch.object(selection.os, "fstat", side_effect=replace_after_read):
            with self.assertRaises(ValueError):
                selection.read_selection(self.settings)
        self.assertEqual(calls, 2)

    def test_same_content_path_identity_change_is_rejected_independently_of_host_sharing(self):
        original_lstat, original_fstat = selection.Path.lstat, selection.os.fstat
        opened_stats, changed = 0, False

        class ChangedIdentity:
            def __init__(self, value):
                self.value = value
                self.st_ino = value.st_ino + 1

            def __getattr__(self, name):
                return getattr(self.value, name)

        def fstat(descriptor):
            nonlocal opened_stats
            opened_stats += 1
            return original_fstat(descriptor)

        def lstat(path, *args, **kwargs):
            nonlocal changed
            value = original_lstat(path, *args, **kwargs)
            if path == self.settings and opened_stats == 2:
                changed = True
                return ChangedIdentity(value)
            return value

        with patch.object(selection.os, "fstat", fstat), patch.object(selection.Path, "lstat", lstat):
            with self.assertRaisesRegex(ValueError, "source_changed"):
                selection.read_selection(self.settings)
        self.assertTrue(changed)

    def test_settings_mutation_while_capturing_pin_is_rejected(self):
        def mutate_profile():
            self.config["model"]["reasoning"] = "high"
            self.write()
            return "a" * 64

        with patch.object(native, "profile_hash", side_effect=mutate_profile):
            with self.assertRaises(ValueError):
                self.pin()

    def test_binary_mutation_while_capturing_pin_is_rejected(self):
        def mutate_profile():
            self.binary.write_bytes(b"changed inert runtime")
            return "a" * 64

        with patch.object(native, "profile_hash", side_effect=mutate_profile):
            with self.assertRaises(ValueError):
                self.pin()

    def test_profile_or_sdk_drift_during_capture_is_rejected(self):
        with patch.object(native, "profile_hash", side_effect=["a" * 64, "b" * 64]):
            with self.assertRaises(ValueError):
                self.pin()
        with patch.object(selection.importlib.metadata, "version", side_effect=["synthetic-sdk", "changed-sdk"]):
            with self.assertRaises(ValueError):
                self.pin()

    def test_last_profile_check_cannot_hide_later_config_or_binary_mutation(self):
        for source in (self.settings, self.binary):
            self.write()
            self.binary.write_bytes(b"synthetic inert runtime; never executable")
            calls = 0

            def mutate_last_profile():
                nonlocal calls
                calls += 1
                if calls == 2:
                    if source == self.settings:
                        changed = copy.deepcopy(self.config)
                        changed["model"]["reasoning"] = "high"
                        self.write(changed)
                    else:
                        source.write_bytes(b"changed inert runtime")
                return "a" * 64

            with patch.object(native, "profile_hash", side_effect=mutate_last_profile):
                with self.assertRaises(ValueError):
                    self.pin()

    def test_unknown_ambiguous_missing_sdk_and_changed_binary_are_blocked(self):
        with patch.object(native, "VERIFIED_NATIVE_RUNTIMES", set()):
            with self.assertRaises(ValueError):
                self.pin()
        with patch.object(native, "VERIFIED_NATIVE_RUNTIMES", {(sys.platform, version, self.digest, "synthetic-sdk")
                                                              for version in ("one", "two")}):
            with self.assertRaises(ValueError):
                self.pin()
        with patch.object(selection.importlib.metadata, "version", side_effect=selection.importlib.metadata.PackageNotFoundError):
            with self.assertRaises(ValueError):
                self.pin()
        self.binary.write_bytes(b"unverified replacement")
        with self.assertRaises(ValueError):
            self.pin()

    def test_relative_missing_and_directory_binary_paths_are_rejected(self):
        for path in (Path("relative-claude.exe"), self.root / "missing.exe", self.root):
            with self.assertRaises(ValueError):
                selection.create_pin(self.settings, path)

    def test_reparse_sources_and_ancestors_are_rejected_without_following(self):
        # Simulate Windows junction metadata without needing symlink creation privileges.
        original = selection.Path.lstat

        class Reparse:
            st_file_attributes = 0x400

            def __init__(self, value):
                self.value = value

            def __getattr__(self, name):
                return getattr(self.value, name)

        for target in (self.settings, self.root, self.binary):
            def lstat(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                return Reparse(value) if path == target else value

            with patch.object(selection.Path, "lstat", lstat):
                with self.assertRaises(ValueError):
                    self.pin()

    def test_hash_rejects_extra_fields_and_does_not_accept_secret_material(self):
        with self.assertRaises(ValueError):
            selection.selection_hash({**self.expected(), "synthetic_secret": "never-output-this"})
        with self.assertRaises(ValueError) as error:
            self.settings.write_text('{"model":,"synthetic":"never-output-this"}', encoding="utf-8")
            selection.read_selection(self.settings)
        self.assertNotIn("never-output-this", str(error.exception))


class SDKResolverTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(selection, "resolve_sdk_cli", None)),
                        "Read-only ordinary SDK CLI resolver is not implemented")
        import claude_cli
        import claude_sdk_engine
        self.cli, self.sdk = claude_cli, claude_sdk_engine
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.current = self.executable("current")
        self.old = self.executable("old")
        self.extra = self.executable(".local/bin")
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"JAVIS_STATE_DIR": str(self.state), "JAVIS_CLAUDE_CLI": "",
            "JAVIS_CLAUDE_BIN": str(self.old), "PATH": str(self.current.parent), "PATHEXT": ".EXE",
            "HOME": str(self.root), "USERPROFILE": str(self.root), "APPDATA": str(self.root / "appdata"),
            "LOCALAPPDATA": str(self.root / "local"), "ProgramFiles": str(self.root / "programs"),
            "ProgramFiles(x86)": str(self.root / "programs86"), "ProgramData": str(self.root / "programdata"),
            "NVM_HOME": str(self.root / "nvm"), "NVM_SYMLINK": str(self.root / "nvm-link")}).start()
        patch.object(self.cli, "_BIN_NHO", {"data": None}).start()
        patch("subprocess.run", side_effect=AssertionError("No native execution permitted")).start()
        patch("subprocess.Popen", side_effect=AssertionError("No native execution permitted")).start()
        patch("config.read_settings", side_effect=AssertionError("No decrypted settings permitted")).start()

    def executable(self, directory):
        path = self.root / directory / ("claude.exe" if os.name == "nt" else "claude")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"inert synthetic executable; never execute")
        path.chmod(0o700)
        return path

    def resolve(self):
        return selection.resolve_sdk_cli(self.cli, self.sdk, self.state)

    def ordinary_branch(self):
        # Run the exact current ordinary _options CLI branch, without constructing
        # an engine, SDK options, or touching the rest of the prompt/auth code.
        tree = ast.parse((SERVER / "claude_sdk_engine.py").read_bytes())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ClaudeSDK")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_options")
        branch = next(node for node in method.body if isinstance(node, ast.If)
                      and ast.dump(node.test) == ast.dump(ast.parse("'cli_path' in fields", mode="eval").body))
        namespace = {"fields": {"cli_path": None}, "kw": {}, "os": os}
        with contextlib.redirect_stderr(io.StringIO()):
            exec(compile(ast.Module(body=[branch], type_ignores=[]), str(SERVER / "claude_sdk_engine.py"), "exec"), namespace)
        return Path(namespace["kw"]["cli_path"]) if "cli_path" in namespace["kw"] else None

    def test_current_path_wins_over_remembered_and_other_finder_override_without_writes(self):
        remembered = self.state / "bin_paths.json"
        remembered.write_text(json.dumps({"claude": str(self.old)}), encoding="utf-8")
        before = remembered.read_bytes()
        self.assertEqual(self.resolve(), self.current)
        self.assertIsNone(self.cli._BIN_NHO["data"])
        self.assertEqual(remembered.read_bytes(), before)
        self.assertEqual(self.ordinary_branch(), self.current)

    def test_sdk_override_wins_and_invalid_override_never_falls_back(self):
        os.environ["JAVIS_CLAUDE_CLI"] = "  " + str(self.old) + "  "
        self.assertEqual(self.resolve(), self.old)
        self.assertEqual(self.ordinary_branch(), self.old)
        for value in (str(self.root / "missing.exe"), "relative.exe", str(self.root / "_bundled" / "claude.exe")):
            os.environ["JAVIS_CLAUDE_CLI"] = value
            with self.assertRaises(ValueError):
                self.resolve()

    def test_real_extra_directories_precede_remembered_cache(self):
        os.environ["PATH"] = str(self.root / "empty")
        self.cli._BIN_NHO["data"] = {"claude": str(self.old)}
        self.assertEqual(self.resolve(), self.extra)
        self.assertEqual(self.cli._BIN_NHO["data"], {"claude": str(self.old)})
        self.assertFalse((self.state / "bin_paths.json").exists())
        self.assertEqual(self.ordinary_branch(), self.extra)

    def test_loaded_cache_wins_over_different_disk_without_cache_mutation(self):
        os.environ["PATH"] = str(self.root / "empty")
        self.extra.unlink()
        self.cli._BIN_NHO["data"] = {"claude": str(self.old)}
        remembered = self.state / "bin_paths.json"
        remembered.write_text(json.dumps({"claude": str(self.current)}), encoding="utf-8")
        before = remembered.read_bytes()
        self.assertEqual(self.resolve(), self.old)
        self.assertEqual(self.ordinary_branch(), self.old)
        self.assertEqual(remembered.read_bytes(), before)

    def test_cold_cache_is_read_without_loading_it_and_unresolved_has_no_sdk_fallback(self):
        os.environ["PATH"] = str(self.root / "empty")
        self.extra.unlink()
        remembered = self.state / "bin_paths.json"
        remembered.write_text(json.dumps({"claude": str(self.old)}), encoding="utf-8")
        self.assertEqual(self.resolve(), self.old)
        self.assertIsNone(self.cli._BIN_NHO["data"])
        self.assertEqual(self.ordinary_branch(), self.old)
        self.cli._BIN_NHO["data"] = None
        remembered.unlink()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.resolve()
        self.assertIsNone(self.cli._BIN_NHO["data"])
        self.assertFalse(remembered.exists())

    def test_unknown_current_binary_cannot_choose_old_allowlisted_binary(self):
        self.cli._BIN_NHO["data"] = {"claude": str(self.old)}
        current = self.resolve()
        self.assertEqual(current, self.current)
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"model": {"main": {"provider": "anthropic-cli", "model": "selected"},
            "reasoning": "medium", "claude_auth": "subscription", "fast": False}}), encoding="utf-8")
        self.old.write_bytes(b"different old inert allowlisted executable")
        with patch.object(native, "VERIFIED_NATIVE_RUNTIMES", {(sys.platform, "old", hashlib.sha256(self.old.read_bytes()).hexdigest(), "sdk")}), \
             patch.object(selection.importlib.metadata, "version", return_value="sdk"):
            with self.assertRaisesRegex(ValueError, "runtime_unverified"):
                selection.create_pin(settings, current)

    def test_concrete_loaded_modules_and_unmodified_runtime_discovery_required(self):
        with self.assertRaises(ValueError):
            selection.resolve_sdk_cli(types.ModuleType("claude_cli"), self.sdk, self.state)
        with patch.object(self.cli, "_THU_MUC_BIN_THEM", (str(self.old.parent),)):
            with self.assertRaisesRegex(ValueError, "discovery_changed"):
                self.resolve()
        with patch.object(self.cli, "tim_binary", lambda name: str(self.old)):
            with self.assertRaisesRegex(ValueError, "discovery_changed"):
                self.resolve()
        with patch.object(self.sdk.ClaudeSDK, "_options", lambda self: {"cli_path": str(self.old)}):
            with self.assertRaisesRegex(ValueError, "discovery_changed"):
                self.resolve()
        with self.assertRaises(ValueError):
            selection.resolve_sdk_cli(self.cli, self.sdk, self.root / "different-state")

    def test_source_runtime_disagreement_cannot_claim_actual_ordinary_priority(self):
        original = selection._read_stable

        def changed_source(path, limit, **kwargs):
            raw, identity = original(path, limit, **kwargs)
            if Path(path).name == "claude_cli.py":
                raw = raw.replace(b'p = shutil.which(ten) or shutil.which(ten, path=_duong_dan_tim_binary())',
                                  b'p = shutil.which(ten, path=_duong_dan_tim_binary()) or shutil.which(ten)')
            return raw, identity

        with patch.object(selection, "_read_stable", side_effect=changed_source):
            with self.assertRaisesRegex(ValueError, "discovery_changed"):
                self.resolve()

    def test_current_binary_replacement_between_resolution_passes_is_rejected(self):
        original = selection._discovery_guard
        calls = 0

        def replace_before_second_pass(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.current.write_bytes(b"changed inert executable")
            return original(*args)

        with patch.object(selection, "_discovery_guard", side_effect=replace_before_second_pass):
            with self.assertRaisesRegex(ValueError, "discovery_changed"):
                self.resolve()
        self.assertEqual(calls, 2)

    def test_missing_discovery_source_constant_returns_a_safe_unavailable_error(self):
        original = selection._read_stable

        def missing_constant(path, limit, **kwargs):
            raw, identity = original(path, limit, **kwargs)
            if Path(path).name == "claude_cli.py":
                raw = raw.replace(b'_BIN_NHO_FILE = "bin_paths.json"', b'# absent discovery constant')
            return raw, identity

        with patch.object(selection, "_read_stable", side_effect=missing_constant):
            with self.assertRaisesRegex(ValueError, "trial_selection_discovery_unavailable"):
                self.resolve()

    def test_loaded_cache_and_environment_change_between_passes_is_rejected(self):
        original = selection._discovery_guard
        for mutate in (lambda: os.environ.__setitem__("JAVIS_CLAUDE_CLI", str(self.current)),
                       lambda: self.cli._BIN_NHO.__setitem__("data", {"claude": str(self.current)})):
            os.environ["JAVIS_CLAUDE_CLI"] = ""
            os.environ["PATH"] = str(self.root / "empty")
            self.extra.unlink(missing_ok=True)
            self.cli._BIN_NHO["data"] = {"claude": str(self.old)}
            calls = 0

            def change(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    mutate()
                return original(*args)

            with patch.object(selection, "_discovery_guard", side_effect=change):
                with self.assertRaisesRegex(ValueError, "discovery_changed"):
                    self.resolve()

    def test_cold_malformed_or_dead_remembered_file_is_unavailable_without_writes(self):
        os.environ["PATH"] = str(self.root / "empty")
        self.extra.unlink()
        remembered = self.state / "bin_paths.json"
        for raw in (b'{', b'{"claude":"missing"}', b'{"claude":3}', b'{"claude":"one","claude":"two"}'):
            remembered.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.resolve()
            self.assertIsNone(self.cli._BIN_NHO["data"])
            self.assertEqual(remembered.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
