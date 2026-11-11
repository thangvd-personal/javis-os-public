"""MCP connections belong to one account, and a share is explicit.

Reported in docs/dev/multi-user-QA.md (13.4 follow-up): mcp_servers.json, the OAuth token file
and the connector credential folders all lived in the machine state folder, so every account
saw the primary account's connections, and a member could not hold their own.

Law after the fix:
  1. A connection added by one account is invisible to every other account.
  2. An admin can share one connection with one member, read-only or full. The member then sees
     it and uses it; they cannot edit, toggle or delete it.
  3. Revoking the share hides it again.
  4. A member may add and delete their own connection. They may not touch the catalog, packs or
     plugins, which stay machine-wide and admin-only.

Run:
    python tests/run.py mcp_per_user
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-mcp-user-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "Javis").mkdir(parents=True, exist_ok=True)
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
(_TMP / "state").mkdir(parents=True, exist_ok=True)

import config as cfgmod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import mcp_store  # noqa: E402

fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")


def login(cl, user, pw):
    main._LOGIN_FAILS.clear()
    return cl.post("/auth/login", data={"username": user, "password": pw})


admin = client()
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
r = admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"})
check("admin creates bob", r.status_code == 200 and r.json().get("ok"))
bob = client()
check("bob logs in", login(bob, "bob", "bobpass123").status_code == 200)
BOB = cfgmod.get_user(username="bob")["user_id"]

POS = {"connector_id": "custom", "label": "POS admin", "url": "http://pos.example/mcp",
       "transport": "http"}


def ids_of(cl):
    return {c["id"] for c in cl.get("/connect/catalog").json()["connections"]}


# ── 1. A member adds their own; the admin never sees it ──────────────────────
r = bob.post("/connect/add", json={"connector_id": "custom", "label": "bob sheets",
                                   "url": "http://sheets.example/mcp", "transport": "http"})
check("member adds their own connection", r.status_code == 200 and r.json().get("ok"), )
bob_cid = (r.json() or {}).get("id")
check("admin does not see the member connection", bob_cid not in ids_of(admin))

# ── 2. The admin's connection stays private until shared ─────────────────────
r = admin.post("/connect/add", json=POS)
check("admin adds a connection", r.status_code == 200 and r.json().get("ok"))
admin_cid = r.json()["id"]
check("member does not see the admin connection", admin_cid not in ids_of(bob))

# ── 3. Sharing, read-only: visible and usable, not editable ──────────────────
r = admin.post("/connect/share", json={"id": admin_cid, "user_id": BOB, "perm": "readonly"})
check("admin shares read-only", r.status_code == 200 and r.json().get("ok"))
seen = {c["id"]: c for c in bob.get("/connect/catalog").json()["connections"]}
check("member now sees the shared connection", admin_cid in seen)
check("shared connection reports its ceiling",
      seen.get(admin_cid, {}).get("shared") and seen[admin_cid].get("perm") == "readonly")
check("member cannot retune a shared connection",
      bob.post("/connect/update", json={"id": admin_cid, "label": "stolen"}).status_code == 403)
check("member cannot toggle a shared connection",
      bob.post("/connect/toggle", json={"id": admin_cid}).status_code == 403)
check("member cannot delete a shared connection",
      bob.post("/connect/delete", json={"id": admin_cid}).status_code == 403)
check("member cannot sign a shared connection in again",
      bob.post("/connect/relogin", json={"id": admin_cid}).status_code == 403
      and bob.post("/connect/oauth/start", json={"id": admin_cid}).status_code == 403)

with cfgmod.as_user(BOB):
    got = mcp_store.resolved(enabled_only=False)
mine = [c for c in got if c["id"] == admin_cid]
check("member's resolved list carries the share, capped readonly",
      len(mine) == 1 and mine[0]["perm"] == "readonly" and mine[0].get("shared_from"))

# ── 4. Revoking hides it again ───────────────────────────────────────────────
r = admin.post("/connect/share", json={"id": admin_cid, "user_id": BOB, "perm": ""})
check("admin revokes the share", r.status_code == 200 and r.json().get("ok"))
check("member no longer sees it", admin_cid not in ids_of(bob))
import connect_health  # noqa: E402
connect_health._state[admin_cid] = {"ok": True, "kind": "ok", "message": "", "checked_at": 0, "tools": 1}
check("member does not see health of an unshared admin connection",
      admin_cid not in bob.get("/connect/health").json()["health"]
      and admin_cid in admin.get("/connect/health").json()["health"])

# ── 5. A member cannot grant shares, nor touch the machine catalog ──────────
check("member cannot share",
      bob.post("/connect/share", json={"id": bob_cid, "user_id": BOB, "perm": "full"}).status_code == 403)
check("member cannot remove a catalog connector",
      bob.post("/connect/core-toggle", json={"id": "telegram", "off": True}).status_code == 403)
check("member cannot install a plugin",
      bob.post("/plugins/install", json={"id": "x"}).status_code in (403, 404, 405))

# ── 6. Stores are physically separate files ──────────────────────────────────
check("member store is in the member state folder",
      mcp_store.store_path(BOB).parent == cfgmod.get_user_state_dir(BOB))
check("primary store keeps the legacy path",
      mcp_store.store_path("") == cfgmod.STATE_DIR / "mcp_servers.json")

print(f"\n{len(fails)} FAIL" if fails else "\nALL PASS")
raise SystemExit(1 if fails else 0)
