"""Giữ Antigravity CLI (`agy`) trên máy luôn ở bản mới, để model mới tự hiện trong Javis (0.137.0).

Danh sách model của Antigravity đọc thẳng từ `agy models` (`antigravity_cli.list_models`), nên
Javis chỉ thấy model mới khi `agy` đã lên bản biết model đó. Báo lỗi 14/10/2026: Gemini 4 Argon
ra rồi mà trình chọn model của Javis không có.

Cùng bệnh với Claude Code (`claude_update`): `agy` chỉ tự cập nhật khi người ta mở nó gõ tay, còn
Javis gọi nó chạy ngầm. Bản Docker còn kẹt nặng hơn: container cài `agy` MỘT LẦN vào thư mục người
dùng trên ổ dữ liệu (`docker/entrypoint.sh`), sống qua mọi lần cập nhật image, và từ đó không ai
cập nhật nó nữa.

Nên Javis tự chạy `agy update` mỗi ngày một lần. Khác Claude Code, đường này chạy được cả trong
Docker: `agy` nằm ở `~/.local/bin` của chính user chạy Javis, nên ghi đè được. Tắt bằng
JAVIS_AGY_AUTO_UPDATE=0.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import antigravity_cli
import claude_update
import config as cfgmod
import localefmt

CHU_KY = 24 * 3600
_TRANG_THAI = "agy-update.json"


def bat() -> bool:
    """Tự cập nhật hằng ngày có bật không."""
    return (os.environ.get("JAVIS_AGY_AUTO_UPDATE") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def _file_trang_thai() -> Path:
    return Path(cfgmod.STATE_DIR) / _TRANG_THAI


def doc_trang_thai() -> dict:
    try:
        return json.loads(_file_trang_thai().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _ghi_trang_thai(d: dict) -> None:
    try:
        p = _file_trang_thai()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    except Exception:
        pass


def den_han(now: float | None = None) -> bool:
    """Đã quá một chu kỳ kể từ lần THỬ gần nhất chưa (thử hỏng cũng tính, khỏi thử dồn dập)."""
    now = time.time() if now is None else now
    return (now - float(doc_trang_thai().get("ts") or 0)) >= CHU_KY


def cap_nhat(ly_do: str = "tay") -> dict:
    """Chạy `agy update`. Không bao giờ ném lỗi: mọi hỏng hóc trả về trong `error`.

    `doi` = phiên bản thật sự đổi, tức caller nên đọc lại danh sách model."""
    now = time.time()
    cli = antigravity_cli.find_antigravity_cli()
    if not cli:
        return {"ok": False, "error": localefmt.chu("Chưa thấy Antigravity CLI trên máy.",
                                                    "Antigravity CLI was not found on this machine.")}
    truoc = claude_update.phien_ban(cli)
    try:
        r = claude_update._chay([cli, "update"], 300)
        rc, ra = r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        rc, ra = -1, localefmt.chu("quá 5 phút chưa xong", "not done after 5 minutes")
    except Exception as e:
        rc, ra = -1, f"{type(e).__name__}: {e}"
    sau = claude_update.phien_ban(cli) or truoc
    kq = {"ok": rc == 0, "truoc": truoc, "sau": sau, "doi": bool(sau and sau != truoc),
          "ts": now, "ly_do": ly_do}
    if rc != 0:
        dong = [x for x in ra.splitlines() if x.strip()]
        kq["error"] = (dong[-1] if dong else localefmt.chu("không rõ lỗi", "unknown error"))[:300]
    _ghi_trang_thai(kq)
    return kq
