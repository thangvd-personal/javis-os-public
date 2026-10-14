"""Every WebSocket handshake passes the Origin/Host rules (0.116.0).

    python tests/run.py ws_origin_guard      (no network)

The HTTP middleware never sees WebSocket scopes, and the WS routes only checked a session when a
password was set. On a default install (127.0.0.1, no password) a web page the user happened to open
could connect to /ws/terminal or /ws from the user's own browser. web_security.WebSocketGuard closes
any cross-origin handshake before a route sees it.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import sys
import tempfile

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-wsguard-")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from starlette.applications import Starlette  # noqa: E402
from starlette.routing import WebSocketRoute  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import web_security as ws  # noqa: E402

_fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(them) + "]") if them and not cond else ""))
    if not cond:
        _fails.append(name)


# ---- 1. pure decision ------------------------------------------------------------------
d = ws.ws_decision
check("same origin passes", d("127.0.0.1:7777", "http://127.0.0.1:7777", False) is None)
check("localhost page to 127.0.0.1 socket passes (both loopback)",
      d("127.0.0.1:7777", "http://localhost:7777", False) is None)
check("no Origin (not a browser) passes", d("127.0.0.1:7777", None, False) is None)
check("CANARY: foreign page blocked with no password", d("127.0.0.1:7777", "https://evil.example", False) is not None)
check("foreign page blocked with a password too", d("127.0.0.1:7777", "https://evil.example", True) is not None)
check("Origin null (sandboxed iframe, file://) blocked", d("127.0.0.1:7777", "null", False) is not None)
check("DNS rebinding Host blocked without a password", d("evil.example:7777", "http://evil.example:7777", False) is not None)
check("LAN IP same origin passes", d("192.168.1.10:7777", "http://192.168.1.10:7777", False) is None)
check("malformed Host refused", d("a/b", None, False) is not None)


# ---- 2. middleware on a small app --------------------------------------------------------
async def echo(websocket):
    await websocket.accept()
    await websocket.send_text("hi")
    await websocket.close()

inner = Starlette(routes=[WebSocketRoute("/ws/terminal", echo)])
app = ws.WebSocketGuard(inner, gate_active=lambda: False)
client = TestClient(app, base_url="http://127.0.0.1:7777")

with client.websocket_connect("/ws/terminal", headers={"host": "127.0.0.1:7777", "origin": "http://127.0.0.1:7777"}) as s:
    check("dashboard's own socket still connects", s.receive_text() == "hi")

try:
    with client.websocket_connect("/ws/terminal", headers={"host": "127.0.0.1:7777", "origin": "https://evil.example"}) as s:
        s.receive_text()
    bi_chan = False
except WebSocketDisconnect as e:
    bi_chan = e.code == 1008
except Exception:      # noqa: BLE001 - some versions raise a different error for a refused handshake
    bi_chan = True
check("CANARY: foreign page cannot open the terminal socket", bi_chan)

# ---- 3. main wires it ---------------------------------------------------------------------
import main  # noqa: E402
check("main.app has WebSocketGuard", any(getattr(m, "cls", None) is ws.WebSocketGuard for m in main.app.user_middleware))

print()
if _fails:
    print(f"{len(_fails)} FAIL")
    sys.exit(1)
print("ALL PASS")
