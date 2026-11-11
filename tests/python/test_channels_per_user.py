"""Owner channels belong to one account.

Reported in docs/dev/multi-user-QA.md: Telegram, Zalo and the other owner channels were one
process-wide bot, one session map and one pairing queue, while settings were already per
account. Saving a member's Telegram token restarted the admin's bot.

Law after the fix:
  1. Saving one account's channel settings starts a bot for THAT account only. The other
     account's bot keeps running, on its own token.
  2. A turn that bot receives runs as its account: brains resolve under that account's folder,
     never the other account's.
  3. The chat maps (conversation, brain, agent) and the pairing queue are per account. One
     account's chat never reads the other's entry, and the files live in that account's state.
  4. /telegram/status and /zalo-bot/status report the caller's own bot.
  5. A report sent as one account uses that account's own token.

Run:
    python tests/run.py channels_per_user
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-channels-user-"))
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
import owner_channels  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")


def save(cl, section, **fields):
    import json as _j
    return cl.post("/settings", data={"section": section, "data": _j.dumps(fields)})


def login(cl, user, pw):
    main._LOGIN_FAILS.clear()
    return cl.post("/auth/login", data={"username": user, "password": pw})


admin = client()
check("setup", admin.post("/auth/setup", data={"username": "admin", "password": "adminpass123"}).status_code == 200)
check("admin creates bob", admin.post("/auth/users", data={"username": "bob", "password": "bobpass123"}).status_code == 200)
bob = client()
check("bob logs in", login(bob, "bob", "bobpass123").status_code == 200)
BOB = cfgmod.get_user(username="bob")["user_id"]
ADMIN = cfgmod.primary_user_id()

# Bots must not open a real socket. start() is what the restart paths call.
_started = []


class _FakeTask:
    def done(self):
        return False

    def cancel(self):
        pass


def _fake_start(self):
    self.status = "running"
    self._task = _FakeTask()
    _started.append(self.token)


main.TelegramBot.start = _fake_start
main.zalo_bot.ZaloBot.start = _fake_start


def _token_of(bot):
    return getattr(bot, "token", None) if bot else None


# ── 1. Each account's bot is its own ─────────────────────────────────────────
r = save(admin, "telegram", enabled=True, token="tg-admin", chat_id="111")
check("admin saves telegram", r.status_code == 200 and r.json().get("ok"), str(r.text)[:200])
admin_tg = main._TG_BOT
check("admin telegram bot runs on the admin token", _token_of(admin_tg) == "tg-admin")

r = save(bob, "telegram", enabled=True, token="tg-bob", chat_id="222")
check("bob saves telegram", r.status_code == 200 and r.json().get("ok"), str(r.text)[:200])
check("admin bot is untouched by the member save", main._TG_BOT is admin_tg and _token_of(main._TG_BOT) == "tg-admin")
check("member bot runs on the member token", _token_of(main._tg_bot_of(BOB)) == "tg-bob")

r = save(admin, "zalo_bot", enabled=True, token="zl-admin", chat_id="aaaa")
check("admin saves zalo", r.status_code == 200 and r.json().get("ok"))
admin_zl = main._ZALO_BOT
r = save(bob, "zalo_bot", enabled=True, token="zl-bob", chat_id="bbbb")
check("bob saves zalo", r.status_code == 200 and r.json().get("ok"))
check("admin zalo bot is untouched", main._ZALO_BOT is admin_zl and _token_of(main._ZALO_BOT) == "zl-admin")
check("member zalo bot runs on the member token", _token_of(main._zalo_bot_of(BOB)) == "zl-bob")

# ── 2. A turn runs as the account the bot belongs to ─────────────────────────
seen = {}


async def _spy(text, meta=None, progress=None, channel="telegram"):
    seen["uid"] = cfgmod.get_current_user_id()
    seen["brain"] = main._brain_root(main._tg_brain(str((meta or {}).get("chat_id") or "")))
    return {"text": "ok", "files": []}


main._tg_owner_answer = _spy
asyncio.run(main._tg_bot_of(BOB).answer_fn("hello", {"chat_id": "222"}))
check("a member bot's turn runs as the member", seen.get("uid") == BOB, str(seen))
check("the member's brain stays in the member folder",
      str(main._user_brains_dir(BOB)) in str(seen.get("brain")), str(seen.get("brain")))
seen.clear()
asyncio.run(admin_tg.answer_fn("hello", {"chat_id": "111"}))
check("the admin bot's turn still runs as the admin", seen.get("uid") == ADMIN, str(seen))

# ── 3. Maps and the pairing queue are per account ────────────────────────────
with cfgmod.as_user(ADMIN):
    main._tg_session("111")["sid"] = "sid-admin"
    main._tg_nho_sid(main._tg_session("111"), "sid-admin")
    main._TG_BRAIN_MAP["111"] = "Admin Brain"
    main._tg_save_brain_map()
with cfgmod.as_user(BOB):
    main._tg_session("222")["sid"] = "sid-bob"
    main._tg_nho_sid(main._tg_session("222"), "sid-bob")
check("the member session map does not hold the admin chat",
      "111" not in main._tg_sess_of(BOB) and main._tg_sess_of(ADMIN).get("111", {}).get("sid") == "sid-admin")
check("the durable sid map is per account",
      main._tg_sid_of(BOB).get("222") == "sid-bob" and "222" not in main._tg_sid_of(ADMIN))
check("the admin sid file stays at the legacy path",
      (cfgmod.STATE_DIR / "tg_sid.json").exists() and "sid-admin" in (cfgmod.STATE_DIR / "tg_sid.json").read_text())
check("the member sid file lives in the member state",
      (cfgmod.get_user_state_dir(BOB) / "tg_sid.json").exists())

# Through the member's own bot, as the transport calls it: no account context set by the caller.
main._zalo_bot_of(BOB).precheck_fn("hi", {"chat_id": "stranger-bob", "user_name": "Lan"})
check("a member's pairing queue is not the admin's",
      "stranger-bob" in main._zalo_cho_of(BOB) and "stranger-bob" not in main._zalo_cho_of(ADMIN))

# ── 4. Status reports the caller's own bot ───────────────────────────────────
check("admin status shows the admin bot", admin.get("/telegram/status").json().get("token_set") is True
      and admin.get("/telegram/status").json().get("chat_id") == "111")
check("member status shows the member bot", bob.get("/telegram/status").json().get("chat_id") == "222")
check("member zalo status is the member's", bob.get("/zalo-bot/status").json().get("chat_id") == "bbbb")
check("admin zalo status is untouched", admin.get("/zalo-bot/status").json().get("chat_id") == "aaaa")

# ── 5. A report uses the account's own token ─────────────────────────────────
sent = []


class _FakeResp:
    status_code = 200
    content = b"{}"

    def json(self):
        return {"ok": True}


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **k):
        sent.append(url)
        return _FakeResp()


import httpx  # noqa: E402
httpx.AsyncClient = _FakeClient
with cfgmod.as_user(BOB):
    ok, err = asyncio.run(main._tg_send_to("222", "report"))
check("a member report goes out on the member token", ok and any("tg-bob" in u for u in sent), str(sent))
check("a member report never uses the admin token", not any("tg-admin" in u for u in sent))

# ── 6. The other owner channels follow the same rule ─────────────────────────
ch = owner_channels.SLACK
ch._transport = lambda: type("T", (), {"__init__": lambda s, *a, **k: None, "start": lambda s: None,
                                       "stop": lambda s: None})
r = save(bob, "slack", enabled=True, bot_token="xoxb-bob", app_token="xapp-bob", allow="UBOB")
check("member saves slack", r.status_code == 200 and r.json().get("ok"), str(r.text)[:200])
check("the shared slack bot is not the member's", ch.bot is None)
check("the member has their own slack bot", owner_channels.bot_of(ch, BOB) is not None)

# ── 7. A customer bot never polls any account's admin token ──────────────────
import chatbot_runtime  # noqa: E402
chatbot_runtime._deps["admin_tokens"] = main._admin_bot_tokens
with cfgmod.as_user(ADMIN):
    toks = chatbot_runtime._admin_tokens("telegram")
check("the admin-token guard sees every account's owner bot", {"tg-admin", "tg-bob"} <= toks, str(toks))

# ── 7b. /chat/jobs: a background chat answer belongs to the account that started it ──
class _Req:
    class state:
        api_token_id = ""
    headers = {}


with cfgmod.as_user(ADMIN):
    owner_admin = main._chat_job_owner(_Req())
with cfgmod.as_user(BOB):
    owner_bob = main._chat_job_owner(_Req())
check("the primary account keeps the old job owner key", owner_admin == "owner", owner_admin)
check("a member's chat jobs have their own owner key", owner_bob == "user:" + BOB, owner_bob)


async def _job_answer():
    return {"text": "admin's private answer"}


async def _start_admin_job():
    jid, _why = main._CHAT_JOBS.start(owner_admin, "lane-admin", _job_answer)
    await asyncio.sleep(0.05)
    return jid


jid = asyncio.run(_start_admin_job())
check("the admin reads their own job", (main._CHAT_JOBS.get(owner_admin, jid) or {}).get("job_id") == jid)
check("bob cannot read the admin's job", main._CHAT_JOBS.get(owner_bob, jid) is None)

# ── 7b. Dedicated customer bots and their channel accounts are per account ───
import channel_accounts  # noqa: E402
import chatbot_runtime  # noqa: E402
import chatbot_store  # noqa: E402
import messenger_bot  # noqa: E402


def _seed_bot(uid, token, name):
    with cfgmod.as_user(uid):
        aid, why = channel_accounts.create_account({"channel": "telegram", "token": token, "label": name})
        assert aid, why
        d = chatbot_store._load()
        bid = f"bot_{name}"
        d["bots"].append({"id": bid, "slug": name, "name": name, "channel": "telegram", "brain": "Brain Default",
                          "agent_slug": "cskh", "enabled": True, "accounts": [aid]})
        chatbot_store._save(d)
        return bid


bid_admin = _seed_bot(ADMIN, "cb-admin", "admin-shop")
bid_bob = _seed_bot(BOB, "cb-bob", "bob-shop")
with cfgmod.as_user(ADMIN):
    admin_sees = [b["id"] for b in chatbot_store.list_bots()]
    admin_accs = [a.get("label") for a in channel_accounts.list_accounts(channel="telegram")]
with cfgmod.as_user(BOB):
    bob_sees = [b["id"] for b in chatbot_store.list_bots()]
check("the admin does not see bob's customer bot", bid_bob not in admin_sees and bid_admin in admin_sees, str(admin_sees))
check("bob does not see the admin's customer bot", bid_admin not in bob_sees and bid_bob in bob_sees, str(bob_sees))
check("channel accounts are per account too", "bob-shop" not in admin_accs, str(admin_accs))
check("bob's bot file lives in bob's state", (cfgmod.get_user_state_dir(BOB) / "chatbots.json").exists())

chatbot_runtime._deps.setdefault("answer", lambda *a, **k: None)   # wired at server startup, not in this test
chatbot_runtime._deps.setdefault("brain_root", lambda b: str(_BRAINS / b))
res = chatbot_runtime.sync_all()
run_a, run_b = chatbot_runtime._RUNNING.get(bid_admin), chatbot_runtime._RUNNING.get(bid_bob)
check("sync starts every account's customer bots", bool(run_a and run_b), str(res))
check("each running bot remembers its account", (run_a or {}).get("user_id") == ADMIN and (run_b or {}).get("user_id") == BOB)
check("bob's poller runs on bob's token", run_b and any(getattr(t, "token", "") == "cb-bob" for t in run_b["pollers"].values()))
check("a pass for one account leaves the other's bots running", chatbot_runtime.sync_all() and bid_admin in chatbot_runtime._RUNNING
      and bid_bob in chatbot_runtime._RUNNING)


class _Owned:
    user_id = BOB


async def _webhook_task():
    async def _who():
        return cfgmod.current_user_id.get()
    with cfgmod.as_user(ADMIN):            # a cookie-less webhook runs as the primary account
        return await messenger_bot._spawn(_Owned(), _who())


check("a webhook event runs as the account that owns the bot", asyncio.run(_webhook_task()) == BOB)

with cfgmod.as_user(BOB):
    _ms_id, _ms_why = channel_accounts.create_account({"channel": "messenger", "label": "bob-page",
                                     "token": "123456789 EAA" + "x" * 60 + " " + "b0" * 16})
check("bob saves a messenger account", bool(_ms_id), _ms_why)
with cfgmod.as_user(ADMIN):
    secrets_seen = main._ms_account_secrets()
check("the webhook signature check knows a member's app secret", "b0" * 16 in secrets_seen, str(secrets_seen))
with cfgmod.as_user(BOB):
    vt_bob = main._ms_verify_token()
with cfgmod.as_user(ADMIN):
    vt_admin = main._ms_verify_token()
check("the webhook verify token is one per machine", vt_bob and vt_bob == vt_admin)

# ── 8. Deleting an account stops its bots ────────────────────────────────────
member_bot = main._tg_bot_of(BOB)
r = admin.post("/auth/users/delete", data={"user_id": BOB})
check("admin deletes bob", r.status_code == 200 and r.json().get("ok"), str(r.text)[:200])
check("the deleted account's bot is gone", main._CH_BOTS.get(("telegram", BOB)) is None
      and member_bot.status == "stopped")
check("the admin bot still runs", main._TG_BOT is admin_tg and admin_tg.status == "running")
check("the deleted account's customer bots stop", bid_bob not in chatbot_runtime._RUNNING)
check("the admin's customer bot keeps running", bid_admin in chatbot_runtime._RUNNING)

print()
if fails:
    print(f"{len(fails)} FAIL: " + ", ".join(fails))
    raise SystemExit(1)
print("ALL PASS")
