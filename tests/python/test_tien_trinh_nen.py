"""Việc chạy nền engine bỏ lại: Javis nhận theo dõi, xong tự báo, tự làm tiếp (0.64.66).

Chủ repo báo 2026-09-27: "Đang render nền, xong mình ghép tiếng và gửi video" rồi im; video xong
lúc 14:57, tới 16:14 hỏi "xong video chưa" mới biết. Test dựng lại đúng cơ chế bằng TIẾN TRÌNH
THẬT: một "engine" chạy session riêng, bật lệnh ngầm rồi thoát.

    python tests/run.py tien_trinh_nen
"""
import os
import sys
import tempfile
import time

from _paths import ROOT, SERVER  # noqa: E402,F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-ttn-")
import asyncio  # noqa: E402
import subprocess  # noqa: E402

import background_status  # noqa: E402
import tien_trinh_nen as T  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


if os.name == "nt":
    print("BỎ QUA: Windows không có nhóm tiến trình, mọi hàm là no-op.")
    sys.exit(0)

WD = tempfile.mkdtemp(prefix="javis-ttn-wd-")


def engine_bo_lai(lenh: str) -> int:
    """Giả một engine CLI: chạy session riêng (y như claude_cli/grok/agy), bật lệnh ngầm rồi thoát."""
    p = subprocess.Popen(["sh", "-c", "(" + lenh + ") &\nexit 0"], cwd=WD, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait()
    return p.pid


# ---- 1. Engine bỏ lại lệnh ngầm: hết lượt thì được nhận theo dõi ----
pid = engine_bo_lai("sleep 1.5; echo xong > video.mp4")
T.ghi_nhom("chat:abc:1", pid)
T.dat_loi_dan("web:abc", "ghép tiếng, kiểm tra, gửi video")
ds = T.nhan_nuoi_theo_tag("chat:abc:1", WD, "web:abc", hua=False)
check("lệnh ngầm còn sống được nhận theo dõi", len(ds) == 1, ds)
v = ds[0] if ds else {}
check("mô tả là lệnh thật chứ không phải vỏ sh", "sleep" in v.get("mo_ta", "") or "sh" in v.get("mo_ta", ""), v.get("mo_ta"))
check("lời dặn op=then gắn vào việc và bật lượt nối tiếp",
      v.get("sau_khi_xong") == "ghép tiếng, kiểm tra, gửi video" and v.get("noi_tiep") is True)
check("việc đang chạy hiện trong danh sách của khung chat", len(T.dang_chay("web:abc")) == 1)
check("chưa xong thì nhịp theo dõi không báo gì", T.kiem_mot_luot() == [])

# Dải trạng thái và cổng hứa suông thấy việc này.
view = background_status.active_view([], [], [], chat_id="web:abc", jobs=T.dang_chay())
check("dải trạng thái đếm việc chạy nền là đang chạy",
      view["running_count"] == 1 and view["items"][0]["kind"] == "job" and view["items"][0]["mine"])
check("có việc chạy nền thì KHÔNG coi lời hứa là hứa suông", background_status.has_pending_work(view))

time.sleep(2.2)
xong = T.kiem_mot_luot()
check("nhóm chết hết thì báo xong đúng một lần", len(xong) == 1 and xong[0]["trang_thai"] == "xong", xong)
check("báo xong kèm file mới sinh ra (video.mp4)",
      bool(xong) and any(f.endswith("video.mp4") for f in xong[0].get("file_moi") or []),
      xong[0].get("file_moi") if xong else "")
check("nhịp sau không báo lại", T.kiem_mot_luot() == [])
check("hết việc thì danh sách đang chạy rỗng", T.dang_chay("web:abc") == [])

# ---- 2. Engine không bỏ lại gì: không nhận, và lời dặn bị bỏ ----
p = subprocess.Popen(["sh", "-c", "exit 0"], start_new_session=True)
p.wait()
T.ghi_nhom("chat:def:1", p.pid)
T.dat_loi_dan("web:def", "gửi file")
check("không còn lệnh ngầm thì không nhận gì", T.nhan_nuoi_theo_tag("chat:def:1", WD, "web:def") == [])
check("lời dặn không có việc để gắn thì bị bỏ, không treo sang lượt sau", "web:def" not in T._LOI_DAN)

# ---- 3. Câu trả lời có hứa thì tự bật lượt nối tiếp dù model không dặn ----
pid = engine_bo_lai("sleep 5")
T.ghi_nhom("chat:ghi:1", pid)
ds = T.nhan_nuoi_theo_tag("chat:ghi:1", WD, "web:ghi", hua=True)
check("lời hứa trong câu trả lời bật lượt nối tiếp", len(ds) == 1 and ds[0]["noi_tiep"] is True)

# ---- 4. Dặn muộn (lượt sau) thì gắn vào việc đang chạy ----
g = T.gan_loi_dan_cho_viec_dang_chay("web:ghi", "gửi video")
check("dặn muộn gắn vào việc đang chạy", bool(g) and g.get("sau_khi_xong") == "gửi video")

# ---- 5. Dừng một việc ----
T.huy(ds[0]["id"])
time.sleep(0.5)
xong = T.kiem_mot_luot()
check("dừng việc thì nhóm chết và báo 'huy'", len(xong) == 1 and xong[0]["trang_thai"] == "huy", xong)

# ---- 6. Callback async được gọi ----
nhan = []


async def cb(v):
    nhan.append(v["id"])

T.dat_khi_xong(cb)
asyncio.run(T._goi_khi_xong({"id": "jtest"}))
check("callback báo xong (async) được gọi", nhan == ["jtest"])

# ---- 7. Lượt lỗi/bị dừng: bỏ ghi nhớ tag ----
T.ghi_nhom("chat:zzz:1", 999999)
T.bo_tag("chat:zzz:1")
check("bo_tag dọn tag của lượt không tới bước nhận nuôi", "chat:zzz:1" not in T._NHOM_THEO_TAG)

# ---- 8. Sổ sống sót qua khởi động lại ----
T._VIEC.clear()
T._nap()
check("sổ việc nạp lại được từ đĩa", len(T._VIEC) >= 2)

# ---- 9. Bộ bắt lời hứa: đúng các câu trong đoạn chat chủ repo gửi ----
bat = background_status.detect_promise
check("bắt 'Đang render nền, xong mình ghép tiếng và gửi video.'",
      bool(bat("Đang render nền, xong mình ghép tiếng và gửi video.")))
check("bắt 'khoảng 5 phút nữa xong'", bool(bat("Em đang render, khoảng 5 phút nữa xong.")))
check("bắt câu tiếng Anh 'once it is done I will send'",
      bool(bat("Rendering in the background, once it is done I will add audio and send it.")))
check("KHÔNG bắt câu giao hàng ngay 'Xong rồi, em gửi anh file đây.'",
      bat("Xong rồi, em gửi anh file đây.") == "")
check("KHÔNG bắt câu báo kết quả 'Xong rồi anh. Video đã có đủ hình'",
      bat("Xong rồi anh. Video đã có đủ hình và giọng đọc, file hoàn chỉnh lúc 14:57.") == "")

# ---- 9b. Tiến trình phụ của engine (máy chủ MCP) không bị nhận nhầm là việc nền ----
_mcp = os.path.join(WD, "mcp-server-demo")
with open(_mcp, "w") as f:
    f.write("#!/bin/sh\nsleep 3\n")
os.chmod(_mcp, 0o755)
pid = engine_bo_lai("./mcp-server-demo")
T.ghi_nhom("chat:mcp:1", pid)
check("co_nhom_song thấy nhóm còn sống mà không lấy khỏi sổ", T.co_nhom_song("chat:mcp:1")
      and "chat:mcp:1" in T._NHOM_THEO_TAG)
check("nhóm chỉ gồm tiến trình MCP thì không nhận nuôi",
      T.nhan_nuoi_theo_tag("chat:mcp:1", WD, "web:mcp") == [])

# ---- 10. Engine có shell đều ghi nhóm tiến trình ----
for f in ("claude_cli.py", "grok_cli.py", "antigravity_cli.py"):
    src = (SERVER / f).read_text(encoding="utf-8")
    check(f"{f} ghi nhóm tiến trình cho tien_trinh_nen", "tien_trinh_nen.ghi_nhom(self.tag, proc.pid)" in src)

MAIN = (SERVER / "main.py").read_text(encoding="utf-8")
i_nuoi = MAIN.find("_cau_nuoi = await _nhan_nuoi_tien_trinh(\n                        turn_tag")
i_hua = MAIN.find("_canh_bao = await _canh_bao_hua_suong(\n                        brain, WEB_CHAT_PREFIX")
check("khung chat web nhận theo dõi TRƯỚC khi xét hứa suông", 0 < i_nuoi < i_hua, (i_nuoi, i_hua))
check("lượt nối tiếp không chặn vòng theo dõi (create_task)",
      "t = asyncio.create_task(runner(" in MAIN)

if fails:
    raise SystemExit(f"\nFAIL - test_tien_trinh_nen: {len(fails)} lỗi")
print("\nOK - test_tien_trinh_nen: tất cả pass")
