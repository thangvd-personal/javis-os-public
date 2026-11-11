"""The loop scheduler's brain registry (loop_config.json) belongs to one account.

Reported in docs/dev/multi-user-QA.md 13.4: `loop_feature.scheduler_brains` read one file for the whole machine, so
an outside brain one account registered was visited by every account's scheduler pass. Reads were filtered by `_brain_allowed`, but the file itself was shared.

Law after the fix:
  1. register_brain as one account writes that account's loop_config.json only.
  2. The other account's scheduler_brains does not list it.
  3. The primary account keeps the legacy file in the state root.

Run:
    python tests/run.py loop_registry_per_user
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-loopreg-user-"))
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


admin = TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
check("admin creates bob, also an admin (a member cannot register an outside brain)",
      admin.post("/auth/users", data={"username": "bob", "password": "bobpass123", "role": "admin"}).status_code == 200)
BOB = cfgmod.get_user(username="bob")["user_id"]
ADMIN = cfgmod.primary_user_id()
lf = main.loop_feature

outside = _TMP / "bob-outside-brain"
(outside / "Javis").mkdir(parents=True, exist_ok=True)
with cfgmod.as_user(BOB):
    lf.register_brain(str(outside))
    bob_list = [str(Path(main._brain_root(b)).resolve()) for b in lf.scheduler_brains()]
    bob_file = lf.config_path
with cfgmod.as_user(ADMIN):
    admin_list = [str(Path(main._brain_root(b)).resolve()) for b in lf.scheduler_brains()]
    admin_file = lf.config_path

check("bob's scheduler visits the brain bob registered", str(outside.resolve()) in bob_list, str(bob_list))
check("the admin's scheduler does not", str(outside.resolve()) not in admin_list, str(admin_list))
check("bob's registry lives in bob's state", bob_file == cfgmod.get_user_state_dir(BOB) / "loop_config.json"
      and bob_file.exists(), str(bob_file))
check("the primary account keeps the legacy file", admin_file == cfgmod.STATE_DIR / "loop_config.json", str(admin_file))

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
