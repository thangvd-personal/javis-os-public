"""The bell (inbox), news read marks and push subscriptions belong to one account.

Reported in docs/dev/multi-user-QA.md 13.4: inbox.json, news_read.json and push_subs.json were one file for the
machine, and the bell's WebSocket ping went to every open page. A member saw, read and deleted the admin's
notices, and an admin's notice could push to a member's phone.

Law after the fix:
  1. A notice filed as one account lands in that account's bell only, and its file lives in that account's state.
  2. Marking read or deleting in one account leaves the other's untouched.
  3. News read marks are per account.
  4. Push subscriptions are per account.
  5. The bell's WebSocket ping reaches only pages signed in as that account.

Run:
    python tests/run.py inbox_per_user
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-inbox-user-"))
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
import inbox  # noqa: E402
import webpush  # noqa: E402
from chat_runtime import ChatRuntime  # noqa: E402

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

# ── 1. A notice lands in its own account's bell ──────────────────────────────
with cfgmod.as_user(ADMIN):
    it_admin = inbox.add("admin's private report", kind="answer")
with cfgmod.as_user(BOB):
    it_bob = inbox.add("bob's own report", kind="answer")

ids_admin = [i["id"] for i in admin.get("/inbox").json().get("items", [])]
ids_bob = [i["id"] for i in bob.get("/inbox").json().get("items", [])]
check("the admin sees their notice, not bob's", it_admin["id"] in ids_admin and it_bob["id"] not in ids_admin, str(ids_admin))
check("bob sees his notice, not the admin's", it_bob["id"] in ids_bob and it_admin["id"] not in ids_bob, str(ids_bob))
check("bob's bell file lives in bob's state", (cfgmod.get_user_state_dir(BOB) / "inbox.json").exists())

# ── 2. Read and delete stay in their account ─────────────────────────────────
r = bob.post("/inbox/read", json={"all": True})
check("bob marks all read", r.status_code == 200, r.text[:200])
check("the admin's notice is still unread", admin.get("/inbox").json().get("unread") == 1)
r = bob.post("/inbox/delete", json={"id": it_admin["id"]})
check("bob cannot delete the admin's notice", not (r.json() or {}).get("ok"))
check("the admin's notice is still there", it_admin["id"] in [i["id"] for i in admin.get("/inbox").json()["items"]])

# ── 3. News read marks ───────────────────────────────────────────────────────
with cfgmod.as_user(BOB):
    inbox.news_mark(["news-1"])
with cfgmod.as_user(ADMIN):
    check("bob's news marks do not touch the admin's", "news-1" not in inbox.news_state()["read"])
with cfgmod.as_user(BOB):
    check("bob keeps his own news marks", "news-1" in inbox.news_state()["read"])

# ── 4. Push subscriptions ────────────────────────────────────────────────────
with cfgmod.as_user(BOB):
    webpush._save_subs({"version": 1, "subs": [{"endpoint": "https://push.example/bob"}]})
with cfgmod.as_user(ADMIN):
    check("the admin's notices do not push to bob's phone", webpush.so_sub() == 0)
with cfgmod.as_user(BOB):
    check("bob's phone is subscribed for bob", webpush.so_sub() == 1)

# ── 5. The bell's ping reaches only that account's pages ─────────────────────
got = {"a": [], "b": [], "x": []}


async def _ping():
    rt = ChatRuntime()

    def w(k):
        async def _w(ev):
            got[k].append(ev)
        return _w
    rt.add_client("a", w("a"), user_id=ADMIN)
    rt.add_client("b", w("b"), user_id=BOB)
    rt.add_client("x", w("x"))
    await rt.publish({"type": "inbox", "session_id": "", "_user": BOB})
    await rt.publish({"type": "stream", "session_id": "s", "content": "hi"})


asyncio.run(_ping())
check("bob's bell ping reaches bob's page only", len(got["b"]) == 2 and len(got["a"]) == 1 and len(got["x"]) == 1, str(got))
check("the account key is not sent to the page", "_user" not in got["b"][0], str(got["b"][:1]))

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
