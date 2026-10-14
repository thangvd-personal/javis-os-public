"""Hạ nhóm chat team về bản trước 0.140.0 bằng snapshot lúc nâng (spec 2026-10-13-team-chat-design mục 18).

    python tools/team_chat_restore_pre.py --yes
    python tools/team_chat_restore_pre.py --state-dir /duong/dan/state --yes

CHẠY KHI JAVIS ĐÃ TẮT. Khôi phục CẢ BỘ ba kho liên quan, nhất quán với nhau (review T1, R6):
- `kanban.sqlite3`, `conversations.db`: về snapshot `<tên>.pre-0.140.0` mà mã 0.140.0 tạo một lần lúc nâng.
- `team.sqlite3`: trước 0.140.0 không có kho này, nên trạng thái trước nâng là KHÔNG CÓ. Kho được cất sang bên (bản sao
  bằng chứng) rồi gỡ khỏi chỗ chạy. Nhờ vậy nâng lại lần sau không có nhóm nào trỏ vào phiên đã mất.

Thứ tự, để không bao giờ báo thành công cho một lần khôi phục dở:
1. Kiểm trước điều kiện của cả bộ, chưa ghi gì: kho nào đã được nâng (có cột mới) thì PHẢI có snapshot hợp lệ (snapshot
   chưa có cột mới). Thiếu hay hỏng một snapshot là dừng, không đụng kho nào.
2. Chép MỌI kho hiện tại sang `<tên>.post-0.140.0-<thời điểm>` bằng SQLite backup API (lấy cả phần còn trong WAL): hồ sơ
   sau nâng (việc nhóm, tin nhóm, nhóm, sổ lượt) giữ nguyên để đọc lại.
3. Gỡ `team.sqlite3` (cùng file -wal, -shm) khỏi chỗ chạy.
4. Ghi đè từng kho bằng snapshot. Một bước hỏng thì trả MỌI kho có thể đã bị đụng về đúng bản ở bước 2 (kho vốn không
   có thì gỡ đi), kiểm lại từng kho sau khi trả, rồi báo lỗi. Một kho được tính là "có thể đã đụng" TRƯỚC khi bắt đầu
   ghi, không phải sau khi ghi xong (review T1 R9): lỗi sau khi backup đã commit (checkpoint, đóng kết nối) vẫn được trả
   về. Trả về không được thì báo rõ kho nào và bản sao nào để cứu tay; không bao giờ nói đã trả về khi chưa.

Mất khỏi kho chạy: mọi việc Kanban, tin chat và nhóm sau lúc nâng. Giữ: các bản `.post-0.140.0-<thời điểm>` cạnh file
kho, và snapshot `.pre-0.140.0` (nâng lại dùng lại đúng snapshot này, không chụp đè). Hạ KHÔNG khôi phục vẫn chạy được:
mã cũ không nhặt việc nhóm, nhưng trang Việc hiện các dòng `team_*` với trạng thái lạ và phiên `team:` hiện như phiên
thường.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Optional

SNAPSHOT_SUFFIX = ".pre-0.140.0"
POST_TAG = ".post-0.140.0-"
# (file kho, bảng, cột chỉ có từ 0.140.0): kiểm kho đã nâng và snapshot đúng là bản trước nâng.
STORES = (("kanban.sqlite3", "tasks", "lane"), ("conversations.db", "messages", "author_kind"))
TEAM_STORE = "team.sqlite3"


def _backup(src: Path, dst: Path) -> None:
    with closing(sqlite3.connect(str(src), timeout=10)) as s, closing(sqlite3.connect(str(dst), timeout=10)) as d:
        s.backup(d)
    with closing(sqlite3.connect(str(dst), timeout=10)) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _columns(path: Path, table: str) -> set:
    with closing(sqlite3.connect(str(path), timeout=10)) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def _remove_db(path: Path) -> None:
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if p.exists():
            p.unlink()


def plan(state_dir: str) -> dict:
    """Bước 1: kiểm cả bộ kho, không ghi gì. Trả {ok, todo: [(kho, snapshot)], team: đường dẫn | None, error}."""
    root = Path(state_dir)
    todo, problems = [], []
    for name, table, marker in STORES:
        store, snap = root / name, root / (name + SNAPSHOT_SUFFIX)
        try:
            upgraded = store.is_file() and marker in _columns(store, table)
        except sqlite3.Error as ex:
            problems.append(f"không đọc được {name}: {ex}")
            continue
        if snap.is_file():
            try:
                snap_cols = _columns(snap, table)
            except sqlite3.Error as ex:
                problems.append(f"snapshot {snap.name} hỏng: {ex}")
                continue
            if marker in snap_cols:
                problems.append(f"{snap.name} đã có cột {marker}; không phải bản trước 0.140.0")
                continue
            if not snap_cols:
                problems.append(f"{snap.name} không có bảng {table}")
                continue
            todo.append((store, snap))
        elif upgraded:
            problems.append(f"{name} đã được 0.140.0 nâng mà thiếu snapshot {snap.name}")
    team = root / TEAM_STORE
    if problems:
        return {"ok": False, "todo": [], "team": None, "error": "; ".join(problems)}
    if not todo:
        return {"ok": False, "todo": [], "team": None,
                "error": "không có snapshot .pre-0.140.0: kho chưa từng được 0.140.0 nâng lên, không có gì để khôi phục"}
    return {"ok": True, "todo": todo, "team": team if team.is_file() else None, "error": ""}


def _tables(path: Path) -> dict:
    """{bảng: số dòng} của một kho (để kiểm kho đã trả về đúng bản sao)."""
    with closing(sqlite3.connect(str(path), timeout=10)) as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                         "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        return {n: c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def _put_back(store: Path, copy: Optional[Path]) -> str:
    """Trả `store` về `copy` (bản sao trước khi khôi phục); `copy` None nghĩa là trước đó kho không có: gỡ đi. Kiểm lại
    sau khi trả. Trả "" nếu đúng, còn không thì lý do."""
    try:
        _remove_db(store)
        if copy is None:
            return "" if not store.exists() else "vẫn còn file"
        _backup(copy, store)
        return "" if _tables(store) == _tables(copy) else "nội dung khác bản sao"
    except Exception as ex:  # noqa: BLE001
        return f"{type(ex).__name__}: {ex}"


def restore(state_dir: str, *, _fail_on: str = "") -> dict:
    """Khôi phục cả bộ kho trong `state_dir` về trước 0.140.0. Trả {ok, restored, kept, removed, error} và, khi trả về
    không trọn, `unrecoverable` [{store, copy, reason}].

    `_fail_on` (chỉ cho test): tên kho mà bước ghi đè giả lập hỏng TRƯỚC khi ghi."""
    p = plan(state_dir)
    if not p["ok"]:
        return {"ok": False, "restored": [], "kept": [], "removed": [], "error": p["error"]}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kept: dict = {}
    # Bước 2: bản sao bằng chứng của mọi kho hiện có.
    currents = [store for store, _ in p["todo"] if store.is_file()] + ([p["team"]] if p["team"] else [])
    try:
        for store in currents:
            post = store.with_name(f"{store.name}{POST_TAG}{stamp}")
            _backup(store, post)
            kept[store] = post
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "restored": [], "kept": [str(v) for v in kept.values()], "removed": [],
                "error": f"không chép được bản sao trước khi khôi phục: {type(ex).__name__}: {ex}"}
    touched: list = []     # kho CÓ THỂ đã bị đổi: thêm vào TRƯỚC khi bắt đầu đụng tới
    restored, removed = [], []
    try:
        # Bước 3: gỡ kho nhóm khỏi chỗ chạy (đã có bản sao).
        if p["team"]:
            touched.append(p["team"])
            _remove_db(p["team"])
            removed.append(str(p["team"]))
        # Bước 4: ghi đè từng kho bằng snapshot.
        for store, snap in p["todo"]:
            touched.append(store)
            if _fail_on and store.name == _fail_on:
                raise OSError(f"giả lập hỏng khi ghi {store.name}")
            _backup(snap, store)
            restored.append(store)
    except Exception as ex:  # noqa: BLE001
        back, bad = [], []
        for store in touched:
            why = _put_back(store, kept.get(store))
            if why:
                bad.append({"store": str(store), "copy": str(kept[store]) if store in kept else "",
                            "reason": why})
            else:
                back.append(store.name)
        msg = (f"khôi phục dở ở {type(ex).__name__}: {ex}; đã trả về bản trước khi khôi phục: "
               + (", ".join(back) or "không có kho nào cần trả"))
        if bad:
            msg += ("; KHÔNG trả về được: "
                    + ", ".join(f"{b['store']} ({b['reason']}; bản sao: {b['copy'] or 'không có, trước đó chưa có kho'})"
                                for b in bad))
        return {"ok": False, "restored": [], "kept": [str(v) for v in kept.values()], "removed": [],
                "error": msg, "unrecoverable": bad}
    return {"ok": True, "restored": [str(s) for s in restored], "kept": [str(v) for v in kept.values()],
            "removed": removed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Khôi phục kho Kanban, kho phiên và kho nhóm về trước 0.140.0 "
                                             "(chạy khi Javis đã tắt).")
    ap.add_argument("--state-dir", default=os.environ.get("JAVIS_STATE_DIR", ""),
                    help="thư mục state của Javis (mặc định JAVIS_STATE_DIR)")
    ap.add_argument("--yes", action="store_true", help="xác nhận Javis đã tắt và chấp nhận mất thay đổi sau lúc nâng")
    a = ap.parse_args(argv)
    if not a.state_dir:
        print("Thiếu --state-dir (hay biến JAVIS_STATE_DIR).", file=sys.stderr)
        return 2
    if not a.yes:
        print("Tắt Javis trước, rồi chạy lại với --yes. Việc Kanban, tin chat và nhóm sau lúc nâng lên 0.140.0 sẽ không "
              "còn trong kho chạy; bản sao các kho hiện tại được giữ cạnh file kho.", file=sys.stderr)
        return 2
    res = restore(a.state_dir)
    if not res["ok"]:
        print("Không khôi phục được: " + res["error"], file=sys.stderr)
        return 1
    print("Đã khôi phục: " + ", ".join(res["restored"])
          + (". Đã gỡ kho nhóm: " + ", ".join(res["removed"]) if res["removed"] else "")
          + (". Bản sao trước khi khôi phục giữ ở: " + ", ".join(res["kept"]) if res["kept"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
