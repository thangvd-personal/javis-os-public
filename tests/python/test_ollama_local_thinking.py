"""Ollama (Local) has its own thinking level, off by default, separate from the Effort button (0.95.1).

    python tests/run.py ollama_local_thinking      (NO network, NO Ollama: httpx MockTransport)

Reported 2026-10-11 by the Mac mini user (qwen3 on Ollama, with a patch tested on the machine):
  1. qwen3 thinks BY DEFAULT. Javis sent no thinking switch to Ollama (Local), with or without tools, so a CSV row count spent
     the whole turn thinking (69 s) and Javis said "Ollama (Local) returned nothing"; a rerun was still thinking after 5 min.
  2. The Effort button is shared by every model: "Medium" for Claude made the local model ramble, "Off" for the local model
     took thinking away from Claude.
  3. A turn that only thought said "returned nothing", which points at a broken connection instead of the real cause.
Also pinned: a model that cannot think at all must not lose the turn when the owner turns local thinking on.
"""
import asyncio
import json
import os
import sys
import tempfile

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-ollama-think-")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx
import localefmt

localefmt.dat_ngon_ngu_yeu_cau("vi")

import config as cfgmod
import engine

_fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        _fails.append(name)


cfgmod.update_settings({"model": {"ollama_local_endpoint": "http://127.0.0.1:11434"}})

BODIES = []
SCRIPT = {"mode": "answer"}


def sse(chunks):
    return "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def handler(request):
    body = json.loads(request.content.decode("utf-8"))
    BODIES.append(body)
    mode = SCRIPT["mode"]
    if mode == "no_think_model" and body.get("reasoning_effort") not in (None, "none"):
        return httpx.Response(400, json={"error": {"message": '"llama3.2" does not support thinking'}})
    if body.get("stream"):
        if mode == "think_only":
            chunks = [{"choices": [{"delta": {"reasoning": "Đếm dòng 1, 2, 3..."}}]},
                      {"choices": [{"delta": {}, "finish_reason": "length"}]}]
        else:
            chunks = [{"choices": [{"delta": {"content": "Có 11 dòng."}}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(chunks))
    msg = ({"role": "assistant", "content": "", "reasoning": "Nghĩ mãi..."} if mode == "think_only"
           else {"role": "assistant", "content": "Có 11 dòng."})
    return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 5, "completion_tokens": 3}})


_RealClient = httpx.AsyncClient
_mt = httpx.MockTransport(handler)


class _Client(_RealClient):
    def __init__(self, *a, **kw):
        kw["transport"] = _mt
        super().__init__(*a, **kw)


engine.httpx.AsyncClient = _Client
MSGS = [{"role": "user", "content": "file khách có bao nhiêu dòng?"}]
TOOLS = [{"fn": "javis_read_file", "description": "đọc file", "schema": {"type": "object", "properties": {}}}]


def stream(reasoning):
    return asyncio.run(_collect(engine.ollama_local_stream("", "qwen3.8:27b", MSGS, reasoning)))


def tools(reasoning):
    return asyncio.run(_collect(engine.ollama_local_chat_with_mcp("", "qwen3.8:27b", MSGS, reasoning, TOOLS, {})))


async def _collect(gen):
    return [ev async for ev in gen]


def text_of(evs):
    return "".join(e.get("content") or "" for e in evs if e.get("type") == "text")


def errors_of(evs):
    return [e.get("content") or "" for e in evs if e.get("type") == "error"]


try:
    # ---- 1. default: thinking off, said out loud, whatever the Effort button says ----
    check("default level is off", engine.ollama_local_reasoning() == "off")
    for eff in ("off", "medium", "ultra"):
        BODIES.clear()
        evs = stream(eff)
        check(f"no tools, Effort={eff}: thinking switched OFF explicitly (reasoning_effort=none) and the answer arrives",
              BODIES and BODIES[-1].get("reasoning_effort") == "none" and text_of(evs) == "Có 11 dòng.", (BODIES[-1:], evs))
        BODIES.clear()
        evs = tools(eff)
        check(f"with tools, Effort={eff}: thinking switched OFF explicitly too",
              BODIES and all(b.get("reasoning_effort") == "none" for b in BODIES) and text_of(evs) == "Có 11 dòng.",
              (BODIES, evs))

    # ---- 2. the owner's own level for the local model ----
    cfgmod.update_settings({"model": {"ollama_local_reasoning": "high"}})
    BODIES.clear()
    stream("off")
    check("own level 'high' is sent even when the shared Effort button is off", BODIES[-1].get("reasoning_effort") == "high",
          BODIES[-1:])
    BODIES.clear()
    tools("off")
    check("own level reaches the tool loop too", BODIES and BODIES[0].get("reasoning_effort") == "high", BODIES[:1])

    # ---- 3. a model that cannot think, with local thinking turned on: the turn still answers ----
    SCRIPT["mode"] = "no_think_model"
    BODIES.clear()
    evs = stream("off")
    check("no tools: a 400 about thinking is asked again with thinking off, and answers",
          [b.get("reasoning_effort") for b in BODIES] == ["high", "none"] and text_of(evs) == "Có 11 dòng."
          and not errors_of(evs), ([b.get("reasoning_effort") for b in BODIES], evs))
    BODIES.clear()
    evs = tools("off")
    check("with tools: a 400 about thinking drops to thinking off for the rest of the turn, and answers",
          BODIES[0].get("reasoning_effort") == "high" and BODIES[-1].get("reasoning_effort") == "none"
          and text_of(evs) == "Có 11 dòng.", ([b.get("reasoning_effort") for b in BODIES], evs))

    # ---- 4. a turn that only thought: the error says so ----
    SCRIPT["mode"] = "think_only"
    for name, evs in (("no tools", stream("off")), ("with tools", tools("off"))):
        errs = errors_of(evs)
        check(f"{name}: thinking without an answer says so and what to change, not just 'returned nothing'",
              len(errs) == 1 and "suy nghĩ hết lượt" in errs[0] and "chia việc nhỏ hơn" in errs[0] and "rỗng" not in errs[0],
              errs)
    cfgmod.update_settings({"model": {"ollama_local_reasoning": ""}})
    check("an empty level falls back to off", engine.ollama_local_reasoning() == "off")
finally:
    engine.httpx.AsyncClient = _RealClient

# ---- 5. other providers keep following the shared Effort button ----
check("DeepSeek still follows the shared Effort level, Ollama 'off' maps to reasoning_effort=none",
      engine._deepseek_thinking("medium").get("thinking", {}).get("type") == "enabled"
      and engine._ollama_thinking("off") == {"reasoning_effort": "none"})
for f in (SERVER / "engine.py", __import__("pathlib").Path(__file__)):
    check(f"no em dash in {f.name}", chr(0x2014) not in f.read_text(encoding="utf-8"))

print()
if _fails:
    print(f"ĐỎ {len(_fails)} mục: " + ", ".join(_fails))
    sys.exit(1)
print("Tất cả xanh.")
