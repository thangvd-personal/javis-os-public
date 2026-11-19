"""Test retry mechanism for transient HTTP 502 / congestion errors in engine.py and team_runner.py.

Run:
    ./.venv/bin/python tests/python/test_engine_transient_502_retry.py
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from _paths import ROOT, SERVER

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-test-502-")
os.environ["BRAINS_DIR"] = tempfile.mkdtemp(prefix="javis-test-502-brains-")
os.environ["JAVIS_TRANSIENT_502_WAIT_S"] = "0.01"  # Fast wait for tests
os.environ["JAVIS_TRANSIENT_502_RETRIES"] = "1"

import httpx
import engine
import team_runner


_real_async_client = httpx.AsyncClient


class TestTransient502Retry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ["JAVIS_TRANSIENT_502_WAIT_S"] = "0.01"
        os.environ["JAVIS_TRANSIENT_502_RETRIES"] = "1"

    def test_qua_tai_recognizes_502(self):
        ev = {"type": "error", "ma": 502, "content": "OpenAI Compatible 502: Bad Gateway"}
        self.assertTrue(engine._qua_tai(ev))

        ev_msg = {"type": "error", "ma": 500, "content": "The server is overloaded"}
        self.assertTrue(engine._qua_tai(ev_msg))

    def test_tool_turn_outcome_ignores_pseudo_tool_calls(self):
        outcome = team_runner.ToolTurnOutcome()
        outcome.note({"type": "tool_call", "name": "javis_wait_retry", "content": "waiting..."})
        outcome.note({"type": "tool_call", "name": "javis_wait_quota", "content": "waiting quota..."})
        outcome.note({"type": "tool_call", "name": "javis_thinking_off", "content": "thinking off..."})
        outcome.note({"type": "tool_call", "name": "javis_no_tools", "content": "no tools..."})
        self.assertEqual(outcome.tool_calls, 0, "Pseudo tool calls should not increment tool_calls counter")

        outcome.note({"type": "tool_call", "name": "javis_file_read", "input": {"path": "a.txt"}})
        self.assertEqual(outcome.tool_calls, 1, "Real tool calls should increment tool_calls counter")

    async def test_cc_tool_loop_retries_502_and_succeeds(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    502,
                    text=json.dumps({"error": {"message": "Vui lòng thử lại hoặc liên hệ Admin. Mã lỗi: 502-5f26e014 (api_error)"}}),
                    headers={"Content-Type": "application/json"}
                )
            return httpx.Response(
                200,
                text=json.dumps({
                    "id": "chatcmpl-1",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "Xin chao, toi da san sang."},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 8}
                }),
                headers={"Content-Type": "application/json"}
            )

        transport = httpx.MockTransport(handler)

        with patch.object(engine.httpx, "AsyncClient", side_effect=lambda **kw: _real_async_client(transport=transport, **kw)):
            events = []
            async for ev in engine._cc_tool_loop(
                url="http://test-gateway/v1/chat/completions",
                headers={"Authorization": "Bearer test"},
                model="test-model",
                messages=[{"role": "user", "content": "chao"}],
                mcp_tools=[],
                mcp_route={},
                reasoning_extra={},
                label="OpenAI Compatible",
            ):
                events.append(ev)

        self.assertEqual(attempts, 2)
        # Should contain wait retry notice
        wait_events = [e for e in events if e.get("type") == "tool_call" and e.get("name") == "javis_wait_retry"]
        self.assertEqual(len(wait_events), 1)
        self.assertIn("502", wait_events[0].get("content", ""))

        # Should finish with success text
        text_events = [e for e in events if e.get("type") == "text"]
        self.assertTrue(len(text_events) >= 1)
        self.assertIn("Xin chao", text_events[0].get("content", ""))

        # No error event
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 0)

    async def test_cc_tool_loop_exhausts_retries_and_yields_error(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                502,
                text=json.dumps({"error": {"message": "Vui lòng thử lại hoặc liên hệ Admin. Mã lỗi: 502-5f26e014 (api_error)"}}),
                headers={"Content-Type": "application/json"}
            )

        transport = httpx.MockTransport(handler)

        with patch.object(engine.httpx, "AsyncClient", side_effect=lambda **kw: _real_async_client(transport=transport, **kw)):
            events = []
            async for ev in engine._cc_tool_loop(
                url="http://test-gateway/v1/chat/completions",
                headers={"Authorization": "Bearer test"},
                model="test-model",
                messages=[{"role": "user", "content": "chao"}],
                mcp_tools=[],
                mcp_route={},
                reasoning_extra={},
                label="OpenAI Compatible",
            ):
                events.append(ev)

        # 1 initial attempt + 1 retry = 2 attempts
        self.assertEqual(attempts, 2)

        # First gave wait notice
        wait_events = [e for e in events if e.get("type") == "tool_call" and e.get("name") == "javis_wait_retry"]
        self.assertEqual(len(wait_events), 1)

        # After exhausting retry, yields error event
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("OpenAI Compatible 502", error_events[0].get("content", ""))

    async def test_openai_compat_stream_retries_502_and_succeeds(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    502,
                    text=json.dumps({"error": {"message": "Gateway congestion 502"}}),
                    headers={"Content-Type": "application/json"}
                )
            sse_content = (
                'data: {"choices":[{"delta":{"content":"Xin "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"chao!"}}]}\n\n'
                'data: [DONE]\n\n'
            )
            return httpx.Response(
                200,
                content=sse_content.encode("utf-8"),
                headers={"Content-Type": "text/event-stream"}
            )

        transport = httpx.MockTransport(handler)

        with patch.object(engine.httpx, "AsyncClient", side_effect=lambda **kw: _real_async_client(transport=transport, **kw)):
            events = []
            async for ev in engine._openai_compat_stream(
                url="http://test-gateway/v1/chat/completions",
                label="OpenAI Compatible",
                api_key="test-key",
                model="test-model",
                messages=[{"role": "user", "content": "chao"}],
                reasoning="off",
                send_reasoning=False,
            ):
                events.append(ev)

        self.assertEqual(attempts, 2)
        text_content = "".join(e.get("content", "") for e in events if e.get("type") == "text")
        self.assertEqual(text_content, "Xin chao!")
        self.assertEqual([e for e in events if e.get("type") == "error"], [])

    async def test_openai_compat_stream_exhausts_retries_and_yields_error(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                502,
                text=json.dumps({"error": {"message": "Gateway congestion 502"}}),
                headers={"Content-Type": "application/json"}
            )

        transport = httpx.MockTransport(handler)

        with patch.object(engine.httpx, "AsyncClient", side_effect=lambda **kw: _real_async_client(transport=transport, **kw)):
            events = []
            async for ev in engine._openai_compat_stream(
                url="http://test-gateway/v1/chat/completions",
                label="OpenAI Compatible",
                api_key="test-key",
                model="test-model",
                messages=[{"role": "user", "content": "chao"}],
                reasoning="off",
                send_reasoning=False,
            ):
                events.append(ev)

        self.assertEqual(attempts, 2)
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("OpenAI Compatible 502", error_events[0].get("content", ""))


if __name__ == "__main__":
    unittest.main()
