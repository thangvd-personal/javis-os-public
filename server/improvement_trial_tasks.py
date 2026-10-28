"""Process-owned explicit trial requests; no polling, automatic resume or model retry.

The owner service verifies host context/capability before start. This component
retains a committing reservation independently of its HTTP caller, and lifespan
shutdown must await close before closing the underlying source/store lifetimes.
"""
from __future__ import annotations

import asyncio

import improvement_cases as IC
import improvement_store as IM
from improvement_trial_runner import _drain, _settled_call
from improvement_trials import TERMINAL
from improvement_sandbox import WorkspaceCleanupError


class TrialTasks:
    def __init__(self, trials, runner):
        if getattr(runner, "trials", None) is not trials or not callable(getattr(runner, "run", None)):
            raise ValueError("trial_runner_store_mismatch")
        self.trials, self.runner = trials, runner
        self._loop = None
        self._operations = set()
        self._reservations = {}
        self._tasks = {}
        self._started = set()
        self._leases = {}
        self._cleanup_failed = False
        self._closed = False
        self._closing = None

    def _bind(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("trial_tasks_loop_changed")

    @staticmethod
    def _consume(task):
        if not task.cancelled():
            task.exception()  # Evidence belongs in durable trial history, not an unhandled-task log.

    async def _join(self, task):
        values, cancelled = await _drain(asyncio.gather(task, return_exceptions=True))
        if cancelled is not None:
            raise cancelled
        if isinstance(values[0], BaseException):
            raise values[0]
        return values[0]

    async def _operation(self, coroutine, *, reservation=None):
        task = asyncio.create_task(coroutine)
        self._operations.add(task)
        if reservation is not None:
            self._reservations[task] = reservation

        def finished(completed):
            self._operations.discard(completed)
            self._reservations.pop(completed, None)
            self._consume(completed)

        task.add_done_callback(finished)
        return await self._join(task)

    async def start(self, principal, request, *, event_key):
        """A committed explicit request remains owned if its HTTP response is cancelled."""
        self._bind()
        brain = IM._brain(principal)
        if self._closed:
            raise RuntimeError("trial_tasks_closed")
        request = IC._frozen(request)
        reservation = (brain, IM._hash(request))
        return await self._operation(self._reserve_and_start(principal, brain, request, event_key), reservation=reservation)

    async def _reserve_and_start(self, principal, brain, request, event_key):
        row = await _settled_call(self.trials.reserve, principal, request, event_key=event_key)
        identifier = row["trial_id"]
        # An idempotency replay may describe the original reserved generation.
        try:
            current = await asyncio.to_thread(self.trials.get, principal, identifier)
        except (Exception, asyncio.CancelledError):
            try:
                await _settled_call(self.trials.reject_unclaimed, principal, identifier,
                    expected_generation=row["generation"], reason="runner_failed")
            except IM.ConflictError:
                pass  # A replay/foreign host owns this generation, not this pending start.
            except Exception:
                self._cleanup_failed = True
                raise
            raise
        row = current
        key = brain, identifier
        if self._closed:
            try:
                return await self._stop(principal, identifier, shutdown=True)
            except Exception:
                self._cleanup_failed = True
                raise
        if row["status"] == "reserved" and key not in self._started:
            self._started.add(key)
            task = asyncio.create_task(self._run(principal, row))
            self._tasks[key] = principal, task

            def finished(completed):
                if self._tasks.get(key, (None, None))[1] is completed:
                    self._tasks.pop(key)
                    self._leases.pop(key, None)
                self._consume(completed)

            task.add_done_callback(finished)
        return row

    async def _run(self, principal, reserved):
        key = IM._brain(principal), reserved["trial_id"]

        def claimed(row):
            self._leases[key] = row["lease_token"]

        try:
            return await self.runner.run(principal, reserved["trial_id"], on_claimed=claimed)
        except (Exception, asyncio.CancelledError) as error:
            # Unexpected pre-claim errors cannot leave a refundable reservation
            # outstanding. A foreign or acquired lease never grants this CAS.
            if isinstance(error, WorkspaceCleanupError):
                self._cleanup_failed = True
            try:
                if key in self._leases:
                    row = await asyncio.to_thread(self.trials.get, principal, reserved["trial_id"])
                    if row["status"] not in TERMINAL:
                        await _settled_call(self.trials.abort, principal, reserved["trial_id"],
                            expected_generation=row["generation"], lease_token=self._leases[key], reason="runner_failed")
                else:
                    await _settled_call(self.trials.reject_unclaimed, principal, reserved["trial_id"],
                        expected_generation=reserved["generation"], reason="runner_failed")
            except (IM.ConflictError, PermissionError):
                if key in self._leases:
                    self._cleanup_failed = True
            except Exception:
                self._cleanup_failed = True
                raise
            raise

    async def cancel(self, principal, identifier, *, expected_generation, event_key):
        """Revoke durable authority before cancelling an owned native worker task."""
        self._bind()
        IM._brain(principal)
        if self._closed:
            raise RuntimeError("trial_tasks_closed")

        async def cancel_and_wait():
            row = await _settled_call(self.trials.cancel, principal, identifier,
                expected_generation=expected_generation, event_key=event_key)
            await self._cancel_owned(principal, identifier)
            return row

        return await self._operation(cancel_and_wait())

    async def _cancel_owned(self, principal, identifier):
        entry = self._tasks.get((IM._brain(principal), identifier))
        if entry is not None:
            task = entry[1]
            if not task.done() and not task.cancelling():
                task.cancel()
            await _drain(asyncio.gather(task, return_exceptions=True))

    async def drain_owned(self, principal, trial_ids):
        """Join matching reservations, then drain local tasks regardless of DB state.

        The experiment service must first commit its stop fence and join its own
        admission/handoff operations. This method never cancels a foreign worker,
        releases database capacity, or claims that filesystem cleanup succeeded.
        It also remains usable while this supervisor is closing.
        """
        self._bind()
        brain = IM._brain(principal)
        from improvement_execution_limits import MAX_TRIALS
        if (type(trial_ids) is not list or len(trial_ids) > MAX_TRIALS
                or any(type(identifier) is not str or not IC._ID.fullmatch(identifier) for identifier in trial_ids)
                or len(set(trial_ids)) != len(trial_ids)):
            raise ValueError("invalid_trial_drain_selection")
        identifiers = tuple(trial_ids)

        async def drain():
            # Authenticate the complete selection before cancelling any owned
            # task; one foreign ID cannot partly drain a valid selection.
            selected = set()
            for identifier in identifiers:
                row = await asyncio.to_thread(self.trials.get, principal, identifier)
                selected.add((brain, IM._hash(row['request'])))
            pending = tuple(task for task in self._operations if self._reservations.get(task) in selected)
            if pending:
                await _drain(asyncio.gather(*pending, return_exceptions=True))
            for identifier in identifiers:
                await self._cancel_owned(principal, identifier)

        return await self._operation(drain())

    async def _stop(self, principal, identifier, *, shutdown):
        try:
            # Only three state advances exist between reserve and evaluation.
            # A failed CAS must refresh; it cannot cancel a different generation.
            for _ in range(4):
                row = await asyncio.to_thread(self.trials.get, principal, identifier)
                if row["status"] in TERMINAL:
                    return row
                lease = self._leases.get((IM._brain(principal), identifier))
                if lease is None:
                    try:
                        return await _settled_call(self.trials.reject_unclaimed, principal, identifier,
                            expected_generation=row["generation"], reason="cancelled")
                    except IM.ConflictError:
                        # An opaque lease belongs to another host, or our runner
                        # is still acquiring it and will close its own claim.
                        return row
                try:
                    return await _settled_call(self.trials.abort, principal, identifier,
                        expected_generation=row["generation"], lease_token=lease, reason="cancelled")
                except IM.ConflictError:
                    continue
            raise RuntimeError("trial_shutdown_cas_unsettled")
        finally:
            # Even if storage is unavailable, lifespan must terminate its owned
            # processes. Runner then attempts its own writer-bound abort.
            if shutdown:
                await self._cancel_owned(principal, identifier)

    async def close(self):
        self._bind()
        self._closed = True
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
            self._closing.add_done_callback(self._consume)
        return await self._join(self._closing)

    async def _close(self):
        # No new operations may register after _closed is set. In-flight starts
        # see that flag after their reservation and refund without dispatch.
        pending = tuple(self._operations)
        if pending:
            await _drain(asyncio.gather(*pending, return_exceptions=True))
        entries = tuple(self._tasks.items())
        results, _ = await _drain(asyncio.gather(*(
            self._stop(principal, key[1], shutdown=True) for key, (principal, _) in entries
        ), return_exceptions=True))
        if self._cleanup_failed or any(isinstance(value, BaseException) for value in results):
            raise RuntimeError("trial_shutdown_incomplete")
