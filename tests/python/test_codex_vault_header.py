"""Hồi quy: header X-Javis-Vault mà Javis gắn cho Codex phải THẬT SỰ tới được hub.

Sự cố 27/09/2026: dashboard đang ở brain "My Bullet Journal", lệnh xem trước đơn TTS lại chạy
trên brain "Ngọc Thu Phạm". Kết nối không có lỗi gì (mcp_store là toàn cục). Hub không nhận được
brain nên đoán theo "phiên cập nhật gần nhất" của CẢ MÁY, và phiên đó thuộc brain khác.

Vì sao header không tới, đọc thẳng từ mã nguồn Codex:
  - `-c key=value`: khoá được tách bằng `path.split('.')` và giữ NGUYÊN dấu nháy
    (codex-rs/config/src/overrides.rs, apply_toml_override). Khoá cũ
    `mcp_servers.javis.http_headers."X-Javis-Vault"` cho ra header tên `"X-Javis-Vault"`.
  - rmcp-client dựng header bằng `HeaderName::from_bytes` và `HeaderValue::from_str`; tên có
    dấu nháy hay giá trị ngoài ASCII đều bị BỎ QUA kèm một dòng warn
    (codex-rs/rmcp-client/src/utils.rs, build_default_headers).

Phép thử này mô phỏng đúng hai bước đó thay vì chỉ kiểm chuỗi override có mặt trong argv, vì
phép kiểm cũ (`test_tool_reliability`) vẫn XANH suốt thời gian header không hề tới hub.

    python tests/run.py codex_vault_header
"""
import os
import re
import sys
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_TMP = Path(tempfile.mkdtemp(prefix="javis-codex-vault-"))
os.environ.setdefault("JAVIS_STATE_DIR", str(_TMP / "state"))
os.environ.setdefault("JAVIS_SESSIONS_DB", str(_TMP / "conversations.db"))
os.environ.setdefault("BRAINS_DIR", str(_TMP / "brains"))
(_TMP / "state").mkdir(parents=True, exist_ok=True)

import mcp_hub

fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{them}]" if them and not cond else ""))
    if not cond:
        fails.append(name)


# Ký tự hợp lệ của tên header HTTP (RFC 9110 "token"), đúng thứ HeaderName::from_bytes đòi.
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def codex_doc_override(override):
    """Làm y như Codex: tách ở dấu '=' đầu tiên, khoá tách theo '.', giá trị là chuỗi TOML."""
    khoa, gia_tri = override.split("=", 1)
    duong = khoa.strip().split(".")
    gia_tri = gia_tri.strip()
    if gia_tri.startswith('"') and gia_tri.endswith('"'):
        gia_tri = gia_tri[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return duong, gia_tri


def header_toi_duoc_hub(override):
    """(tên, giá trị) nếu rmcp-client chịu gửi header này, None nếu nó bị bỏ qua."""
    duong, gia_tri = codex_doc_override(override)
    if duong[:3] != ["mcp_servers", "javis", "http_headers"] or len(duong) != 4:
        return None
    ten = duong[3]
    if not _TOKEN.match(ten) or not gia_tri.isascii():
        return None
    return ten, gia_tri


brains = _TMP / "brains"
co_dau = brains / "Ngọc Thu Phạm"
khong_dau = brains / "My Bullet Journal"
for b in (co_dau, khong_dau):
    b.mkdir(parents=True, exist_ok=True)

# ---- 1. Khoá cũ có dấu nháy thì header chết, khoá mới thì sống ------------------------
cu = 'mcp_servers.javis.http_headers."X-Javis-Vault"="/x"'
check("CANARY: khoá có dấu nháy bị Codex đọc thành tên header không hợp lệ",
      header_toi_duoc_hub(cu) is None)
for b in (khong_dau, co_dau):
    ov = mcp_hub.codex_vault_override(str(b))
    h = header_toi_duoc_hub(ov)
    check(f"header brain '{b.name}' tới được hub", h is not None, ov)
    if h:
        check(f"tên header đúng X-Javis-Vault ({b.name})", h[0] == "X-Javis-Vault", h[0])
        root, nguon, hong = mcp_hub.resolve_vault(h[1])
        check(f"hub đọc ra ĐÚNG brain '{b.name}' từ header",
              root and Path(root).resolve() == b.resolve() and nguon == "header" and not hong,
              (root, nguon, hong))

# ---- 2. Hub vẫn đọc được mọi dạng cũ -------------------------------------------------
root, nguon, _ = mcp_hub.resolve_vault(str(co_dau))
check("đường dẫn có dấu gửi nguyên văn vẫn dùng được", nguon == "header")
thoi = str(co_dau).encode("utf-8").decode("latin-1")   # cách Starlette giải byte UTF-8 thô
root, nguon, _ = mcp_hub.resolve_vault(thoi)
check("byte UTF-8 thô bị giải thành latin-1 vẫn đọc ra đúng brain",
      nguon == "header" and Path(root).resolve() == co_dau.resolve(), (root, nguon))

# ---- 3. Đổi brain qua lại trên CÙNG một CodexCLI thì chỉ còn đúng brain hiện tại --------
extra = ["model_reasoning_effort=high", 'mcp_servers.javis.http_headers."X-Javis-Vault"="/cu"']
for b in (khong_dau, co_dau, khong_dau):
    mcp_hub.dat_codex_vault(extra, str(b))
vault = [x for x in extra if "X-Javis-Vault" in x]
check("đổi brain A -> B -> A chỉ còn MỘT override brain", len(vault) == 1, vault)
check("override còn lại là brain hiện tại (A)",
      vault and mcp_hub.resolve_vault(header_toi_duoc_hub(vault[0])[1])[0]
      == str(khong_dau.resolve()), vault)
check("không đụng các override khác", "model_reasoning_effort=high" in extra)
mcp_hub.dat_codex_vault(extra, None)
check("không có brain thì bỏ override brain, không để lại brain cũ",
      not any("X-Javis-Vault" in x for x in extra), extra)

print(("ĐỎ: " + str(len(fails))) if fails else "XANH: tất cả đều qua")
sys.exit(1 if fails else 0)
