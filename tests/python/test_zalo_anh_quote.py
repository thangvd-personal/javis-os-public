"""Zalo: a message that QUOTES a photo shows the bot that photo (0.141.0).

    python tests/run.py zalo_anh_quote      (no network)

Field report 2026-10-14: in a group someone quoted a product photo and tagged the bot ("@bot xem ảnh này"); the
bot only got the text and said it could not see the photo. The owner's workaround was to send the photo again and
tag the bot within 3 minutes. The quote carries the photo's message id (`replyTo.msgId`), and Javis stored that
photo, link included, when it read it.

Pinned:
  1. A group message quoting a stored photo reaches the bot with that photo's link, marked as a quote.
  2. Same in a private chat.
  3. A quote of a TEXT message, of an id from ANOTHER chat, or of an unknown id brings no photo.
  4. Fallback: a link inside the quote payload (JSON) is used; a quoted plain-text line holding a URL is not.
  5. The bot's turn says the photo is the quoted one, both when it can see it and when the download fails.
  6. "Reply for me" in the inbox resolves the quote too.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-anhquote-")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chatbot_runtime as cr  # noqa: E402
import conversations  # noqa: E402
import image_vision  # noqa: E402
import zalo_personal_channel as zc  # noqa: E402
from channels.zalo_personal import Transport  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        _fails.append(name)


ANH = "https://f21-zpc.zdn.vn/jpg/san-pham.jpg"


def luu(chat, mid, loai="image", chat_type="group", **md):
    ev = {"channel": "zalo_personal", "account_id": "conn-1", "account_name": "Zalo", "external_chat_id": chat,
          "chat_type": chat_type, "chat_title": "Nhóm" if chat_type == "group" else "",
          "sender_type": "customer", "sender_id": "u5", "sender_name": "Tuấn", "message_type": loai,
          "text": ANH if loai == "image" else "giá bao nhiêu", "external_message_id": mid,
          "created_at": time.time() - 3600, "metadata": md}
    check(f"lưu tin {mid}", conversations.ghi_su_kien(ev).get("ok"))


luu("g1", "777", image_url=ANH)
luu("g1", "778", loai="text")
luu("g2", "900", image_url="https://f21-zpc.zdn.vn/jpg/nhom-khac.jpg")
luu("u9", "555", chat_type="private", image_url=ANH)

zc._ID_MINH["conn-1"] = {"uid": "u-bot", "ten": "Bot"}
goi = []


async def _answer(text, meta, _p):
    goi.append(meta)
    return {"im_lang": True}


def _pre(text, meta):
    return None


def tin(chat="g1", chat_type="group", text="@Bot xem ảnh này", **md):
    return {"chat_type": chat_type, "message_type": "text", "text": text, "external_chat_id": chat,
            "external_message_id": "m-" + str(time.time()), "sender_id": "u9" if chat_type == "private" else "u7",
            "sender_name": "Lan", "created_at": time.time(), "metadata": md}


def chay(ev):
    goi.clear()
    asyncio.run(Transport("conn-1", [], _answer, precheck_fn=_pre).xu_ly(ev))
    return goi[0] if goi else {}


# ---- 1. Group quote of a stored photo ----
m = chay(tin(replyTo={"senderId": "u5", "senderName": "Tuấn", "text": "", "msgId": "777"}))
check("nhóm: quote ảnh thì bot nhận link ảnh", m.get("image_url") == ANH and m.get("co_anh") and m.get("anh_quote"), m)

# ---- 2. Private chat ----
m = chay(tin(chat="u9", chat_type="private", text="cái này còn không", replyTo={"msgId": "555"}))
check("chat riêng: quote ảnh thì bot nhận link ảnh", m.get("image_url") == ANH and m.get("anh_quote"), m)

# ---- 3. No photo ----
m = chay(tin(replyTo={"msgId": "778", "text": "giá bao nhiêu"}))
check("quote tin chữ: không có ảnh", not m.get("co_anh") and not m.get("image_url"), m)
m = chay(tin(replyTo={"msgId": "900"}))
check("id của nhóm KHÁC: không lấy ảnh nhóm đó", not m.get("co_anh"), m)
m = chay(tin(replyTo={"msgId": "123456"}))
check("id không có trong kho: không có ảnh", not m.get("co_anh"), m)
m = chay(tin())
check("tin không quote: không có ảnh", not m.get("co_anh"), m)

# ---- 4. Fallback on the quote payload ----
payload = json.dumps({"title": "", "href": "https://f20-zpc.zdn.vn/jpg/cu.jpg", "thumb": "https://x/t.jpg"})
check("link nằm trong payload của quote",
      zc.anh_duoc_quote("conn-1", "g1", {"replyTo": {"msgId": "1", "text": payload}}) == "https://f20-zpc.zdn.vn/jpg/cu.jpg")
check("dòng chữ thường có URL không bị coi là ảnh",
      zc.anh_duoc_quote("conn-1", "g1", {"replyTo": {"text": "xem https://shop.vn/sp nhé"}}) == "")
check("payload link http (không https) bị bỏ",
      zc.anh_duoc_quote("conn-1", "g1", {"replyTo": {"text": json.dumps({"href": "http://x/a.jpg"})}}) == "")
check("replyTo không phải dict", zc.anh_duoc_quote("conn-1", "g1", {"replyTo": "777"}) == "")

# ---- 5. The bot's turn says the photo is the quoted one ----
BRAIN = Path(tempfile.mkdtemp(prefix="javis-brain-anhquote-"))
cr._deps["brain_root"] = lambda b: BRAIN
goc_fetch = image_vision.fetch_image


async def _ok(url, folder, msg):
    p = Path(folder)
    p.mkdir(parents=True, exist_ok=True)
    f = p / "q.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(f), ""


async def _hong(url, folder, msg):
    return None, "không phải ảnh"


META = {"chat_id": "g1", "message_id": "m1", "co_anh": True, "image_url": ANH, "anh_quote": True}
try:
    image_vision.fetch_image = _ok
    text, paths = asyncio.run(cr.anh_cho_bot("@Bot xem ảnh này", dict(META), {"brain": "b"}))
    check("xem được: lượt có ảnh và nói đó là ảnh được quote",
          len(paths) == 1 and text.startswith(cr._QUOTE_ANH) and text.endswith("@Bot xem ảnh này"), (text, paths))
    image_vision.fetch_image = _hong
    text, paths = asyncio.run(cr.anh_cho_bot("@Bot xem ảnh này", dict(META), {"brain": "b"}))
    check("tải hỏng: nói thật là không xem được ảnh được quote",
          paths == [] and text.startswith(cr._QUOTE_ANH_MU), (text, paths))
finally:
    image_vision.fetch_image = goc_fetch
check("tin ảnh thường giữ nhãn cũ", cr.gan_nhan_anh("x", {"co_anh": True}).startswith(cr._KEM_ANH))

# ---- 6. Inbox "reply for me" ----
conv = {"channel_account_id": "zalo_personal:conn-1", "external_chat_id": "g1", "chat_type": "group",
        "title": "Nhóm", "channel": "zalo_personal"}
last = {"sender_id": "u7", "sender_name": "Lan", "external_message_id": "m9", "message_type": "text",
        "metadata": {"replyTo": {"msgId": "777"}}, "created_at": time.time()}
m = cr.manual_meta(conv, last)
check("Hộp thư nhờ bot: tin quote ảnh cũng có ảnh", m.get("image_url") == ANH and m.get("anh_quote"), m)
m = cr.manual_meta(dict(conv, channel="telegram", channel_account_id="telegram:conn-1"), last)
check("kênh khác Zalo cá nhân: không tra quote", not m.get("co_anh"), m)

print()
if _fails:
    print(f"FAIL {len(_fails)}: {_fails}")
    sys.exit(1)
print("OK")
