"""Owner transport with real encrypted scratch stores; no main lifespan or engine calls."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import importlib.util
import json
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import test_improvement_host_snapshots as fixtures
import test_improvement_trial_requests as requests_fixture
import improvement_store as IM
from improvement_trial_records import HostContextRecords
from improvement_trial_service import OwnerTrialService

_REAL_RUN, _REAL_POPEN = subprocess.run, subprocess.Popen


def _acl_only(original, command, *args, **kwargs):
    if Path(command[0]).name.lower() not in {"whoami", "whoami.exe", "icacls", "icacls.exe"}:
        raise AssertionError("Only scratch workspace ACL metadata commands are permitted")
    return original(command, *args, **kwargs)

if importlib.util.find_spec("improvement_trial_api"):
    import improvement_trial_api as API
else:
    API = None


class TrialApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(API, "Owner trial transport is missing")
        self.f = fixtures.HostSnapshotTests();self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.assets = self.f.assets
        self.calls = []
        self.loop_thread = threading.get_ident()
        self.current_service = None
        self.app = FastAPI()
        self.deps = API.TrialApiDeps(read_store=self.read_store, write_store=self.write_store,
            resolve_brain=self.resolve_brain, owner_session=self.owner_session, capture_host=self.capture_host,
            service=self.service, active_service=self.active_service)
        API.register(self.app, self.deps)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    def touched(self, name):
        self.calls.append((name, threading.get_ident()))

    def owner_session(self, request):
        self.touched("owner")
        return None if request.cookies.get("owner") == "yes" else JSONResponse({"ok": False, "reason": "owner_session_required"}, status_code=401)

    def resolve_brain(self, value):
        self.touched("brain")
        if value == "brain":return self.f.owner.brain_id
        if value == "other":return self.f.other.brain_id
        raise ValueError("PRIVATE /unknown/brain")

    def read_store(self):
        self.touched("read")
        return IM.read_existing(self.assets.store.path, self.assets.blobs)

    def write_store(self):
        self.touched("write")
        return self.assets

    def capture_host(self, selections):
        self.touched("host")
        self.selections = selections
        return self.f.host

    def service(self):
        self.touched("service")
        if self.current_service is None:raise ValueError("unsupported_current_runtime PRIVATE")
        return self.current_service

    def active_service(self):
        self.touched("active")
        return self.current_service

    async def request(self, method, url, **kwargs):
        return await self.client.request(method, url, headers={"cookie": "owner=yes", **kwargs.pop("headers", {})}, **kwargs)

    def context_body(self):
        return {"event_key": "api-create-context", "surface": self.f.surface,
            "target_ids": [self.f.target["target_id"]], "source_selections": [{"source_ref": self.f.ref,
                **{key: self.f.metadata[key] for key in ("timezone", "data_period", "cutoff", "schema")}}]}

    def trial_fixture(self, *, terminal=False):
        import test_improvement_trial_results as results_fixture
        result = results_fixture.TrialResultTests();result.setUp()
        self.addCleanup(result.f.doCleanups)
        self.addCleanup(result.doCleanups)
        if terminal:
            # Actual scratch workspace privacy uses OS ACL helpers on Windows.
            # No Claude/auth/version/model executable is ever permitted.
            with patch("subprocess.run", side_effect=lambda *a, **kw:_acl_only(_REAL_RUN, *a, **kw)), \
                 patch("subprocess.Popen", side_effect=lambda *a, **kw:_acl_only(_REAL_POPEN, *a, **kw)):
                row, token, _ = result.staged(supported=True)
                result.finish(row, token)
            row = result.f.trials.get(result.f.owner, row["trial_id"])
        else:
            row = result.f.reserve()
        self.assets = result.f.assets
        def resolve(value):
            self.touched("brain")
            if value == "brain":return result.f.owner.brain_id
            if value == "other":return result.f.other.brain_id
            raise ValueError("unknown_brain")
        self.deps.resolve_brain = resolve
        return result, row

    def trial_service(self):
        from improvement_trial_host import HostContextStore
        host = HostContextStore(self.assets, context_capture=self.f.context_capture,
            selected_engine=lambda:copy.deepcopy(self.f.engine), target_observer=self.f.targets, fixture_observer=self.f.provider)
        self.current_service = OwnerTrialService(host)
        self.addAsyncCleanup(self.current_service.close)
        return self.current_service

    async def test_required_owner_gate_has_no_open_default(self):
        with self.assertRaises(TypeError):
            API.TrialApiDeps(read_store=lambda:None, write_store=lambda:None, resolve_brain=lambda x:x,
                capture_host=lambda x:None, service=lambda:None, active_service=lambda:None)
        with self.assertRaises((TypeError, ValueError)):
            API.TrialApiDeps(read_store=lambda:None, write_store=lambda:None, resolve_brain=lambda x:x, owner_session=None,
                capture_host=lambda x:None, service=lambda:None, active_service=lambda:None)

    async def test_owner_denial_precedes_body_paths_brain_and_every_dependency_on_all_routes(self):
        routes = [("POST", "/improvement-contexts/bad!"), ("GET", "/improvement-contexts/bad!"),
            ("POST", "/improvement-contexts/x/revoke"), ("POST", "/improvement-contexts/x/snapshots"),
            ("POST", "/improvement-contracts"), ("POST", "/improvement-contexts/x/case-sets"),
            ("POST", "/improvement-bundles"), ("POST", "/improvement-trials"), ("GET", "/improvement-trials/x"),
            ("POST", "/improvement-trials/x/cancel"), ("POST", "/improvement-trials/recover"),
            ("POST", "/improvement-trials/x/attempts/invalid/owner-decisions"),
            ("POST", "/improvement-trials/x/attempts/invalid/reevaluate")]
        with patch.object(Request, "stream", side_effect=AssertionError("Owner denial must not read body")):
            for method, path in routes:
                self.calls.clear()
                response = await self.client.request(method, path + "?brain=invalid", content=b"not JSON",
                    headers={"authorization": "Bearer full-api-token"})
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual([name for name, _ in self.calls], ["owner"])

    async def test_safe_context_get_is_read_only_no_live_factories_and_no_native_path(self):
        self.calls.clear()
        with patch("usage_index.improvement_events", side_effect=AssertionError("No indexing")), \
             patch.object(self.f.host, "_snapshot_observe", side_effect=AssertionError("No live capture")):
            response = await self.request("GET", "/improvement-contexts/experiment-owner")
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()["context"]
        self.assertEqual(value["grant_status"], "active")
        self.assertEqual(set(value["engine_config"]), {"provider", "engine_id", "model", "reasoning"})
        for private in ("cli_path", str(self.f.root), self.f.raw.decode(), "Synthetic complete owner instructions"):
            self.assertNotIn(private, response.text)
        self.assertEqual([name for name, _ in self.calls], ["owner", "brain", "read"])
        self.assertTrue(all(thread != self.loop_thread for name, thread in self.calls if name != "owner"))

    async def test_cold_get_returns_fixed_unavailable_without_writing_or_factories(self):
        self.deps.read_store = lambda:None
        response = await self.request("GET", "/improvement-contexts/missing")
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn("write", [name for name, _ in self.calls])
        self.assertNotIn("host", [name for name, _ in self.calls])
        self.assertFalse(response.json()["ok"])

    async def test_real_context_creation_snapshot_and_replay_are_safe(self):
        body = self.context_body()
        # A synthetic fixture context is intentionally not a production Dashboard claim.
        body["surface"] = {"kind": "dashboard", "session_id": "session-1", "language": "vi"}
        with patch.object(self.f.host, "context_capture", lambda principal,surface,targets:
                self.f.context_capture(principal,surface,targets)):
            first = await self.request("POST", "/improvement-contexts/api-experiment", json=body)
            self.assertEqual(first.status_code, 201, first.text)
            replay = await self.request("POST", "/improvement-contexts/api-experiment", json=body)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(self.selections, body["source_selections"])
        self.assertNotIn("cli_path", first.text)
        response = await self.request("POST", "/improvement-contexts/experiment-owner/snapshots",
            json={"event_key": "api-snapshot-capture", "source_ref": self.f.ref})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["snapshot"]["status"], "sufficient")
        self.assertIsNone(self.selections)
        self.assertNotIn(self.f.raw.decode(), response.text)

    async def test_real_contract_bundle_and_associated_private_case_import(self):
        response = await self.request("POST", "/improvement-contracts", json=requests_fixture.contract())
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()["contract"]
        snapshot = self.f.capture()
        case_body = {"event_key": "api-private-case-set", "contract_id": saved["contract_id"], "seed": 11,
            "cases": [{"id": "holdout", "family_key": "family-holdout", "split": "qualification", "task": "Report fixture.",
                "snapshot_ids": [snapshot["snapshot_id"]], "answer": {"expected": "PRIVATE OWNER ANSWER"}}]}
        response = await self.request("POST", "/improvement-contexts/experiment-owner/case-sets", json=case_body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["case_set"]["counts"]["qualification"], 1)
        self.assertNotIn("PRIVATE OWNER ANSWER", response.text)
        response = await self.request("POST", "/improvement-bundles", json={"event_key": "api-real-bundle", "revision_ids": [self.f.target["revision_id"]]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["bundle"]["revision_ids"], [self.f.target["revision_id"]])
        self.assertEqual(self.f.count("improvement_case_exposures"), 0)
        self.assertEqual(self.f.count("improvement_qualification"), 0)
        self.assertEqual(self.f.count("agent_calls"), 0)

    async def test_revoke_uses_records_and_cross_brain_scope_remains_closed(self):
        response = await self.request("POST", "/improvement-contexts/experiment-owner/revoke",
            json={"event_key": "api-revoke-context", "expected_generation": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["context"]["grant_status"], "revoked")
        self.assertNotIn("host", [name for name, _ in self.calls])
        for method, suffix, body in (("GET", "", None), ("POST", "/revoke", {"event_key": "foreign-revoke-1", "expected_generation": 2})):
            response = await self.request(method, "/improvement-contexts/experiment-owner" + suffix + "?brain=other", **({"json": body} if body else {}))
            self.assertIn(response.status_code, (403, 404))

    async def test_invalid_body_and_byte_limit_never_construct_write_or_live_dependencies(self):
        from improvement_trial_requests import MAX_BODY_BYTES
        for raw, status in ((b'{"event_key":"abcdefgh","event_key":"ijklmnop"}', 400),
                            (b"x" * (MAX_BODY_BYTES + 1), 413),
                            (json.dumps({"event_key":"recover-event","engine_config": {}}).encode(), 400)):
            self.calls.clear()
            response = await self.request("POST", "/improvement-trials/recover", content=raw)
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual([name for name, _ in self.calls], ["owner", "brain"])

    async def test_fixed_errors_never_echo_host_exception_body_or_paths(self):
        self.deps.read_store = lambda: (_ for _ in ()).throw(OSError("PRIVATE /owner/secret.db native stderr"))
        response = await self.request("GET", "/improvement-contexts/experiment-owner")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn("/owner", response.text)
        self.assertEqual(response.json()["reason"], "trial_unavailable")

    async def test_unsupported_start_is_unavailable_and_never_touches_write_store(self):
        response = await self.request("POST", "/improvement-trials", json=requests_fixture.bodies()["start_trial"])
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn("write", [name for name, _ in self.calls])
        self.assertEqual(self.f.count("agent_calls"), 0)

    async def test_recovery_uses_fixed_route_and_real_idempotent_helper_without_live_service(self):
        body = {"event_key": "api-real-recover"}
        first = await self.request("POST", "/improvement-trials/recover", json=body)
        again = await self.request("POST", "/improvement-trials/recover", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), again.json())
        self.assertEqual(first.json()["recovery"], {"not_run": 0, "unknown": 0})
        self.assertNotIn("service", [name for name, _ in self.calls])
        self.assertNotIn("host", [name for name, _ in self.calls])

    async def test_off_policy_and_nonconcrete_capture_host_cannot_create_sources(self):
        self.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="api-policy-disabled")
        response = await self.request("POST", "/improvement-contexts/experiment-owner/snapshots", json={"event_key":"api-disabled-snapshot", "source_ref":self.f.ref})
        self.assertEqual(response.status_code, 403, response.text)
        self.deps.capture_host = lambda selections: object()
        response = await self.request("POST", "/improvement-contexts/experiment-owner/snapshots", json={"event_key":"api-fake-snapshot", "source_ref":self.f.ref})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.f.count("improvement_snapshots"), 0)

    async def test_off_policy_rejects_new_public_bundle_without_event_or_service(self):
        self.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="api-disable-bundle")
        before = self.f.count("improvement_events")
        response = await self.request("POST", "/improvement-bundles", json={"event_key": "api-new-disabled-bundle",
            "revision_ids": [self.f.target["revision_id"]]})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.f.count("improvement_bundles"), 0)
        self.assertEqual(self.f.count("improvement_events"), before)
        self.assertNotIn("service", [name for name, _ in self.calls])

    async def test_actual_reserved_status_and_cancel_without_warming_service_or_sources(self):
        result, trial = self.trial_fixture()
        response = await self.request("GET", "/improvement-trials/" + trial["trial_id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["trial"]["status"], "reserved")
        response = await self.request("POST", "/improvement-trials/" + trial["trial_id"] + "/cancel",
            json={"event_key":"api-no-warm-cancel", "expected_generation":trial["generation"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["trial"]["status"], "cancelled")
        self.assertEqual(result.f.ledger.rows()[0]["status"], "refunded")
        self.assertNotIn("host", [name for name, _ in self.calls])
        self.assertNotIn("service", [name for name, _ in self.calls])
        for private in ("cli_path", "engine_config", "request", "private-answer", str(result.f.root)):
            self.assertNotIn('"' + private + '"' if private in ("request", "engine_config") else private, response.text)

    async def test_active_service_cancel_uses_actual_durable_cancel_and_preserves_cross_brain_scope(self):
        result, trial = self.trial_fixture()
        service = self.trial_service()
        with patch.object(service, "cancel", wraps=service.cancel) as cancel:
            response = await self.request("POST", "/improvement-trials/" + trial["trial_id"] + "/cancel?brain=other",
                json={"event_key":"api-foreign-cancel", "expected_generation":trial["generation"]})
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(result.f.trials.get(result.f.owner, trial["trial_id"])["status"], "reserved")
            response = await self.request("POST", "/improvement-trials/" + trial["trial_id"] + "/cancel",
                json={"event_key":"api-active-cancel", "expected_generation":trial["generation"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["trial"]["status"], "cancelled")
        self.assertEqual(cancel.call_count, 2)
        self.assertEqual(result.f.ledger.rows()[0]["status"], "refunded")

    async def test_start_transport_delegates_once_and_returns_real_stored_safe_view(self):
        # Synthetic transport seam only: admission/runner have their own real-component suites.
        result, trial = self.trial_fixture()
        service = self.trial_service()
        safe = API.read_trial(self.assets, result.f.owner, trial["trial_id"])
        with patch.object(service, "start", new=AsyncMock(return_value=safe)) as start:
            response = await self.request("POST", "/improvement-trials", json=requests_fixture.bodies()["start_trial"])
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["trial"], safe)
        start.assert_awaited_once()
        self.assertEqual(start.call_args.args[0].brain_id, result.f.owner.brain_id)
        self.assertNotIn("cli_path", response.text)
        self.assertEqual(len(result.f.ledger.rows()), 1)
        self.assertNotIn("write", [name for name, _ in self.calls])

    async def test_start_cancellation_propagates_and_never_becomes_a_host_error(self):
        self.trial_fixture()
        service = self.trial_service()
        with patch.object(service, "start", new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.request("POST", "/improvement-trials", json=requests_fixture.bodies()["start_trial"])

    async def test_http_cancel_during_committing_standalone_cancel_drains_owned_storage(self):
        from improvement_trials import TrialStore
        result, trial = self.trial_fixture()
        entered, release = threading.Event(), threading.Event()
        original = TrialStore.cancel
        def committing(store, *args, **kwargs):
            row = original(store, *args, **kwargs)
            entered.set()
            release.wait(10)
            return row
        with patch.object(TrialStore, "cancel", committing):
            pending = asyncio.create_task(self.request("POST", "/improvement-trials/" + trial["trial_id"] + "/cancel",
                json={"event_key":"api-committing-cancel", "expected_generation":trial["generation"]}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            pending.cancel();await asyncio.sleep(.02);pending.cancel()
            self.assertFalse(pending.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        self.assertEqual(result.f.trials.get(result.f.owner, trial["trial_id"])["status"], "cancelled")
        self.assertEqual(result.f.ledger.rows()[0]["status"], "refunded")

    async def test_explicit_local_owner_policy_and_async_owner_gate_are_supported(self):
        self.deps.owner_session = lambda request:None
        response = await self.client.get("/improvement-contexts/experiment-owner")
        self.assertEqual(response.status_code, 200, response.text)
        async def denied(request):
            return JSONResponse({"reason":"owner_session_required"}, status_code=401)
        self.deps.owner_session = denied
        self.calls.clear()
        response = await self.client.get("/improvement-contexts/experiment-owner")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.calls, [])

    async def test_real_terminal_owner_decision_and_reevaluation_return_safe_views_without_new_usage(self):
        result, trial = self.trial_fixture(terminal=True)
        identifier = trial["trial_id"]
        report = result.results.read(result.f.owner, identifier, 1)
        criterion = next(item["id"] for item in report["evaluation"]["criteria"] if item["kind"] == "owner_confirm")
        calls = copy.deepcopy(result.f.ledger.rows())
        body = {"event_key":"api-owner-decision", "criterion_id":criterion, "verdict":"pass",
            "output_hash":report["artifact_hash"], "contract_hash":report["contract_hash"], "expected_generation":trial["generation"]}
        response = await self.request("POST", f"/improvement-trials/{identifier}/attempts/1/owner-decisions", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()["trial"]
        replay = await self.request("POST", f"/improvement-trials/{identifier}/attempts/1/owner-decisions", json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        response = await self.request("POST", f"/improvement-trials/{identifier}/attempts/1/reevaluate",
            json={"event_key":"api-owner-reevaluate", "expected_generation":current["generation"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["trial"]["evaluation"]["business_verdict"], "pass")
        self.assertEqual(result.f.ledger.rows(), calls)
        self.assertNotIn("private-answer", response.text)
        self.assertNotIn("output_manifest", response.text)
        self.assertNotIn("host", [name for name, _ in self.calls])
        self.assertNotIn("service", [name for name, _ in self.calls])

    async def test_stale_terminal_decision_and_attempt_or_path_errors_are_fixed(self):
        result, trial = self.trial_fixture(terminal=True)
        identifier = trial["trial_id"]
        body = {"event_key":"api-stale-owner-decision", "criterion_id":"owner", "verdict":"pass",
            "output_hash":"a" * 64, "contract_hash":"b" * 64, "expected_generation":999}
        response = await self.request("POST", f"/improvement-trials/{identifier}/attempts/1/owner-decisions", json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["reason"], "trial_conflict")
        for path in (f"/improvement-trials/{identifier}/attempts/0/reevaluate", "/improvement-trials/bad!/attempts/1/reevaluate"):
            response = await self.request("POST", path, json={"event_key":"api-invalid-path", "expected_generation":1})
            self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
