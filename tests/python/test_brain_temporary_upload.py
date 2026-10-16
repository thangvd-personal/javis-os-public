"""Văn bản dài dán từ clipboard / tệp đính kèm khung chat phải nằm trong `<brain>/temporary/`.

    python tests/run.py brain_temporary_upload      (KHÔNG mạng)

Bối cảnh sự cố (2026-10-16):
  Khi người dùng dán đoạn văn bản dài (> 1500 ký tự hoặc > 25 dòng) vào ô chat, `pasteAsTxt`
  (dashboard/app.js) tự động đóng gói thành tệp `van-ban-dan-HHMMSS.txt` và gửi lên `POST /upload`
  kèm `brain` hiện hành. Trước đây `POST /upload` bỏ qua `brain` và luôn lưu vào `STATE_DIR/.staging`
  (`server/.staging` nằm ngoài thư mục brain). Hệ quả:
    1. Khi engine AI (như Codex/Claude) trả lời và dẫn link tới tệp vừa đọc
       `[van-ban-dan-165823.txt](D:/.../server/.staging/van-ban-dan-165823.txt)`, bộ chuẩn hoá link
       `_chuan_hoa_link_file` và bộ kiểm tra link `_link_file_khong_thay` kiểm tra `cand.relative_to(broot)`
       đều thất bại vì tệp nằm ngoài brain -> Javis tự đẩy cảnh báo giả:
       "⚠ Câu trả lời có dẫn link tới file mà mình không tìm thấy trong brain...".
    2. Người dùng bấm vào link hoặc mở trang Tệp tin của brain không thể xem được tệp đó.
    3. Các tác nhân / công cụ bị khoá phạm vi trong thư mục brain (`_safe_path`) không thể truy cập.

Giải pháp (Phương án A):
  - Lưu tệp upload/paste từ khung chat vào `<brain_root>/temporary/` khi biết `brain` (fallback về
    `STATE_DIR/.staging` khi không truyền `brain` hoặc không ghi được).
  - `/upload/raw` đọc được từ `<brain_root>/temporary/` lẫn `STATE_DIR/.staging` (tương thích ngược).
  - `temporary/` được loại khỏi git của brain (`git_brain.py`), khỏi đồ thị 3D (`graph_builder.py`),
    khỏi bộ đếm note dropdown (`_count_md`), và được `media_gc` dọn tự động sau `staging_days` (3 ngày,
    xoá cả `.txt` và `.md`).
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP_STATE = tempfile.mkdtemp(prefix="javis-temp-state-")
_TMP_BRAINS = tempfile.mkdtemp(prefix="javis-temp-brains-")
os.environ["JAVIS_STATE_DIR"] = _TMP_STATE
os.environ["BRAINS_DIR"] = _TMP_BRAINS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import main  # noqa: E402
import media_gc  # noqa: E402
import git_brain  # noqa: E402
import graph_builder  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

_fails = []


def check(ten, dieu_kien, them=""):
    print(("ok   " if dieu_kien else "FAIL ") + ten
          + (("  [" + str(them) + "]") if them and not dieu_kien else ""))
    if not dieu_kien:
        _fails.append(ten)


main.cfgmod.gate_active = lambda: False
client = TestClient(main.app, base_url="http://127.0.0.1")

BRAIN_DIR = Path(_TMP_BRAINS) / "SME-BTOS"
BRAIN_DIR.mkdir(parents=True, exist_ok=True)
(BRAIN_DIR / "Wiki").mkdir(parents=True, exist_ok=True)
(BRAIN_DIR / "Wiki" / " trang-chu.md".strip()).write_text("# Trang chủ\n[[tam-ung]]", encoding="utf-8")

NOI_DUNG_DAN = "Nội dung văn bản dài dán từ clipboard tiếng Việt có dấu — kiểm thử temporary." * 30

# ============================================================
# 1. POST /upload kèm brain lưu vào <brain_root>/temporary/
# ============================================================
r = client.post(
    "/upload",
    data={"brain": str(BRAIN_DIR)},
    files={"file": ("van-ban-dan-165823.txt", NOI_DUNG_DAN.encode("utf-8"), "text/plain")},
)
up = r.json()
check("POST /upload thành công", r.status_code == 200 and up.get("ok") is True, up)

staged_path = Path(up.get("staged", "")).resolve()
expected_temp_dir = (BRAIN_DIR / "temporary").resolve()
check("tệp được lưu vào <brain_root>/temporary/ thay vì server/.staging",
      staged_path.parent == expected_temp_dir,
      f"staged={staged_path}, expected_parent={expected_temp_dir}")
check("thư mục temporary được tạo tự động trong brain", expected_temp_dir.is_dir())
check("nội dung tệp UTF-8 được lưu nguyên vẹn",
      staged_path.is_file() and staged_path.read_text(encoding="utf-8") == NOI_DUNG_DAN)
check("trả về url /upload/raw?name=...",
      str(up.get("url", "")).startswith("/upload/raw?name="), up.get("url"))

# ============================================================
# 2. GET /upload/raw phục vụ được tệp từ <brain_root>/temporary/
# ============================================================
r_raw = client.get(up["url"])
check("GET /upload/raw đọc được tệp trong <brain_root>/temporary/",
      r_raw.status_code == 200 and r_raw.content.decode("utf-8") == NOI_DUNG_DAN,
      r_raw.status_code)

# Chặn leo thư mục (path traversal)
for _bad in ("../Wiki/trang-chu.md", "..\\Wiki\\trang-chu.md", "..", "."):
    r_bad = client.get("/upload/raw", params={"name": _bad})
    check(f"chặn path traversal {_bad!r}", r_bad.status_code in (400, 404, 422), r_bad.status_code)

# ============================================================
# 3. Chuẩn hoá link & kiểm tra link trong câu trả lời của Javis
# ============================================================
cau_tra_loi = f"Mình đã đọc file [van-ban-dan-165823.txt]({staged_path.as_posix()}) của bạn."
da_chuan_hoa = main._chuan_hoa_link_file(str(BRAIN_DIR), cau_tra_loi)
check("_chuan_hoa_link_file viết lại đường dẫn tuyệt đối thành tương đối temporary/...",
      da_chuan_hoa == "Mình đã đọc file [van-ban-dan-165823.txt](temporary/van-ban-dan-165823.txt) của bạn.",
      da_chuan_hoa)

thieu_truoc = main._link_file_khong_thay(str(BRAIN_DIR), cau_tra_loi)
thieu_sau = main._link_file_khong_thay(str(BRAIN_DIR), da_chuan_hoa)
check("_link_file_khong_thay KHÔNG còn báo thiếu file khi dẫn link tuyệt đối tới temporary/",
      thieu_truoc == [], thieu_truoc)
check("_link_file_khong_thay KHÔNG báo thiếu file khi dẫn link tương đối temporary/...",
      thieu_sau == [], thieu_sau)

# ============================================================
# 4. Trang Tệp tin (/files/list & /files/read) truy cập được temporary/
# ============================================================
r_list = client.get("/files/list", params={"brain": str(BRAIN_DIR), "path": "temporary/van-ban-dan-165823.txt"})
j_list = r_list.json()
check("/files/list mở được đường dẫn temporary/van-ban-dan-165823.txt và focus đúng file",
      r_list.status_code == 200 and j_list.get("focus") == "van-ban-dan-165823.txt", j_list)

r_read = client.get("/files/read", params={"brain": str(BRAIN_DIR), "path": "temporary/van-ban-dan-165823.txt"})
j_read = r_read.json()
check("/files/read đọc được nội dung file trong temporary/",
      r_read.status_code == 200 and j_read.get("content") == NOI_DUNG_DAN, j_read)

# ============================================================
# 5. Media GC: dọn cả .txt/.md quá hạn trong <brain_root>/temporary/
# ============================================================
check("media_gc.temp_dirs tìm thấy <brain_root>/temporary",
      hasattr(media_gc, "temp_dirs") and [Path(p).resolve() for p in media_gc.temp_dirs(str(BRAIN_DIR))] == [expected_temp_dir])

# Tạo thêm 1 file .md quá hạn 5 ngày và 1 file .txt được ghim vào project (giu_path)
expected_temp_dir.mkdir(parents=True, exist_ok=True)
md_cu = expected_temp_dir / "nhap-dan-cu.md"
md_cu.write_text("# Nháp cũ", encoding="utf-8")
txt_ghim = expected_temp_dir / "tai-lieu-ghim.txt"
txt_ghim.write_text("Tài liệu đã ghim vào project", encoding="utf-8")
old_ts = time.time() - 5 * 86400
os.utime(staged_path, (old_ts, old_ts))
os.utime(md_cu, (old_ts, old_ts))
os.utime(txt_ghim, (old_ts, old_ts))

kq_gc = media_gc.sweep_staging(str(expected_temp_dir), max_age_days=3, giu_path={str(txt_ghim)})
check("sweep_staging xoá cả .txt và .md quá hạn 3 ngày trong temporary/",
      not staged_path.exists() and not md_cu.exists() and kq_gc.get("files") == 2, kq_gc)
check("sweep_staging chừa lại file đang được ghim trong giu_path",
      txt_ghim.exists())

# Sau khi dọn, /upload/raw trả 404
r_after_gc = client.get(up["url"])
check("sau khi dọn temporary/, GET /upload/raw trả 404", r_after_gc.status_code == 404, r_after_gc.status_code)

# ============================================================
# 6. Gitignore, Graph Builder & _count_md bỏ qua temporary/
# ============================================================
check("git_brain._GITIGNORE chặn temporary/ và Temporary/",
      "temporary/\n" in git_brain._GITIGNORE and "Temporary/\n" in git_brain._GITIGNORE)

(expected_temp_dir / "tam-ung.md").write_text("# Tạm ứng\n[[trang-chu]]", encoding="utf-8")
g = graph_builder.build_graph([str(BRAIN_DIR)], include_orphans=True)
node_ids = {n["id"] for n in g.get("nodes", [])}
check("graph_builder bỏ qua file .md nằm trong temporary/",
      "tam-ung" not in node_ids and "trang-chu" in node_ids, node_ids)

so_md = main._count_md(str(BRAIN_DIR), 100)
check("_count_md chỉ đếm note thật trong brain (1), không đếm .md trong temporary/",
      so_md == 1, so_md)

print()
if _fails:
    print(f"FAIL {len(_fails)}: " + "; ".join(_fails))
    raise SystemExit(1)
print("TẤT CẢ PASS")
