"""Multi-user hardening: regressions found in the review of the multi-user branch.

Each block pins one bug that was reproduced against the running app:
1. Login must check username and password against the SAME account
   (`admin` + bob's password used to sign in as admin).
"""
import os
import sys
import tempfile

from _paths import ROOT, SERVER  # noqa: F401

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


state_root = tempfile.mkdtemp(prefix="javis-mu-hardening-")
os.environ["JAVIS_STATE_DIR"] = state_root
os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
os.environ["BRAINS_DIR"] = tempfile.mkdtemp(prefix="javis-mu-brains-")
for k in ("JAVIS_ADMIN_PASSWORD", "JAVIS_ADMIN_USER", "JAVIS_USER_ID", "JAVIS_AUTH_PATH", "JAVIS_SESSIONS_PATH"):
    os.environ.pop(k, None)

import config as c  # noqa: E402
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")


def login(cl, user, pw):
    main._LOGIN_FAILS.clear()
    return cl.post("/auth/login", data={"username": user, "password": pw})


admin = client()
check("setup creates the first account", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
admin_uid = c.read_auth().get("user_id")
r = admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"})
check("admin creates bob", r.status_code == 200 and r.json().get("ok"))
bob_uid = r.json().get("user_id")

# ---- 1. Username and password belong to the same account ----
r = login(client(), "admin", "bobpass123")
check("admin + bob's password is rejected", r.status_code == 401)
r = login(client(), "bob", "adminpass123")
check("bob + admin's password is rejected", r.status_code == 401)
check("verify_password does not accept another user's password",
      not c.verify_password("bobpass123", {"auth": c.get_user(user_id=admin_uid)}))
cl = client()
r = login(cl, "Admin ", "adminpass123")
check("admin still signs in (case and spaces ignored)", r.status_code == 200
      and c.get_session_user_id(cl.cookies.get("javis_session")) == admin_uid)
cl = client()
r = login(cl, "bob", "bobpass123")
check("bob signs in as bob", r.status_code == 200
      and c.get_session_user_id(cl.cookies.get("javis_session")) == bob_uid)

# ---- end ----
print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("All multi-user hardening checks passed.")
