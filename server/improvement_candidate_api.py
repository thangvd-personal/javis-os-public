"""Browser-owner authoring/review transport with shutdown-owned offloaded work."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse, Response

import localefmt
import improvement_assets as IA
import improvement_cases as IC
import improvement_store as IM
from evidence_store import redact
from improvement_blobs import AssetUnavailable, MAX_ASSET_BYTES
from improvement_candidate_records import CandidateRecords, identifier
from improvement_private_review import PrivateReview
from improvement_skill_candidates import SkillCandidates
from improvement_skill_patch import _FIELDS as PATCH_FIELDS
from improvement_trial_requests import read_json, _json_tree, TrialRequestError
from improvement_trial_runner import _drain, _settled_call
from resonance_store import Principal


class CandidateOperations:
    """Retain complete SQL/blob operations, including reads, through cancellation."""
    def __init__(self):
        self._loop = None
        self._operations = set()
        self._closed = False
        self._closing = None

    def _bind(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("candidate_operations_loop_changed")

    @staticmethod
    def _consume(task):
        if not task.cancelled():
            task.exception()

    @staticmethod
    async def _join(task):
        values, cancelled = await _drain(asyncio.gather(task, return_exceptions=True))
        if cancelled is not None:
            raise cancelled
        if isinstance(values[0], BaseException):
            raise values[0]
        return values[0]

    async def run(self, action):
        self._bind()
        if self._closed:
            raise RuntimeError("candidate_operations_closed")
        task = asyncio.create_task(_settled_call(action))
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        task.add_done_callback(self._consume)
        return await self._join(task)

    async def close(self):
        self._bind()
        self._closed = True
        if self._closing is None:
            async def finish():
                await _drain(asyncio.gather(*tuple(self._operations), return_exceptions=True))
            self._closing = asyncio.create_task(finish())
            self._closing.add_done_callback(self._consume)
        await self._join(self._closing)


@dataclass
class CandidateApiDeps:
    read_store: object
    write_store: object
    resolve_brain: object
    owner_session: object
    operations: object
    pack_state_dir: object

    def __post_init__(self):
        for name in ("read_store", "write_store", "resolve_brain", "owner_session", "operations", "pack_state_dir"):
            callback = getattr(self, name)
            if not callable(callback):
                raise TypeError("candidate_api_dependencies_required")
            if name != "owner_session" and (inspect.iscoroutinefunction(callback)
                    or inspect.iscoroutinefunction(getattr(callback, "__call__", None))):
                raise TypeError("synchronous_candidate_factory_required")


def _integer(value, low=1, high=2 ** 63 - 1):
    if type(value) is not int or not low <= value <= high:
        raise TrialRequestError()


def _validate(value, operation):
    fields = {"region": {"experiment_id", "base_revision_id", "start", "end", "expected_target_generation", "event_key"},
        "stage": {"experiment_id", "patch", "event_key"},
        "authorize": {"expected_generation", "before_hash", "after_hash", "event_key"},
        "revoke": {"expected_generation", "event_key"}, "review": set(), "artifact": {"attempt"}, "baseline": {"target_id"}}
    try:
        _json_tree(value)
        IC._fields(value, fields[operation])
        if "event_key" in value and (type(value["event_key"]) is not str or not 8 <= len(value["event_key"]) <= 160):
            raise TrialRequestError()
        for field in ("experiment_id", "base_revision_id", "target_id"):
            if field in value:
                identifier(value[field])
        if operation == "stage":
            IC._fields(value["patch"], PATCH_FIELDS)
            identifier(value["patch"]["target_id"])
            identifier(value["patch"]["base_revision_id"])
            IC._digest(value["patch"]["before_hash"])
        if operation == "region":
            _integer(value["start"], 0, MAX_ASSET_BYTES)
            _integer(value["end"], value["start"], MAX_ASSET_BYTES)
            _integer(value["expected_target_generation"])
        for field in ("expected_generation", "attempt"):
            if field in value:
                _integer(value[field])
        for field in ("before_hash", "after_hash"):
            if field in value:
                IC._digest(value[field])
        return value
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise TrialRequestError() from None


def _error(error):
    if isinstance(error, TrialRequestError):
        status = error.status_code
        reason = "candidate_request_too_large" if status == 413 else "invalid_candidate_request"
    elif isinstance(error, IM.ConflictError):
        status, reason = 409, "candidate_conflict"
    elif isinstance(error, PermissionError):
        status, reason = 403, "candidate_scope_unavailable"
    elif isinstance(error, ValueError) and not isinstance(error, IA.SourceError):
        status, reason = 400, "invalid_candidate_request"
    else:
        status, reason = 503, "candidate_unavailable"
    return JSONResponse({"ok": False, "reason": reason, "error": localefmt.chu(
        "Không thể thực hiện yêu cầu cải tiến trong phạm vi hiện tại.",
        "The improvement request is unavailable in the current scope.")}, status_code=status)


def register(app, deps: CandidateApiDeps):
    if type(deps) is not CandidateApiDeps:
        raise TypeError("candidate_api_dependencies_required")

    async def dispatch(request, brain, action, *, key, operation=None, write=False, ids=(), status=200, query=None):
        try:
            denied = deps.owner_session(request)
            if inspect.isawaitable(denied):
                denied = await denied
            if denied is not None:
                if not isinstance(denied, Response):
                    raise TypeError("candidate_owner_session_response_required")
                return denied
            for value in ids:
                identifier(value)
            if query is not None:
                query()
            root = await _settled_call(deps.resolve_brain, brain)
            if type(root) is not str or not root:
                raise TypeError("candidate_brain_required")
            principal = Principal("owner", "owner", root)
            body = _validate(await read_json(request), operation) if operation is not None else None

            def perform():
                assets = deps.write_store() if write else deps.read_store()
                if assets is not None and type(assets) is not IM.ImprovementStore:
                    raise TypeError("concrete_candidate_store_required")
                return action(assets, principal, body)

            if operation is None:
                value = await _settled_call(perform)
            else:
                owner = await _settled_call(deps.operations)
                if type(owner) is not CandidateOperations:
                    raise TypeError("concrete_candidate_operations_required")
                value = await owner.run(perform)
            if redact(value) != value:
                raise AssetUnavailable("candidate_view_unavailable")
            return JSONResponse({"ok": True, key: value}, status_code=status)
        except Exception as error:
            # Cancellation is a BaseException; retained work settles before it escapes.
            return _error(error)

    def required(assets):
        if assets is None:
            raise PermissionError("candidate_records_unavailable")
        return assets

    def skills(assets):
        return SkillCandidates(required(assets), pack_state_dir=deps.pack_state_dir())

    def private(assets):
        return PrivateReview(required(assets), pack_state_dir=deps.pack_state_dir())

    @app.get("/improvement-candidate-contexts")
    async def improvement_candidate_contexts(request: Request, brain: str = "brain", slug: str = "", cursor: str = "", limit: str = "50"):
        def query():
            if (not IA._SLUG.fullmatch(slug) or slug in IA._DEVICES or len(limit) > 3
                    or not limit.isascii() or not limit.isdecimal() or not 1 <= int(limit) <= 100):
                raise TrialRequestError()
            if cursor:
                identifier(cursor)
        return await dispatch(request, brain, lambda assets, principal, _: CandidateRecords(assets).contexts(
            principal, f"skills/{slug}/SKILL.md", cursor=cursor or None, limit=int(limit)) if assets else {"items": [], "next_cursor": None},
            key="contexts", query=query)

    @app.get("/improvement-candidates")
    async def improvement_candidates(request: Request, brain: str = "brain", experiment_id: str = ""):
        return await dispatch(request, brain, lambda assets, principal, _: CandidateRecords(assets).list(principal, experiment_id) if assets else [],
                              key="candidates", ids=(experiment_id,))

    @app.post("/improvement-edit-regions")
    async def improvement_edit_region(request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, body: skills(assets).declare_region(principal,
            body["experiment_id"], body["base_revision_id"], body["start"], body["end"],
            expected_target_generation=body["expected_target_generation"], event_key=body["event_key"]),
            key="authority", operation="region", write=True, status=201)

    @app.post("/improvement-candidates")
    async def improvement_candidate_stage(request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, body: skills(assets).stage(principal,
            body["experiment_id"], body["patch"], event_key=body["event_key"]), key="candidate", operation="stage", write=True, status=201)

    @app.post("/improvement-candidates/{candidate_id}/review")
    async def improvement_candidate_review(candidate_id: str, request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, _: private(assets).candidate(principal, candidate_id),
                              key="review", operation="review", ids=(candidate_id,))

    @app.post("/improvement-candidates/{candidate_id}/authorize")
    async def improvement_candidate_authorize(candidate_id: str, request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, body: skills(assets).authorize_diff(principal, candidate_id,
            expected_generation=body["expected_generation"], before_hash=body["before_hash"], after_hash=body["after_hash"], event_key=body["event_key"]),
            key="authority", operation="authorize", write=True, ids=(candidate_id,))

    @app.post("/improvement-edit-authorities/{authority_id}/revoke")
    async def improvement_candidate_authority_revoke(authority_id: str, request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, body: CandidateRecords(required(assets)).revoke_authority(
            principal, authority_id, expected_generation=body["expected_generation"], event_key=body["event_key"]),
            key="authority", operation="revoke", write=True, ids=(authority_id,))

    @app.post("/improvement-trials/{trial_id}/artifact-review")
    async def improvement_artifact_review(trial_id: str, request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, body: private(assets).artifact(principal, trial_id, body["attempt"]),
                              key="review", operation="artifact", ids=(trial_id,))

    @app.post("/improvement-candidate-baselines/{experiment_id}/review")
    async def improvement_candidate_baseline_review(experiment_id: str, request: Request, brain: str = "brain"):
        return await dispatch(request, brain, lambda assets, principal, body: private(assets).baseline(
            principal, experiment_id, body["target_id"]), key="review", operation="baseline", ids=(experiment_id,))
