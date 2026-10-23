"""Owner candidate HTTP operations retain real encrypted writes through cancellation."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import importlib.util
import json
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import improvement_store as IM
import test_improvement_skill_candidates as fixtures

if importlib.util.find_spec("improvement_candidate_api"):
    import improvement_candidate_api as API
else:
    API = None


class CandidateApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(API, "Browser-owner candidate transport is absent")
        self.f = fixtures.SkillCandidateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.assets = self.f.f.assets
        self.calls = []
        self.loop_thread = threading.get_ident()
        self.operations = API.CandidateOperations()
        self.addAsyncCleanup(self.operations.close)
        self.app = FastAPI()
        self.deps = API.CandidateApiDeps(read_store=self.read_store, write_store=self.write_store,
            resolve_brain=self.resolve_brain, owner_session=self.owner_session, operations=self.operation_factory,
            pack_state_dir=lambda: self.f.f.root / "packs")
        API.register(self.app, self.deps)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    def touched(self, name):
        self.calls.append((name, threading.get_ident()))

    def owner_session(self, request):
        self.touched("owner")
        if request.cookies.get("owner") != "yes":
            return JSONResponse({"ok": False, "reason": "owner_session_required"}, status_code=403)

    def resolve_brain(self, value):
        self.touched("brain")
        if value == "brain":
            return self.f.owner.brain_id
        if value == "other":
            return self.f.f.other.brain_id
        raise ValueError("unknown_brain")

    def read_store(self):
        self.touched("read")
        return IM.read_existing(self.assets.store.path, self.assets.blobs)

    def write_store(self):
        self.touched("write")
        return self.assets

    def operation_factory(self):
        self.touched("operations")
        return self.operations

    async def request(self, method, path, **kwargs):
        headers = {"cookie": "owner=yes", **kwargs.pop("headers", {})}
        return await self.client.request(method, path, headers=headers, **kwargs)

    def stage_body(self, key="candidate-api-stage"):
        return {"experiment_id": "experiment-owner", "patch": self.f.patch(), "event_key": key}

    async def staged(self):
        response = await self.request("POST", "/improvement-candidates", json=self.stage_body())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["candidate"]

    async def test_owner_gate_precedes_body_and_all_factories_even_for_api_token(self):
        response = await self.client.post("/improvement-candidates", content=b"not json", headers={"authorization": "Bearer full-api-token"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [("owner", self.loop_thread)])
        for path in ("/improvement-candidate-contexts?slug=../x", "/improvement-candidates?experiment_id=../x"):
            self.calls.clear()
            self.assertEqual((await self.client.get(path)).status_code, 403)
            self.assertEqual(self.calls, [("owner", self.loop_thread)])

    async def test_cold_get_never_constructs_operations_or_decrypts(self):
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Cold metadata must not decrypt")):
            response = await self.request("GET", "/improvement-candidate-contexts?slug=report")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["contexts"]["items"]), 1)
            response = await self.request("GET", "/improvement-candidates?experiment_id=experiment-owner")
            self.assertEqual(response.json()["candidates"], [])
        self.assertNotIn("operations", [name for name, _ in self.calls])
        self.assertNotIn("write", [name for name, _ in self.calls])
        self.assertTrue(all(thread != self.loop_thread for name, thread in self.calls if name != "owner"))

    async def test_baseline_review_is_exact_scoped_and_rejects_changed_source(self):
        path = "/improvement-candidate-baselines/experiment-owner/review"
        body = {"target_id": self.f.patch()["target_id"]}
        response = await self.request("POST", path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        review = response.json()["review"]
        self.assertEqual(review["baseline"]["text"].encode("utf-8"), self.f.raw)
        self.assertEqual(review["baseline"]["hash"], self.f.patch()["before_hash"])
        self.assertLessEqual(review["editable_start"], self.f.start)
        self.assertEqual((await self.request("POST", path + "?brain=other", json=body)).status_code, 403)
        self.f.f.skill.write_bytes(self.f.raw + b"changed")
        self.assertEqual((await self.request("POST", path, json=body)).status_code, 409)
        self.f.f.skill.write_bytes(self.f.raw)

    async def test_actual_stage_private_review_exact_authorize_and_revoke(self):
        candidate = await self.staged()
        review = await self.request("POST", f'/improvement-candidates/{candidate["candidate_id"]}/review', json={})
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(review.json()["review"]["after"]["hash"], candidate["content_hash"])
        options = {"expected_generation": 1, "before_hash": candidate["base_hash"], "after_hash": candidate["content_hash"],
                   "event_key": "candidate-api-approve"}
        authorized = await self.request("POST", f'/improvement-candidates/{candidate["candidate_id"]}/authorize', json=options)
        self.assertEqual(authorized.status_code, 200, authorized.text)
        authority = authorized.json()["authority"]
        revoked = await self.request("POST", f'/improvement-edit-authorities/{authority["authority_id"]}/revoke',
            json={"expected_generation": 1, "event_key": "candidate-api-revoke"})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["authority"]["status"], "revoked")
        replay = await self.request("POST", f'/improvement-candidates/{candidate["candidate_id"]}/authorize', json=options)
        self.assertEqual(replay.json()["authority"], authority)
        current = await self.request("GET", "/improvement-candidates?experiment_id=experiment-owner")
        self.assertEqual(current.json()["candidates"][0]["edit_authority"]["status"], "revoked")
        self.assertEqual(current.json()["candidates"][0]["semantic_status"], "unreviewed")

    async def test_closed_duplicate_deep_and_oversized_body_reject_before_write_factory(self):
        values = (
            (b'{"experiment_id":"x","experiment_id":"y"}', 400),
            (b'{"value":' + b'[' * 40 + b'0' + b']' * 40 + b'}', 400),
            (b'{"actor":"pretend-owner"}', 400),
            (b' ' * (2 * 1024 * 1024 + 1), 413),
        )
        for raw, expected in values:
            self.calls.clear()
            response = await self.request("POST", "/improvement-candidates", content=raw)
            self.assertEqual(response.status_code, expected, response.text)
            self.assertNotIn("write", [name for name, _ in self.calls])
            self.assertNotIn("operations", [name for name, _ in self.calls])

    async def test_foreign_scope_and_off_policy_do_not_refresh_permissions(self):
        candidate = await self.staged()
        response = await self.request("POST", f'/improvement-candidates/{candidate["candidate_id"]}/review?brain=other', json={})
        self.assertEqual(response.status_code, 403, response.text)
        self.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="api-policy-off")
        replay = await self.request("POST", "/improvement-candidates", json=self.stage_body())
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["candidate"], candidate)
        denied = await self.request("POST", "/improvement-candidates", json=self.stage_body("new-stage-while-off"))
        self.assertEqual(denied.status_code, 403, denied.text)

    async def test_repeated_http_cancel_and_shutdown_retain_complete_real_write(self):
        entered, release = threading.Event(), threading.Event()
        original = self.assets.blobs.write
        results = []

        def blocked(*args):
            if not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release write")
            value = original(*args)
            results.append(value)
            return value

        with patch.object(self.assets.blobs, "write", side_effect=blocked):
            request = asyncio.create_task(self.request("POST", "/improvement-candidates", json=self.stage_body()))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            request.cancel()
            await asyncio.sleep(.02)
            request.cancel()
            closing = asyncio.create_task(self.operations.close())
            await asyncio.sleep(.03)
            self.assertFalse(request.done(), "HTTP cancellation must retain the actual SQL/blob write")
            self.assertFalse(closing.done(), "Shutdown must join the retained write")
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            await closing
        self.assertEqual(len(self.f.records.list(self.f.owner, "experiment-owner")), 1)
        self.assertGreaterEqual(len(results), 3)
        self.assertFalse(self.operations._operations)
        with self.assertRaises(RuntimeError):
            await self.operations.run(lambda: self.fail("Closed operations must not run"))


if __name__ == "__main__":
    unittest.main()
