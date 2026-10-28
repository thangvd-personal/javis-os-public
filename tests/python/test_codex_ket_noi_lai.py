"""Codex bị ngắt WebSocket: không hiện lỗi đỏ, và lượt sau đi thẳng HTTPS.

    python tests/run.py codex_ket_noi_lai      (KHÔNG mạng)

Bug 2026-09-28 trên VPS: mỗi lượt chat bằng ChatGPT hiện liền bốn năm bong bóng
"Codex: Reconnecting... 2/5 (stream disconnected before completion: websocket closed by server
before response.completed)". Hai tầng hỏng:

  1. Đó là Codex TỰ thử lại (sau 5 lần nó lùi về HTTPS), lượt chưa hỏng, nhưng `codex exec
     --json` in nó thành sự kiện `error` và Javis đẩy thẳng lên thành bong bóng lỗi.
  2. `codex exec` là tiến trình mới mỗi lượt nên không nhớ đã lùi: lượt nào cũng mất năm lần
     thử WebSocket trước. Không có cờ tắt (features.responses_websockets đã bị gỡ, provider
     `openai` cấm ghi đè), nên Javis khai provider riêng cùng địa chỉ ChatGPT với
     supports_websockets=false.

File này dựng một Codex CLI giả in đúng khuôn JSONL đó và soi cả hai tầng.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

TMP = Path(tempfile.mkdtemp(prefix="javis-codexws-"))
os.environ["JAVIS_STATE_DIR"] = str(TMP / "state")
os.environ["CODEX_HOME"] = str(TMP / "codexhome")
(TMP / "state").mkdir()
(TMP / "codexhome").mkdir()
os.environ.pop("JAVIS_CODEX_TRANSPORT", None)

import claude_cli
from claude_cli import CodexCLI

_fails = []


def check(ten, dieu_kien, them=""):
    print(("ok   " if dieu_kien else "FAIL ") + ten
          + (("  [" + str(them) + "]") if them and not dieu_kien else ""))
    if not dieu_kien:
        _fails.append(ten)


MARK = Path(os.environ["JAVIS_STATE_DIR"]) / "codex_ws_hong.json"
RECON = ("Reconnecting... 2/5 (stream disconnected before completion: websocket closed by "
         "server before response.completed)")


def co_http(args):
    return f'model_provider="{claude_cli.CODEX_HTTP_PROVIDER}"' in args


def args_moi():
    c = CodexCLI(cwd=str(TMP))
    c.cli_path = "codex"
    return c._build_args()


# 1) Nhận diện tin thử lại.
check("nhận ra Reconnecting n/5", claude_cli.la_thong_bao_ket_noi_lai(RECON))
check("nhận ra lùi về HTTPS",
      claude_cli.la_thong_bao_ket_noi_lai("Falling back from WebSockets to HTTPS transport. x"))
check("lỗi thật không bị nuốt",
      not claude_cli.la_thong_bao_ket_noi_lai("unexpected status 401 Unauthorized")
      and not claude_cli.la_thong_bao_ket_noi_lai(""))
check("câu trạng thái tiếng Việt có số lần",
      claude_cli.cau_ket_noi_lai(RECON) == "Kết nối tới ChatGPT bị ngắt, Codex đang tự kết nối lại (lần 2/5)…",
      claude_cli.cau_ket_noi_lai(RECON))

# 2) Máy khoẻ: để Codex tự dùng WebSocket như mặc định.
check("mặc định: không ép HTTPS", not co_http(args_moi()))

# 3) Một lượt gặp WebSocket hỏng: không có sự kiện lỗi, vẫn ra câu trả lời, nhớ dấu hỏng.
fake = [
    {"type": "thread.started", "thread_id": "t-ws"},
    {"type": "error", "message": RECON},
    {"type": "error", "message": RECON.replace("2/5", "5/5")},
    {"type": "item.completed", "item": {"id": "i0", "type": "error",
                                        "message": "Falling back from WebSockets to HTTPS transport. x"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "Dạ xong."}},
    {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
]
script = "import json\n" + "\n".join(
    f"print(json.dumps({json.dumps(ev, ensure_ascii=False)}), flush=True)" for ev in fake)
cli = CodexCLI(cwd=str(TMP))
cli.cli_path = sys.executable
cli._build_args = lambda: [sys.executable, "-c", script]


async def _gom():
    return [e async for e in cli.query("chào")]


evs = asyncio.run(_gom())
loai = [e.get("type") for e in evs]
check("không có sự kiện error nào", "error" not in loai, loai)
check("ba tin thử lại thành sự kiện retry", loai.count("retry") == 3, loai)
check("không biến tin thử lại thành bước tool", "item" not in loai and "tool_call" not in loai, loai)
check("vẫn ra câu trả lời",
      next((e for e in evs if e.get("type") == "final"), {}).get("content") == "Dạ xong.")
check("ghi dấu WebSocket hỏng", MARK.is_file())

# 4) Lượt sau đi thẳng HTTPS với provider riêng đúng khuôn đã thử thật.
a = args_moi()
check("sau khi hỏng: ép HTTPS", co_http(a), a)
check("provider riêng tắt WebSocket và dùng đăng nhập ChatGPT",
      f"model_providers.{claude_cli.CODEX_HTTP_PROVIDER}.supports_websockets=false" in a
      and f"model_providers.{claude_cli.CODEX_HTTP_PROVIDER}.requires_openai_auth=true" in a
      and f'model_providers.{claude_cli.CODEX_HTTP_PROVIDER}.base_url="https://chatgpt.com/backend-api/codex"' in a)
check("cờ -c đứng TRƯỚC subcommand exec", a.index("exec") > a.index(f'model_provider="{claude_cli.CODEX_HTTP_PROVIDER}"'))
check("không dùng tên provider dựng sẵn 'openai' (Codex cấm ghi đè)",
      not any(x.startswith("model_providers.openai.") for x in a))

# 5) Dấu hỏng hết hạn → thử lại WebSocket.
MARK.write_text(json.dumps({"ts": time.time() - 4 * 86400}), encoding="utf-8")
check("dấu cũ quá hạn: về WebSocket", not co_http(args_moi()))

# 6) Cờ môi trường.
os.environ["JAVIS_CODEX_TRANSPORT"] = "http"
check("JAVIS_CODEX_TRANSPORT=http: luôn HTTPS", co_http(args_moi()))
MARK.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
os.environ["JAVIS_CODEX_TRANSPORT"] = "ws"
check("JAVIS_CODEX_TRANSPORT=ws: không ép dù vừa hỏng", not co_http(args_moi()))
os.environ.pop("JAVIS_CODEX_TRANSPORT")

# 7) Người dùng tự trỏ Codex sang provider khác → Javis không giành.
(Path(os.environ["CODEX_HOME"]) / "config.toml").write_text('model_provider = "ollama"\n', encoding="utf-8")
check("config.toml có model_provider riêng: không ép HTTPS", not co_http(args_moi()))
(Path(os.environ["CODEX_HOME"]) / "config.toml").write_text('model_provider = "openai"\n', encoding="utf-8")
check("config.toml để openai: vẫn ép HTTPS khi vừa hỏng", co_http(args_moi()))

# 8) Lỗi thật vẫn là lỗi.
fake2 = [{"type": "thread.started", "thread_id": "t2"},
         {"type": "turn.failed", "error": {"message": "unexpected status 401"}}]
cli._build_args = lambda: [sys.executable, "-c", "import json\n" + "\n".join(
    f"print(json.dumps({json.dumps(ev)}), flush=True)" for ev in fake2)]
evs2 = asyncio.run(_gom())
check("turn.failed vẫn báo lỗi", any(e.get("type") == "error" and "401" in e.get("content", "") for e in evs2))

print()
if _fails:
    print(f"{len(_fails)} FAIL")
    sys.exit(1)
print("TẤT CẢ OK")
