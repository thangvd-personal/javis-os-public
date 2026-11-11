"""The MCP hub must act as the account that owns the calling engine, not always the primary.

Reported in docs/dev/multi-user-QA.md (13.4 follow-up): `/hub/mcp` is exempt from `_auth_guard`
and authenticated only by one machine-wide `hub_token`, and `handle_http` never binds
`current_user_id`. A member's Claude Code / Codex turn then writes into the primary account's
stores, and a forged `X-Javis-Vault` header can name another account's brain.

Law after the fix:
  1. A request carrying a per-account hub token runs as THAT account (`current_user_id`).
  2. A request carrying only the legacy machine token runs as the primary account, so every
     config file written before this change keeps working.
  3. `X-Javis-Vault` naming a brain the account may not open is refused, never served.
  4. With no vault header the fallback brain is the account's own, never another account's.

Run:
    python tests/run.py hub_user_identity
"""
import json
import os
import sys
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401  - put server/ on sys.path

_TMP = Path(tempfile.mkdtemp(prefix="javis-hub-user-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "Javis").mkdir(parents=True, exist_ok=True)
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
(_TMP / "state").mkdir(parents=True, exist_ok=True)

import config as cfgmod  # noqa: E402
import mcp_hub  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def _account(username, role):
    u = cfgmod.save_user({
        "username": username, "role": role,
        "password_hash": cfgmod.hash_password(username + "-pw"),
        "salt": "s", "totp": {"enabled": False, "secret": "", "recovery": []},
    })
    return u["user_id"]


PRIMARY = _account("admin", "admin")
MEMBER = _account("bob", "member")
BOB_BRAIN = _BRAINS / ".users" / MEMBER / "Brain Default"
BOB_BRAIN.mkdir(parents=True, exist_ok=True)
ADMIN_BRAIN = str((_BRAINS / "Brain Default").resolve())

SEEN = []


async def _fake_handle_one(msg, mode, include_plugins, include_ambient, vault_root, vault_nguon,
                     vault_header_hong, for_bot=False, gate=None):
    """Stand in for the real dispatcher: record whose account and which brain the hub picked."""
    SEEN.append({"user": cfgmod.get_current_user_id(), "vault": vault_root,
                 "method": msg.get("method")})
    return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"ok": True}}


mcp_hub._handle_one = _fake_handle_one
client = TestClient(main.app)


def call(token, vault=None):
    SEEN.clear()
    headers = {"Authorization": "Bearer " + token}
    if vault:
        headers["X-Javis-Vault"] = vault
    r = client.post("/hub/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return r


# ── 1. A per-account token binds that account ────────────────────────────────
bob_token = mcp_hub.hub_token_for(MEMBER)
r = call(bob_token, str(BOB_BRAIN))
check("member token is accepted", r.status_code == 200)
check("member token runs as the member", SEEN and SEEN[0]["user"] == MEMBER)
check("member token keeps the member's own brain", SEEN and SEEN[0]["vault"] == str(BOB_BRAIN.resolve()))

# ── 2. The legacy machine token stays the primary account ────────────────────
r = call(mcp_hub.hub_token(), ADMIN_BRAIN)
check("legacy machine token still works", r.status_code == 200)
check("legacy token runs as the primary account", SEEN and SEEN[0]["user"] == PRIMARY)

# ── 3. A vault the account may not open is refused ───────────────────────────
r = call(bob_token, ADMIN_BRAIN)
check("member token cannot open the primary brain", r.status_code == 403)
check("refused request dispatched nothing", SEEN == [])

# ── 4. No header falls back to the CALLING account's brain, not the primary's
mcp_hub.quen_brain_dang_mo()
r = call(bob_token)
check("member request without a vault header still runs as the member",
      SEEN and SEEN[0]["user"] == MEMBER)
check("fallback brain belongs to the member",
      SEEN and str(SEEN[0]["vault"] or "").startswith(str((_BRAINS / ".users" / MEMBER).resolve())))

# ── 5. A random bearer is still just unauthorized ────────────────────────────
r = call("not-a-real-token")
check("unknown token is rejected", r.status_code == 401)

# ── 6. Config files written for one account carry THAT account's token ───────
mcp_hub._has_connections = lambda: True
with cfgmod.as_user(MEMBER):
    bob_cfg = mcp_hub.claude_config_path("full", vault_root=str(BOB_BRAIN))
    bob_profile = mcp_hub.codex_profile_name()
with cfgmod.as_user(PRIMARY):
    admin_cfg = mcp_hub.claude_config_path("full", vault_root=ADMIN_BRAIN)
    admin_profile = mcp_hub.codex_profile_name()
p = json.loads(Path(bob_cfg).read_text())
check("claude config for the member carries the member token",
      p["mcpServers"]["javis"]["headers"]["Authorization"] == "Bearer " + bob_token)
check("member config file lives in the member's state folder",
      Path(bob_cfg).parent == cfgmod.get_user_state_dir(MEMBER))
check("admin config keeps the machine token",
      json.loads(Path(admin_cfg).read_text())["mcpServers"]["javis"]["headers"]["Authorization"]
      == "Bearer " + mcp_hub.hub_token_for(""))
check("member and admin get different Codex profiles", bob_profile != admin_profile)
check("primary keeps the legacy Codex profile name", admin_profile == "javis")

print(f"\n{len(fails)} FAIL" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
