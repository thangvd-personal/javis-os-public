"""Automatic learning from chat keeps one learner per account.

Reported in docs/dev/multi-user-QA.md 13.4: one AutoLearner (store improvement/auto_learning.sqlite3 in the machine's
state) took every account's turns, and the scheduler stepped it as the primary account, so a member's turns were
judged and learned with the admin's settings, engine and policy.

Law after the fix:
  1. The learner of a member lives in that member's state folder; the primary account keeps the old store.
  2. The scheduler steps each account's learner, running as that account.

Run:
    python tests/run.py auto_learning_per_user
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-autolearn-user-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "Javis").mkdir(parents=True, exist_ok=True)
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
os.environ.pop("JAVIS_AUTO_LEARNING_PAUSED", None)
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

with cfgmod.as_user(ADMIN):
    la = main._auto_learner()
with cfgmod.as_user(BOB):
    lb = main._auto_learner()
check("each account has its own learner", la is not lb)
check("the admin keeps the old store", Path(la.store.path) == Path(cfgmod.STATE_DIR) / "improvement" / "auto_learning.sqlite3",
      str(getattr(la.store, "path", "")))
check("bob's store lives in bob's state",
      Path(lb.store.path) == cfgmod.get_user_state_dir(BOB) / "improvement" / "auto_learning.sqlite3",
      str(getattr(lb.store, "path", "")))

seen = []
for uid, ln in ((ADMIN, la), (BOB, lb)):
    ln.step = (lambda u: (lambda: seen.append((u, cfgmod.current_user_id.get())) or {}))(uid)
asyncio.run(main._auto_learning_tick())
check("the tick steps each account's learner as that account", sorted(seen) == sorted([(ADMIN, ADMIN), (BOB, BOB)]), str(seen))

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
