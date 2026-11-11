"""Sharing one brain with another account (multi-user, docs/dev/multi-user-QA.md part 8).

Law:
  1. An admin grants a member access to one of the admin's own brains, as editor or viewer. A member grants nothing.
  2. A grant is an invitation: the brain stays invisible until the member accepts it.
  3. Once accepted, the member opens it, reads it, and (editor only) writes in it. A viewer gets read-only, including
     the chat turn's permission. The member never sees the admin's other brains.
  4. The admin revokes at any time, and the member loses access immediately. A member cannot grant a shared brain on.
  5. A member still cannot open another account's brain by guessing its path.

Run:
    python tests/run.py brain_share
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-share-"))
_BRAINS = _TMP / "brains"
(_BRAINS / "Brain Default" / "Javis").mkdir(parents=True, exist_ok=True)
os.environ["JAVIS_STATE_DIR"] = str(_TMP / "state")
os.environ["JAVIS_SESSIONS_DB"] = str(_TMP / "conversations.db")
os.environ["BRAINS_DIR"] = str(_BRAINS)
os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
(_TMP / "state").mkdir(parents=True, exist_ok=True)

import config as cfgmod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def client(port):
    return TestClient(main.app, client=("127.0.0.1", port), base_url="http://localhost:7777")


admin = client(50000)
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
check("admin creates bob", admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"}).status_code == 200)
BOB = cfgmod.get_user(username="bob")["user_id"]

SHARED = _BRAINS / "Chung"
SHARED.mkdir()
(SHARED / "note.md").write_text("bi mat cua admin", encoding="utf-8")
PRIVATE = _BRAINS / "Rieng"
PRIVATE.mkdir()

bob = client(50001)
check("bob signs in", bob.post("/auth/login", data={"username": "bob", "password": "bobpass123"}).status_code == 200)

r = bob.post("/brains/share", data={"brain": str(SHARED), "username": "admin", "role": "viewer"})
check("a member cannot grant", r.status_code == 403, r.text[:160])

r = admin.post("/brains/share", data={"brain": str(SHARED), "username": "bob", "role": "editor"})
check("admin grants bob editor on one brain", r.status_code == 200 and r.json().get("ok"), r.text[:200])
check("the grant is pending until accepted", r.json().get("status") == "pending", r.text[:200])

names = [b["name"] for b in bob.get("/brains").json()["brains"]]
check("a pending grant is invisible", "Chung" not in names and "Rieng" not in names, str(names))

r = bob.post("/brains/share/accept", data={"brain": str(SHARED)})
check("bob accepts", r.status_code == 200 and r.json().get("ok"), r.text[:160])
listed = {b["name"]: b for b in bob.get("/brains").json()["brains"]}
check("bob now sees the shared brain, not the private one", "Chung" in listed and "Rieng" not in listed, str(list(listed)))
check("the shared brain says it is shared and editable",
      listed["Chung"].get("shared") == "editor", str(listed.get("Chung")))

r = bob.get("/files/read", params={"brain": str(SHARED), "path": "note.md"})
check("bob reads the shared brain", r.status_code == 200 and "bi mat" in r.text, r.text[:160])
r = bob.get("/files/read", params={"brain": str(PRIVATE), "path": "note.md"})
check("bob cannot read the private brain", r.status_code != 200 or "bi mat" not in r.text, str(r.status_code))

with cfgmod.as_user(BOB):
    mode = main._muc_quyen_luot_chat({"id": "", "channel": ""}, "", brain=str(SHARED))
check("an editor's chat turn keeps full access", mode == "full", mode)
with cfgmod.as_user(BOB):
    check("bob resolves the shared brain to itself", main._brain_root(str(SHARED)) == str(SHARED))
    check("bob's guess at the private brain falls back to his own default",
          Path(main._brain_root(str(PRIVATE))).resolve() != PRIVATE.resolve())

r = admin.post("/brains/share", data={"brain": str(SHARED), "username": "bob", "role": "viewer"})
check("admin lowers bob to viewer", r.status_code == 200 and r.json().get("role") == "viewer", r.text[:160])
r = bob.post("/files/write", data={"brain": str(SHARED), "path": "note.md", "content": "bob sua"})
check("a viewer cannot write", r.status_code == 403, str(r.status_code))
check("the file is untouched", (SHARED / "note.md").read_text(encoding="utf-8") == "bi mat cua admin")
with cfgmod.as_user(BOB):
    mode = main._muc_quyen_luot_chat({"id": "", "channel": ""}, "", brain=str(SHARED))
check("a viewer's chat turn is read-only", mode == "suggest", mode)

r = bob.post("/brains/share", data={"brain": str(SHARED), "username": "admin", "role": "editor"})
check("a member cannot re-grant a brain shared with them", r.status_code == 403, str(r.status_code))

r = admin.post("/brains/share/revoke", data={"brain": str(SHARED), "username": "bob"})
check("admin revokes", r.status_code == 200 and r.json().get("ok"), r.text[:160])
names = [b["name"] for b in bob.get("/brains").json()["brains"]]
check("bob loses the brain at once", "Chung" not in names, str(names))
with cfgmod.as_user(BOB):
    check("the revoked brain no longer resolves for bob",
          Path(main._brain_root(str(SHARED))).resolve() != SHARED.resolve())

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
