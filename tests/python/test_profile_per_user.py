"""The person profile (`user_profile.md`) belongs to one account.

Reported in docs/dev/multi-user-QA.md 13.4: the profile shared by every brain lived in the machine's STATE_DIR, so a
member reading /profile saw the admin's profile, a member writing it overwrote the admin's, and every account's chat
prompt carried the same person's profile.

Law after the fix:
  1. /profile reads and writes the signed-in account's own profile, in that account's state folder.
  2. The prompt's profile block is the profile of the account the turn runs as.

Run:
    python tests/run.py profile_per_user
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-profile-user-"))
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
import memory_control  # noqa: E402

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

# ── 1. /profile is the signed-in account's own ───────────────────────────────
r = admin.post("/profile", json={"text": "Admin: runs the shop, prefers short reports."})
check("admin saves a profile", r.status_code == 200, r.text[:200])
check("bob does not see the admin's profile", bob.get("/profile").json().get("text") == "", bob.get("/profile").text[:200])
r = bob.post("/profile", json={"text": "Bob: student, studies at night."})
check("bob saves his profile", r.status_code == 200, r.text[:200])
check("the admin's profile is untouched", "Admin" in admin.get("/profile").json().get("text", ""))
check("bob reads his own profile", "Bob" in bob.get("/profile").json().get("text", ""))
check("bob's profile lives in bob's state", (cfgmod.get_user_state_dir(BOB) / "user_profile.md").exists())

# ── 2. The prompt carries the turn's account profile ─────────────────────────
with cfgmod.as_user(BOB):
    blk = memory_control.profile_block()
    check("bob's turn gets bob's profile", "Bob" in blk and "Admin" not in blk, blk[:200])
with cfgmod.as_user(ADMIN):
    blk = memory_control.profile_block()
    check("the admin's turn gets the admin's profile", "Admin" in blk and "Bob" not in blk, blk[:200])

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
