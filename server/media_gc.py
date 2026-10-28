"""
media_gc.py - Dọn vùng cache media của brain.

Vì sao tồn tại: ảnh Javis tự tạo (image_gen.py:202) và file user gửi qua Telegram
(main.py:6242) rơi vào `<brain>/attachments/` và `<brain>/inbox/` rồi nằm đó vĩnh viễn.
Không ai dọn, mà trước đây chúng còn được commit vào git của brain nên xoá file cũng
không lấy lại được dung lượng. Quyết định: hai thư mục đó là VÙNG CACHE, không phải tri
thức. Tri thức là file .md. Cái gì trong đó cũng có thể biến mất mà brain vẫn nguyên vẹn.

Tách làm hai tầng để test được:
  - plan_deletions: THUẦN. Nhận sẵn danh sách (path, size, mtime), trả danh sách cần xoá.
    Không chạm đĩa, không đọc đồng hồ -> test không cần fixture.
  - scan / media_dirs / sweep: chạm đĩa. Dùng os.scandir và phải gọi qua asyncio.to_thread.

Stdlib-only.
"""
from __future__ import annotations

import os
import re
import time

# Đuôi file VĂN BẢN mà máy dọn không bao giờ đụng (khi keep_md=True). Tri thức là chữ, không
# phải ảnh; .txt đứng cạnh .md vì đó là định dạng văn bản mặc định của chủ repo.
DUOI_VAN_BAN = (".md", ".txt")


def plan_deletions(entries, now, max_age_days, max_mb, keep_md=True, giu_path=None):
    """Quyết định file nào phải xoá. HÀM THUẦN.

    entries      : list[(path, size_bytes, mtime)] - mọi file trong vùng cache của MỘT brain.
    now          : mốc thời gian tham chiếu (time.time()).
    max_age_days : file già hơn ngần này ngày thì xoá. <= 0 = tắt luật tuổi.
    max_mb       : trần dung lượng vùng cache. <= 0 = tắt luật trần.
    keep_md      : True (mặc định) = không bao giờ xoá file VĂN BẢN (.md và .txt), vì vùng
                   cache của brain có thể lạc note vào mà note là tri thức. Trước 0.55.58 chỉ
                   chừa .md; nhưng chủ repo quy ước file văn bản mặc định là .txt, nên một bài
                   viết .txt rơi vào attachments/ là bị máy dọn xoá như ảnh cũ - đúng kiểu
                   "file đã viết rồi biến mất" mà không có một dòng báo nào (báo 2026-09-08).
                   Đặt False cho thư mục THUẦN trung chuyển (staging), nơi một file .md chỉ là
                   thứ user vừa dán vào chat, không phải note.
    giu_path     : set đường dẫn TUYỆT ĐỐI không bao giờ được xoá, bất kể tuổi hay trần.

    Vì sao có giu_path: vùng cache "cái gì cũng biến mất được" chỉ đúng khi KHÔNG AI trỏ vào
    nó. Một tài liệu gắn vào project thì có người trỏ - xoá nó đi là để lại một hàng trong
    khung Project dẫn tới hư không, ghim nó thì prompt nạp rỗng. Luật tuổi không biết điều đó
    nên phải nói cho nó biết.

    Trả list path theo ĐÚNG thứ tự xoá: nhóm quá hạn trước, rồi nhóm bị trần cắt (cũ tới mới).
    """
    khoa = {os.path.normcase(os.path.abspath(x)) for x in (giu_path or ())}
    giu = [t for t in entries
           if not (keep_md and str(t[0]).lower().endswith(DUOI_VAN_BAN))
           and os.path.normcase(os.path.abspath(str(t[0]))) not in khoa]
    xoa, con_lai = [], []
    if max_age_days and max_age_days > 0:
        han = now - max_age_days * 86400.0
        for t in giu:
            (xoa if t[2] < han else con_lai).append(t)
    else:
        con_lai = list(giu)
    if max_mb and max_mb > 0:
        tran = max_mb * 1024 * 1024
        tong = sum(t[1] for t in con_lai)
        # Trần là van an toàn cho trường hợp sinh cả trăm ảnh trong một ngày: lúc đó
        # luật tuổi chưa kịp cứu. Xoá từ CŨ NHẤT và dừng ngay khi xuống dưới trần.
        for t in sorted(con_lai, key=lambda x: x[2]):
            if tong <= tran:
                break
            xoa.append(t)
            tong -= t[1]
    return [t[0] for t in xoa]


# Cùng luật nhận diện thư mục attachments với image_gen.py:40 và main.py:1931: tên có thể là
# "attachments", "Attachments", hay có tiền tố số thứ tự kiểu "05 - attachments".
_ATTACH_RE = r"^(\d+\s*[-_.]\s*)?attachments$"


def media_dirs(brain_root, attachments=True):
    """Các thư mục VÙNG CACHE cấp 1 của brain: attachments (mọi biến thể tên) + inbox.

    attachments=False: CHỈ inbox. Dùng khi bật đồng bộ ảnh lên GitHub (backup.sync_images) -
    lúc đó attachments/ không còn là cache dùng-xong-vứt nữa mà là thứ người dùng muốn GIỮ:
    để máy dọn xoá ảnh quá hạn thì lượt sync sau ghi nhận "đã xoá" rồi lan sang mọi máy,
    ảnh backup tự biến mất đúng hạn. inbox thì vẫn dọn - nó không bao giờ được sync."""
    ra = []
    try:
        with os.scandir(brain_root) as it:
            for d in it:
                try:
                    if not d.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ten = d.name.strip()
                if ten.lower() == "inbox" or attachments and re.match(_ATTACH_RE, ten, re.IGNORECASE):
                    ra.append(d.path)
    except OSError:
        pass
    return ra


def scan(dirs):
    """Duyệt đệ quy các thư mục -> list[(path, size, mtime)].

    Dùng os.scandir chứ KHÔNG dùng glob: glob đi hết cây rồi mới cắt nên không có trần thật,
    và stat đi kèm entry của scandir thì rẻ hơn hẳn stat riêng từng file. Lỗi từng file
    (đang bị khoá, vừa bị xoá, thiếu quyền) thì bỏ qua chứ không làm hỏng cả lượt quét.
    """
    ra = []
    for d in dirs:
        stack = [d]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for x in it:
                        try:
                            if x.is_dir(follow_symlinks=False):
                                stack.append(x.path)
                            elif x.is_file(follow_symlinks=False):
                                st = x.stat()
                                ra.append((x.path, st.st_size, st.st_mtime))
                        except OSError:
                            continue
            except OSError:
                continue
    return ra


def _xoa(can_xoa, kich_thuoc):
    """Xoá thật danh sách path, trả {"files", "bytes"}. Lỗi từng file thì bỏ qua: file vừa bị
    xoá tay hoặc đang bị tiến trình khác giữ -> để lượt sau dọn, không làm hỏng cả lượt."""
    n, b = 0, 0
    for p in can_xoa:
        try:
            os.remove(p)
        except OSError:
            continue
        n += 1
        b += kich_thuoc.get(p, 0)
    return {"files": n, "bytes": b}


def sweep(brain_root, max_age_days=30, max_mb=300, now=None, attachments=True, giu_path=None):
    """Dọn vùng cache của MỘT brain. CHẶN vì đụng đĩa - phải gọi qua asyncio.to_thread.

    attachments=False (đang bật đồng bộ ảnh): chỉ dọn inbox, xem media_dirs.
    giu_path: đường dẫn tuyệt đối phải giữ (tài liệu đang gắn vào project) - xem plan_deletions.
    Trả {"files": số file đã xoá, "bytes": tổng byte đã giải phóng}.
    """
    entries = scan(media_dirs(brain_root, attachments=attachments))
    can_xoa = plan_deletions(entries, float(now if now is not None else time.time()),
                             max_age_days, max_mb, giu_path=giu_path)
    return _xoa(can_xoa, {t[0]: t[1] for t in entries})


def temp_dirs(brain_root):
    """Thư mục tạm `temporary/` nằm trong brain (<brain_root>/temporary).

    Vì sao tách khỏi `media_dirs`:
      - `media_dirs` (attachments/ + inbox/) đi qua `sweep()` với `keep_md=True` (không bao giờ
        xoá `.md` và `.txt` vì sợ xoá nhầm bài viết/ghi chú của người dùng) và hạn 30 ngày.
      - Ngược lại, `temporary/` chứa các tệp đính kèm / văn bản dài dán từ clipboard vào ô chat
        (ví dụ `van-ban-dan-HHMMSS.txt`). Bản chất của chúng là tệp trung chuyển tạm thời phục vụ
        quá trình xử lý yêu cầu trong brain hiện tại, và phần lớn lại mang đuôi `.txt`. Nếu đưa
        vào `media_dirs` (`keep_md=True`), các tệp `van-ban-dan-*.txt` sẽ KHÔNG BAO GIỜ được dọn.
      - Vì vậy `temporary/` được quét riêng qua `sweep_staging` (`keep_md=False`, hạn mặc định
        `staging_days` = 3 ngày, nhưng vẫn tôn trọng `giu_path` nếu tệp được ghim vào Project/Session).
    """
    ra = []
    try:
        with os.scandir(brain_root) as it:
            for d in it:
                try:
                    if not d.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if d.name.strip().lower() == "temporary":
                    ra.append(d.path)
    except OSError:
        pass
    return ra


def sweep_staging(staging_root, max_age_days=3, now=None, giu_path=None):
    """Dọn thư mục trung chuyển tạm (`<brain>/temporary/` và `STATE_DIR/.staging`): nơi file
    user dán từ clipboard hoặc tải lên khung chat rơi xuống để engine đọc trong lượt xử lý.

    Khác `sweep` vùng cache brain ở hai điểm, cả hai đều vì đây là chỗ TRUNG CHUYỂN thuần:
      - `keep_md=False`: xoá cả `.txt` (`van-ban-dan-HHMMSS.txt`) và `.md` khi quá hạn, vì tệp
        trong `temporary/` / `.staging` là dữ liệu dán tạm lúc trò chuyện chứ không phải note tri thức.
      - Chỉ có luật tuổi (`staging_days`, mặc định 3 ngày), không cần trần dung lượng riêng.
      - Vẫn nhận `giu_path` để nếu người dùng chủ động ghim một tệp từ `temporary/` vào Project
        hoặc Cuộc trò chuyện thì không xoá mất tệp đang được tham chiếu.
    """
    entries = scan([staging_root])
    can_xoa = plan_deletions(entries, float(now if now is not None else time.time()),
                             max_age_days, 0, keep_md=False, giu_path=giu_path)
    return _xoa(can_xoa, {t[0]: t[1] for t in entries})

