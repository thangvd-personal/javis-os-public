"""The incident list (failed background runs) belongs to one account.

Reported in docs/dev/multi-user-QA.md 13.4: incidents.json was one file for the machine and /incidents did not filter
by account, so a member saw the admin's failed jobs with their error text and could mark them resolved.

Law after the fix:
  1. An incident reported while running as one account lands in that account's list and state folder only.
  2. One account cannot resolve another's incident.

Run:
    python tests/run.py incidents_per_user
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-incidents-user-"))
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

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")


admin = client()
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
check("admin creates bob", admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"}).status_code == 200)
bob = client()
main._LOGIN_FAILS.clear()
check("bob logs in", bob.post("/auth/login", data={"username": "bob", "password": "bobpass123"}).status_code == 200)
BOB = cfgmod.get_user(username="bob")["user_id"]
ADMIN = cfgmod.primary_user_id()

# Record only: triage (a model turn and a post) is not what this test is about.
async def _no_triage(inc):
    return None
main.incident_desk._triage = _no_triage


def report(uid, source, ref, **kw):
    async def _go():
        with cfgmod.as_user(uid):
            main.incident_desk.report(source, ref, **kw)   # create_task copies the account into the intake task
        await main.incident_desk.wait_idle()
    asyncio.run(_go())


report(ADMIN, "kanban", "t-admin", title="admin's export", error="token sk-admin-secret expired")

ids = lambda c: [i.get("id") for i in c.get("/incidents").json().get("incidents", [])]  # noqa: E731
a_ids = ids(admin)
check("the admin sees their incident", len(a_ids) == 1, str(a_ids))
check("bob does not see the admin's incident", ids(bob) == [], str(ids(bob)))
check("bob cannot resolve the admin's incident", not bob.post("/incidents/resolve", json={"id": a_ids[0] if a_ids else ""}).json().get("ok"))
check("the admin's incident is still open",
      [i.get("status") for i in admin.get("/incidents").json()["incidents"]] == ["open"],
      str(admin.get("/incidents").json()))

report(BOB, "reminder", "r-bob", title="bob's reminder", error="no channel")
check("bob sees his own incident", len(ids(bob)) == 1)
check("the admin still sees only theirs", ids(admin) == a_ids)
check("bob's incidents live in bob's state", (cfgmod.get_user_state_dir(BOB) / "incidents.json").exists())

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
