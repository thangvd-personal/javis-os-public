"""Owner-only bounded transport and cold experiment/report history."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import json
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import improvement_store as IM
from improvement_trial_service import OwnerTrialService
from improvement_experiment_service import ExperimentService
import test_improvement_experiment_lineage as fixtures

if importlib.util.find_spec("improvement_experiment_api"):
    import improvement_experiment_api as API
else:
    API = None


class ExperimentApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(API, "Owner experiment API is absent")
        self.h = fixtures.LineageTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.assets = self.h.assets
        self.calls = []
        self.loop_thread = threading.get_ident()
        self.trials = OwnerTrialService(self.h.h.host)
        self.addAsyncCleanup(self.trials.close)
        self.service = ExperimentService(self.h.h.plans(), self.trials)
        self.addAsyncCleanup(self.service.close)
        self.app = FastAPI()
        API.register(self.app, API.ExperimentApiDeps(read_store=self.read_store,
            resolve_brain=self.resolve_brain, owner_session=self.owner_session, service=self.service_factory))
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
            return self.h.owner.brain_id
        if value == "other":
            return self.h.h.f.other.brain_id
        raise ValueError("unknown_brain")

    def read_store(self):
        self.touched("read")
        return IM.read_existing(self.assets.store.path, self.assets.blobs)

    def service_factory(self):
        self.touched("service")
        return self.service

    async def request(self, method, path, **kwargs):
        headers = {"cookie": "owner=yes", **kwargs.pop("headers", {})}
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def test_owner_gate_precedes_body_path_brain_and_all_factories(self):
        for method, path in (("POST", "/improvement-experiments/association-experiment/cancel?brain=invalid"),
                             ("GET", "/improvement-experiments")):
            self.calls.clear()
            result = await self.client.request(method, path, content=b"not json",
                headers={"authorization": "Bearer full-api-token"})
            self.assertEqual(result.status_code, 403)
            self.assertEqual(self.calls, [("owner", self.loop_thread)])

    async def test_metadata_get_is_cold_and_omits_canonical_root(self):
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Metadata cannot decrypt")), \
             patch.object(self.h.h.host, "_snapshot_observe", side_effect=AssertionError("Metadata cannot observe")):
            listing = await self.request("GET", "/improvement-experiments")
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertEqual(len(listing.json()["experiments"]["items"]), 1)
            detail = await self.request("GET", "/improvement-experiments/association-experiment")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["experiment"]["authority_status"], "not_checked")
        self.assertNotIn("service", [name for name, _ in self.calls])
        self.assertNotIn(self.h.owner.brain_id, listing.text + detail.text)
        self.assertTrue(all(thread != self.loop_thread for name, thread in self.calls if name != "owner"))

    async def test_closed_duplicate_deep_and_oversized_body_reject_before_service(self):
        for raw, status in ((b'{"expected_generation":1,"expected_generation":2}', 400),
                            (b'{"cell_position":0,"event_key":"arbitrary-slot"}', 400),
                            (b'{"value":' + b'[' * 40 + b'0' + b']' * 40 + b'}', 400),
                            (b' ' * (2 * 1024 * 1024 + 1), 413)):
            self.calls.clear()
            result = await self.request("POST", "/improvement-experiments/association-experiment/next", content=raw)
            self.assertEqual(result.status_code, status, result.text)
            self.assertNotIn("service", [name for name, _ in self.calls])

    async def test_foreign_brain_cannot_read_or_stop_same_identifier(self):
        read = await self.request("GET", "/improvement-experiments/association-experiment?brain=other")
        self.assertEqual(read.status_code, 403)
        stopped = await self.request("POST", "/improvement-experiments/association-experiment/cancel?brain=other",
            json={"expected_generation":self.h.frozen["generation"], "event_key":"cross-brain-api-stop"})
        self.assertEqual(stopped.status_code, 403)

    async def test_real_report_publication_and_cold_history_survive_stop(self):
        result = await self.request("POST", "/improvement-experiments/association-experiment/report",
            json={"comparison_id": self.h.comparison["id"], "expected_generation": self.h.frozen["generation"],
                  "event_key": "api-incomplete-report"})
        self.assertEqual(result.status_code, 200, result.text)
        saved = result.json()["result"]
        self.h.h.plans().select(self.h.owner, "association-experiment", "keep_base", ordinal=None,
            expected_generation=saved["generation"], event_key="stop-after-api-report")
        self.calls.clear()
        historical = await self.request("GET", "/improvement-comparison-reports/" + saved["report_id"])
        self.assertEqual(historical.status_code, 200, historical.text)
        self.assertFalse(historical.json()["report"]["summary"]["complete"])
        self.assertNotIn("service", [name for name, _ in self.calls])
        self.assertNotIn(self.h.owner.brain_id, json.dumps(historical.json()))


if __name__ == "__main__":
    unittest.main()
