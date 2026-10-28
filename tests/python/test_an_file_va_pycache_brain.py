"""Kiểm tra ẩn các thư mục / tập tin bắt đầu bằng dấu . và __pycache__ trong Vault và Tệp tin.

Chạy:
    python tests/python/test_an_file_va_pycache_brain.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-an-file-"))

import main

fails = []


def check(name: str, condition: bool, extra: str = "") -> None:
    msg = ("ok   " if condition else "FAIL ") + name
    if not condition and extra:
        msg += f" [{extra}]"
    print(msg)
    if not condition:
        fails.append(name)


_TMP = Path(tempfile.mkdtemp(prefix="javis-an-brain-")).resolve()
BRAIN = _TMP / "brains" / "Brain Default"
BRAIN.mkdir(parents=True)

# Tạo các thư mục và file giả lập cấu trúc brain thực tế
(BRAIN / ".agents").mkdir()
(BRAIN / ".antigravitycli").mkdir()
(BRAIN / ".claude").mkdir()
(BRAIN / ".javis").mkdir()
(BRAIN / "__pycache__").mkdir()
(BRAIN / "00 - Dashboard").mkdir()
(BRAIN / "skills").mkdir()
(BRAIN / "skills" / "__pycache__").mkdir()
(BRAIN / "skills" / ".disabled").mkdir()
(BRAIN / "skills" / "my_skill.py").write_text("# code", encoding="utf-8")

(BRAIN / ".javis-learn.lock").write_text("lock", encoding="utf-8")
(BRAIN / "AGENTS.md").write_text("# agents", encoding="utf-8")
(BRAIN / "CLAUDE.md").write_text("# claude", encoding="utf-8")

main._brain_root = lambda brain: str(BRAIN)
main.BRAINS_DIR = str(_TMP / "brains")
os.environ["JAVIS_HOST"] = "0.0.0.0"
os.environ.pop("JAVIS_FILES_ROOT", None)
os.environ.pop("JAVIS_REQUIRE_LOGIN", None)


def _list(path=None, all_files=False):
    return asyncio.run(main.files_list(brain="brain", path=path, all=all_files))


# ---- 1. Test /files/list tại gốc Brain ----
d_root = _list(None)
names = [it["name"] for it in d_root.get("items", [])]

check("Gốc brain: ẩn thư mục .agents", ".agents" not in names, str(names))
check("Gốc brain: ẩn thư mục .antigravitycli", ".antigravitycli" not in names, str(names))
check("Gốc brain: ẩn thư mục .claude", ".claude" not in names, str(names))
check("Gốc brain: ẩn thư mục .javis", ".javis" not in names, str(names))
check("Gốc brain: ẩn thư mục __pycache__", "__pycache__" not in names, str(names))
check("Gốc brain: ẩn file .javis-learn.lock", ".javis-learn.lock" not in names, str(names))
check("Gốc brain: vẫn giữ thư mục bình thường 00 - Dashboard", "00 - Dashboard" in names, str(names))
check("Gốc brain: vẫn giữ thư mục bình thường skills", "skills" in names, str(names))
check("Gốc brain: vẫn giữ file AGENTS.md", "AGENTS.md" in names, str(names))
check("Gốc brain: vẫn giữ file CLAUDE.md", "CLAUDE.md" in names, str(names))

# ---- 2. Test /files/list trong thư mục con ----
d_sub = _list("skills")
sub_names = [it["name"] for it in d_sub.get("items", [])]
check("Thư mục con: ẩn __pycache__", "__pycache__" not in sub_names, str(sub_names))
check("Thư mục con: ẩn .disabled", ".disabled" not in sub_names, str(sub_names))
check("Thư mục con: vẫn thấy my_skill.py", "my_skill.py" in sub_names, str(sub_names))

# ---- 3. Test cờ all=True (hiện khi được yêu cầu rõ) ----
d_all = _list(None, all_files=True)
all_names = [it["name"] for it in d_all.get("items", [])]
check("Khi all=True: thấy lại .agents", ".agents" in all_names)
check("Khi all=True: thấy lại __pycache__", "__pycache__" in all_names)
check("Khi all=True: thấy lại .javis-learn.lock", ".javis-learn.lock" in all_names)

# ---- 4. Test tìm kiếm /files/search bỏ qua file ẩn ----
hits = asyncio.run(main.files_search(brain="brain", q="learn.lock", limit=10, mode="name"))
check("files_search: không tìm thấy file ẩn .javis-learn.lock", len(hits.get("items", [])) == 0)

# ---- 5. Hợp đồng Frontend: console.js có bộ lọc dotfiles và __pycache__ ----
console_js = (ROOT / "dashboard" / "console.js").read_text(encoding="utf-8")
check(
    "console.js: load() trong Tệp tin lọc dotfiles và __pycache__",
    'it.name.startsWith(".")' in console_js and 'it.name !== "__pycache__"' in console_js,
)
check(
    "console.js: _vtList() cho Vault tree lọc dotfiles và __pycache__",
    '_vtList' in console_js and 'items = (d.items || []).filter' in console_js,
)

# ---- 6. Đảm bảo CHỈ ẨN HIỂN THỊ, KHÔNG ẢNH HƯỞNG hoạt động đọc/ghi của brain ----
# File ẩn vẫn phải đọc được qua API /files/read khi có đường dẫn cụ thể
read_res = asyncio.run(main.files_read(brain="brain", path=".javis-learn.lock"))
check("files_read: vẫn ĐỌC được file ẩn .javis-learn.lock bình thường",
      isinstance(read_res, dict) and read_res.get("content") == "lock")

# File ẩn vẫn phải ghi được qua API /files/write
write_res = asyncio.run(main.files_write(brain="brain", path=".test-hidden.txt", content="noi dung an"))
check("files_write: vẫn GHI được file ẩn .test-hidden.txt bình thường",
      isinstance(write_res, dict) and write_res.get("ok") is True)
check("Xác nhận file ẩn vừa ghi tồn tại trên đĩa", (BRAIN / ".test-hidden.txt").is_file())

# Đọc lại file ẩn vừa ghi
read_res2 = asyncio.run(main.files_read(brain="brain", path=".test-hidden.txt"))
check("files_read: đọc đúng nội dung file ẩn vừa ghi",
      isinstance(read_res2, dict) and read_res2.get("content") == "noi dung an")

print()
if fails:
    print(f"THẤT BẠI {len(fails)}: {', '.join(fails)}")
    exit(1)
print("OK - test_an_file_va_pycache_brain: tất cả pass")
