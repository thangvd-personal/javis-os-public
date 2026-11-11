"""The self-learning config and queue belong to one account.

Reported in docs/dev/multi-user-QA.md 13.4: learn_config.json and the learning queue were one per machine and the
learn tick ran only as the primary account. "brain" is each account's own default brain, so a member's turn queued
as "brain" was learned into the ADMIN's brain, and a member switching learning off switched it off for everyone.

Law after the fix:
  1. learn_config.json is per account; the primary account keeps the legacy file.
  2. A turn queued by one account is pending only for that account.
  3. The scheduler runs the learn tick once per account.

Run:
    python tests/run.py learn_per_user
"""
import asyncio
import os
import re
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-learn-user-"))
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
LF = main.learn_feature

# ── 1. Config per account ────────────────────────────────────────────────────
r = bob.post("/learn/config", data={"enabled": "0"})
check("bob turns learning off", r.status_code == 200 and r.json()["config"]["enabled"] is False, r.text[:200])
check("the admin's learning stays on", admin.get("/learn/config").json().get("enabled") is True)
check("bob's config lives in bob's state", (cfgmod.get_user_state_dir(BOB) / "learn_config.json").exists())
with cfgmod.as_user(ADMIN):
    check("the primary account keeps the legacy file", LF.config_path == Path(cfgmod.STATE_DIR) / "learn_config.json",
          str(LF.config_path))

# ── 2. Queue per account ─────────────────────────────────────────────────────
bob.post("/learn/config", data={"enabled": "1"})
TURN = ("Please remember that I always want the weekly report split by channel and sorted by revenue, "
        "with last week's numbers beside each row so I can compare at a glance.")


async def _queue():
    with cfgmod.as_user(BOB):
        await LF.enqueue("brain", "bob-conv", TURN, "Noted: weekly report by channel, sorted by revenue, with last week.")
    with cfgmod.as_user(ADMIN):
        admin_pending = dict(LF._pending)
    with cfgmod.as_user(BOB):
        bob_pending = dict(LF._pending)
    return admin_pending, bob_pending


a_p, b_p = asyncio.run(_queue())
check("bob's turn is pending for bob", "brain" in b_p and "bob-conv" in b_p["brain"]["convs"], str(b_p))
check("bob's turn is not pending for the admin", "brain" not in a_p, str(a_p))

# ── 3. The scheduler ticks learning per account ──────────────────────────────
src = (Path(SERVER) / "main.py").read_text(encoding="utf-8")
check("learn tick runs per account", re.search(r"_for_each_user\(learn_feature\.tick\)", src) is not None)
check("curator tick runs per account", re.search(r"_for_each_user\(learn_feature\.curator_tick\)", src) is not None)

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
