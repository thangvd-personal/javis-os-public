"""The layer-3 pulse tick visits every account's resonance store, each with its own host.

Reported in docs/dev/multi-user-QA.md 13.4: the tick probed the primary account's resonance.sqlite3 and ticked one
shared host, so a member's assistants never pulsed, and the host's in-process state (in-flight reason ids, the context
refresh mark) was shared across stores whose integer row ids collide.

Law after the fix:
  1. Each account that has a store is ticked once, running as that account, on that account's store and its own host.
  2. An account without a store creates no file and no host.
  3. A member's judge labels land in the member's own agent_judge.sqlite3, not the primary account's.

Run:
    python tests/run.py pulse_tick_per_user
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-pulse-user-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "Javis").mkdir(parents=True, exist_ok=True)
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
os.environ.pop("JAVIS_PULSE_TICK_PAUSED", None)
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

# Only bob has a store.
main._resonance_store(BOB)
check("bob's store exists", (cfgmod.get_user_state_dir(BOB) / "resonance.sqlite3").is_file())
check("the admin has no store", not (Path(cfgmod.STATE_DIR) / "resonance.sqlite3").is_file())

seen = []
hosts = []


class FakeHost:
    def __init__(self, uid):
        self.uid = uid
        self.store = main._pulse_store()

    async def tick(self):
        seen.append((self.uid, cfgmod.current_user_id.get(), str(self.store.path)))


class Recorder(FakeHost):
    async def tick(self):
        hosts.append(self)
        await super().tick()


main._build_pulse_host = lambda: Recorder(cfgmod.current_user_id.get())
main._learn_tick = lambda: asyncio.sleep(0)
asyncio.run(main._pulse_tick())

check("only bob is ticked", [u for u, _, _ in seen] == [BOB], str(seen))
check("the tick runs as bob", seen and seen[0][1] == BOB, str(seen))
check("the tick used bob's own store", seen and str(cfgmod.get_user_state_dir(BOB)) in seen[0][2], str(seen))
check("bob got his own host", len(hosts) == 1 and hosts[0].uid == BOB, str([h.uid for h in hosts]))
check("no store is created for the admin", not (Path(cfgmod.STATE_DIR) / "resonance.sqlite3").is_file())
check("no host is built for the admin", ADMIN not in main._PULSE_HOSTS, str(list(main._PULSE_HOSTS)))

# A second tick must reuse the same host, so its in-flight reason ids survive between passes.
asyncio.run(main._pulse_tick())
check("the same host serves bob on the next pass", len(hosts) == 2 and hosts[0] is hosts[1])

# The judge store follows the account too.
with cfgmod.as_user(BOB):
    main._agent_judge_store()
check("bob's judge labels go to bob's state folder",
      (cfgmod.get_user_state_dir(BOB) / "agent_judge.sqlite3").is_file())
check("the admin gets no judge store", not (Path(cfgmod.STATE_DIR) / "agent_judge.sqlite3").is_file())

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
