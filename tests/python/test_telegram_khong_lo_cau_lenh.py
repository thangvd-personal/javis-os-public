"""Telegram không được in nguyên câu lệnh shell lên dòng trạng thái / dòng vết.

    python tests/run.py telegram_khong_lo_cau_lenh      (KHÔNG mạng)

Bug 2026-09-28 (ảnh chụp chủ repo gửi): bot KJavis chạy engine ChatGPT (Codex) để ingest một
trang web, và tin trạng thái chốt thành dòng vết dài chín dòng:

    ⚙ /bin/sh -lc "sed -n '1,260p' skills/ingest-source/SKILL.md" · /bin/sh -lc "curl -L
    --fail --max-time 30 -A 'Mozilla/5.0' 'https://duytung.vn/t · /bin/sh -lc "perl -0pe ...

Gốc: CodexCLI lấy `command` làm TÊN công cụ, Telegram ghép các tên thành dòng vết. Sửa hai
tầng: Codex đặt tên theo loại việc (`command_execution`), và lớp bot rút mọi tên về nhãn ngắn
(`bot_gateway.nhan_ngan`) nên dù engine nào lọt câu lệnh lên thì dòng vết vẫn sạch.
"""
import asyncio
import json
import sys
import tempfile

from _paths import ROOT, SERVER  # noqa: F401
from bot_gateway import dong_vet, nhan_ngan, ten_tool
from claude_cli import CodexCLI
from telegram_bot import TelegramBot

_fails = []


def check(ten, dieu_kien, them=""):
    print(("ok   " if dieu_kien else "FAIL ") + ten
          + (("  [" + repr(them) + "]") if them and not dieu_kien else ""))
    if not dieu_kien:
        _fails.append(ten)


# Đúng các chuỗi trong ảnh (engine Telegram-Codex gửi "⚙ Đang gọi: <tên>").
ANH = [
    "⚙ Đang gọi: /bin/sh -lc \"sed -n '1,260p' skills/ingest-source/SKILL.md\"",
    "⚙ Đang gọi: /bin/sh -lc \"curl -L --fail --max-time 30 -A 'Mozilla/5.0' 'https://duytung.vn/t'\"",
    "⚙ Đang gọi: /bin/sh -lc \"perl -0pe 's/<script\\b[^>]*>.*?<\\/script>//gsi'\"",
    "⚙ Đang gọi: /bin/sh -lc \"rg --files sources | rg -i 'cham-soc|windows|may-tinh' || true\"",
    "⚙ Đang gọi: /bin/sh -lc \"tail -40 wiki/log.md; sed -n '1,50p' wiki/_open-questions.md\"",
    "⚙ Đang gọi: /bin/sh -lc \"git diff --check; rg -n 'skill-cham-soc-may-tinh'\"",
]

# ---- 1. Nhãn ngắn ----
for s in ANH:
    check(f"câu lệnh thành 'Chạy lệnh': {s[14:40]}", ten_tool(s) == "Chạy lệnh", ten_tool(s))
check("command_execution → Chạy lệnh", nhan_ngan("command_execution") == "Chạy lệnh")
check("Bash → Chạy lệnh", nhan_ngan("Bash") == "Chạy lệnh")
check("web_search_call → Tìm trên web", nhan_ngan("web_search_call") == "Tìm trên web")
check("tên MCP bỏ tiền tố máy chủ", nhan_ngan("mcp__pancake-pos__pos_order") == "pos_order")
check("tên thường giữ nguyên", nhan_ngan("pos_statistics") == "pos_statistics"
      and nhan_ngan("Read") == "Read")
check("tên dài bị cắt", len(nhan_ngan("x" * 90)) == 40)
check("chuỗi không nói về công cụ vẫn rỗng", ten_tool("✍ Đang soạn câu trả lời…") == "")

# ---- 2. Dòng vết của đúng lượt trong ảnh ----
tools = []
for s in ANH:
    t = ten_tool(s)
    if t and t not in tools:
        tools.append(t)
vet = dong_vet(tools, 159)
check("dòng vết gọn: ⚙ Chạy lệnh · 2m39s", vet == "⚙ Chạy lệnh · 2m39s", vet)


# ---- 3. Chạy trọn một lượt Telegram với client giả ----
class _Resp:
    def __init__(self, d):
        self._d, self.status_code, self.content, self.text = d, 200, b"{}", str(d)

    def json(self):
        return self._d


class _Client:
    def __init__(self):
        self.calls, self._mid = [], 100

    async def post(self, url, json=None, data=None, files=None):
        m = url.rsplit("/", 1)[-1]
        self.calls.append((m, json or data or {}))
        if m == "sendMessage":
            self._mid += 1
            return _Resp({"ok": True, "result": {"message_id": self._mid}})
        return _Resp({"ok": True, "result": {}})


async def _answer(text, meta, progress):
    for s in ANH:
        await progress(s)
    await progress("⚙ Đang gọi: pos_statistics")
    return {"text": "Em đã ingest trang này vào Second Brain."}


bot = TelegramBot.__new__(TelegramBot)
bot.token, bot.chat_ids, bot.giau_trang_thai = "t", ["7"], False
bot.answer_fn = _answer
c = _Client()
asyncio.run(bot._handle_turn(c, "7", "ingest giúp em trang này"))
sua = [p.get("text") or "" for m, p in c.calls if m == "editMessageText"]
tat_ca = " ".join(sua) + " ".join((p.get("text") or "") for m, p in c.calls if m == "sendMessage")
check("không tin nào chứa câu lệnh shell", "/bin/sh" not in tat_ca and "sed -n" not in tat_ca, sua)
check("dòng vết cuối liệt kê nhãn ngắn",
      bool(sua) and sua[-1].startswith("⚙ Chạy lệnh · pos_statistics · "), sua[-1] if sua else None)
check("trạng thái tạm lúc chạy hiện nhãn", any(s.startswith("⏳ ⚙ Chạy lệnh") for s in sua), sua)


# ---- 4. Gốc: CodexCLI đặt tên theo loại việc, câu lệnh vẫn nằm trong item ----
fake = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "item.completed", "item": {"type": "command_execution",
                                        "command": "/bin/sh -lc \"sed -n '1,260p' x.md\"",
                                        "status": "completed"}},
    {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "javis",
                                        "tool": "pos_statistics", "arguments": {}}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "xong"}},
    {"type": "turn.completed", "usage": {}},
]
script = "import json\n" + "\n".join(
    f"print(json.dumps({json.dumps(ev, ensure_ascii=False)}), flush=True)" for ev in fake)
cli = CodexCLI(cwd=tempfile.gettempdir())
cli.cli_path = sys.executable
cli._build_args = lambda: [sys.executable, "-c", script]


async def _gom():
    return [e async for e in cli.query("x")]


tc = [e for e in asyncio.run(_gom()) if e.get("type") == "tool_call"]
check("Codex: lệnh shell mang tên command_execution",
      len(tc) == 2 and tc[0].get("name") == "command_execution", [e.get("name") for e in tc])
check("Codex: câu lệnh vẫn đi kèm trong item",
      bool(tc) and "sed -n" in (tc[0].get("item") or {}).get("command", ""))
check("Codex: MCP lấy tên tool chứ không phải tên máy chủ",
      len(tc) == 2 and tc[1].get("name") == "pos_statistics", [e.get("name") for e in tc])

print()
if _fails:
    print(f"{len(_fails)} FAIL")
    sys.exit(1)
print("TẤT CẢ OK")
