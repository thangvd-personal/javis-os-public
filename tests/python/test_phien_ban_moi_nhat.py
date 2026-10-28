"""Bản mới nhất lấy số LỚN HƠN giữa VERSION và CHANGELOG.md trên GitHub (main._latest_remote_version).

    python tests/run.py phien_ban_moi_nhat

Vì sao file này tồn tại (0.64.69): raw.githubusercontent.com đệm MỖI FILE riêng 5 phút. Ngay
sau khi phát hành 0.64.67, chủ dự án mở trang Cập nhật trên VPS: khung trên (đọc VERSION, còn
số cũ) báo "Đang dùng bản mới nhất (v0.64.66)" và KHÔNG có nút cập nhật, trong khi danh sách
bên dưới (đọc CHANGELOG.md, đã mới) báo "Có bản mới: v0.64.67". Không chạm mạng: httpx giả.
"""
import asyncio
import os
import tempfile

from _paths import ROOT, SERVER  # noqa: F401

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-banmoi-"))

import httpx
import main

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


GH = {"VERSION": "0.64.66\n", "CHANGELOG": None, "loi_version": False}
DEM = {"changelog": 0}


def _changelog(*ver):
    return "# Nhật ký\n\n" + "".join(f"## [{v}] - 2026-09-27\n### Sửa lỗi\n- Sửa {v}.\n\n" for v in ver)


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        if url.endswith("/VERSION"):
            if GH["loi_version"]:
                raise httpx.ConnectError("mất mạng")
            return _Resp(200, GH["VERSION"])
        if url.endswith("/CHANGELOG.md"):
            DEM["changelog"] += 1
            return _Resp(200, GH["CHANGELOG"]) if GH["CHANGELOG"] else _Resp(404, "")
        return _Resp(404, "")


_goc_client, _goc_ver = httpx.AsyncClient, main._read_version


def dat_lai():
    main._CL_REMOTE.update({"at": 0.0, "releases": [], "err": None})
    DEM["changelog"] = 0


try:
    httpx.AsyncClient = _FakeClient
    main._read_version = lambda: "0.64.66"

    # ---- 1. Đúng cảnh 27/09: VERSION còn cũ, CHANGELOG đã mới ----
    dat_lai()
    GH.update({"VERSION": "0.64.66\n", "CHANGELOG": _changelog("0.64.67", "0.64.66"), "loi_version": False})
    d = asyncio.run(main.version_info())
    check("VERSION cũ + CHANGELOG mới: có bản mới 0.64.67", d["update_available"] and d["latest"] == "0.64.67")
    check("không báo lỗi khi đã có số từ CHANGELOG", d["error"] is None)

    # ---- 2. Ngược lại: VERSION mới, CHANGELOG còn trong cache cũ ----
    dat_lai()
    GH.update({"VERSION": "0.64.67\n", "CHANGELOG": _changelog("0.64.66")})
    d = asyncio.run(main.version_info())
    check("VERSION mới + CHANGELOG cũ: vẫn có bản mới 0.64.67", d["update_available"] and d["latest"] == "0.64.67")

    # ---- 3. Hai nguồn cùng bằng bản đang cài: không có gì mới ----
    dat_lai()
    GH.update({"VERSION": "0.64.66\n", "CHANGELOG": _changelog("0.64.66", "0.64.65")})
    d = asyncio.run(main.version_info())
    check("hai nguồn bằng bản đang cài: không báo bản mới", not d["update_available"] and d["latest"] == "0.64.66")

    # ---- 4. VERSION hỏng mạng, CHANGELOG có: vẫn biết bản mới ----
    dat_lai()
    GH.update({"loi_version": True, "CHANGELOG": _changelog("0.64.67")})
    d = asyncio.run(main.version_info())
    check("VERSION lỗi mạng: lấy từ CHANGELOG, bỏ lỗi", d["latest"] == "0.64.67" and d["error"] is None)
    GH["loi_version"] = False

    # ---- 5. Cả hai đều hỏng: trả lỗi, không báo bản mới ----
    dat_lai()
    GH.update({"loi_version": True, "CHANGELOG": None})
    d = asyncio.run(main.version_info())
    check("cả hai hỏng: không có bản mới, có lỗi", not d["update_available"] and d["latest"] is None and d["error"])
    GH["loi_version"] = False

    # ---- 6. Cache nhật ký: tải lại khi cũ hơn 1 phút, không tải lại liên tục ----
    dat_lai()
    GH.update({"VERSION": "0.64.66\n", "CHANGELOG": _changelog("0.64.66")})
    asyncio.run(main.version_info())
    n1 = DEM["changelog"]
    asyncio.run(main.version_info())
    check("bấm Kiểm tra lại liền tay: không tải lại CHANGELOG (cache còn mới)", DEM["changelog"] == n1 == 1)
    main._CL_REMOTE["at"] -= 120                      # cache đã cũ hơn một phút
    GH["CHANGELOG"] = _changelog("0.64.67", "0.64.66")
    d = asyncio.run(main.version_info())
    check("cache cũ hơn 1 phút mà VERSION chưa thấy mới: tải lại CHANGELOG, ra bản mới",
          DEM["changelog"] == 2 and d["latest"] == "0.64.67" and d["update_available"])

    # ---- 7. Nút cập nhật dùng CÙNG nguồn khi ghi phiên bản đích ----
    src = (SERVER / "main.py").read_text(encoding="utf-8")
    check("main.py không còn tự đọc VERSION ở chỗ khác ngoài _latest_remote_version",
          src.count("/main/VERSION") == 1)
finally:
    httpx.AsyncClient, main._read_version = _goc_client, _goc_ver

if _fails:
    print("\nFAIL:", len(_fails), _fails)
    raise SystemExit(1)
print("\nOK - phien_ban_moi_nhat")
