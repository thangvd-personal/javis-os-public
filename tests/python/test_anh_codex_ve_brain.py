"""Ảnh Codex tự vẽ phải về brain và hiện ngay trong khung chat.

    python tests/run.py anh_codex_ve_brain      (KHÔNG mạng)

Bug 2026-09-28: nhờ Javis (engine ChatGPT qua Codex) làm 3 ảnh công thức 9:16. Codex vẽ bằng
skill `imagegen` riêng của nó, lưu ở `~/.codex/generated_images/<thread>/exec-*.png`, NGOÀI
brain. Câu trả lời dẫn `[Thành phẩm](/home/javis/.codex/generated_images/...png)`: bấm vào
ra 404, ảnh không hiện trong khung chat, lại thêm dòng "không tìm thấy trong brain", người dùng
phải tự đi lưu ảnh về.

File này dựng đúng cảnh đó (CODEX_HOME tạm + một Codex CLI giả in JSONL) và soi:
  1. Link trong câu trả lời thành ảnh nhúng `![..](attachments/..)`, file có thật trong brain.
  2. Ảnh vẽ trong lượt mà câu trả lời quên nhắc vẫn được nhúng ở cuối.
  3. Ảnh cũ của lượt trước, ảnh của thread khác: KHÔNG kéo vào.
  4. Đường dẫn ngoài generated_images (vd file hệ thống) không bị chép vào brain.
  5. Chạy lại không đẻ bản sao; phiên Coding (cwd là repo) vẫn đổ ảnh về brain.
  6. Lớp soát link của main không còn báo "không tìm thấy" cho câu đã viết lại.
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

TMP = Path(tempfile.mkdtemp(prefix="javis-anhcodex-"))
os.environ["CODEX_HOME"] = str(TMP / "codexhome")
os.environ.setdefault("JAVIS_STATE_DIR", str(TMP / "state"))

import anh_codex
from claude_cli import CodexCLI

_fails = []


def check(ten, dieu_kien, them=""):
    print(("ok   " if dieu_kien else "FAIL ") + ten
          + (("  [" + str(them) + "]") if them and not dieu_kien else ""))
    if not dieu_kien:
        _fails.append(ten)


PNG = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 13 + b"\x00" * 4
       + (0).to_bytes(4, "big") + b"IEND" + b"\xaeB`\x82")

THREAD = "01a0e847-be5a-7670-a937-686c13489694"
gen = Path(os.environ["CODEX_HOME"]) / "generated_images"
tdir = gen / THREAD
tdir.mkdir(parents=True)
vault = TMP / "brain"
vault.mkdir()

# Ảnh của lượt TRƯỚC (cũ) trong cùng thread, và ảnh của thread khác.
cu = tdir / "exec-cu.png"
cu.write_bytes(PNG)
os.utime(cu, (time.time() - 3600, time.time() - 3600))
khac = gen / "thread-khac"
khac.mkdir()
(khac / "exec-khac.png").write_bytes(PNG)
os.utime(khac / "exec-khac.png", (time.time() - 3600, time.time() - 3600))

t0 = time.time()
a1, a2, a3, a4 = (tdir / f"exec-{n}.png" for n in ("aaa", "bbb", "ccc", "ddd"))
for p in (a1, a2, a3, a4):
    p.write_bytes(PNG)

bi_mat = TMP / "bi-mat.png"
bi_mat.write_bytes(PNG)

cau = (
    "Em đã làm 3 ảnh mẫu dọc 9:16:\n\n"
    f"1. [Thành phẩm]({a1.as_posix()})\n"
    f"2. [Cận cảnh phần nhân](<{a2.as_posix()}>)\n"
    f"3. Ảnh thưởng thức ở `{a3.as_posix()}`.\n\n"
    f"Thêm: [file lạ]({bi_mat.as_posix()})"
)
moi, ds = anh_codex.dua_anh_ve_brain(cau, THREAD, t0, str(vault))
attach = vault / "attachments"
ten = sorted(p.name for p in attach.iterdir()) if attach.is_dir() else []

check("ảnh được chép vào attachments/ của brain", len(ten) == 4, ten)
check("link markdown thành ảnh nhúng", "1. ![Thành phẩm](attachments/codex-01a0e847-exec-aaa.png)" in moi, moi)
check("link dạng <...> cũng thành ảnh nhúng",
      "![Cận cảnh phần nhân](attachments/codex-01a0e847-exec-bbb.png)" in moi, moi)
check("đường trong backtick đổi sang đường trong brain",
      "`attachments/codex-01a0e847-exec-ccc.png`" in moi, moi)
check("ảnh chỉ nhắc trong backtick vẫn được nhúng ở cuối",
      "![](attachments/codex-01a0e847-exec-ccc.png)" in moi, moi)
check("ảnh vẽ trong lượt mà không nhắc tới vẫn được nhúng",
      "![](attachments/codex-01a0e847-exec-ddd.png)" in moi, moi)
check("không còn đường generated_images nào trong câu trả lời", "generated_images" not in moi, moi)
check("ảnh cũ của lượt trước không bị kéo vào", "exec-cu" not in moi and not any("exec-cu" in n for n in ten))
check("ảnh của thread khác không bị kéo vào", not any("khac" in n for n in ten))
check("file ngoài generated_images giữ nguyên, không chép",
      f"[file lạ]({bi_mat.as_posix()})" in moi and not any("bi-mat" in n for n in ten))
check("trả đúng danh sách đường tuyệt đối trong brain",
      len(ds) == 4 and all(Path(x).is_file() and Path(x).parent == attach for x in ds), ds)
check("PNG được gắn nhãn Javis như ảnh tự tạo",
      b"javisos" in (attach / "codex-01a0e847-exec-aaa.png").read_bytes().lower())

# Chạy lại (resume, gọi hai lần) → không đẻ bản sao.
moi2, _ = anh_codex.dua_anh_ve_brain(cau, THREAD, t0, str(vault))
check("chạy lại không đẻ bản sao", len(list(attach.iterdir())) == 4)
check("chạy lại ra đúng câu như lần đầu", moi2 == moi)

# Không có ảnh gì → nguyên văn.
van = "Chào chị, em chưa vẽ gì."
check("không có ảnh thì trả nguyên văn", anh_codex.dua_anh_ve_brain(van, "", t0, str(vault))[0] == van)
check("thread id lạ (trèo thư mục) bị bỏ qua", anh_codex.anh_moi_cua_thread("../x", 0) == [])

# Lớp soát link của main không còn báo thiếu.
import main

# File lạ cố tình nằm ngoài brain nên vẫn phải bị báo; chỉ ảnh Codex là hết bị báo.
_bao = main._link_file_khong_thay(str(vault), moi)
check("main không còn báo 'không tìm thấy trong brain' cho ảnh Codex",
      _bao == [bi_mat.as_posix()], _bao)

# Đi trọn qua CodexCLI.query với một Codex CLI giả, cwd là repo (phiên trang Coding).
repo = TMP / "repo"
repo.mkdir()
vault2 = TMP / "brain2"
vault2.mkdir()
THREAD2 = "019fa10e-5df0-73f1-8ec8-5ae78f0bc71f"
out_png = gen / THREAD2 / "call_xyz.png"
script = (
    "import json, pathlib\n"
    f"print(json.dumps({{'type': 'thread.started', 'thread_id': {THREAD2!r}}}), flush=True)\n"
    f"p = pathlib.Path({str(out_png)!r}); p.parent.mkdir(parents=True, exist_ok=True)\n"
    f"p.write_bytes({PNG!r})\n"
    f"print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message', "
    f"'text': 'Xong: [ảnh](' + p.as_posix() + ')'}}}}), flush=True)\n"
    "print(json.dumps({'type': 'turn.completed', 'usage': {}}), flush=True)\n"
)
cli = CodexCLI(cwd=str(repo))
cli.cli_path = sys.executable
cli.vault_root = str(vault2)
cli._build_args = lambda: [sys.executable, "-c", script]


async def _gom():
    return [e async for e in cli.query("vẽ giúp")]


evs = asyncio.run(_gom())
final = next((e for e in evs if e.get("type") == "final"), {})
check("CodexCLI: câu cuối nhúng ảnh trong brain",
      final.get("content") == "Xong: ![ảnh](attachments/codex-019fa10e-call_xyz.png)", final.get("content"))
check("CodexCLI: ảnh vào brain chứ không vào repo",
      (vault2 / "attachments" / "codex-019fa10e-call_xyz.png").is_file()
      and not (repo / "attachments").exists())

# _apply_codex_hub gắn brain vào CodexCLI (đường chat dashboard + Telegram + workflow).
cc = CodexCLI(cwd=str(repo))
main._apply_codex_hub(cc, str(vault2))
check("_apply_codex_hub đặt vault_root", cc.vault_root == str(vault2))

print()
if _fails:
    print(f"{len(_fails)} FAIL")
    sys.exit(1)
print("TẤT CẢ OK")
