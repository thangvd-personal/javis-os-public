"""Lark / Feishu as a CHATBOT channel (0.102.0).

    python tests/run.py lark_chatbot      (NO network: a fake Lark Open API through httpx.MockTransport)

Lark was only an admin channel (0.85.0). The owner asked on 2026-10-12 for it on the Chatbot page too. Pinned here:
  1. The channel registry: one module, every capability the customer bot needs (groups, text, files, reply from the inbox).
  2. One account secret "cli_... SECRET [lark|feishu]": parsed, and verified against Lark then Feishu.
  3. An app already used by the admin Lark channel is refused (Lark splits events between connections of one app).
  4. The transport finds the right cloud by itself when the secret does not pin it.
  5. Customer mode: group messages reach the bot without an @mention (the chatbot decides whether to answer), the
     inbox gets the sender's and the group's names, a missing permission is asked once and then cached.
  6. Replying from the inbox goes through the same Lark API.
"""
import asyncio
import json
import os
import sys
import tempfile

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-larkbot-")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx
import localefmt

localefmt.dat_ngon_ngu_yeu_cau("vi")

import bot_images
import channels
import config as cfgmod
import lark_bot
from channels import lark

_fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        _fails.append(name)


# ---- fake Lark / Feishu -------------------------------------------------------------------------------------------
CALLS = []
APPS = {"lark": {"cli_lark": "s1", "cli_nobot": "s3"}, "feishu": {"cli_feishu": "s2"}}
PERMS = {"contact": True, "chat": True}


def handler(request):
    url = str(request.url)
    cloud = "feishu" if "feishu.cn" in url else "lark"
    path = request.url.path
    CALLS.append((cloud, request.method, path, request.url.query.decode() if request.url.query else ""))
    body = json.loads(request.content.decode() or "{}") if request.content else {}
    if path.endswith("/tenant_access_token/internal"):
        if APPS[cloud].get(body.get("app_id")) == body.get("app_secret"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": f"t-{cloud}-{body['app_id']}", "expire": 7200})
        return httpx.Response(200, json={"code": 10003, "msg": "invalid param"})
    tok = request.headers.get("authorization", "")
    if path == "/open-apis/bot/v3/info":
        if "cli_nobot" in tok:
            return httpx.Response(200, json={"code": 99991672, "msg": "app has no bot ability"})
        return httpx.Response(200, json={"code": 0, "bot": {"open_id": "ou_bot", "app_name": "Shop Bot"}})
    if path.startswith("/open-apis/contact/v3/users/"):
        if not PERMS["contact"]:
            return httpx.Response(200, json={"code": 99991672, "msg": "no permission"})
        return httpx.Response(200, json={"code": 0, "data": {"user": {"name": "Anna Müller"}}})
    if path.startswith("/open-apis/im/v1/chats/"):
        if not PERMS["chat"]:
            return httpx.Response(200, json={"code": 99991672, "msg": "no permission"})
        return httpx.Response(200, json={"code": 0, "data": {"name": "Kunden Gruppe"}})
    if path == "/open-apis/im/v1/messages":
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_sent", "body": body}})
    return httpx.Response(404, json={"code": 404, "msg": "not mocked"})


_Real = httpx.AsyncClient
_mt = httpx.MockTransport(handler)


class _Client(_Real):
    def __init__(self, *a, **kw):
        kw["transport"] = _mt
        super().__init__(*a, **kw)


httpx.AsyncClient = _Client

try:
    # ---- 1. registry ----
    s = channels.spec("lark")
    check("Lark is in the channel registry as a token bot", s is not None and s.kind == "bot" and "lark" in channels.bot_token_ids())
    check("capabilities: groups, text, files, reply from the inbox, attach a bot",
          all(s.nl(k) for k in ("nhom", "gui_chu", "gui_file", "tra_loi_tu_javis", "bot")), s.nang_luc)
    ui = next(x for x in channels.cho_giao_dien() if x["id"] == "lark")
    check("the Chatbot page gets the setup line and the Lark logo", "App ID" in ui["lay_token"] and ui["logo"] == "lark" and ui["co_nhom"])
    check("a customer bot on Lark sends the images it writes", bot_images.channel_sends_files("lark"))

    # ---- 2. the account secret ----
    check("parse: id, secret, no cloud", lark.parse_token(" cli_a  sec ") == {"app_id": "cli_a", "app_secret": "sec", "domain": ""})
    check("parse: a trailing cloud word pins the cloud",
          lark.parse_token("cli_a sec Feishu") == {"app_id": "cli_a", "app_secret": "sec", "domain": "feishu"})

    v = lambda tok: asyncio.run(lark.verify_token(tok))
    r = v("cli_lark s1")
    check("verify: a Lark app", r.get("ok") and r["username"] == "cli_lark" and r["bot_name"] == "Shop Bot"
          and r["account_type"] == "Lark" and r["vao_duoc_nhom"], r)
    r = v("cli_feishu s2")
    check("verify: a Feishu app is found without the cloud word", r.get("ok") and r["account_type"] == "Feishu", r)
    CALLS.clear()
    r = v("cli_feishu s2 feishu")
    check("verify: a pinned cloud asks only that cloud", r.get("ok") and {c[0] for c in CALLS} == {"feishu"}, CALLS)
    r = v("cli_lark wrong")
    check("verify: wrong secret says so and points at Feishu", not r.get("ok") and "feishu" in r["error"], r)
    r = v("cli_nobot s3")
    check("verify: an app without the Bot feature gets the exact fix", not r.get("ok") and "Bot" in r["error"], r)
    r = v("cli_lark")
    check("verify: a missing secret is caught before any call", not r.get("ok") and "App Secret" in r["error"], r)

    # ---- 3. not the admin channel's app ----
    cfgmod.update_settings({"lark": {"app_id": "cli_lark"}})
    r = v("cli_lark s1")
    check("verify: the admin channel's app is refused", not r.get("ok") and "kênh admin" in r["error"], r)
    cfgmod.update_settings({"lark": {"app_id": ""}})

    # ---- 4. the transport finds its cloud ----
    async def token_of(tok):
        b = lark.Transport(tok, "", None, giau_trang_thai=True)
        async with httpx.AsyncClient() as c:
            t = await b._tenant_token(c)
        return b, t

    b, t = asyncio.run(token_of("cli_feishu s2"))
    check("transport: a Feishu app with no cloud word switches to Feishu and stays there",
          t == "t-feishu-cli_feishu" and b.domain == "feishu" and b.base == lark_bot.DOMAINS["feishu"], (t, b.domain))
    try:
        asyncio.run(token_of("cli_feishu s2 lark"))
        pinned_ok = False
    except RuntimeError:
        pinned_ok = True
    check("transport: a pinned cloud is never switched behind the owner's back", pinned_ok)
    check("transport: customer mode, no allow-list", b.giau_trang_thai and not b.chat_ids)

    # ---- 5. customer mode events ----
    def event(chat_type, text="Hallo, gibt es das in Blau?", mid="om_1", mentions=None):
        return {"header": {"event_type": "im.message.receive_v1", "event_id": "e-" + mid},
                "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_anna"}},
                          "message": {"message_id": mid, "chat_id": "oc_g1" if chat_type == "group" else "oc_p1",
                                      "chat_type": chat_type, "message_type": "text",
                                      "content": json.dumps({"text": text}), "mentions": mentions or []}}}

    async def run_event(bot, env):
        got = []

        async def fake_dispatch(client, chat, text, meta):
            got.append((chat, text, dict(meta)))
        bot._dispatch = fake_dispatch
        async with httpx.AsyncClient() as c:
            await bot._on_event(c, env)
        return got

    bot = lark.Transport("cli_lark s1", "", None, giau_trang_thai=True)
    CALLS.clear()
    got = asyncio.run(run_event(bot, event("group")))
    check("customer bot: a group message without @mention still reaches the chatbot", len(got) == 1, got)
    meta = got[0][2] if got else {}
    check("customer bot: the inbox gets the sender's name and the group's name",
          meta.get("user_name") == "Anna Müller" and meta.get("chat_title") == "Kunden Gruppe", meta)
    n_lookups = sum(1 for c in CALLS if "/contact/" in c[2] or "/im/v1/chats/" in c[2])
    asyncio.run(run_event(bot, event("group", mid="om_2")))
    n_after = sum(1 for c in CALLS if "/contact/" in c[2] or "/im/v1/chats/" in c[2])
    check("customer bot: names are cached, not asked on every message", n_lookups == 2 and n_after == 2, (n_lookups, n_after))

    PERMS["contact"] = PERMS["chat"] = False
    bot2 = lark.Transport("cli_lark s1", "", None, giau_trang_thai=True)
    got = asyncio.run(run_event(bot2, event("private", mid="om_3")))
    check("customer bot: without the contact permission the message still goes through, id kept",
          len(got) == 1 and got[0][2].get("user_name") == "" and got[0][2].get("user_id") == "ou_anna", got)
    PERMS["contact"] = PERMS["chat"] = True

    admin = lark_bot.LarkBot({"app_id": "cli_lark", "app_secret": "s1"}, "ou_anna", None)
    CALLS.clear()
    got = asyncio.run(run_event(admin, event("group", mid="om_4")))
    check("admin channel unchanged: a group message without @mention is ignored, no name lookups",
          got == [] and not any("/contact/" in c[2] for c in CALLS), (got, CALLS))

    # ---- 6. reply from the inbox ----
    CALLS.clear()
    ok, err = asyncio.run(channels.gui("lark", {"token": "cli_lark s1"}, "oc_g1", "Ja, in Blau ab Lager.", "group"))
    sent = [c for c in CALLS if c[2] == "/open-apis/im/v1/messages"]
    check("reply from the inbox reaches the Lark chat", ok and not err and sent and "receive_id_type=chat_id" in sent[0][3],
          (ok, err, CALLS))
    ok, err = asyncio.run(channels.gui("lark", {"token": ""}, "oc_g1", "x"))
    check("an account without credentials says so", not ok and "App ID" in err, err)
finally:
    httpx.AsyncClient = _Real

for f in (SERVER / "channels" / "lark.py", SERVER / "lark_bot.py", __import__("pathlib").Path(__file__)):
    check(f"no em dash in {f.name}", chr(0x2014) not in f.read_text(encoding="utf-8"))

print()
if _fails:
    print(f"ĐỎ {len(_fails)} mục: " + ", ".join(_fails))
    sys.exit(1)
print("Tất cả xanh.")
