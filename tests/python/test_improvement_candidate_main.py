"""Real application owner/CSRF gates and lazy candidate shutdown wiring."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_improvement_trial_service as fixtures
import improvement_candidate_api as API


class MainCandidateTests(unittest.TestCase):
    def setUp(self):
        self.main = fixtures.fixtures.H
        self.assertTrue(hasattr(self.main, "_improvement_candidate_operations"), "Candidate main wiring is missing")
        self.f = fixtures.DashboardServiceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.stack = self.f.stack
        self.stack.enter_context(patch.object(self.main, "_IMPROVEMENT_CANDIDATE_OPERATIONS", None))
        self.stack.enter_context(patch.object(self.main, "_IMPROVEMENT_CANDIDATE_SHUTTING_DOWN", False))
        self.client = TestClient(self.main.app)

    def routes(self):
        return [(r.path, sorted(r.methods)[0]) for r in self.main.app.routes
                if getattr(r, "name", "") in {
                    "improvement_candidate_contexts", "improvement_candidates", "improvement_edit_region",
                    "improvement_candidate_stage", "improvement_candidate_review", "improvement_candidate_authorize",
                    "improvement_candidate_authority_revoke", "improvement_artifact_review", "improvement_candidate_baseline_review"}]

    def test_api_token_is_denied_before_all_nine_route_dependencies(self):
        routes = self.routes()
        self.assertEqual(len(routes), 9)
        with patch.object(self.main.cfgmod, "gate_active", return_value=True), \
                patch.object(self.main.cfgmod, "valid_session", return_value=False), \
                patch.object(self.main, "_token_ok", return_value=True), \
                patch.object(API, "SkillCandidates", side_effect=AssertionError("Denied before source")), \
                patch.object(self.main, "_resonance_store", side_effect=AssertionError("Denied before store")):
            for path, method in routes:
                path = path.replace("{candidate_id}", "invalid").replace("{authority_id}", "invalid").replace("{trial_id}", "invalid").replace("{experiment_id}", "invalid")
                response = self.client.request(method, path, headers={"Authorization": "Bearer synthetic-full-token"}, content=b"invalid")
                self.assertEqual(response.status_code, 403, (path, response.text))
        self.assertIsNone(self.main._IMPROVEMENT_CANDIDATE_OPERATIONS)

    def test_actual_global_auth_and_csrf_protect_candidate_mutations(self):
        with patch.object(self.main.cfgmod, "gate_active", return_value=True), \
                patch.object(self.main.cfgmod, "valid_session", side_effect=lambda cookie: cookie == "owner-cookie"), \
                patch.object(self.main, "_token_ok", return_value=False):
            self.assertEqual(self.client.get("/improvement-candidates").status_code, 401)
            for path, method in self.routes():
                if method != "POST":
                    continue
                response = self.client.post(path, cookies={"javis_session": "owner-cookie"},
                    headers={"Origin": "https://foreign.example"}, json={})
                self.assertEqual(response.status_code, 403, (path, response.text))
                self.assertEqual(response.json()["blocked"], "web_security")
        self.assertIsNone(self.main._IMPROVEMENT_CANDIDATE_OPERATIONS)

    def test_cold_reads_do_not_initialize_store_operations_or_trial_engine(self):
        with patch.object(self.main.cfgmod, "gate_active", return_value=False), \
                patch.object(self.main, "_resonance_store", side_effect=AssertionError("No store initialization")), \
                patch.object(API, "CandidateOperations", side_effect=AssertionError("No operation owner initialization")), \
                patch.object(self.main.improvement_trial_service, "dashboard_host", side_effect=AssertionError("No engine capture")):
            for path in ("/improvement-candidates", "/improvement-candidate-contexts"):
                response = self.client.get(path, params={"brain": str(self.f.f.brain)})
                self.assertIn(response.status_code, (403, 400, 503))
        self.assertIsNone(self.main._IMPROVEMENT_CANDIDATE_OPERATIONS)

    def test_shutdown_retains_pending_work_and_closes_before_trial_lifetimes(self):
        async def check():
            owner = await asyncio.to_thread(self.main._improvement_candidate_operations)
            self.assertIs(owner, await asyncio.to_thread(self.main._improvement_candidate_operations))
            entered, release = asyncio.Event(), asyncio.Event()
            original = owner.close
            async def delayed():
                entered.set()
                await release.wait()
                await original()
            with patch.object(owner, "close", side_effect=delayed):
                closing = asyncio.create_task(self.main._shutdown_improvement_candidates())
                await asyncio.wait_for(entered.wait(), 5)
                closing.cancel()
                await asyncio.sleep(0)
                closing.cancel()
                await asyncio.sleep(0)
                self.assertFalse(closing.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await closing
            self.assertTrue(owner._closed)
            with self.assertRaises(RuntimeError):
                self.main._improvement_candidate_operations()
        asyncio.run(check())
        observed = []
        async def candidates(): observed.append("candidates")
        async def trials(): observed.append("trials")
        async def other(): observed.append("other")
        with patch.object(self.main, "_shutdown_improvement_candidates", new=candidates), \
                patch.object(self.main, "_shutdown_improvement_trials", new=trials), \
                patch.object(self.main.tasks_feature, "shutdown", new=other), \
                patch.object(self.main.zalo_personal_channel, "stop"), \
                patch.object(self.main.owner_channels, "ALL", []), \
                patch.object(self.main.mcp_client.pool, "close_all", new=other), \
                patch.object(self.main.terminal.KHO, "dong_het"):
            asyncio.run(self.main._shutdown_mcp_pool())
        self.assertEqual(observed[:3], ["candidates", "trials", "other"])


if __name__ == "__main__":
    unittest.main()
