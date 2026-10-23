"""Actual application wiring: owner/CSRF gates and source-before-shutdown ordering."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_improvement_trial_service as fixtures
from fastapi.testclient import TestClient
import improvement_trial_service as S


class MainTrialTests(unittest.TestCase):
    def setUp(self):
        self.main = fixtures.fixtures.H
        self.assertTrue(hasattr(self.main, "_improvement_trial_service"), "Owner trial main wiring is missing")
        self.f = fixtures.DashboardServiceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.main = self.f.host
        self.stack = self.f.stack
        self.stack.enter_context(patch.object(self.main, "_IMPROVEMENT_TRIAL_SERVICE", None))
        self.stack.enter_context(patch.object(self.main, "_IMPROVEMENT_TRIAL_SHUTTING_DOWN", False))
        self.stack.enter_context(patch.object(self.main, "_resonance_store", return_value=self.f.assets.store))
        self.client = TestClient(self.main.app)

    def test_cold_get_uses_existing_metadata_without_source_or_service_factories(self):
        with patch.object(self.main.cfgmod, "gate_active", return_value=False), \
                patch.object(S, "dashboard_host", side_effect=AssertionError("No source capture")) as capture, \
                patch.object(self.main, "_resonance_store", side_effect=AssertionError("No store initialization")) as store, \
                patch.object(S, "OwnerTrialService", side_effect=AssertionError("No service initialization")) as service:
            for path in ("/improvement-contexts/missing", "/improvement-trials/missing"):
                response = self.client.get(path, params={"brain": str(self.f.f.brain)})
                self.assertIn(response.status_code, (403, 503), response.text)
                self.assertNotIn("cli_path", response.text)
            capture.assert_not_called()
            store.assert_not_called()
            service.assert_not_called()
        self.assertIsNone(self.main._IMPROVEMENT_TRIAL_SERVICE)

    def test_api_token_cannot_replace_real_owner_session_for_any_new_route(self):
        routes = [(route.path, sorted(route.methods)[0]) for route in self.main.app.routes
                  if getattr(route, "path", "").startswith(("/improvement-contexts", "/improvement-trials",
                                                              "/improvement-contracts", "/improvement-bundles"))
                  and getattr(route, "name", "") != "improvement_artifact_review"]
        self.assertEqual(len(routes), 13)
        with patch.object(self.main.cfgmod, "gate_active", return_value=True), \
                patch.object(self.main.cfgmod, "valid_session", return_value=False), \
                patch.object(self.main, "_token_ok", return_value=True), \
                patch.object(S, "dashboard_host", side_effect=AssertionError("Denied before source factory")):
            for path, method in routes:
                path = path.replace("{experiment_id}", "exp").replace("{trial_id}", "trial").replace("{attempt}", "1")
                response = self.client.request(method, path, headers={"Authorization": "Bearer synthetic-full-token"},
                                               content=b"malformed JSON")
                self.assertEqual(response.status_code, 403, (path, response.text))
        self.assertIsNone(self.main._IMPROVEMENT_TRIAL_SERVICE)

    def test_existing_global_auth_and_csrf_still_precede_owner_trial_actions(self):
        with patch.object(self.main.cfgmod, "gate_active", return_value=True), \
                patch.object(self.main.cfgmod, "valid_session", side_effect=lambda cookie: cookie == "owner-cookie"), \
                patch.object(self.main, "_token_ok", return_value=False), \
                patch.object(S, "dashboard_host", side_effect=AssertionError("No capture on denied request")):
            self.assertEqual(self.client.get("/improvement-trials/missing").status_code, 401)
            response = self.client.post("/improvement-trials", cookies={"javis_session": "owner-cookie"},
                headers={"Origin": "https://foreign.example"}, json={"event_key": "csrf-denied-event"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["blocked"], "web_security")
        self.assertIsNone(self.main._IMPROVEMENT_TRIAL_SERVICE)

    def test_concurrent_explicit_factory_retains_one_actual_service_without_probes(self):
        actual = S.OwnerTrialService
        with patch.object(S, "OwnerTrialService", wraps=actual) as constructor:
            with ThreadPoolExecutor(max_workers=3) as pool:
                services = list(pool.map(lambda _: self.main._improvement_trial_service(), range(3)))
        self.assertTrue(all(value is services[0] for value in services))
        self.assertEqual(constructor.call_count, 1)
        self.assertIs(self.main._improvement_trial_active_service(), services[0])
        asyncio.run(self.main._shutdown_improvement_trials())
        with self.assertRaises(RuntimeError):
            self.main._improvement_trial_service()

    def test_trial_close_precedes_other_shutdown_lifetimes(self):
        observed = []
        async def trials():
            observed.append("trials")
        async def kanban():
            observed.append("kanban")
        async def mcp():
            observed.append("mcp")
        with patch.object(self.main, "_shutdown_improvement_trials", new=trials), \
                patch.object(self.main.tasks_feature, "shutdown", new=kanban), \
                patch.object(self.main.zalo_personal_channel, "stop"), \
                patch.object(self.main.owner_channels, "ALL", []), \
                patch.object(self.main.mcp_client.pool, "close_all", new=mcp), \
                patch.object(self.main.terminal.KHO, "dong_het"):
            asyncio.run(self.main._shutdown_mcp_pool())
        self.assertEqual(observed[:3], ["trials", "kanban", "mcp"])

    def test_shutdown_does_not_block_event_loop_on_inflight_factory_lock(self):
        entered, release = threading.Event(), threading.Event()
        actual = S.OwnerTrialService
        def construct(host):
            entered.set()
            if not release.wait(6):
                raise AssertionError("Test constructor gate timed out")
            return actual(host)
        async def check():
            factory = asyncio.create_task(asyncio.to_thread(self.main._improvement_trial_service))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            # This timer is only a deadlock safeguard. The event loop must yield
            # while construction owns the native lock, before the timer releases.
            timer = threading.Timer(5, release.set)
            timer.start()
            closing = asyncio.create_task(self.main._shutdown_improvement_trials())
            try:
                await asyncio.sleep(0)
                self.assertFalse(release.is_set(), "Shutdown blocked its event loop on the factory lock")
                self.assertFalse(closing.done())
            finally:
                release.set()
                timer.cancel()
                await factory
                await closing
        with patch.object(S, "OwnerTrialService", side_effect=construct):
            asyncio.run(check())

    def test_repeated_shutdown_cancellation_waits_for_actual_service_close(self):
        service = self.main._improvement_trial_service()
        async def check():
            entered, release = asyncio.Event(), asyncio.Event()
            original = service.close
            async def delayed_close():
                entered.set()
                await release.wait()
                return await original()
            with patch.object(service, "close", side_effect=delayed_close):
                closing = asyncio.create_task(self.main._shutdown_improvement_trials())
                await asyncio.wait_for(entered.wait(), 5)
                closing.cancel()
                await asyncio.sleep(0)
                closing.cancel()
                await asyncio.sleep(0)
                self.assertFalse(closing.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await closing
            self.assertTrue(service._closed)
            self.assertTrue(service.tasks._closed)
            with self.assertRaises(RuntimeError):
                self.main._improvement_trial_service()
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
