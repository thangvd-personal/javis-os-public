"""Resonance principals carry the real account, and a team leader has its own kind.

docs/dev/multi-user-QA.md 12.3 and 13.4: every owner principal was the literal Principal("owner", "owner", brain), so
the `by` columns could not say which account acted; and a Full-authority team leader ran as kind "owner" with id
"leader:<key>", so every owner-only gate in the store was open on that path.

Law after the fix:
  1. A request from an account records `owner:<user_id>`; before setup it stays `owner:owner`.
  2. A leader acts as kind "leader": it may toggle Resonance (its one admin op on the store) but no owner-only call.

Run:
    python tests/run.py principal_identity
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-principal-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "agents").mkdir(parents=True, exist_ok=True)
(_BRAINS / "Brain Default" / "agents" / "lan.md").write_text("---\nname: Lan\n---\nhi\n", encoding="utf-8")
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
(_TMP / "state").mkdir(parents=True, exist_ok=True)

import config as cfgmod  # noqa: E402
import resonance_store as RS  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


check("before setup the owner is the single local owner", RS.Principal.of_owner("b").by == "owner:owner")

admin = TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
ADMIN = cfgmod.primary_user_id()

with cfgmod.as_user(ADMIN):
    p = RS.Principal.of_owner("b")
check("an account's principal names that account", p.kind == "owner" and p.by == f"owner:{ADMIN}", p.by)

B = str(_BRAINS / "Brain Default")
r = admin.post("/resonance/agents/toggle", params={"brain": B}, json={"slug": "lan", "enabled": True})
check("the owner turns Resonance on", r.status_code == 200 and r.json().get("ok"), r.text[:200])
store = main._resonance_store()
key = r.json()["agent"]["agent_key"]
bk = main._brain_key(B)
ev = store.agent_events(bk, key)
check("the event records the signed-in account", ev and ev[-1]["by"] == f"owner:{ADMIN}", ev[-1:] if ev else ev)

lead = main._admin_principal("leader:ag_lead", bk)
check("a team leader acts as kind leader", lead.kind == "leader" and lead.by == "leader:ag_lead", lead)
check("a team leader may toggle Resonance", store.agent_set_enabled(lead, "lan", False) is not None)
try:
    store.agent_retire(lead, "lan")
    check("a team leader cannot retire an agent (owner-only)", False)
except PermissionError:
    check("a team leader cannot retire an agent (owner-only)", True)
try:
    store.agent_confirm(lead, key, True)
    check("a team leader cannot confirm an agent (owner-only)", False)
except PermissionError:
    check("a team leader cannot confirm an agent (owner-only)", True)
check("an admin op from the dashboard still runs as the owner", main._admin_principal("owner", bk).kind == "owner")

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
