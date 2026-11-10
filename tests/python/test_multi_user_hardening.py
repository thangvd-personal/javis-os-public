"""Multi-user hardening: regressions found in the review of the multi-user branch.

Each block pins one bug that was reproduced against the running app:
1. Login must check username and password against the SAME account
   (`admin` + bob's password used to sign in as admin).
2. A password change drops every other session of that account, and only that account.
3. An install upgraded from single-user keeps its account (migrated from settings.json to
   auth.json) and its state folder.
4. A WebSocket (chat, voice, terminal) runs as the account of its session cookie.
"""
import os
import sys
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


state_root = tempfile.mkdtemp(prefix="javis-mu-hardening-")
os.environ["JAVIS_STATE_DIR"] = state_root
os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
os.environ["BRAINS_DIR"] = tempfile.mkdtemp(prefix="javis-mu-brains-")
for k in ("JAVIS_ADMIN_PASSWORD", "JAVIS_ADMIN_USER", "JAVIS_USER_ID", "JAVIS_AUTH_PATH", "JAVIS_SESSIONS_PATH"):
    os.environ.pop(k, None)

import config as c  # noqa: E402
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")


def login(cl, user, pw):
    main._LOGIN_FAILS.clear()
    return cl.post("/auth/login", data={"username": user, "password": pw})


admin = client()
check("setup creates the first account", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
admin_uid = c.read_auth().get("user_id")
r = admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"})
check("admin creates bob", r.status_code == 200 and r.json().get("ok"))
bob_uid = r.json().get("user_id")

# ---- 1. Username and password belong to the same account ----
r = login(client(), "admin", "bobpass123")
check("admin + bob's password is rejected", r.status_code == 401)
r = login(client(), "bob", "adminpass123")
check("bob + admin's password is rejected", r.status_code == 401)
check("verify_password does not accept another user's password",
      not c.verify_password("bobpass123", {"auth": c.get_user(user_id=admin_uid)}))
cl = client()
r = login(cl, "Admin ", "adminpass123")
check("admin still signs in (case and spaces ignored)", r.status_code == 200
      and c.get_session_user_id(cl.cookies.get("javis_session")) == admin_uid)
cl = client()
r = login(cl, "bob", "bobpass123")
check("bob signs in as bob", r.status_code == 200
      and c.get_session_user_id(cl.cookies.get("javis_session")) == bob_uid)

# ---- 2. Password change signs THIS account out everywhere, and only this account ----
bob_a, bob_b, admin_2 = client(), client(), client()
login(bob_a, "bob", "bobpass123")
login(bob_b, "bob", "bobpass123")
login(admin_2, "admin", "adminpass123")
tok_bob_b = bob_b.cookies.get("javis_session")
tok_admin = admin_2.cookies.get("javis_session")
r = bob_a.post("/auth/password", data={"current_password": "bobpass123", "password": "bobnewpass123"})
check("bob changes his password", r.status_code == 200 and r.json().get("ok"))
check("bob's session on another device is dropped", not c.valid_session(tok_bob_b))
check("admin's session survives bob's password change", c.valid_session(tok_admin))
check("bob's current device gets a fresh bob session",
      c.get_session_user_id(bob_a.cookies.get("javis_session")) == bob_uid)
check("bob signs in with the new password", login(client(), "bob", "bobnewpass123").status_code == 200)

# ---- 3. Upgrade from a single-user install: the account moves to auth.json ----
# config migrates at import time, so the old layout is booted in a fresh interpreter.
import json  # noqa: E402
import subprocess  # noqa: E402

legacy_root = Path(tempfile.mkdtemp(prefix="javis-mu-legacy-"))
h, s = c.hash_password("oldpass123")
(legacy_root / "settings.json").write_text(json.dumps(
    {"setup_done": True, "workspace_name": "Old shop", "auth": {"username": "owner", "password_hash": h, "salt": s}}),
    encoding="utf-8")
(legacy_root / "kanban.sqlite3").write_bytes(b"")
probe = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import config as c
from fastapi.testclient import TestClient
import main
cl = TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")
r = cl.post("/auth/login", data={"username": "owner", "password": "oldpass123"})
print(json.dumps({"enabled": c.auth_enabled(), "login": r.status_code, "note": r.json().get("note"),
                  "uid": c.get_session_user_id(cl.cookies.get("javis_session")),
                  "state": str(c.STATE_DIR), "user_dir": str(c.get_user_state_dir()),
                  "ws": c.read_settings().get("workspace_name")}))
"""
env = {**os.environ, "JAVIS_STATE_DIR": str(legacy_root)}
out = subprocess.run([sys.executable, "-c", probe, str(SERVER)], cwd=str(SERVER), env=env,
                     capture_output=True, text=True, timeout=240)
try:
    res = json.loads(out.stdout.strip().splitlines()[-1])
except Exception:
    res = {}
    print(out.stdout[-2000:], out.stderr[-2000:])
migrated = json.loads((legacy_root / "auth.json").read_text(encoding="utf-8")) if (legacy_root / "auth.json").exists() else {}
check("upgrade: auth.json is created from settings.json", migrated.get("username") == "owner"
      and bool(migrated.get("user_id")) and len(migrated.get("users") or []) == 1)
check("upgrade: login stays enabled (no setup screen)", res.get("enabled") is True)
check("upgrade: the old owner signs in with the old password", res.get("login") == 200 and not res.get("note"))
check("upgrade: the session carries the migrated user_id", res.get("uid") == migrated.get("user_id"))
check("upgrade: the primary account keeps the old state folder", res.get("state") == str(legacy_root)
      and res.get("user_dir") == str(legacy_root))
check("upgrade: existing settings are still read", res.get("ws") == "Old shop")
check("upgrade: data files were not moved", (legacy_root / "kanban.sqlite3").exists())
# The start scripts used to export <root>/<uid>; that must resolve to the same root.
check("a <root>/<user_id> JAVIS_STATE_DIR resolves to the root", subprocess.run(
    [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import config as c; print(c.get_auth_path())", str(SERVER)],
    cwd=str(SERVER), env={**env, "JAVIS_STATE_DIR": str(legacy_root / migrated.get("user_id", "x"))},
    capture_output=True, text=True, timeout=120).stdout.strip().endswith(str(legacy_root / "auth.json")))

# ---- 4. A WebSocket runs as the account of its session cookie ----
from starlette.websockets import WebSocket  # noqa: E402


@main.app.websocket("/ws/_test_whoami")
async def _whoami(ws: WebSocket):
    await ws.accept()
    await ws.send_json({"ctx": c.current_user_id.get(), "resolved": c.get_current_user_id(),
                        "state": str(c.get_user_state_dir())})
    await ws.close()


main.app.middleware_stack = None   # rebuild so the route added above is served


def ws_whoami(cl):
    # Pass the cookie explicitly: the test client's WS host is not the cookie's domain.
    tok = cl.cookies.get("javis_session")
    with cl.websocket_connect("/ws/_test_whoami", headers={"cookie": f"javis_session={tok}"}) as w:
        return w.receive_json()


bob_ws = client()
login(bob_ws, "bob", "bobnewpass123")
admin_ws = client()
login(admin_ws, "admin", "adminpass123")
seen_bob = ws_whoami(bob_ws)
seen_admin = ws_whoami(admin_ws)
check("bob's WebSocket runs as bob", seen_bob.get("ctx") == bob_uid and seen_bob.get("resolved") == bob_uid)
check("bob's WebSocket uses bob's state folder", seen_bob.get("state") == str(c.get_user_state_dir(bob_uid)))
check("admin's WebSocket runs as admin", seen_admin.get("resolved") == admin_uid)
check("the identity does not leak out of the connection", c.current_user_id.get() == "")
src = (SERVER / "main.py").read_text(encoding="utf-8")
check("the WebSocket identity middleware is installed", "app.add_middleware(_WebSocketUser)" in src)

# ---- end ----
print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("All multi-user hardening checks passed.")
