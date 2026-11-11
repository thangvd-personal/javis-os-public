"""The owner's control channels on Slack, WhatsApp (0.71.0), Discord and Lark/Feishu (0.85.0).

"Control channel" = the owner talks to Javis itself (full brain, tools, background work), the
way the Telegram and Zalo control bots work. Customer-facing bots live elsewhere
(`chatbot_runtime` + `channels/`).

Why one class instead of a third and fourth copy of the Zalo code in main.py: the pairing queue,
the prefix on chat ids, the restart/status/allow/test endpoints and the "send a result back"
path are the same rules for every channel. Only the transport and the settings shape differ.

Access is FAIL-CLOSED, like Zalo and unlike Telegram: an empty allow-list lets nobody in. A
stranger who writes gets a 4-digit pairing code and lands in a queue the owner approves with
one click on the Admin channels page. Slack allows by USER id (U...), WhatsApp by phone number,
Discord by user id, Lark by open_id (ou_...).

Each channel also DESCRIBES its settings (`plain`, `secrets`), so main.py masks, saves and
restarts every channel with one loop instead of a branch per platform.
"""
from __future__ import annotations

import re
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path

import localefmt
from bot_gateway import parse_chat_ids

QUEUE_MAX = 20
QUEUE_TTL = 30 * 60
QUEUE_REMIND = 10 * 60


class OwnerChannel:
    def __init__(self, key: str, label: str, prefix: str, transport: Callable,
                 credentials: Callable[[dict], object], required: tuple, id_label: tuple,
                 plain: dict[str, Callable] | None = None, secret_fields: tuple = (),
                 trust_turn_ids: bool = False):
        self.key = key                  # settings section and channel name: "slack" | "whatsapp"
        self.label = label
        self.prefix = prefix            # owner_chat prefix: "slack:" | "whatsapp:"
        self._transport = transport     # () -> transport class
        self._credentials = credentials  # settings section -> token argument for the transport
        self.required = required        # settings fields that must be non-empty to start
        self.id_label = id_label        # (vi, en): what the allow-list holds, for messages
        # Settings fields: plain ones are shown back to the form (each with a normaliser),
        # secrets are masked on read and only overwritten when a new value is sent.
        self.plain: dict[str, Callable] = dict(plain or {})
        self.secret_fields = tuple(secret_fields)
        # The allow-list holds PEOPLE, but a turn's chat id may be a channel or DM id (Slack D...,
        # a Discord channel, a Lark oc_...). Ids the turn itself produced are trusted for replies.
        self.trust_turn_ids = bool(trust_turn_ids)
        self.bot = None
        self.queue: dict[str, dict] = {}
        self.deps: dict[str, Callable] = {}
        # One bot and one pairing queue PER ACCOUNT (multi-user). The fields above are the primary
        # account's, kept as plain attributes so every caller and test that reads `ch.bot` or
        # `ch.queue` keeps working. Any other account lives in the dicts, keyed by user id.
        self._bots: dict[str, object] = {}
        self._queues: dict[str, dict] = {}


    # ---- per account ------------------------------------------------------------------
    def _uid(self) -> str:
        """The account this call belongs to. Empty before setup, which is the primary account."""
        import config
        return config.current_user_id.get() or config.primary_user_id()

    def _primary(self, uid: str = "") -> bool:
        import config
        uid = uid or self._uid()
        return not uid or uid == config.primary_user_id()

    def bot_of(self, uid: str = "") -> object:
        uid = uid or self._uid()
        return self.bot if self._primary(uid) else self._bots.get(uid)

    def _set_bot(self, bot, uid: str = "") -> None:
        uid = uid or self._uid()
        if self._primary(uid):
            self.bot = bot
        elif bot is None:
            self._bots.pop(uid, None)
        else:
            self._bots[uid] = bot

    def queue_of(self, uid: str = "") -> dict:
        uid = uid or self._uid()
        if self._primary(uid):
            return self.queue
        return self._queues.setdefault(uid, {})

    def stop_all(self) -> None:
        """Shutdown: every account's bot, not only the one the request happens to be."""
        self.stop()
        for uid in list(self._bots):
            bot = self._bots.pop(uid)
            try:
                bot.stop()
            except Exception as e:  # noqa: BLE001 - shutdown must finish for the next account
                print(f"[{self.key} stop {uid[:8]}] {type(e).__name__}: {e}", file=sys.stderr)

    # ---- wiring -----------------------------------------------------------------------
    def wire(self, **deps):
        """answer, command, stt, brain_root_for, read_settings, update_settings."""
        self.deps.update(deps)

    def idl(self) -> str:
        return localefmt.chu(*self.id_label)

    def cfg(self) -> dict:
        rs = self.deps.get("read_settings")
        if rs is None:              # not wired yet (tests, or a call before startup)
            import config
            rs = config.read_settings
        return (rs().get(self.key) or {})

    def allowed(self, c: dict | None = None) -> list:
        return parse_chat_ids((c if c is not None else self.cfg()).get("allow"))

    def configured(self, c: dict | None = None) -> bool:
        c = c if c is not None else self.cfg()
        return all(str(c.get(k) or "").strip() for k in self.required)

    def running(self) -> bool:
        b = self.bot_of()
        return bool(b and b._task and not b._task.done())

    # ---- pairing ----------------------------------------------------------------------
    def _who(self, meta) -> str:
        """The id the allow-list is checked against: the sender, not the chat."""
        m = meta or {}
        return str(m.get("user_id") or m.get("chat_id") or "").strip()

    def _prune(self):
        now = time.time()
        for k in [k for k, v in self.queue_of().items() if now - v.get("ts", 0) > QUEUE_TTL]:
            self.queue_of().pop(k, None)

    def _refusal(self, code: str) -> str:
        return localefmt.chu(
            f"Bạn chưa được cấp quyền dùng Javis này.\nMã ghép nối của bạn: {code}\n"
            "Đưa mã này cho chủ máy để họ cho phép ở trang Kênh Admin.",
            f"You are not allowed to use this Javis yet.\nYour pairing code: {code}\n"
            "Give this code to the owner so they can allow you on the Admin channels page.")

    def _queue(self, meta) -> str:
        """Put a stranger in the pairing queue. Returns what to reply ("" = stay quiet)."""
        self._prune()
        who = self._who(meta)
        if not who:
            return ""
        name = str((meta or {}).get("user_name") or "")[:80]
        old = self.queue_of().get(who)
        if old:
            old["ts"] = time.time()
            old["lan"] = old.get("lan", 0) + 1
            old["ten"] = name or old.get("ten", "")
            if time.time() - old.get("nhac", 0) < QUEUE_REMIND:
                return ""          # one refusal per 10 minutes, so nobody floods the queue
            old["nhac"] = time.time()
            return self._refusal(old["ma"])
        if len(self.queue_of()) >= QUEUE_MAX:
            self.queue_of().pop(min(self.queue_of(), key=lambda k: self.queue_of()[k].get("ts", 0)), None)
        code = f"{secrets.randbelow(9000) + 1000}"
        self.queue_of()[who] = {"chat_id": who, "ten": name, "ma": code, "ts": time.time(),
                           "nhac": time.time(), "lan": 1}
        return self._refusal(code)

    def precheck(self, text, meta):
        if self._who(meta) in self.allowed():
            return None
        return {"reply": self._queue(meta)}

    # ---- turns ------------------------------------------------------------------------
    async def answer(self, text, meta=None, progress=None, channel=None, bot=None):
        """Prefix the chat id before the shared core sees it: it is the session key, the brain
        key and the `owner_chat` background results are routed by. The transport keeps the
        raw id it needs to reply."""
        m = dict(meta or {})
        raw = str(m.get("chat_id") or "").strip()
        m["chat_id"] = (self.prefix + raw) if raw else "default"
        return await self.deps["answer"](text, m, progress, channel=self.key, bot=bot)

    def inbox_dir(self, chat=None):
        root = self.deps["brain_root_for"](self.prefix + str(chat or ""))
        return str(Path(root) / "inbox" / self.key)

    def restart(self) -> bool:
        """Restart THIS account's bot. Another account's bot is a different object and is not touched.

        The bot is built and started inside `config.as_user`, so its poll task (an asyncio task copies
        the context it was created in) serves this account for its whole life."""
        import config
        uid = self._uid()
        old = self.bot_of(uid)
        if old:
            try:
                old.stop()
            except Exception as e:
                print(f"[{self.key} stop] {e}", file=sys.stderr)
            self._set_bot(None, uid)
        c = self.cfg()
        if not (c.get("enabled") and self.configured(c)):
            return False
        Lop = self._transport()
        # No allow-list for the transport: `precheck` handles strangers and hands them a code.
        bot = Lop(self._credentials(c), "", config.bind_user(self.answer, uid),
                  config.bind_user(self.deps.get("command"), uid),
                  download_dir=config.bind_user(self.inbox_dir, uid),
                  precheck_fn=config.bind_user(self.precheck, uid),
                  stt_fn=self.deps.get("stt"))
        with config.as_user(uid):
            bot.start()
        self._set_bot(bot, uid)
        return True

    def stop(self):
        """Stop THIS account's bot."""
        uid = self._uid()
        bot = self.bot_of(uid)
        if bot:
            bot.stop()
            self._set_bot(None, uid)

    # ---- API helpers ------------------------------------------------------------------
    def status(self) -> dict:
        c = self.cfg()
        self._prune()
        b = self.bot_of()
        return {
            "enabled": bool(c.get("enabled")), "configured": self.configured(c),
            "allow": c.get("allow", ""), "allow_ids": self.allowed(c),
            "running": self.running(),
            "status": b.status if b else "off",
            "last_error": (b.last_error if b else "") or "",
            "bot_name": (b.bot_username if b else "") or "",
            "loi_danh_tinh": (getattr(b, "loi_danh_tinh", "") if b else "") or "",
            "cho": sorted(self.queue_of().values(), key=lambda x: x.get("ts", 0), reverse=True),
        }

    def allow(self, who: str, on: bool) -> list:
        who = str(who or "").strip()
        if on and who:
            ids = self.allowed()
            if who not in ids:
                ids.append(who)
            # Cập nhật TỪNG PHẦN. Trước 0.86.2 chỗ này đưa đúng mảnh này cho write_settings, mà hàm
            # đó thay cả file: cho phép một người là mất sạch tên miền, mật khẩu, khoá API.
            up = self.deps.get("update_settings")
            if up is None:
                import config
                up = config.update_settings
            up({self.key: {"allow": ", ".join(ids)}})
        self.queue_of().pop(who, None)
        return self.allowed()

    def _sender(self):
        """A short-lived transport for one outgoing message (no socket, no registration)."""
        c = self.cfg()
        if not (c.get("enabled") and self.configured(c)):
            return None
        return self._transport()(self._credentials(c), "", None, giau_trang_thai=True)

    async def send_to(self, chat_id, text) -> tuple:
        """Send a background result to `chat_id` (raw id, prefix already stripped). An empty id or one outside the
        allow-list is refused (0.186.0), like Telegram and Zalo."""
        b = self._sender()
        if not b:
            return False, localefmt.chu(f"Kênh {self.label} chưa bật hoặc thiếu thông tin",
                                        f"The {self.label} channel is off or not configured")
        ids = self.allowed()
        cid = str(chat_id or "").strip()
        if self.trust_turn_ids:
            # A Slack DM channel id (D...) is not what the allow-list holds (user ids), so any
            # id the turn itself produced is trusted.
            target = cid
        else:
            target = cid if (cid and cid in ids) else ""
        # 0.186.0: no fallback to the first allowed id; that may be someone else. The report stays in the bell.
        if not target:
            return False, localefmt.chu(f"Chưa có {self.idl()} nào được phép",
                                        f"No allowed {self.idl()} yet")
        return await b.send_text(target, text)

    async def test(self) -> dict:
        ids = self.allowed()
        b = self._sender()
        if not b or not ids:
            return {"ok": False, "error": localefmt.chu(
                f"Bật kênh, điền đủ thông tin và cho phép ít nhất một {self.idl()} trước đã.",
                f"Turn the channel on, fill in the credentials and allow at least one {self.idl()} first.")}
        sent, errs = 0, []
        for who in ids:
            ok, err = await b.send_text(who, localefmt.chu(
                f"Javis đã kết nối qua {self.label}. Nhắn câu hỏi bất kỳ nhé.",
                f"Javis is connected through {self.label}. Ask anything."))
            if ok:
                sent += 1
            else:
                errs.append(f"{who}: {str(err)[:120]}")
        return {"ok": sent > 0, "sent": sent, "total": len(ids), "error": "; ".join(errs)[:400]}

    def missing(self) -> str:
        """Why this channel cannot carry background results ("" = it can)."""
        c = self.cfg()
        if not c.get("enabled"):
            return localefmt.chu(f"{self.label} chưa bật", f"{self.label} is not enabled")
        if not self.configured(c):
            return localefmt.chu(f"{self.label} thiếu thông tin đăng nhập", f"{self.label} is missing credentials")
        if not self.allowed(c):
            return localefmt.chu(f"{self.label} chưa có {self.idl()} được phép",
                                 f"{self.label} has no allowed {self.idl()}")
        return ""


def _slack_transport():
    from slack_bot import SlackBot
    return SlackBot


def _wa_transport():
    from whatsapp_bot import WhatsAppBot
    return WhatsAppBot


def _discord_transport():
    from discord_bot import DiscordBot
    return DiscordBot


def _lark_transport():
    from lark_bot import LarkBot
    return LarkBot


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def lark_domain(v) -> str:
    """"lark" (international, open.larksuite.com) unless the owner picked "feishu" (China). Lark is
    the default because it is what businesses outside mainland China sign up for."""
    return "feishu" if str(v or "").strip().lower() == "feishu" else "lark"


SLACK = OwnerChannel(
    "slack", "Slack", "slack:", _slack_transport,
    lambda c: f"{c.get('bot_token', '')} {c.get('app_token', '')}",
    ("bot_token", "app_token"), ("Slack user ID", "Slack user ID"),
    secret_fields=("bot_token", "app_token"), trust_turn_ids=True)

WHATSAPP = OwnerChannel(
    "whatsapp", "WhatsApp", "whatsapp:", _wa_transport,
    lambda c: {"phone_number_id": c.get("phone_number_id", ""),
               "access_token": c.get("access_token", ""), "app_secret": c.get("app_secret", "")},
    ("phone_number_id", "access_token", "app_secret"), ("số điện thoại", "phone number"),
    plain={"phone_number_id": _digits}, secret_fields=("access_token", "app_secret"))

DISCORD = OwnerChannel(
    "discord", "Discord", "discord:", _discord_transport,
    lambda c: str(c.get("bot_token", "") or ""),
    ("bot_token",), ("Discord user ID", "Discord user ID"),
    secret_fields=("bot_token",), trust_turn_ids=True)

LARK = OwnerChannel(
    "lark", "Lark", "lark:", _lark_transport,
    lambda c: {"app_id": str(c.get("app_id", "") or "").strip(),
               "app_secret": c.get("app_secret", ""), "domain": lark_domain(c.get("domain"))},
    ("app_id", "app_secret"), ("Lark open_id", "Lark open_id"),
    plain={"app_id": lambda v: str(v or "").strip(), "domain": lark_domain},
    secret_fields=("app_secret",), trust_turn_ids=True)

ALL = (SLACK, WHATSAPP, DISCORD, LARK)


def get(key: str):
    """The channel whose settings section is `key`, or None."""
    for ch in ALL:
        if ch.key == key:
            return ch
    return None


def bot_of(ch, uid: str = "") -> object:
    """That channel's bot for one account (the current one when `uid` is empty)."""
    return ch.bot_of(uid)


def by_prefix(owner_chat: str):
    """(channel, raw id) for an owner_chat like "slack:D123", or (None, "")."""
    s = str(owner_chat or "")
    for ch in ALL:
        if s.startswith(ch.prefix):
            return ch, s[len(ch.prefix):]
    return None, ""


def whatsapp_app_secrets(account_secrets=()) -> list:
    """Every app secret a valid WhatsApp webhook may be signed with: the control number's plus
    each customer-bot account's."""
    import config
    out = []
    # Every account's control number: Meta calls one public webhook with no session, so the request carries no
    # account, and a member's number would fail the check if only the primary account's secret counted.
    for uid in (config.list_user_ids() or [""]):
        with config.as_user(uid):
            s = str(WHATSAPP.cfg().get("app_secret") or "").strip()
        if s and s not in out:
            out.append(s)
    out.extend(x for x in account_secrets if x)
    return out
