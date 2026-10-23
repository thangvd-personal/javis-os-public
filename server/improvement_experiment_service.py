"""Owned experiment operations over the existing trial dispatcher and ledger."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import closing

import improvement_store as IM
from improvement_comparison_plans import ExperimentPlans
from improvement_experiments import _generation, _identifier
from improvement_experiment_schema import TERMINAL_PHASES
from improvement_trial_records import _brain
from improvement_trial_runner import _drain, _settled_call
from improvement_trial_service import OwnerTrialService
from improvement_trials import TERMINAL


class ExperimentService:
    def __init__(self, plans, trial_service):
        if (type(plans) is not ExperimentPlans or type(trial_service) is not OwnerTrialService
                or plans.host is not trial_service.host or plans.assets is not trial_service.assets
                or plans.admission.trials.store is not trial_service.trials.store
                or plans.admission.trials.workspace.root != trial_service.trials.workspace.root):
            raise TypeError("concrete_experiment_service_required")
        self.plans, self.records, self.assets = plans, plans.records, plans.assets
        self.store, self.trial_service = plans.store, trial_service
        self.trials, self.tasks = trial_service.trials, trial_service.tasks
        from improvement_experiment_admission import ExperimentAdmission
        self.admission = ExperimentAdmission(plans)
        self._operations = {}
        self._loop = None
        self._closed = False
        self._closing = None

    def _bind(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("experiment_service_loop_changed")

    @staticmethod
    def _consume(task):
        if not task.cancelled():
            task.exception()

    async def _join(self, task):
        values, cancelled = await _drain(asyncio.gather(task, return_exceptions=True))
        if cancelled is not None:
            raise cancelled
        if isinstance(values[0], BaseException):
            raise values[0]
        return values[0]

    async def _operation(self, principal, experiment_id, event_key, kind, payload, callback):
        self._bind()
        brain = _brain(principal)
        _identifier(experiment_id)
        # Match the shared event-key contract before allocating an owned task.
        if type(event_key) is not str or not 8 <= len(event_key) <= 160:
            raise ValueError("invalid_event_key")
        if self._closed:
            raise RuntimeError("experiment_service_closed")
        key = brain, experiment_id, event_key
        digest = IM._hash([kind, payload])
        existing = self._operations.get(key)
        if existing is not None:
            if existing[0] != digest:
                raise IM.ConflictError("experiment_operation_identity_conflict")
            return await self._join(existing[2])
        task = asyncio.create_task(callback())
        self._operations[key] = digest, kind, task

        def finished(completed):
            if self._operations.get(key, (None, None, None))[2] is completed:
                self._operations.pop(key)
            self._consume(completed)

        task.add_done_callback(finished)
        return await self._join(task)

    def _snapshot(self, connection, brain, experiment_id):
        links = [dict(row) for row in connection.execute(
            "SELECT l.*,t.status AS trial_status,t.generation AS trial_generation,t.attempt AS current_attempt,"
            "t.request_hash AS actual_request_hash FROM improvement_experiment_trial_links l "
            "JOIN improvement_trials t ON t.id=l.trial_id AND t.brain_id=l.brain_id "
            "WHERE l.brain_id=? AND l.experiment_id=? ORDER BY l.comparison_id,l.cell_position LIMIT 12001",
            (brain, experiment_id))]
        attempts = [dict(row) for row in connection.execute(
            "SELECT a.trial_id,a.attempt,a.status,a.call_id,a.engine_at,a.materialization_hash,a.workspace_json "
            "FROM improvement_trial_attempts a "
            "JOIN improvement_experiment_trial_links l ON l.trial_id=a.trial_id AND l.brain_id=a.brain_id "
            "WHERE l.brain_id=? AND l.experiment_id=? ORDER BY a.trial_id,a.attempt LIMIT 24001",
            (brain, experiment_id))]
        recorded_attempts = {(a["trial_id"], a["attempt"]) for a in attempts}
        if (len(links) > 12000 or len(attempts) > 24000
                or any(link["request_hash"] != link["actual_request_hash"] for link in links)
                or any((link["trial_id"], link["current_attempt"]) not in recorded_attempts for link in links)):
            raise IM.ConflictError("experiment_trial_history_changed")
        return links, attempts

    def _finish_stop(self, principal, experiment_id):
        """Filesystem work outside the writer; final CAS under the real brain lock."""
        brain = _brain(principal)
        workspace = self.trials.workspace
        handle = workspace.acquire_brain_execution(brain)
        if handle is None:
            return self.records.get(principal, experiment_id)
        try:
            with closing(self.store._conn()) as connection:
                row = self.records._row(connection, brain, experiment_id)
                current = self.records._public(row)
                if row["phase"] != "stopping":
                    return current
                identity = [row[key] for key in ("generation", "stop_kind", "stop_event_key", "stop_reason", "definition_hash")]
                snapshot = self._snapshot(connection, brain, experiment_id)
            if (any(row["trial_status"] not in TERMINAL for row in snapshot[0])
                    or any(row["status"] not in TERMINAL for row in snapshot[1])):
                return current
            for attempt in snapshot[1]:
                receipt = json.loads(attempt["workspace_json"]) if attempt["workspace_json"] is not None else None
                if not workspace.cleanup_proven(attempt["trial_id"], attempt["attempt"], receipt):
                    return current
            event_key = "experiment-finish-" + IM._hash([brain, experiment_id, identity[2]])
            payload = {"experiment_id": experiment_id, "stop_identity": identity}
            with self.store._Tx(self.store) as connection:
                row = self.records._row(connection, brain, experiment_id)
                if row["phase"] in TERMINAL_PHASES:
                    return self.records._public(row)
                if (row["phase"] != "stopping"
                        or [row[key] for key in ("generation", "stop_kind", "stop_event_key", "stop_reason", "definition_hash")] != identity
                        or self._snapshot(connection, brain, experiment_id) != snapshot):
                    raise IM.ConflictError("experiment_stop_state_changed")
                connection.execute("UPDATE improvement_experiments SET phase=stop_kind,generation=generation+1,updated_at=? "
                    "WHERE id=? AND brain_id=?", (time.time(), experiment_id, brain))
                result = self.records._public(self.records._row(connection, brain, experiment_id))
                self.assets._event(connection, principal, event_key, "experiment_finish_stop", payload, result)
                return result
        finally:
            handle.close()

    async def _settle_stop(self, principal, experiment_id):
        brain = _brain(principal)
        # The durable stopping fence precedes this join. No admitted native
        # handoff can escape the following refreshed immutable link snapshot.
        pending = tuple(entry[2] for key, entry in self._operations.items()
                        if key[:2] == (brain, experiment_id) and entry[1] == "next")
        if pending:
            await _drain(asyncio.gather(*pending, return_exceptions=True))

        def snapshot():
            with closing(self.store._conn()) as connection:
                row = self.records._row(connection, brain, experiment_id)
                current = self.records._public(row)
                if row["phase"] != "stopping":
                    return current, None, []
                return current, row["stop_event_key"], self._snapshot(connection, brain, experiment_id)[0]

        current, stop_key, links = await _settled_call(snapshot)
        if stop_key is None:
            return current
        identifiers = [link["trial_id"] for link in links]
        for identifier in identifiers:
            # A bounded number of actual reserve/prepare/run/evaluate advances.
            for _ in range(5):
                current = await asyncio.to_thread(self.trials.get, principal, identifier)
                if current["status"] in TERMINAL:
                    break
                try:
                    await self.trial_service.cancel(principal, identifier,
                        expected_generation=current["generation"],
                        event_key="experiment-cancel-" + IM._hash([brain, experiment_id, stop_key, identifier, current["generation"]]))
                    break
                except IM.ConflictError:
                    continue
        await self.tasks.drain_owned(principal, identifiers)
        return await _settled_call(self._finish_stop, principal, experiment_id)

    async def cancel(self, principal, experiment_id, *, expected_generation, event_key):
        _generation(expected_generation)

        async def stop():
            acknowledgment = await _settled_call(self.records.begin_stop, principal, experiment_id,
                kind="cancelled", reason="owner_requested", expected_generation=expected_generation, event_key=event_key)
            current = await self._settle_stop(principal, experiment_id)
            return {"acknowledgment": acknowledgment, "current": current}

        return await self._operation(principal, experiment_id, event_key, "cancel",
            {"expected_generation": expected_generation}, stop)

    def _next_slot(self, principal, experiment_id, comparison_id, expected_generation, event_key):
        brain = _brain(principal)
        with closing(self.store._conn()) as connection:
            # An admission replay already chose its immutable slot. Recover it
            # before current-state checks, then Admission verifies the exact event.
            event = connection.execute("SELECT operation,result_json FROM improvement_events WHERE brain_id=? AND event_key=?",
                                       (brain, event_key)).fetchone()
            if event is not None:
                if event["operation"] != "experiment_admit_slot":
                    raise IM.ConflictError("event_payload_conflict")
                saved = json.loads(event["result_json"])
                trial = self.trials._row(connection, brain, saved["trial_id"])
                return json.loads(trial["request_json"])["lineage"]["cell_position"]
            row = self.records._row(connection, brain, experiment_id)
            self.records._public(row)
            if row["generation"] != expected_generation or row["phase"] not in ("probing", "comparing", "qualifying"):
                raise IM.ConflictError("experiment_state_stale")
            comparison = connection.execute("SELECT * FROM improvement_comparisons WHERE brain_id=? AND experiment_id=? AND id=?",
                                            (brain, experiment_id, comparison_id)).fetchone()
            if comparison is None:
                raise PermissionError("experiment_comparison_unavailable")
            links = list(connection.execute("SELECT l.cell_position,t.status FROM improvement_experiment_trial_links l "
                "JOIN improvement_trials t ON t.id=l.trial_id AND t.brain_id=l.brain_id "
                "WHERE l.brain_id=? AND l.comparison_id=? ORDER BY l.cell_position", (brain, comparison_id)))
            if ([row["cell_position"] for row in links] != list(range(len(links)))
                    or any(row["status"] not in TERMINAL for row in links)):
                raise IM.ConflictError("experiment_cell_pending_or_gapped")
            if len(links) >= comparison["cell_count"]:
                raise IM.ConflictError("experiment_comparison_exhausted")
            return len(links)

    async def next(self, principal, experiment_id, comparison_id, *, expected_generation, event_key):
        _identifier(comparison_id)
        _generation(expected_generation)

        async def start():
            admitted, handed_off = None, False
            try:
                position = await _settled_call(self._next_slot, principal, experiment_id, comparison_id, expected_generation, event_key)
                admitted = await _settled_call(self.admission.admit, principal, experiment_id, comparison_id, position,
                    expected_generation=expected_generation, event_key=event_key)
                current = await asyncio.to_thread(self.trials.get, principal, admitted.trial_id)
                state = await asyncio.to_thread(self.records.get, principal, experiment_id)
                if current["status"] == "reserved":
                    if self._closed or state["phase"] in (*TERMINAL_PHASES, "stopping", "blocked"):
                        await self.trial_service._abandon_unclaimed(principal, admitted)
                    else:
                        await self.tasks.start(principal, admitted.request, event_key=admitted.reserve_event_key)
                        handed_off = True
                from improvement_trial_views import read_trial
                return await _settled_call(read_trial, self.assets, principal, admitted.trial_id)
            except (Exception, asyncio.CancelledError):
                if admitted is not None and not handed_off:
                    await self.trial_service._abandon_unclaimed(principal, admitted)
                raise

        return await self._operation(principal, experiment_id, event_key, "next",
            {"comparison_id": comparison_id, "expected_generation": expected_generation}, start)

    async def control(self, principal, experiment_id, operation, value):
        from improvement_experiment_requests import validate
        data = validate(value, operation)
        if operation not in ("create", "bind", "freeze", "report", "select"):
            raise ValueError("invalid_experiment_control")
        if operation == "create" and data["experiment_id"] != experiment_id:
            raise ValueError("invalid_experiment_identity")
        event_key = data["event_key"]
        arguments = {key: item for key, item in data.items() if key != "event_key"}

        def write():
            if operation == "create":
                return self.plans.create(principal, arguments, event_key=event_key)
            if operation == "bind":
                return self.plans.bind_arm(principal, experiment_id, event_key=event_key, **arguments)
            if operation == "freeze":
                return self.plans.freeze(principal, experiment_id, event_key=event_key, **arguments)
            if operation == "select":
                return self.plans.select(principal, experiment_id, event_key=event_key, **arguments)
            with closing(self.store._conn()) as connection:
                self.records._row(connection, _brain(principal), experiment_id)
                if connection.execute("SELECT 1 FROM improvement_comparisons WHERE id=? AND brain_id=? AND experiment_id=?",
                    (data["comparison_id"], _brain(principal), experiment_id)).fetchone() is None:
                    raise PermissionError("experiment_comparison_unavailable")
            from improvement_comparison_results import ComparisonResults
            return ComparisonResults(self.plans).create(principal, event_key=event_key, **arguments)

        async def execute():
            acknowledgment = await _settled_call(write)
            if operation == "select" and data["choice"] == "keep_base":
                return {"acknowledgment": acknowledgment, "current": await self._settle_stop(principal, experiment_id)}
            return acknowledgment

        return await self._operation(principal, experiment_id, event_key, operation, data, execute)

    def _resume(self, principal, experiment_id, expected_generation, event_key):
        """Explicit preflight for observable source/runtime blockers, never regrant."""
        from improvement_claude import ClaudeTrialAdapter
        from improvement_experiment_protocol import definition
        brain = _brain(principal)
        # The owner event pins owner intent, not a browser-supplied fingerprint.
        payload = {"experiment_id": experiment_id, "expected_generation": expected_generation}
        with closing(self.store._conn()) as connection:
            replay = self.assets._replay(connection, brain, event_key, "experiment_owner_resume", payload)
            if replay is not None:
                return replay
            row = self.records._row(connection, brain, experiment_id)
            self.records._public(row)
            if row["generation"] != expected_generation or row["phase"] != "blocked":
                raise IM.ConflictError("experiment_state_stale")
            blocker = json.loads(row["blocker_json"])
            if blocker["code"] not in ("source_unavailable", "runtime_unavailable"):
                raise PermissionError("experiment_blocker_resume_unavailable")
            frozen = definition(json.loads(row["definition_json"]))
            public = {key: frozen[key] for key in ("experiment_id", "contract_id", "case_set_id", "base_bundle_id", "timeout_seconds")}
            public.update(expected_context_generation=frozen["context_generation"], repeat_policy=frozen["repeat_policy"]["kind"])
            prepared = self.plans._prepare(connection, principal, public)
            for arm in connection.execute("SELECT ordinal FROM improvement_experiment_arms WHERE brain_id=? AND experiment_id=?",
                                          (brain, experiment_id)):
                self.plans._frozen_arm(connection, principal, row, prepared, arm["ordinal"])
        self.plans.host._snapshot_observe(principal, prepared[1])
        available = ClaudeTrialAdapter(prepared[1]["engine_config"]).verify()
        if type(available) is not dict or available.get("supported") is not True:
            raise PermissionError("experiment_runtime_unavailable")
        from improvement_cases import _digest
        _digest(available.get("capability_hash"))
        fingerprint = IM._hash({"code": blocker["code"], "status": "available", "capability_hash": available["capability_hash"],
            "binding_hash": frozen["binding_hash"], "engine_config_hash": frozen["engine_config_hash"]})
        self.plans.host._snapshot_observe(principal, prepared[1])
        internal_key = "experiment-resume-" + IM._hash([brain, event_key])
        # Resume owns no worker/capacity transfer. Actual dispatch repeats the
        # full source/arm/native guard after this metadata transition.
        with self.store._Tx(self.store) as connection:
            replay = self.assets._replay(connection, brain, event_key, "experiment_owner_resume", payload)
            if replay is not None:
                return replay
            result = self.records._resume_in(connection, principal, experiment_id, observed_fingerprint=fingerprint,
                expected_generation=expected_generation, event_key=internal_key)
            self.assets._event(connection, principal, event_key, "experiment_owner_resume", payload, result)
        return result

    async def resume(self, principal, experiment_id, *, expected_generation, event_key):
        _generation(expected_generation)
        return await self._operation(principal, experiment_id, event_key, "resume",
            {"expected_generation": expected_generation},
            lambda: _settled_call(self._resume, principal, experiment_id, expected_generation, event_key))

    def _recover_ack(self, principal, experiment_id, event_key):
        brain = _brain(principal)
        payload = {"experiment_id": experiment_id}
        with self.store._Tx(self.store) as connection:
            saved = self.assets._replay(connection, brain, event_key, "experiment_recover", payload)
            if saved is not None:
                return saved
            result = self.records._public(self.records._row(connection, brain, experiment_id))
            self.assets._event(connection, principal, event_key, "experiment_recover", payload, result)
            return result

    async def recover(self, principal, experiment_id, *, event_key):
        async def recover():
            acknowledgment = await _settled_call(self._recover_ack, principal, experiment_id, event_key)
            await _settled_call(self._recover_linked, principal, experiment_id)
            current = await self._settle_stop(principal, experiment_id)
            return {"acknowledgment": acknowledgment, "current": current}

        return await self._operation(principal, experiment_id, event_key, "recover", {}, recover)

    def _recover_linked(self, principal, experiment_id):
        """Settle and clean only actual links; broad owner recovery stays separate."""
        brain = _brain(principal)
        with self.store._Tx(self.store) as connection:
            self.records._public(self.records._row(connection, brain, experiment_id))
            identifiers = [row[0] for row in connection.execute(
                "SELECT trial_id FROM improvement_experiment_trial_links WHERE brain_id=? AND experiment_id=? "
                "ORDER BY trial_id LIMIT 12001", (brain, experiment_id))]
            if len(identifiers) > 12000:
                raise IM.ConflictError("experiment_trial_history_changed")
            result = self.trials._recover_in(connection, principal, trial_ids=identifiers)
        for identifier in identifiers:
            self.trials.cleanup_terminal(principal, identifier)
        return result

    async def close(self):
        self._bind()
        self._closed = True
        if self._closing is None:
            pending = tuple(entry[2] for entry in self._operations.values())

            async def close_operations():
                if pending:
                    await _drain(asyncio.gather(*pending, return_exceptions=True))

            self._closing = asyncio.create_task(close_operations())
            self._closing.add_done_callback(self._consume)
        return await self._join(self._closing)
