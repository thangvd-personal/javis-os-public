"""Compile reuse never caches live-source readability or a runtime match verdict."""
from _paths import ROOT, SERVER  # noqa: F401

import builtins
import os
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

_BOOT = Path(tempfile.mkdtemp(prefix="dc-"))
for key, child in {"JAVIS_STATE_DIR": "state", "BRAINS_DIR": "brains", "BRAIN_PATH": "brain",
                  "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources", "JAVIS_SESSIONS_DB": "sessions.db"}.items():
    os.environ[key] = str(_BOOT / child)

import improvement_trial_dashboard as D


class CompiledSourceCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(D, "_SERVER", self.root))
        self.module = self.module_at("_public_cache_test")

    def module_at(self, name):
        path = self.root / (name + ".py")
        raw = b"def read_public():\n    return 1\n"
        path.write_bytes(raw)
        module = types.ModuleType(name)
        module.__file__ = str(path)
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
        self.stack.enter_context(patch.dict(sys.modules, {name: module}))
        return module

    def verify(self, module=None):
        module = self.module if module is None else module
        D._verified_function(module.read_public, module, "read_public", {}, "runtime_source_changed")

    def test_fresh_scans_read_source_twice_but_compile_identical_bytes_once(self):
        with patch.object(D, "compile", wraps=builtins.compile, create=True) as compiler, \
             patch.object(D, "_read_stable", wraps=D._read_stable) as reads:
            self.verify()
            self.verify()
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(compiler.call_count, 1, "Identical public source must not compile again on the next scan")

    def test_same_mtime_and_size_changed_bytes_do_not_reuse_old_code(self):
        self.verify()
        path = Path(self.module.__file__)
        before = path.stat()
        path.write_bytes(path.read_bytes().replace(b"return 1", b"return 2"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        with self.assertRaises(ValueError):
            self.verify()

    def test_warm_compile_does_not_cache_live_function_match(self):
        self.verify()
        replacement = compile(b"def read_public():\n    return 2\n", self.module.__file__, "exec", dont_inherit=True)
        self.module.read_public.__code__ = next(code for code in replacement.co_consts if isinstance(code, types.CodeType))
        with self.assertRaises(ValueError):
            self.verify()

    def test_warm_compile_still_requires_actual_readable_regular_source_path(self):
        self.verify()
        path = Path(self.module.__file__)
        path.unlink()
        with self.assertRaises(ValueError):
            self.verify()
        path.mkdir()
        with self.assertRaises(ValueError):
            self.verify()

    def test_concurrent_fresh_reads_compile_one_immutable_source_tuple(self):
        gate = threading.Barrier(2)

        def verify(_):
            gate.wait()
            self.verify()

        with patch.object(D, "compile", wraps=builtins.compile, create=True) as compiler, ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(verify, (1, 2)))
        self.assertEqual(compiler.call_count, 1)

    def test_cache_retains_at_most_32_paths_and_eviction_recompiles(self):
        cache = getattr(D, "_COMPILED_CODE_CACHE", None)
        self.assertIsNotNone(cache, "Bounded compile cache is absent")
        first = self.module_at("_public_cache_first")
        self.verify(first)
        for index in range(33):
            self.verify(self.module_at("_public_cache_" + str(index)))
        self.assertLessEqual(len(cache), 32)
        with patch.object(D, "compile", wraps=builtins.compile, create=True) as compiler:
            self.verify(first)
        self.assertEqual(compiler.call_count, 1)


if __name__ == "__main__":
    unittest.main()
