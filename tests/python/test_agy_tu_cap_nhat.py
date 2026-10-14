"""Antigravity CLI (`agy`) phải tự lên bản mới, để model mới tự hiện trong Javis (0.137.0).

    python tests/run.py agy_tu_cap_nhat

Báo lỗi 14/10/2026: Gemini 4 Argon ra rồi mà Javis không có. Danh sách model của Antigravity lấy
thẳng từ `agy models`, nên thứ kẹt là chính binary: bản Docker cài `agy` một lần vào ổ dữ liệu lúc
khởi động rồi không bao giờ cập nhật, bản cài trên máy thì chỉ tự cập nhật khi người ta mở `agy` gõ
tay. Cùng bệnh Claude Code đã chữa ở `claude_update`, khác một điều: đường này chạy cả trong Docker
vì `agy` nằm ở thư mục của chính user chạy Javis.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import subprocess
import sys
import tempfile
import time

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-agy-upd-")

import agy_update as au  # noqa: E402
import antigravity_cli  # noqa: E402
import claude_update  # noqa: E402
import deploy_info  # noqa: E402
import main  # noqa: E402

_fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(them) + "]") if them and not cond else ""))
    if not cond:
        _fails.append(name)


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def _gia_chay(ban_truoc, ban_sau, rc_update=0, ra_update="✓ Updated"):
    """Giả `agy --version` / `agy update`: sau lệnh update thì --version ra bản mới."""
    trang = {"da_update": False, "lenh": []}

    def _chay(cmd, timeout):
        trang["lenh"].append(cmd[1:])
        if cmd[1:] == ["update"]:
            trang["da_update"] = rc_update == 0
            return _R(rc_update, ra_update)
        return _R(0, f"{ban_sau if trang['da_update'] else ban_truoc}\n")
    return _chay, trang


_goc = (claude_update._chay, antigravity_cli.find_antigravity_cli, deploy_info.deploy_mode)
try:
    antigravity_cli.find_antigravity_cli = lambda: "/data/home/.local/bin/agy"

    # 1. Lên bản mới
    claude_update._chay, _t = _gia_chay("1.3.5", "1.4.0")
    _kq = au.cap_nhat("tu_dong")
    check("cập nhật thành công thì ok và báo phiên bản đổi",
          _kq.get("ok") and _kq.get("doi") and (_kq["truoc"], _kq["sau"]) == ("1.3.5", "1.4.0"), _kq)
    check("gọi đúng lệnh `agy update`", ["update"] in _t["lenh"], _t["lenh"])
    check("trạng thái được ghi để trang Models đọc", au.doc_trang_thai().get("sau") == "1.4.0")
    check("vừa thử xong thì chưa tới hạn chạy lại", not au.den_han())
    check("qua 24 giờ thì tới hạn", au.den_han(time.time() + au.CHU_KY + 1))

    # 2. Docker vẫn cập nhật (khác Claude Code): agy nằm ở thư mục của user chạy Javis
    deploy_info.deploy_mode = lambda: "docker"
    claude_update._chay, _t = _gia_chay("1.3.5", "1.4.0")
    _kq = au.cap_nhat("tu_dong")
    check("CANARY: bản Docker cũng chạy `agy update` (cài một lần trên ổ dữ liệu là kẹt mãi)",
          _kq.get("ok") and ["update"] in _t["lenh"], _kq)

    # 3. Đã mới nhất
    claude_update._chay, _ = _gia_chay("1.3.5", "1.3.5", ra_update="✓ You are already on the latest version.")
    _kq = au.cap_nhat()
    check("đã mới nhất thì ok nhưng không báo đổi", _kq.get("ok") and not _kq.get("doi"), _kq)

    # 4. Hỏng: không ném lỗi, giữ dòng cuối CLI tự nói
    claude_update._chay, _ = _gia_chay("1.3.5", "1.3.5", rc_update=1,
                                       ra_update="Checking...\npermission denied: /data/home/.local/bin/agy")
    _kq = au.cap_nhat()
    check("hỏng thì ok=False kèm dòng lỗi cuối của CLI",
          not _kq.get("ok") and "permission denied" in (_kq.get("error") or ""), _kq)

    # 5. Không có agy
    antigravity_cli.find_antigravity_cli = lambda: None
    _kq = au.cap_nhat()
    check("máy chưa cài agy thì báo bằng lời, không chạy gì", not _kq.get("ok") and _kq.get("error"), _kq)

    # 6. Bản đổi thì bỏ danh sách model đang nhớ, để model mới hiện ngay chứ không chờ 10 phút
    antigravity_cli.find_antigravity_cli = lambda: "/data/home/.local/bin/agy"
    claude_update._chay, _ = _gia_chay("1.3.5", "1.4.0")
    main._PROV_MODELS_CACHE["antigravity-cli"] = {"ids": ["gemini-3.8-flash-high"], "ts": time.time()}
    asyncio.run(main._cap_nhat_agy("tu_dong"))
    check("lên bản mới thì xoá cache danh sách model của Antigravity",
          "antigravity-cli" not in main._PROV_MODELS_CACHE, main._PROV_MODELS_CACHE.get("antigravity-cli"))
    claude_update._chay, _ = _gia_chay("1.4.0", "1.4.0")
    main._PROV_MODELS_CACHE["antigravity-cli"] = {"ids": ["x"], "ts": time.time()}
    asyncio.run(main._cap_nhat_agy("tu_dong"))
    check("không đổi bản thì giữ cache", "antigravity-cli" in main._PROV_MODELS_CACHE)

    # 7. Tắt được, và có vòng chạy lúc khởi động
    os.environ["JAVIS_AGY_AUTO_UPDATE"] = "0"
    check("JAVIS_AGY_AUTO_UPDATE=0 thì tắt", not au.bat())
    os.environ.pop("JAVIS_AGY_AUTO_UPDATE", None)
    check("mặc định bật", au.bat())
    src = (SERVER / "main.py").read_text(encoding="utf-8")
    check("server có vòng tự cập nhật agy lúc khởi động, không chặn Docker",
          "async def _tu_cap_nhat_agy" in src
          and "docker" not in src[src.index("async def _tu_cap_nhat_agy"):src.index("async def _tu_cap_nhat_agy") + 900])
    check("trang Models đọc được lần cập nhật gần nhất", '"cap_nhat"] = agy_update.doc_trang_thai()' in src)
finally:
    claude_update._chay, antigravity_cli.find_antigravity_cli, deploy_info.deploy_mode = _goc

print()
if _fails:
    print(f"FAIL {len(_fails)} mục: " + ", ".join(_fails))
    sys.exit(1)
print("OK - test_agy_tu_cap_nhat: tất cả pass")
