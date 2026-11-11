"""Two-factor auth belongs to the signed-in account, not always to the primary one.

Before this, /auth/2fa/start|enable|disable|recovery read and wrote the primary account's
2FA block whatever account was signed in, and the pending secret was one machine-wide slot:
a member turning on 2FA locked the ADMIN behind the member's phone. /auth/disable (machine-wide
login switch) is the primary account's call only.
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


os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-2fa-pu-")
os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
os.environ["BRAINS_DIR"] = tempfile.mkdtemp(prefix="javis-2fa-pu-brains-")
for k in ("JAVIS_ADMIN_PASSWORD", "JAVIS_ADMIN_USER", "JAVIS_USER_ID", "JAVIS_AUTH_PATH", "JAVIS_SESSIONS_PATH"):
    os.environ.pop(k, None)

import config as c  # noqa: E402
import main  # noqa: E402
import totp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")


def login(cl, user, pw, code=""):
    main._LOGIN_FAILS.clear()
    data = {"username": user, "password": pw}
    if code:
        data["code"] = code
    return cl.post("/auth/login", data=data)


admin = client()
admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"})
admin_uid = c.read_auth().get("user_id")
bob_uid = admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"}).json().get("user_id")
bob = client()
check("bob signs in", login(bob, "bob", "bobpass123").status_code == 200)

# Both start a setup: the pending secrets must not overwrite each other.
s_admin = admin.post("/auth/2fa/start").json()["secret"]
s_bob = bob.post("/auth/2fa/start").json()["secret"]
check("each account gets its own pending secret", s_admin != s_bob)
step = totp.buoc_hien_tai()
r = bob.post("/auth/2fa/enable", data={"code": totp.ma_cua_buoc(s_bob, step)})
check("bob turns on 2FA with his code", r.status_code == 200 and r.json().get("ok"))
bob_rec = r.json().get("recovery") or []
check("bob's 2FA is on", c.totp_enabled(user_dict=c.get_user(user_id=bob_uid)))
check("admin's 2FA is NOT on", not c.totp_enabled(user_dict=c.get_user(user_id=admin_uid)))
r = admin.post("/auth/2fa/enable", data={"code": totp.ma_cua_buoc(s_admin, step)})
check("admin's own pending setup still completes", r.status_code == 200 and r.json().get("ok"))
check("admin's secret is admin's, not bob's",
      c.totp_secret(user_dict=c.get_user(user_id=admin_uid)) == s_admin
      and c.totp_secret(user_dict=c.get_user(user_id=bob_uid)) == s_bob)

# Sign-in asks each account for its own code.
check("bob needs a code", login(client(), "bob", "bobpass123").json().get("needs_2fa") is True)
check("bob signs in with his code", login(client(), "bob", "bobpass123", totp.ma_cua_buoc(s_bob, step + 1)).status_code == 200)
check("admin's code does not open bob", login(client(), "bob", "bobpass123", totp.ma_cua_buoc(s_admin, step + 2)).status_code == 401)

# Recovery and disable work on bob's block with bob's password.
r = bob.post("/auth/2fa/recovery", data={"password": "bobpass123"})
check("bob regenerates his recovery codes with his password", r.status_code == 200 and r.json().get("ok"))
new_rec = r.json().get("recovery") or []
check("admin's password cannot touch bob's 2FA",
      bob.post("/auth/2fa/disable", data={"password": "adminpass123", "code": new_rec[0]}).status_code == 401)
r = bob.post("/auth/2fa/disable", data={"password": "bobpass123", "code": new_rec[0]})
check("bob turns his 2FA off", r.status_code == 200 and r.json().get("ok"))
check("bob's 2FA is off", not c.totp_enabled(user_dict=c.get_user(user_id=bob_uid)))
check("admin's 2FA is still on", c.totp_enabled(user_dict=c.get_user(user_id=admin_uid)))
check("old recovery codes are gone", len(bob_rec) == len(new_rec) and bob_rec != new_rec)

# The machine-wide login switch is the primary account's.
r = bob.post("/auth/disable", data={"password": "bobpass123"})
check("a member cannot turn off login", r.status_code == 403)
check("login is still on", c.auth_enabled(c.read_settings(user_id=admin_uid)))

if _fails:
    print(f"\n{len(_fails)} FAIL")
    sys.exit(1)
print("\nall ok")
