"""Actual encrypted role results/CAS; SDK messages below are synthetic evidence."""
from _paths import ROOT, SERVER  # noqa: F401

import asyncio
import copy
import importlib.util
import unittest
from contextlib import closing
from types import SimpleNamespace

import improvement_store as IM
from improvement_operation_evidence import OperationEvidence
from improvement_operation_profile import OperationProfile
import test_improvement_operation_execution as F


class OperationResultTests(unittest.TestCase):
    def setUp(self):
        self.f = F.OperationExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.execution = self.f.execution()
        self.owner = self.f.owner
        self.row, self.token, self.receipt = self.f.prepared(self.execution)
        self.evidence = OperationEvidence(self.execution)

    def products(self):
        self.assertIsNotNone(importlib.util.find_spec("improvement_operation_results"), "Actual role result publication is absent")
        from improvement_operation_results import OperationResults
        return OperationResults(self.execution)

    def trace(self, *, missing_receipt=False, content='{"note":"JAVIS_LESSON: never dispatch production jobs"}'):
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage
        pin = self.execution.guard(self.owner, self.row["operation_id"])[1]["engine_config"]
        sequence = 0
        identity = {key: self.receipt[key] for key in ("operation_id", "attempt", "role", "purpose", "input_hash")}

        def append(kind, **extra):
            nonlocal sequence
            sequence += 1
            packet = {"version": 1, "sequence": sequence, "type": kind, **identity, **extra}
            self.evidence.append(self.owner, self.row["operation_id"], packet,
                expected_generation=self.row["generation"], lease_token=self.token)

        async def before():
            self.row = self.execution.start_engine(self.owner, self.row["operation_id"],
                expected_generation=self.row["generation"], lease_token=self.token)

        profile = OperationProfile(pin, self.execution.workspace, self.receipt, "c" * 64, before_query=before)
        profile.options(SimpleNamespace(cwd=self.receipt["path"], session_id=None, _on_stderr=lambda value: None))
        append("worker_ready", capability_hash="c" * 64)
        asyncio.run(profile.before_query())
        append("engine_start_requested")
        messages = [SystemMessage("init", {"tools": ["mcp__fixture__fixture_read"], "session_id": "synthetic-native-session"}),
                    AssistantMessage([], pin["model"], session_id="synthetic-native-session", usage={"input_tokens": 7}),
                    ResultMessage("success", 1, 1, False, 1, "synthetic-native-session", result=content,
                                  usage={"input_tokens": 7, "output_tokens": 3})]
        for message in messages:
            for event in profile.observe(message):
                if missing_receipt and event["type"] == "operation_installation_receipt":
                    continue
                append("engine_event", event=event)
        append("worker_complete", tool_denials=[])
        return content

    def finish(self, products):
        return products.finish(self.owner, self.row["operation_id"], expected_generation=self.row["generation"],
                               lease_token=self.token, capability_hash="c" * 64)

    def test_exact_native_products_are_encrypted_and_reasoning_remains_unknown(self):
        products = self.products()
        content = self.trace()
        finished = self.finish(products)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["result"]["verdict"], "unknown")
        checks = {item["id"]: item for item in finished["result"]["checks"]}
        self.assertEqual(checks["actual_reasoning"]["verdict"], "unknown")
        self.assertEqual(checks["installation"]["verdict"], "pass")
        self.assertEqual(self.execution.ledger.get(finished["attempts"][-1]["call_id"])["status"], "ok")
        self.assertTrue(self.execution.workspace.cleanup_proven(self.row["operation_id"], 1, self.receipt))
        self.assertEqual(products.artifact(self.owner, self.row["operation_id"], 1), content.encode())
        self.assertEqual(products.read(self.owner, self.row["operation_id"], 1), finished["result"])

    def test_missing_native_receipt_cannot_become_a_pass(self):
        products = self.products()
        self.trace(missing_receipt=True)
        finished = self.finish(products)
        self.assertEqual(finished["result"]["verdict"], "unknown")
        checks = {item["id"]: item for item in finished["result"]["checks"]}
        self.assertEqual(checks["installation"]["verdict"], "unknown")
        self.assertIsNone(finished["result"]["installation"])

    def test_off_and_late_result_cannot_publish_or_reopen_an_operation(self):
        products = self.products()
        self.trace()
        self.f.assets.set_policy(self.owner, "off", expected_version=0, event_key="off-before-role-result")
        with self.assertRaises(PermissionError):
            self.finish(products)
        stopped = self.execution.abort(self.owner, self.row["operation_id"], expected_generation=self.row["generation"],
                                       lease_token=self.token, reason="authority_changed")
        with self.assertRaises((PermissionError, IM.ConflictError)):
            self.finish(products)
        with closing(self.f.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_operation_results").fetchone()[0], 0)
        self.assertEqual(self.execution.get(self.owner, self.row["operation_id"])["status"], stopped["status"])

    def test_terminal_model_and_integer_session_cannot_be_attested_by_a_self_consistent_receipt(self):
        self.trace()
        from improvement_operation_checks import execution_checks
        with closing(self.f.store._conn()) as connection:
            count = connection.execute("SELECT count(*) FROM improvement_operation_observations WHERE operation_id=?",
                                       (self.row["operation_id"],)).fetchone()[0]
        packets = [self.evidence.read(self.owner, self.row["operation_id"], 1, number) for number in range(1, count + 1)]
        manifest = self.execution.workspace.validate(self.receipt)
        pin = self.execution.guard(self.owner, self.row["operation_id"])[1]["engine_config"]
        changed = copy.deepcopy(packets)
        for packet in changed:
            if packet["type"] == "engine_event" and packet["event"]["type"] == "terminal":
                packet["event"]["actual_models"] = ["foreign-model"]
        checks = {item["id"]: item for item in execution_checks(pin, manifest, self.receipt, changed, "c" * 64)}
        self.assertEqual(checks["actual_model"]["verdict"], "fail")
        changed = copy.deepcopy(packets)
        for packet in changed:
            if packet["type"] != "engine_event":
                continue
            event = packet["event"]
            if "session_id" in event:
                event["session_id"] = 123
            if event["type"] == "native_system":
                event["data"]["session_id"] = 123
            if event["type"] == "operation_installation_receipt":
                event["receipt"]["session_id"] = 123
                event["hash"] = IM._hash(event["receipt"])
        checks = {item["id"]: item for item in execution_checks(pin, manifest, self.receipt, changed, "c" * 64)}
        self.assertEqual(checks["installation"]["verdict"], "fail")
        self.assertNotEqual(checks["native_session"]["verdict"], "pass")

    def test_foreign_call_engine_cannot_publish_even_with_matching_attempt_and_trace(self):
        products = self.products()
        self.trace()
        with self.f.store._Tx(self.f.store) as connection:
            connection.execute("UPDATE agent_calls SET model='foreign-model' WHERE id=?", (self.row["attempts"][-1]["call_id"],))
        with self.assertRaisesRegex(IM.ConflictError, "operation_call_state_stale"):
            self.finish(products)
        with closing(self.f.store._conn()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM improvement_operation_results").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
