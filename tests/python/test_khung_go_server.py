"""Khung gõ mới (0.153.0): khoá Fast mode trong settings và đường gắn file có sẵn trong brain.

    python tests/run.py khung_go_server
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import json
import shutil
import uuid
import os
import sys
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-khunggo-")
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import config as cfgmod  # noqa: E402

fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(them) + "]") if them and not cond else ""))
    if not cond:
        fails.append(name)


client = TestClient(main.app, base_url="http://localhost")   # STATE_DIR mới tinh: chưa có tài khoản nên không cần đăng nhập (như test_messenger)

# 1. fast lưu được và chỉ nhận boolean
s = cfgmod.read_settings()
check("mặc định fast tắt", (s.get("model") or {}).get("fast") is False, s.get("model"))
r = client.post("/settings", data={"section": "model", "data": json.dumps({"fast": True})})
check("POST /settings lưu fast=true", r.status_code == 200 and cfgmod.read_settings()["model"]["fast"] is True, r.text)
client.post("/settings", data={"section": "model", "data": json.dumps({"fast": "yes"})})
check("giá trị không phải boolean bị đọc thành false", cfgmod.read_settings()["model"]["fast"] is False)

# 2. /files/resolve
# _brain_root trỏ vào brain THẬT của checkout: dùng thư mục tên ngẫu nhiên và dọn ở cuối.
brain = Path(main._brain_root("brain"))
TMPDIR = brain / ("_test-khung-go-" + uuid.uuid4().hex[:8])
TMPDIR.mkdir(parents=True, exist_ok=True)
f = TMPDIR / "bao-cao.md"
f.write_text("doanh thu", encoding="utf-8")
root = Path(main._files_root("brain")).resolve()
rel = f.resolve().relative_to(root).as_posix()
r = client.get("/files/resolve", params={"brain": "brain", "path": rel})
d = r.json()
check("resolve trả đường dẫn tuyệt đối trong brain", r.status_code == 200 and d.get("ok")
      and Path(d["path"]) == f.resolve() and d["name"] == "bao-cao.md" and d["kind"] == "file", d)
for bad in ("../../etc/passwd", str(f.resolve()), "", TMPDIR.relative_to(root).as_posix() + "/khong-co.md"):
    r = client.get("/files/resolve", params={"brain": "brain", "path": bad})
    check(f"resolve từ chối {bad!r}", r.status_code == 400 and r.json().get("ok") is False, r.text)
outside = Path(tempfile.mkdtemp(prefix="ngoai-")) / "x.txt"
outside.write_text("x", encoding="utf-8")
try:
    (TMPDIR / "link-ra-ngoai.txt").symlink_to(outside)
    r = client.get("/files/resolve", params={"brain": "brain", "path": (TMPDIR / "link-ra-ngoai.txt").relative_to(root).as_posix()})
    check("resolve từ chối symlink trỏ ra ngoài brain", r.status_code == 400, r.text)
except OSError:
    print("skip symlink (máy không tạo được symlink)")

shutil.rmtree(TMPDIR, ignore_errors=True)

if fails:
    print("\nFAIL - test_khung_go_server: " + ", ".join(fails))
    sys.exit(1)
print("\nOK - test_khung_go_server: tất cả pass")
