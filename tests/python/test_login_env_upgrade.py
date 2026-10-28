"""Updating never overwrites a password the owner set in the dashboard (0.96.1).

    python tests/run.py login_env_upgrade      (NO network)

Many owners reported on 2026-10-11 "after the update I cannot sign in". Their servers set the admin from the environment
(Docker, Hostinger) and the message they got was "Wrong username or password ... the username is JAVIS_ADMIN_USER".

Cause, reproduced by booting 0.91.0 then the current code on the same state: 0.92.1 started cleaning surrounding whitespace off
JAVIS_ADMIN_PASSWORD, but the `env_applied` marker written by older versions is the hash of the value WITH its whitespace. The
first boot after the update compared only the cleaned value, read "the env changed", and reset the account to the env value:
the owner's own password was overwritten and their username became "admin". This file pins:
  1. same env value with whitespace + password changed in the dashboard: nothing is overwritten (password, username, 2FA,
     sessions);
  2. same env value with whitespace + password never changed: stored clean, so the owner signs in without the invisible space;
  3. a REAL change of the env value still resets, as since 0.77.1.
"""
import os
import sys
import tempfile

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-login-upg-")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config as c

_fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        _fails.append(name)


CLEARED = []
c.clear_sessions = lambda: CLEARED.append(1)
ENV_RAW = "Hostinger-Pass-9 "          # what the owner pasted, trailing space included
TOTP = {"enabled": True, "secret": "enc:blob"}


def old_version_state(username, password_typed):
    """settings.json as 0.91.0 left it: the env value applied WITH its whitespace, then maybe changed in the dashboard."""
    m_salt = "m-salt"
    h, salt = c.hash_password(password_typed)
    cfg = c.read_settings()
    cfg["auth"] = {"username": username, "password_hash": h, "salt": salt, "totp": dict(TOTP),
                   "env_applied": {"username": "admin", "salt": m_salt, "hash": c.hash_password(ENV_RAW, m_salt)[0]}}
    c.write_settings(cfg)


os.environ["JAVIS_ADMIN_USER"] = "admin"
os.environ["JAVIS_ADMIN_PASSWORD"] = ENV_RAW

# ---- 1. owner changed username and password in the dashboard, env unchanged (with its space) ----
old_version_state("long", "Mat-khau-rieng-1")
CLEARED.clear()
res = c.provision_admin_from_env()
cfg = c.read_settings()
check("first boot after the update does NOT reset", res == "", res)
check("the owner's own password still signs in", c.verify_password("Mat-khau-rieng-1", cfg))
check("the env password does NOT replace it", not c.verify_password("Hostinger-Pass-9", cfg))
check("the owner's username is kept (not turned into admin)", cfg["auth"]["username"] == "long", cfg["auth"]["username"])
check("2FA stays on", (cfg["auth"].get("totp") or {}).get("enabled") is True, cfg["auth"].get("totp"))
check("nobody is signed out", CLEARED == [])
check("the next boot does nothing either", c.provision_admin_from_env() == ""
      and c.verify_password("Mat-khau-rieng-1", c.read_settings()))

# ---- 2. password never changed: stored clean, both forms sign in ----
old_version_state("admin", ENV_RAW)
CLEARED.clear()
res = c.provision_admin_from_env()
cfg = c.read_settings()
check("unchanged password: no reset reported, nobody signed out", res == "" and CLEARED == [], (res, CLEARED))
check("unchanged password: the clean value now signs in", c.verify_password("Hostinger-Pass-9", cfg))
check("unchanged password: typed with the space still signs in", c.verify_password(ENV_RAW, cfg))
check("unchanged password: stored clean", c.hash_password("Hostinger-Pass-9", cfg["auth"]["salt"])[0] == cfg["auth"]["password_hash"])

# ---- 3. a real change of the env value still resets ----
old_version_state("long", "Mat-khau-rieng-1")
os.environ["JAVIS_ADMIN_PASSWORD"] = "Mat-khau-moi-tren-Hostinger"
CLEARED.clear()
res = c.provision_admin_from_env()
cfg = c.read_settings()
check("env really changed: reset, as since 0.77.1", res == "reset" and c.verify_password("Mat-khau-moi-tren-Hostinger", cfg),
      res)
check("env really changed: 2FA stays on and other sessions are dropped",
      (cfg["auth"].get("totp") or {}).get("enabled") is True and CLEARED == [1], (cfg["auth"].get("totp"), CLEARED))
check("env really changed: the same new value next boot is a no-op", c.provision_admin_from_env() == "")

# ---- 4. a new env user name with the same password is a change too ----
old_version_state("admin", ENV_RAW)
os.environ["JAVIS_ADMIN_PASSWORD"] = ENV_RAW
os.environ["JAVIS_ADMIN_USER"] = "quan-tri"
check("env user name changed: reset to the new name", c.provision_admin_from_env() == "reset"
      and c.read_settings()["auth"]["username"] == "quan-tri")

os.environ.pop("JAVIS_ADMIN_PASSWORD", None)
os.environ.pop("JAVIS_ADMIN_USER", None)
for f in (SERVER / "config.py", __import__("pathlib").Path(__file__)):
    check(f"no em dash in {f.name}", chr(0x2014) not in f.read_text(encoding="utf-8"))

print()
if _fails:
    print(f"ĐỎ {len(_fails)} mục: " + ", ".join(_fails))
    sys.exit(1)
print("Tất cả xanh.")
