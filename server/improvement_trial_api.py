"""Owner-session trial transport; global authentication/CSRF remain application policy.

Registration does not construct a service, migrate a store or enable capabilities.
Every dependency is host supplied. Requests never supply principals or runtime pins.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

import localefmt
import improvement_cases as IC
import improvement_store as IM
from evidence_store import redact
from improvement_blobs import AssetUnavailable
from improvement_trial_associations import TrialCaseAssociations
from improvement_trial_decisions import OwnerDecisionStore
from improvement_trial_host import HostContextStore
from improvement_trial_records import HostContextRecords
from improvement_trial_requests import TrialRequestError, parse_body, project_contract
from improvement_trial_results import TrialResults
from improvement_trial_runner import _settled_call
from improvement_trial_service import OwnerTrialService, recover_trials
from improvement_trial_views import read_trial
from improvement_trials import TrialStore
from resonance_store import Principal


@dataclass
class TrialApiDeps:
    read_store: Callable
    write_store: Callable
    resolve_brain: Callable
    owner_session: Callable
    capture_host: Callable
    service: Callable
    active_service: Callable
    checker_registry: dict = field(default_factory=dict)

    def __post_init__(self):
        if not all(callable(getattr(self, name)) for name in
                   ("read_store", "write_store", "resolve_brain", "owner_session", "capture_host", "service", "active_service")):
            raise TypeError("trial_api_dependencies_required")
        if type(self.checker_registry) is not dict:
            raise TypeError("trial_api_checker_registry_required")
        for name in ("read_store", "write_store", "resolve_brain", "capture_host", "service", "active_service"):
            callback = getattr(self, name)
            if inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(getattr(callback, "__call__", None)):
                raise TypeError("synchronous_trial_api_factory_required")


def _identifier(value):
    if type(value) is not str or not IC._ID.fullmatch(value):
        raise TrialRequestError()


def _attempt(value):
    if type(value) is not str or not value.isascii() or not value.isdecimal() or len(value) > 19:
        raise TrialRequestError()
    result = int(value)
    if not 1 <= result < 2 ** 63:
        raise TrialRequestError()
    return result


def _context_view(value):
    keys = ("context_id", "experiment_id", "baseline_context_hash", "scope_hash", "grant_hash", "engine_config_hash",
            "target_generations", "policy_version", "grant_status", "generation")
    result = {key: value[key] for key in keys}
    result["engine_config"] = {key: value["engine_config"][key] for key in ("provider", "engine_id", "model", "reasoning")}
    return result


def _error(error):
    if isinstance(error, TrialRequestError):
        code, reason = error.status_code, error.code
    elif isinstance(error, IM.ConflictError):
        code, reason = 409, "trial_conflict"
    elif isinstance(error, PermissionError):
        code, reason = 403, "trial_scope_unavailable"
    elif isinstance(error, ValueError) and (str(error) == "unknown_brain" or str(error).startswith(("invalid_", "duplicate_"))):
        code, reason = 422, "invalid_trial_request"
    else:
        code, reason = 503, "trial_unavailable"
    message = localefmt.chu("Không thể thực hiện yêu cầu thử nghiệm trong phạm vi hiện tại.",
                           "The trial request is unavailable in the current scope.")
    return JSONResponse({"ok": False, "reason": reason, "error": message}, status_code=code)


def register(app, deps: TrialApiDeps):
    if type(deps) is not TrialApiDeps:
        raise TypeError("trial_api_dependencies_required")

    async def store(write=False):
        assets = await _settled_call(deps.write_store if write else deps.read_store)
        if assets is None and not write:
            raise PermissionError("trial_records_unavailable")
        if type(assets) is not IM.ImprovementStore:
            raise TypeError("concrete_trial_api_store_required")
        return assets

    async def host(selections):
        result = await _settled_call(deps.capture_host, selections)
        if type(result) is not HostContextStore:
            raise TypeError("concrete_trial_api_host_required")
        return result

    async def service(active=False):
        value = await _settled_call(deps.active_service if active else deps.service)
        if value is None and active:
            return None
        if type(value) is not OwnerTrialService:
            raise TypeError("concrete_trial_api_service_required")
        return value

    async def dispatch(request, brain, operation, action, *, response_key, status=200, identifiers=(), attempt=None):
        try:
            # Gate precedes body consumption, path/brain validation and every factory.
            denied = deps.owner_session(request)
            if inspect.isawaitable(denied):
                denied = await denied
            if denied is not None:
                if not isinstance(denied, Response):
                    raise TypeError("trial_owner_session_response_required")
                return denied
            root = await _settled_call(deps.resolve_brain, brain)
            if type(root) is not str or not root:
                raise TypeError("concrete_trial_brain_required")
            principal = Principal("owner", "owner", root)
            for identifier in identifiers:
                _identifier(identifier)
            number = _attempt(attempt) if attempt is not None else None
            data = await parse_body(request, operation) if operation is not None else None
            value = await action(principal, data, number)
            if redact(value) != value:
                raise AssetUnavailable("trial_api_view_unavailable")
            return JSONResponse({"ok": True, response_key: value}, status_code=status)
        except Exception as error:
            # CancelledError is deliberately not caught; owned service/SQL work drains.
            return await _settled_call(_error, error)

    # Fixed recovery route is registered before variable trial paths.
    @app.post("/improvement-trials/recover")
    async def improvement_trial_recover(request: Request, brain: str = "brain"):
        async def action(principal, data, number):
            return await _settled_call(recover_trials, await store(True), principal, event_key=data["event_key"])
        return await dispatch(request, brain, "recover_trials", action, response_key="recovery")

    @app.post("/improvement-contexts/{experiment_id}")
    async def improvement_context_create(request: Request, experiment_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            capture = await host(data["source_selections"])
            value = await _settled_call(capture.create, principal, experiment_id, data["surface"], data["target_ids"],
                source_refs=[entry["source_ref"] for entry in data["source_selections"]], event_key=data["event_key"])
            return _context_view(value)
        return await dispatch(request, brain, "create_context", action, response_key="context", status=201, identifiers=(experiment_id,))

    @app.get("/improvement-contexts/{experiment_id}")
    async def improvement_context_get(request: Request, experiment_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            return _context_view(await _settled_call(HostContextRecords(await store()).get, principal, experiment_id))
        return await dispatch(request, brain, None, action, response_key="context", identifiers=(experiment_id,))

    @app.post("/improvement-contexts/{experiment_id}/revoke")
    async def improvement_context_revoke(request: Request, experiment_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            return _context_view(await _settled_call(HostContextRecords(await store(True)).revoke, principal, experiment_id, **data))
        return await dispatch(request, brain, "revoke_context", action, response_key="context", identifiers=(experiment_id,))

    @app.post("/improvement-contexts/{experiment_id}/snapshots")
    async def improvement_snapshot_create(request: Request, experiment_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            capture = await host(None)
            return await _settled_call(capture.capture_snapshot, principal, experiment_id, **data)
        return await dispatch(request, brain, "capture_snapshot", action, response_key="snapshot", status=201, identifiers=(experiment_id,))

    @app.post("/improvement-contracts")
    async def improvement_contract_create(request: Request, brain: str = "brain"):
        async def action(principal, data, number):
            contract = await _settled_call(project_contract, data, checker_registry=deps.checker_registry)
            return await _settled_call(IC.CaseStore(await store(True)).put_contract, principal, contract, event_key=data["event_key"])
        return await dispatch(request, brain, "create_contract", action, response_key="contract", status=201)

    @app.post("/improvement-contexts/{experiment_id}/case-sets")
    async def improvement_case_set_create(request: Request, experiment_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            associations = TrialCaseAssociations(await host(None), checker_registry=deps.checker_registry)
            return await _settled_call(associations.create, principal, experiment_id, data["contract_id"], data["cases"],
                                       data["seed"], event_key=data["event_key"])
        return await dispatch(request, brain, "create_case_set", action, response_key="case_set", status=201, identifiers=(experiment_id,))

    @app.post("/improvement-bundles")
    async def improvement_bundle_create(request: Request, brain: str = "brain"):
        async def action(principal, data, number):
            return await _settled_call((await store(True)).create_bundle, principal, **data)
        return await dispatch(request, brain, "create_bundle", action, response_key="bundle", status=201)

    @app.post("/improvement-trials")
    async def improvement_trial_start(request: Request, brain: str = "brain"):
        async def action(principal, data, number):
            return await (await service()).start(principal, data)
        return await dispatch(request, brain, "start_trial", action, response_key="trial", status=202)

    @app.get("/improvement-trials/{trial_id}")
    async def improvement_trial_get(request: Request, trial_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            return await _settled_call(read_trial, await store(), principal, trial_id)
        return await dispatch(request, brain, None, action, response_key="trial", identifiers=(trial_id,))

    @app.post("/improvement-trials/{trial_id}/cancel")
    async def improvement_trial_cancel(request: Request, trial_id: str, brain: str = "brain"):
        async def action(principal, data, number):
            active = await service(True)
            if active is not None:
                return await active.cancel(principal, trial_id, **data)
            assets = await store(True)
            await _settled_call(TrialStore(assets).cancel, principal, trial_id, **data)
            return await _settled_call(read_trial, assets, principal, trial_id)
        return await dispatch(request, brain, "cancel_trial", action, response_key="trial", identifiers=(trial_id,))

    @app.post("/improvement-trials/{trial_id}/attempts/{attempt}/owner-decisions")
    async def improvement_owner_decision(request: Request, trial_id: str, attempt: str, brain: str = "brain"):
        async def action(principal, data, number):
            assets = await store(True)
            await _settled_call(OwnerDecisionStore(TrialStore(assets)).record, principal, trial_id, number,
                data["criterion_id"], data["verdict"], **{key: data[key] for key in
                    ("output_hash", "contract_hash", "expected_generation", "event_key")})
            return await _settled_call(read_trial, assets, principal, trial_id)
        return await dispatch(request, brain, "record_owner_decision", action, response_key="trial", identifiers=(trial_id,), attempt=attempt)

    @app.post("/improvement-trials/{trial_id}/attempts/{attempt}/reevaluate")
    async def improvement_trial_reevaluate(request: Request, trial_id: str, attempt: str, brain: str = "brain"):
        async def action(principal, data, number):
            assets = await store(True)
            await _settled_call(TrialResults(TrialStore(assets)).reevaluate, principal, trial_id, number,
                **data, checker_registry=deps.checker_registry)
            return await _settled_call(read_trial, assets, principal, trial_id)
        return await dispatch(request, brain, "reevaluate", action, response_key="trial", identifiers=(trial_id,), attempt=attempt)
