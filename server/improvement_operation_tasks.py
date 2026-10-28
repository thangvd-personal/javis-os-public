"""Own explicit role admission and its actual runner independently of HTTP."""
from __future__ import annotations

import asyncio

import improvement_store as IM
from improvement_operation_execution import OperationExecution
from improvement_operation_runner import OperationRunner
from improvement_operation_schema import TERMINAL
from improvement_operation_workspace import OperationWorkspaceCleanupError
from improvement_trial_runner import _drain, _settled_call


class OperationTasks:
    def __init__(self, execution, runner):
        if type(execution) is not OperationExecution or type(runner) is not OperationRunner or runner.execution is not execution:
            raise TypeError("concrete_operation_tasks_required")
        self.execution, self.runner = execution, runner
        self._loop, self._closing = None, None
        self._operations, self._tasks, self._leases, self._started = {}, {}, {}, set()
        self._closed, self._cleanup_failed = False, False

    def _bind(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("operation_tasks_loop_changed")

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

    async def start(self, principal, identifier, *, expected_generation, event_key):
        self._bind()
        brain = IM._brain(principal)
        if self._closed or self._cleanup_failed:
            raise RuntimeError("operation_tasks_closed")
        from improvement_experiment_protocol import _id
        from improvement_experiments import _generation
        _id(identifier)
        _generation(expected_generation)
        if type(event_key) is not str or not 8 <= len(event_key) <= 160:
            raise ValueError("invalid_event_key")
        return await self._operation(principal, event_key, identifier, ["start", identifier, expected_generation],
            lambda: self._reserve_and_start(principal, identifier, expected_generation, event_key))

    async def _operation(self, principal, event_key, identifier, payload, factory):
        key, digest = (IM._brain(principal), event_key), IM._hash(payload)
        prior = self._operations.get(key)
        if prior is not None:
            if prior[0] != digest:
                raise IM.ConflictError("operation_start_identity_conflict")
            return await self._join(prior[1])
        task = asyncio.create_task(factory())
        self._operations[key] = digest, task, identifier, principal

        def finished(done):
            if self._operations.get(key, (None, None))[1] is done:
                self._operations.pop(key)
            self._consume(done)

        task.add_done_callback(finished)
        return await self._join(task)

    async def _reserve_and_start(self, principal, identifier, generation, event_key):
        reserved = await _settled_call(self.execution.reserve, principal, identifier, expected_generation=generation, event_key=event_key)
        try:
            current = await asyncio.to_thread(self.execution.get, principal, identifier)
        except BaseException:
            try:
                await _settled_call(self.execution.reject_unclaimed, principal, identifier,
                    expected_generation=reserved["generation"], reason="runner_failed")
            except IM.ConflictError:
                pass
            except BaseException:
                self._cleanup_failed = True
                raise
            raise
        key = current["brain_id"], identifier
        if self._closed:
            if current["status"] == "reserved":
                return await _settled_call(self.execution.reject_unclaimed, principal, identifier,
                    expected_generation=current["generation"], reason="cancelled")
            return current
        attempt_key = (*key, current["attempt"])
        if current["status"] == "reserved" and attempt_key not in self._started and key not in self._tasks:
            self._started.add(attempt_key)
            task = asyncio.create_task(self._run(principal, reserved))
            self._tasks[key] = principal, task

            def finished(done):
                if self._tasks.get(key, (None, None))[1] is done:
                    self._tasks.pop(key)
                    self._leases.pop(key, None)
                self._consume(done)

            task.add_done_callback(finished)
        return current

    async def _run(self, principal, reserved):
        key = IM._brain(principal), reserved["operation_id"]

        def claimed(row):
            self._leases[key] = row["lease_token"]

        try:
            while True:
                result = await self.runner.run(principal, reserved["operation_id"], on_claimed=claimed)
                attempts = result["attempts"]
                if (self._closed or result["attempt"] != 1 or result["status"] not in ("not_run", "interrupted")
                        or not attempts or attempts[-1]["failure_reason"] not in ("capacity_unavailable", "native_transient_error")):
                    return result
                # The first runner has closed its worker/result/cleanup lifetime.
                # Retain the actual retry reservation through repeated cancellation.
                self._leases.pop(key, None)
                retry = asyncio.create_task(asyncio.to_thread(self.execution.retry, principal, reserved["operation_id"],
                    expected_generation=result["generation"], event_key="operation-auto-retry:" + reserved["operation_id"]))
                values, cancelled = await _drain(asyncio.gather(retry, return_exceptions=True))
                retried = values[0]
                if isinstance(retried, dict):
                    reserved = retried
                    self._started.add((*key, retried["attempt"]))
                if cancelled is not None:
                    if isinstance(retried, dict):
                        await _settled_call(self.execution.reject_unclaimed, principal, reserved["operation_id"],
                            expected_generation=reserved["generation"], reason="cancelled")
                    raise cancelled
                if isinstance(retried, BaseException):
                    if isinstance(retried, (PermissionError, IM.ConflictError, ValueError)):
                        return await self.runner._response(principal, reserved["operation_id"])
                    raise retried
        except BaseException as error:
            if isinstance(error, OperationWorkspaceCleanupError):
                self._cleanup_failed = True
            try:
                current = await asyncio.to_thread(self.execution.get, principal, reserved["operation_id"])
                if current["status"] not in TERMINAL:
                    if key in self._leases:
                        await _settled_call(self.execution.abort, principal, reserved["operation_id"],
                            expected_generation=current["generation"], lease_token=self._leases[key], reason="runner_failed")
                    else:
                        await _settled_call(self.execution.reject_unclaimed, principal, reserved["operation_id"],
                            expected_generation=reserved["generation"], reason="runner_failed")
            except (IM.ConflictError, PermissionError):
                if key in self._leases:
                    self._cleanup_failed = True
            except BaseException:
                self._cleanup_failed = True
                raise
            raise

    async def join(self, principal, identifier):
        self._bind()
        key = IM._brain(principal), identifier
        entry = self._tasks.get(key)
        if entry is not None:
            return await self._join(entry[1])
        return await self.runner._response(principal, identifier)

    async def _cancel_owned(self, principal, identifier):
        entry = self._tasks.get((IM._brain(principal), identifier))
        if entry is not None:
            entry[1].cancel()
            values, cancelled = await _drain(asyncio.gather(entry[1], return_exceptions=True))
            if isinstance(values[0], BaseException) and not isinstance(values[0], asyncio.CancelledError):
                raise values[0]
            if cancelled is not None:
                raise cancelled

    async def cancel(self, principal, identifier, *, expected_generation, event_key):
        self._bind()
        brain = IM._brain(principal)
        if self._closed:
            raise RuntimeError("operation_tasks_closed")
        if type(event_key) is not str or not 8 <= len(event_key) <= 160:
            raise ValueError("invalid_event_key")

        async def stop():
            row = await _settled_call(self.execution.cancel, principal, identifier,
                                     expected_generation=expected_generation, event_key=event_key)
            await self._cancel_owned(principal, identifier)
            return row

        return await self._operation(principal, event_key, identifier, ["cancel", identifier, expected_generation], stop)

    async def close(self):
        self._bind()
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._close())
        return await self._join(self._closing)

    async def _close(self):
        operations = list(self._operations.items())
        pending = [value[1] for _, value in operations]
        if pending:
            values = await asyncio.gather(*pending, return_exceptions=True)
            for ((brain, _), (_, _, identifier, principal)), value in zip(operations, values):
                if isinstance(value, BaseException):
                    # Preflight can legitimately fail before reserving anything.
                    # A committing failure must not be hidden as quiescent.
                    try:
                        current = await asyncio.to_thread(self.execution.get, principal, identifier)
                        if current["attempt"] and current["status"] not in TERMINAL and (brain, identifier) not in self._tasks:
                            await _settled_call(self.execution.reject_unclaimed, principal, identifier,
                                expected_generation=current["generation"], reason="runner_failed")
                    except BaseException:
                        self._cleanup_failed = True
        entries = list(self._tasks.items())
        try:
            for key, (principal, task) in entries:
                current = await asyncio.to_thread(self.execution.get, principal, key[1])
                if current["status"] not in TERMINAL:
                    if key in self._leases:
                        await _settled_call(self.execution.abort, principal, key[1], expected_generation=current["generation"],
                            lease_token=self._leases[key], reason="cancelled")
                    else:
                        try:
                            await _settled_call(self.execution.reject_unclaimed, principal, key[1],
                                expected_generation=current["generation"], reason="cancelled")
                        except IM.ConflictError:
                            pass
        finally:
            for _, (_, task) in entries:
                task.cancel()
            values = await asyncio.gather(*(task for _, (_, task) in entries), return_exceptions=True)
            if any(isinstance(value, BaseException) and not isinstance(value, asyncio.CancelledError) for value in values):
                self._cleanup_failed = True
        if self._cleanup_failed:
            raise OperationWorkspaceCleanupError()
        return True
