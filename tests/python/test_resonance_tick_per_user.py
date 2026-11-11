"""The Resonance scheduler pass visits every account's goal store.

Reported in docs/dev/multi-user-QA.md 13.4: each account has its own resonance.sqlite3, but the tick checked only the
primary account's file and ticked only its store, so a member's goals never ran (and never ran at all when the admin
had no store).

Law after the fix:
  1. Each account that has a store is ticked once, running as that account, on that account's store.
  2. An account without a store creates no file.

Run:
    python tests/run.py resonance_tick_per_user
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-resonance-user-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "Javis").mkdir(parents=True, exist_ok=True)
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
os.environ.pop("JAVIS_RESONANCE_TICK_PAUSED", None)
(_TMP / "state").mkdir(parents=True, exist_ok=True)

import config as cfgmod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


admin = TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
check("admin creates bob", admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"}).status_code == 200)
BOB = cfgmod.get_user(username="bob")["user_id"]
ADMIN = cfgmod.primary_user_id()

# Only bob has goals.
main._resonance_store(BOB)
check("bob's store exists", (cfgmod.get_user_state_dir(BOB) / "resonance.sqlite3").is_file())
check("the admin has no store", not (Path(cfgmod.STATE_DIR) / "resonance.sqlite3").is_file())

seen = []


async def fake_tick(store, now, deps):
    seen.append((cfgmod.current_user_id.get(), str(store.path if hasattr(store, "path") else store)))


async def fake_drain(store, notify, already=None):
    return None


main.resonance.tick = fake_tick
main.resonance.drain_outbox = fake_drain
asyncio.run(main._resonance_tick())

check("bob's store is ticked, as bob", [u for u, _ in seen] == [BOB], str(seen))
check("the tick used bob's own store", seen and str(cfgmod.get_user_state_dir(BOB)) in seen[0][1], str(seen))
check("no store is created for the admin", not (Path(cfgmod.STATE_DIR) / "resonance.sqlite3").is_file())

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
