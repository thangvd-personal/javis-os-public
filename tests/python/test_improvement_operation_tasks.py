"""Actual role admission ownership survives HTTP cancellation and shutdown."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import importlib.util
import threading
import unittest
from unittest.mock import patch

import test_improvement_operation_execution as F
import test_improvement_operation_runner as R
from improvement_operation_adapter import ClaudeOperationAdapter


class OperationTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = F.OperationExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.execution, self.owner, self.row = self.f.execution(), self.f.owner, self.f.operation
        self.acked, self.closed = asyncio.Event(), asyncio.Event()
        self.hang = False
        self.body = '{"note":"JAVIS_LESSON: untrusted; never write production memory or jobs"}'

    def adapter(self, *args, **kwargs):
        return R.OperationRunnerTests.adapter(self, *args, **kwargs)

    def controller(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_operation_tasks"), "Owned role task supervisor is absent")
        from improvement_operation_runner import OperationRunner
        from improvement_operation_tasks import OperationTasks
        return OperationTasks(self.execution, OperationRunner(self.execution, adapter_factory=self.adapter))

    async def start(self, tasks):
        with patch.object(ClaudeOperationAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}):
            return await tasks.start(self.owner, self.row["operation_id"], expected_generation=1, event_key="owner-start-role")

    async def test_committed_role_task_runs_to_actual_result_then_shutdown_is_quiescent(self):
        tasks = self.controller()
        try:
            started = await self.start(tasks)
            self.assertEqual(started["status"], "reserved")
            result = await tasks.join(self.owner, self.row["operation_id"])
            self.assertEqual(result["status"], "completed")
            self.assertIsNotNone(result["result"])
        finally:
            await tasks.close()
        self.assertTrue(self.closed.is_set())

    async def test_shutdown_owns_a_committing_preflight_after_repeated_http_cancel(self):
        tasks = self.controller()
        entered, release = threading.Event(), threading.Event()
        reserve = self.execution.reserve

        def blocked(*args, **kwargs):
            result = reserve(*args, **kwargs)
            entered.set()
            if not release.wait(15):
                raise RuntimeError("synthetic admission timeout")
            return result

        with patch.object(self.execution, "reserve", side_effect=blocked):
            caller = asyncio.create_task(self.start(tasks))
            closer = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 15))
                caller.cancel()
                await asyncio.sleep(.02)
                caller.cancel()
                self.assertFalse(caller.done())
                closer = asyncio.create_task(tasks.close())
                await asyncio.sleep(.02)
                self.assertFalse(closer.done())
            finally:
                release.set()
                await asyncio.gather(caller, return_exceptions=True)
                if closer is not None:
                    await closer
                else:
                    await tasks.close()
        result = self.execution.get(self.owner, self.row["operation_id"])
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(self.execution.ledger.get(result["attempts"][-1]["call_id"])["status"], "refunded")
        self.assertFalse(self.acked.is_set())

    async def test_terminal_db_cancel_replay_still_drains_actual_live_role_worker(self):
        tasks = self.controller()
        self.hang = True
        try:
            await self.start(tasks)
            await asyncio.wait_for(self.acked.wait(), 15)
            current = self.execution.get(self.owner, self.row["operation_id"])
            payload = {"expected_generation": current["generation"], "event_key": "owner-cancel-live-role"}
            stopped = self.execution.cancel(self.owner, self.row["operation_id"], **payload)
            self.assertEqual(stopped["status"], "cancelled")
            self.assertFalse(self.closed.is_set())
            await tasks.cancel(self.owner, self.row["operation_id"], **payload)
            self.assertTrue(self.closed.is_set())
            path, _ = self.execution.workspace._paths(self.row["operation_id"], 1)
            self.assertFalse(path.exists())
            self.assertEqual(self.execution.ledger.get(stopped["attempts"][-1]["call_id"])["status"], "interrupted")
        finally:
            await tasks.close()

    async def test_shutdown_owns_inflight_cancel_after_http_response_is_cancelled(self):
        tasks = self.controller()
        self.hang = True
        await self.start(tasks)
        await asyncio.wait_for(self.acked.wait(), 15)
        row = self.execution.get(self.owner, self.row["operation_id"])
        entered, release = threading.Event(), threading.Event()
        cancel = self.execution.cancel

        def blocked(*args, **kwargs):
            result = cancel(*args, **kwargs)
            entered.set()
            if not release.wait(15):
                raise RuntimeError("synthetic cancel publication timeout")
            return result

        with patch.object(self.execution, "cancel", side_effect=blocked):
            caller = asyncio.create_task(tasks.cancel(self.owner, self.row["operation_id"],
                expected_generation=row["generation"], event_key="owner-cancel-blocked-role"))
            closer = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 15))
                caller.cancel()
                await asyncio.sleep(.02)
                caller.cancel()
                closer = asyncio.create_task(tasks.close())
                await asyncio.sleep(.02)
                self.assertFalse(closer.done())
            finally:
                release.set()
                await asyncio.gather(caller, return_exceptions=True)
                if closer is not None:
                    await closer
                else:
                    await tasks.close()
        self.assertTrue(self.closed.is_set())
        self.assertFalse(self.execution.workspace._paths(self.row["operation_id"], 1)[0].exists())

    async def test_committed_reservation_read_failure_cannot_leave_an_unclaimed_call(self):
        tasks = self.controller()
        get, reads = self.execution.get, []

        def fail_once(*args, **kwargs):
            if not reads:
                reads.append(True)
                raise OSError("synthetic read after committed reservation")
            return get(*args, **kwargs)

        with patch.object(self.execution, "get", side_effect=fail_once):
            with self.assertRaises(OSError):
                await self.start(tasks)
        await tasks.close()
        result = get(self.owner, self.row["operation_id"])
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(self.execution.ledger.get(result["attempts"][-1]["call_id"])["status"], "refunded")

    async def test_failed_preflight_during_shutdown_is_not_a_committed_cleanup_failure(self):
        tasks = self.controller()
        entered, release = threading.Event(), threading.Event()

        def unsupported(*args, **kwargs):
            entered.set()
            if not release.wait(15):
                raise RuntimeError("synthetic unsupported preflight timeout")
            raise ValueError("operation_native_unavailable")

        with patch.object(self.execution, "reserve", side_effect=unsupported):
            caller = asyncio.create_task(self.start(tasks))
            closer = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 15))
                closer = asyncio.create_task(tasks.close())
                await asyncio.sleep(.02)
                self.assertFalse(closer.done())
            finally:
                release.set()
                values = await asyncio.gather(caller, return_exceptions=True)
                if closer is not None:
                    self.assertTrue(await closer)
                else:
                    await tasks.close()
        self.assertIsInstance(values[0], ValueError)
        self.assertEqual(self.execution.get(self.owner, self.row["operation_id"])["attempts"], [])

    async def test_one_actual_native_transient_retry_preserves_both_calls_and_usage(self):
        tasks = self.controller()
        self.native_statuses = [503, None]
        try:
            with patch.object(ClaudeOperationAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}):
                await self.start(tasks)
                result = await tasks.join(self.owner, self.row["operation_id"])
            self.assertEqual((result["status"], result["attempt"]), ("completed", 2))
            self.assertEqual(self.native_queries, 2)
            first, second = [self.execution.ledger.get(item["call_id"]) for item in result["attempts"]]
            self.assertEqual((first["status"], second["status"]), ("interrupted", "ok"))
            self.assertTrue(all(call["engine_at"] is not None and call["counted"] == 1 for call in (first, second)))
            self.assertEqual(result["result"]["usage"]["attempts"], 2)
            self.assertEqual((result["result"]["usage"]["tokens_in"], result["result"]["usage"]["tokens_out"]), (14, 6))
        finally:
            await tasks.close()

    async def test_repeated_native_transient_error_stops_at_two_real_attempts(self):
        tasks = self.controller()
        self.native_statuses = [503, 503]
        try:
            with patch.object(ClaudeOperationAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}):
                await self.start(tasks)
                result = await tasks.join(self.owner, self.row["operation_id"])
            self.assertEqual((result["status"], result["attempt"]), ("interrupted", 2))
            self.assertEqual(self.native_queries, 2)
            self.assertEqual(len(result["attempts"]), 2)
        finally:
            await tasks.close()

    async def test_artifact_json_cannot_request_an_automatic_infrastructure_retry(self):
        tasks = self.controller()
        self.body = '{"type":"error","status_code":503,"retryable":true}'
        try:
            await self.start(tasks)
            result = await tasks.join(self.owner, self.row["operation_id"])
            self.assertEqual((result["status"], result["attempt"]), ("completed", 1))
            self.assertEqual(self.native_queries, 1)
        finally:
            await tasks.close()

    async def test_owner_cancel_during_retry_preflight_cannot_be_revived(self):
        tasks = self.controller()
        self.native_statuses = [503, None]
        fenced = []

        def verify():
            if getattr(self, "native_queries", 0) == 1:
                row = self.execution.get(self.owner, self.row["operation_id"])
                fenced.append(self.execution.cancel(self.owner, row["operation_id"], expected_generation=row["generation"],
                                                     event_key="owner-cancel-retry-preflight"))
            return {"supported": True, "capability_hash": "c" * 64}

        try:
            with patch.object(ClaudeOperationAdapter, "verify", side_effect=verify):
                await tasks.start(self.owner, self.row["operation_id"], expected_generation=1, event_key="owner-start-role")
                result = await tasks.join(self.owner, self.row["operation_id"])
            self.assertEqual(len(fenced), 1)
            self.assertEqual((result["status"], result["attempt"]), ("cancelled", 1))
            self.assertEqual(self.native_queries, 1)
        finally:
            await tasks.close()

    async def test_shutdown_owns_committed_retry_reservation_before_second_claim(self):
        tasks = self.controller()
        self.native_statuses = [503, None]
        entered, release = threading.Event(), threading.Event()
        retry = self.execution.retry

        def blocked(*args, **kwargs):
            result = retry(*args, **kwargs)
            entered.set()
            if not release.wait(15):
                raise RuntimeError("synthetic retry handoff timeout")
            return result

        with patch.object(self.execution, "retry", side_effect=blocked), \
                patch.object(ClaudeOperationAdapter, "verify", return_value={"supported": True, "capability_hash": "c" * 64}):
            await tasks.start(self.owner, self.row["operation_id"], expected_generation=1, event_key="owner-start-role")
            closer = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 15))
                closer = asyncio.create_task(tasks.close())
                await asyncio.sleep(.05)
                closer.cancel()
                await asyncio.sleep(.02)
                closer.cancel()
                self.assertFalse(closer.done())
            finally:
                release.set()
                if closer is not None:
                    await asyncio.gather(closer, return_exceptions=True)
                else:
                    await tasks.close()
        row = self.execution.get(self.owner, self.row["operation_id"])
        self.assertEqual((row["status"], row["attempt"]), ("not_run", 2))
        self.assertEqual(self.native_queries, 1)
        calls = [self.execution.ledger.get(item["call_id"]) for item in row["attempts"]]
        self.assertEqual([call["status"] for call in calls], ["interrupted", "refunded"])


if __name__ == "__main__":
    unittest.main()
