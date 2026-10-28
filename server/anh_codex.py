"""anh_codex.py - Đưa ảnh Codex tự vẽ về brain để khung chat hiện được.

Vì sao tồn tại (bug 2026-09-28, chị dùng Javis nhờ làm 3 ảnh công thức 9:16): Codex CLI có
sẵn skill `imagegen` riêng. Khi được nhờ vẽ, model thường dùng skill đó thay vì tool
`javis_generate_image`, và Codex lưu ảnh ở `$CODEX_HOME/generated_images/<thread_id>/`, NGOÀI
brain. Câu trả lời dẫn link `/home/javis/.codex/generated_images/...png`, dashboard chỉ phục vụ
file trong brain nên link hỏng, ảnh không hiện trong khung chat, và lớp soát link gắn thêm
dòng "không tìm thấy trong brain". Người dùng phải tự đi lưu ảnh về.

Chữa ở tầng code chứ không bằng lời dặn: hết mỗi lượt Codex, chép ảnh mới của đúng thread đó
(cùng ảnh mà câu trả lời trỏ tới) vào `attachments/` của brain, rồi viết lại câu trả lời thành
`![...](attachments/...)`. Ảnh vừa vẽ mà câu trả lời quên nhắc cũng được nhúng ở cuối.

Chỉ đụng tới file ẢNH nằm TRONG thư mục generated_images của Codex: model không thể nhờ đường
này kéo một file bất kỳ trên máy (vd /etc/passwd) vào brain.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

import image_gen

DUOI_ANH = (".png", ".jpg", ".jpeg", ".webp", ".gif")
TRAN_ANH_MOI_LUOT = 12          # một lượt vẽ nhiều hơn thế thì lạ, thà thiếu còn hơn ngập brain
TRAN_BYTE = 40 * 1024 * 1024

# Target markdown (ảnh hoặc link). Cùng khuôn với channel_context._MD_LINK_RE, gộp gọn ở đây
# để module này không phụ thuộc main/channel_context.
_MD_RE = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*(?:<([^>\n]+)>|((?:[^\n()]|\([^\n()]*\))*?))\s*\)")
# Đường dẫn trần có chứa generated_images (POSIX, Windows, ~, file://). Dừng ở khoảng trắng,
# dấu ngoặc, backtick, nháy: đường dẫn ảnh Codex tự đặt không bao giờ chứa mấy ký tự đó.
_TRAN_RE = re.compile(
    r"(?:file:(?://[^/\s`]*)?)?(?:[A-Za-z]:)?[~\w./\\:-]*generated_images[\\/][^\s`\"'()\[\]<>]+?"
    r"\.(?:png|jpe?g|webp|gif)(?![\w.])", re.IGNORECASE)


def thu_muc_goc() -> Path:
    """`$CODEX_HOME/generated_images`, mặc định `~/.codex/generated_images` (như Codex tự đọc)."""
    home = os.getenv("CODEX_HOME")
    return (Path(home) if home else Path.home() / ".codex") / "generated_images"


def _giai_duong(raw: str) -> Path | None:
    """Chuỗi đường dẫn trong câu trả lời → file ảnh CÓ THẬT nằm trong generated_images, hoặc None."""
    t = str(raw or "").strip().strip("<>").strip("'\"")
    if not t:
        return None
    if t.lower().startswith("file:"):
        t = re.sub(r"^file:(?://[^/]*)?", "", t, flags=re.IGNORECASE)
    if "%" in t:
        try:
            t = urllib.parse.unquote(t)
        except Exception:
            pass
    t = t.replace("\\", "/")
    if re.match(r"^/[A-Za-z]:/", t):
        t = t[1:]
    if not t.lower().endswith(DUOI_ANH):
        return None
    try:
        goc = thu_muc_goc().resolve()
        p = Path(t).expanduser()
        if not p.is_absolute():
            return None
        p = p.resolve()
        p.relative_to(goc)
        return p if p.is_file() else None
    except Exception:
        return None


def anh_moi_cua_thread(thread_id: str, t0: float) -> list:
    """Ảnh Codex vẽ trong lượt này: file ảnh trong `generated_images/<thread_id>/` có mtime >= t0.

    Theo đúng thread chứ không quét cả thư mục: hai phiên Codex chạy song song thì ảnh của
    phiên này không được lạc sang khung chat của phiên kia."""
    tid = str(thread_id or "").strip()
    if not tid or "/" in tid or "\\" in tid or tid in (".", ".."):
        return []
    d = thu_muc_goc() / tid
    try:
        ds = [p for p in d.iterdir()
              if p.is_file() and p.suffix.lower() in DUOI_ANH and p.stat().st_mtime >= float(t0) - 2]
    except Exception:
        return []
    ds.sort(key=lambda p: p.stat().st_mtime)
    return ds[:TRAN_ANH_MOI_LUOT]


def chep_vao_brain(src: Path, vault_root: str) -> str | None:
    """Chép MỘT ảnh vào attachments/ của brain, trả đường tương đối theo gốc brain.

    Tên đích cố định theo tên nguồn (`codex-<thread 8 ký tự>-<tên>`), nên chạy lại lượt hay
    resume cũng không đẻ bản sao thứ hai. PNG đi qua đúng đường gắn nhãn của ảnh Javis tự tạo."""
    try:
        vault = Path(vault_root).resolve()
        adir = image_gen._attachments_dir(vault)
        ten = f"codex-{src.parent.name[:8]}-{src.stem}{src.suffix.lower()}"
        ten = re.sub(r"[^\w.-]", "-", ten)
        dst = adir / ten
        if not dst.exists():
            if src.stat().st_size > TRAN_BYTE:
                return None
            raw = src.read_bytes()
            if src.suffix.lower() == ".png":
                if image_gen._strip_c2pa_on():
                    raw = image_gen.strip_c2pa_png(raw)
                raw = image_gen.brand_png(raw)
            dst.write_bytes(raw)
        return os.path.relpath(dst, vault).replace(os.sep, "/")
    except Exception:
        return None


def dua_anh_ve_brain(text: str, thread_id: str, t0: float, vault_root: str):
    """Viết lại câu trả lời Codex để ảnh nó vẽ hiện ngay trong khung chat.

    Trả (text_moi, [đường tuyệt đối các ảnh đã đưa vào brain]). Không có ảnh nào thì trả
    nguyên văn và danh sách rỗng."""
    text = text or ""
    if not vault_root:
        return text, []
    ban_do = {}        # đường nguồn đã resolve → rel trong brain

    def _nhap(p: Path) -> str | None:
        if p in ban_do:
            return ban_do[p]
        rel = chep_vao_brain(p, vault_root)
        if rel:
            ban_do[p] = rel
        return rel

    # 1) Link/ảnh markdown trỏ vào generated_images → ảnh nhúng. Một link thường
    #    `[Thành phẩm](/…/x.png)` cũng đổi thành ảnh: người dùng nhờ vẽ để XEM, không để bấm.
    def _md(m):
        raw = (m.group(3) if m.group(3) is not None else m.group(4)) or ""
        raw = re.sub(r"""\s+(?:"[^"\n]*"|'[^'\n]*')\s*$""", "", raw.strip())
        p = _giai_duong(raw)
        if p is None:
            return m.group(0)
        rel = _nhap(p)
        if not rel:
            return m.group(0)
        nhan = m.group(2) or p.stem
        return f"![{nhan}]({rel})"

    ra = _MD_RE.sub(_md, text)

    # 2) Đường dẫn trần hoặc trong backtick → đổi thành đường trong brain (giữ backtick nếu có).
    #    Ảnh ở dạng này chưa được nhúng, nên bước 3 sẽ nhúng thêm ở cuối.
    def _tran(m):
        p = _giai_duong(m.group(0))
        if p is None:
            return m.group(0)
        rel = _nhap(p)
        return rel or m.group(0)

    ra = _TRAN_RE.sub(_tran, ra)

    # 3) Ảnh mới của thread trong lượt này mà câu trả lời không nhắc tới → vẫn đưa về brain.
    for p in anh_moi_cua_thread(thread_id, t0):
        try:
            _nhap(p.resolve())
        except Exception:
            continue

    # 4) Ảnh nào chưa có dạng `![..](rel)` trong câu trả lời thì nhúng ở cuối.
    thieu = [rel for rel in ban_do.values()
             if not re.search(r"!\[[^\]\n]*\]\(" + re.escape(rel) + r"\)", ra)]
    if thieu:
        ra = ra.rstrip() + "\n\n" + "\n\n".join(f"![]({rel})" for rel in dict.fromkeys(thieu))
    vault = Path(vault_root).resolve()
    return ra, [str(vault / rel) for rel in dict.fromkeys(ban_do.values())]
