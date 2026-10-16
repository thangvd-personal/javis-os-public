"""Hạ các mốc team-adaptive (T2 0.154.0, T3 0.156.0) bằng snapshot lúc nâng (thiết kế 2026-10-15-team-adaptive mục 12).

    python tools/team_adaptive_restore_pre.py --to 0.154.0              # chỉ xem kế hoạch, không ghi gì
    python tools/team_adaptive_restore_pre.py --to 0.154.0 --yes
    python tools/team_adaptive_restore_pre.py --to 0.154.0 --state-dir /duong/dan/state --yes

CHẠY KHI JAVIS ĐÃ TẮT. `--to X` đưa mọi kho mà mốc X và các mốc SAU X đụng tới về trạng thái ngay trước khi mã X chạy lần
đầu. Danh sách kho theo mốc nằm ở bảng `MILESTONES`; thêm mốc mới là thêm dòng, không sửa luồng. Mỗi dòng là một trong
ba cách:
- `snapshot`: kho về `<tên>.pre-<mốc>` mà mã mốc đó tạo một lần lúc nâng. Kho đã được nâng (có dấu của mốc) mà thiếu
  snapshot, hay snapshot hỏng hoặc không phải bản trước nâng, là DỪNG trước khi ghi bất cứ gì.
- `snapshot_if_present`: có snapshot thì khôi phục như trên; không có thì để nguyên kho.
- `absent`: trước mốc không có kho này. Kho được cất sang bên (bản sao bằng chứng) rồi gỡ khỏi chỗ chạy.
Một kho có ở nhiều mốc thì dùng mốc SỚM NHẤT không trước X (vd `--to 0.154.0` lấy `resonance.sqlite3.pre-0.154.0`, không
lấy `.pre-0.156.0`). Snapshot của mốc muộn hơn của chính kho đó được đổi tên thành `<snapshot>.superseded-<thời điểm>`
(vẫn giữ), để lần nâng lại chụp một bản mới đúng dòng thời gian thay vì dùng lại bản của lần nâng trước.

Thứ tự, giống `tools/team_chat_restore_pre.py`, để không bao giờ báo thành công cho một lần khôi phục dở:
1. Kiểm trước điều kiện của cả bộ, chưa ghi gì (snapshot đọc được, `PRAGMA quick_check` ok, có bảng gốc, CHƯA có dấu
   của mốc).
2. Chép MỌI kho sắp bị đụng sang `<tên>.post-<X>-<thời điểm>` bằng SQLite backup API (lấy cả phần còn trong WAL).
3. Gỡ kho `absent`, ghi đè kho `snapshot` bằng snapshot, đổi tên snapshot bị thay thế, rồi kiểm lại từng kho (bảng và số
   dòng khớp snapshot, không còn dấu của mốc; kho đã gỡ không còn file).
4. Một bước hỏng (kể cả bước kiểm) thì trả MỌI thứ có thể đã bị đụng về đúng bản ở bước 2 (kho vốn không có thì gỡ đi,
   snapshot đã đổi tên thì đổi lại), kiểm lại sau khi trả, rồi báo lỗi. Một kho được tính là "có thể đã đụng" TRƯỚC khi
   bắt đầu ghi. Trả về không được thì báo rõ kho nào và bản sao nào để cứu tay; không bao giờ nói đã trả về khi chưa.

`--to 0.154.0` mất khỏi kho chạy mọi thay đổi sau lúc nâng của `team.sqlite3`, `conversations.db`, `resonance.sqlite3`
(và `agent_judge.sqlite3` nếu đã lên 0.156.0): tin và phiên chat, reaction ở chat riêng và nhóm, nhóm, sổ lượt, mục tiêu.
`kanban.sqlite3` KHÔNG đổi (mốc này không chụp nó). Giữ: các bản `.post-*` cạnh file kho và snapshot `.pre-*`. Hạ KHÔNG
khôi phục vẫn chạy được: bản cũ bỏ qua bảng và cột mới; reaction của trưởng nhóm/thành viên trong `team_reactions`
(reactor `agent:...`) hiện như reaction thường ở bản cũ.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SNAPSHOT = "snapshot"
IF_PRESENT = "snapshot_if_present"
ABSENT = "absent"


@dataclass(frozen=True)
class Marker:
    """Dấu "kho đã được mốc này nâng". `column` rỗng: chính bảng `table` là dấu. `base`: bảng mà snapshot hợp lệ phải có
    (mặc định là `table` khi dấu là cột). `table` rỗng: chưa biết dấu, chỉ kiểm bảng gốc."""
    table: str = ""
    column: str = ""
    base: str = ""

    def base_table(self) -> str:
        return self.base or (self.table if self.column else "")


@dataclass(frozen=True)
class Entry:
    store: str
    kind: str
    marker: Optional[Marker] = None
    lost: str = ""


# Mốc -> các kho mã của mốc đó nâng. Tên dấu đối chiếu với mã: team_store._T2_REACTION_COLS (source_kind mới ở T2),
# sessions.py bảng dm_reactions, resonance_store.T2_TABLES (agent_calls); bảng gốc theo điều kiện chụp của từng kho.
MILESTONES = {
    "0.154.0": (
        Entry("team.sqlite3", SNAPSHOT, Marker(table="team_reactions", column="source_kind"),
              "nhóm, thành viên, reaction nhóm và sổ lượt nhóm"),
        Entry("conversations.db", SNAPSHOT, Marker(table="dm_reactions", base="messages"),
              "tin và phiên chat (chat riêng lẫn nhóm), reaction chat riêng"),
        Entry("resonance.sqlite3", SNAPSHOT, Marker(table="agent_calls", base="goals"),
              "mục tiêu Cộng hưởng, sổ lượt chung, lý do thức, nhịp agent"),
    ),
    "0.156.0": (
        # T3 tạo kho mới; trước 0.156.0 không có.
        Entry("agent_judge.sqlite3", ABSENT, None, "sổ phán xử và tự rà soát của agent"),
        # T3 có thể chụp `resonance.sqlite3.pre-0.156.0`. Dấu bảng chưa chốt: T3 điền `table` khi có.
        Entry("resonance.sqlite3", IF_PRESENT, Marker(base="goals"),
              "mục tiêu Cộng hưởng và dữ liệu T3 trong kho này"),
    ),
    # T4 (0.170.0) không tạo kho, bảng hay cột nào: chỉ thêm chỉ mục (`team_reactions_status` ở team.sqlite3,
    # `idx_messages_team_author`, `idx_messages_team_decision` ở conversations.db, `pulse_contrib_result` ở
    # resonance.sqlite3). Bản trước (T3, phát hành 0.169.0) bỏ qua chỉ mục lạ, nên `--to 0.170.0` không có gì để khôi phục; hạ thẳng về 0.169.x
    # không cần công cụ. Xem docs/dev/team-chat-t4-ops.md.
    "0.170.0": (),
}


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def _backup(src: Path, dst: Path) -> None:
    with closing(sqlite3.connect(str(src), timeout=10)) as s, closing(sqlite3.connect(str(dst), timeout=10)) as d:
        s.backup(d)
    with closing(sqlite3.connect(str(dst), timeout=10)) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _rename(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _remove_db(path: Path) -> None:
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if p.exists():
            p.unlink()


def _db_gone(path: Path) -> bool:
    return not any(p.exists() for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")))


def _ro(path: Path):
    """Kết nối để đọc một file đã có (chỉ gọi khi file tồn tại, nên không tạo file mới)."""
    return sqlite3.connect(str(path), timeout=10)


def _names(path: Path) -> set:
    with closing(_ro(path)) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _columns(path: Path, table: str) -> set:
    with closing(_ro(path)) as c:
        return {r[1] for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _has_marker(path: Path, m: Marker) -> bool:
    if not m.table:
        return False
    if m.column:
        return m.column in _columns(path, m.table)
    return m.table in _names(path)


def _tables(path: Path) -> dict:
    """{bảng: số dòng} của một kho (để kiểm kho đã khớp bản nguồn)."""
    with closing(_ro(path)) as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                         "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        return {n: c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def _snapshot_problem(snap: Path, m: Optional[Marker], ver: str) -> str:
    """"" nếu `snap` là bản trước mốc `ver` dùng được, còn không thì lý do."""
    try:
        with closing(_ro(snap)) as c:
            qc = c.execute("PRAGMA quick_check").fetchone()
        if not qc or qc[0] != "ok":
            return f"snapshot {snap.name} hỏng (quick_check: {qc[0] if qc else 'không có kết quả'})"
        if m is None:
            return ""
        base = m.base_table()
        if base and base not in _names(snap):
            return f"snapshot {snap.name} không có bảng {base}"
        if _has_marker(snap, m):
            what = f"cột {m.table}.{m.column}" if m.column else f"bảng {m.table}"
            return f"{snap.name} đã có {what}; không phải bản trước {ver}"
        return ""
    except sqlite3.Error as ex:
        return f"snapshot {snap.name} hỏng: {ex}"


def plan(state_dir: str, to: str) -> dict:
    """Bước 1: kiểm cả bộ, không ghi gì. Trả {ok, error, restore: [(kho, snapshot, mốc, mô tả, dấu)],
    remove: [(kho, mô tả)], supersede: [snapshot]}."""
    empty = {"ok": False, "restore": [], "remove": [], "supersede": []}
    if to not in MILESTONES:
        return {**empty, "error": f"không biết mốc {to}; chỉ có: {', '.join(sorted(MILESTONES, key=_ver))}"}
    root = Path(state_dir)
    if not root.is_dir():
        return {**empty, "error": f"không có thư mục state {root}"}
    chosen: dict = {}
    later: dict = {}
    for v in sorted((v for v in MILESTONES if _ver(v) >= _ver(to)), key=_ver):
        for e in MILESTONES[v]:
            if e.store in chosen:
                later.setdefault(e.store, []).append((v, e))
            else:
                chosen[e.store] = (v, e)
    restore, remove, problems = [], [], []
    for name, (v, e) in chosen.items():
        store = root / name
        if e.kind == ABSENT:
            if not _db_gone(store):
                remove.append((store, e.lost))
            continue
        snap = root / f"{name}.pre-{v}"
        if snap.is_file():
            why = _snapshot_problem(snap, e.marker, v)
            if why:
                problems.append(why)
            else:
                restore.append((store, snap, v, e.lost, e.marker))
            continue
        if e.kind == SNAPSHOT and store.is_file() and e.marker is not None:
            try:
                upgraded = _has_marker(store, e.marker)
            except sqlite3.Error as ex:
                problems.append(f"không đọc được {name}: {ex}")
                continue
            if upgraded:
                problems.append(f"{name} đã được {v} nâng mà thiếu snapshot {snap.name} (kho được tạo mới từ {v} trở "
                                "đi thì không có bản trước nâng để về)")
    if problems:
        return {**empty, "error": "; ".join(problems)}
    if not restore and not remove and not chosen:
        return {**empty, "error": f"mốc {to} không nâng kho, bảng hay cột nào (chỉ thêm chỉ mục, bản cũ bỏ qua): không "
                                  "có gì để khôi phục, hạ thẳng về bản trước là đủ"}
    if not restore and not remove:
        return {**empty, "error": f"không có snapshot nào của mốc {to} trở đi và không có kho nào cần gỡ: kho chưa từng "
                                  f"được {to} nâng lên, không có gì để khôi phục"}
    touched = {s.name for s, *_ in restore} | {s.name for s, _ in remove}
    supersede = []
    for name, items in later.items():
        if name not in touched:
            continue
        for v, e in items:
            snap = root / f"{name}.pre-{v}"
            if e.kind in (SNAPSHOT, IF_PRESENT) and snap.is_file():
                supersede.append(snap)
    return {"ok": True, "restore": restore, "remove": remove, "supersede": supersede, "error": ""}


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.name}-{n}").exists():
        n += 1
    return path.with_name(f"{path.name}-{n}")


def _stat(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _put_back(store: Path, copy: Optional[Path], before: Optional[tuple] = None) -> str:
    """Trả `store` về `copy` (bản sao trước khi khôi phục); `copy` None nghĩa là trước đó kho không có: gỡ đi. Kiểm lại
    sau khi trả. Trả "" nếu đúng, còn không thì lý do.

    `before`: (cỡ, mtime) của file kho ngay sau bước chép bản sao. Không gỡ được file kho (vd tiến trình khác đang giữ)
    mà file vẫn y nguyên cỡ và mtime đó thì kho chưa bị ghi: `_remove_db` gỡ file chính TRƯỚC, nên hỏng ở đó là chưa gỡ gì."""
    try:
        try:
            _remove_db(store)
        except OSError:
            if copy is not None and before is not None and _stat(store) == before:
                return ""
            raise
        if copy is None:
            return "" if _db_gone(store) else "vẫn còn file"
        _backup(copy, store)
        return "" if _tables(store) == _tables(copy) else "nội dung khác bản sao"
    except Exception as ex:  # noqa: BLE001
        return f"{type(ex).__name__}: {ex}"


def _rename_back(src: Path, aside: Path) -> str:
    try:
        if aside.exists() and not src.exists():
            _rename(aside, src)
        return "" if src.is_file() and not aside.exists() else "snapshot chưa về tên cũ"
    except Exception as ex:  # noqa: BLE001
        return f"{type(ex).__name__}: {ex}"


def _hint(ex: Exception) -> str:
    """File bị tiến trình khác giữ (thường là Javis chưa tắt hẳn): nói thẳng cách sửa."""
    if isinstance(ex, PermissionError) or getattr(ex, "winerror", None) == 32 or "locked" in str(ex).lower():
        return " (file đang bị tiến trình khác giữ: Javis còn chạy? Tắt hẳn Javis rồi chạy lại)"
    return ""


def restore(state_dir: str, to: str, *, _fail_on: str = "") -> dict:
    """Khôi phục cả bộ kho trong `state_dir` về trước mốc `to`. Trả {ok, restored, removed, superseded, kept, error} và,
    khi trả về không trọn, `unrecoverable` [{store, copy, reason}].

    `_fail_on` (chỉ cho test): tên file (kho hay snapshot bị thay thế) mà bước ghi giả lập hỏng TRƯỚC khi ghi."""
    p = plan(state_dir, to)
    out = {"ok": False, "restored": [], "removed": [], "superseded": [], "kept": []}
    if not p["ok"]:
        return {**out, "error": p["error"]}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kept: dict = {}
    # Bước 2: bản sao bằng chứng của mọi kho sắp bị đụng mà đang có.
    currents = [s for s, *_ in p["restore"] if s.is_file()] + [s for s, _ in p["remove"] if s.is_file()]
    try:
        for store in currents:
            post = _unique(store.with_name(f"{store.name}.post-{to}-{stamp}"))
            _backup(store, post)
            kept[store] = post
    except Exception as ex:  # noqa: BLE001
        return {**out, "kept": [str(v) for v in kept.values()],
                "error": f"không chép được bản sao trước khi khôi phục: {type(ex).__name__}: {ex}{_hint(ex)}"}
    stats = {store: _stat(store) for store in kept}
    touched: list = []     # ("store", kho) hay ("rename", snapshot, tên mới): thêm vào TRƯỚC khi đụng tới
    restored, removed, superseded = [], [], []
    try:
        # Bước 3a: gỡ kho vốn không có trước mốc (đã có bản sao).
        for store, _ in p["remove"]:
            touched.append(("store", store))
            if _fail_on and store.name == _fail_on:
                raise OSError(f"giả lập hỏng khi gỡ {store.name}")
            _remove_db(store)
            removed.append(store)
        # Bước 3b: ghi đè từng kho bằng snapshot.
        for store, snap, _, _, marker in p["restore"]:
            touched.append(("store", store))
            if _fail_on and store.name == _fail_on:
                raise OSError(f"giả lập hỏng khi ghi {store.name}")
            _remove_db(store)
            _backup(snap, store)
            restored.append((store, snap, marker))
        # Bước 3c: snapshot của mốc muộn hơn không còn đúng dòng thời gian: đổi tên, giữ lại.
        for snap in p["supersede"]:
            aside = _unique(snap.with_name(f"{snap.name}.superseded-{stamp}"))
            touched.append(("rename", snap, aside))
            if _fail_on and snap.name == _fail_on:
                raise OSError(f"giả lập hỏng khi đổi tên {snap.name}")
            _rename(snap, aside)
            superseded.append(aside)
        # Bước 3d: kiểm lại; sai là lỗi, trả về như mọi lỗi khác.
        for store, snap, marker in restored:
            if _tables(store) != _tables(snap):
                raise RuntimeError(f"kiểm sau khi ghi: {store.name} khác snapshot {snap.name}")
            if marker is not None and _has_marker(store, marker):
                raise RuntimeError(f"kiểm sau khi ghi: {store.name} vẫn còn dấu của mốc")
        for store in removed:
            if not _db_gone(store):
                raise RuntimeError(f"kiểm sau khi gỡ: {store.name} vẫn còn file")
    except Exception as ex:  # noqa: BLE001
        back, bad = [], []
        for item in reversed(touched):
            if item[0] == "rename":
                why = _rename_back(item[1], item[2])
                target, copy = item[1], item[2]
            else:
                target = item[1]
                copy = kept.get(target)
                why = _put_back(target, copy, stats.get(target))
            if why:
                bad.append({"store": str(target), "copy": str(copy) if copy else "", "reason": why})
            else:
                back.append(target.name)
        msg = (f"khôi phục dở ở {type(ex).__name__}: {ex}{_hint(ex)}; đã trả về bản trước khi khôi phục: "
               + (", ".join(back) or "không có gì cần trả"))
        if bad:
            msg += ("; KHÔNG trả về được: "
                    + ", ".join(f"{b['store']} ({b['reason']}; bản sao: {b['copy'] or 'không có, trước đó chưa có kho'})"
                                for b in bad))
        return {**out, "kept": [str(v) for v in kept.values()], "error": msg, "unrecoverable": bad}
    return {"ok": True, "restored": [str(s) for s, _, _ in restored], "removed": [str(s) for s in removed],
            "superseded": [str(s) for s in superseded], "kept": [str(v) for v in kept.values()], "error": ""}


def describe(p: dict, to: str) -> str:
    """Kế hoạch dạng chữ cho người dùng (chưa ghi gì)."""
    lines = [f"Kế hoạch khôi phục về trước {to}:"]
    for store, snap, _, lost, _ in p["restore"]:
        lines.append(f"- {store.name}: ghi đè bằng {snap.name}; mất khỏi kho chạy: {lost} sau lúc nâng")
    for store, lost in p["remove"]:
        lines.append(f"- {store.name}: cất bản sao rồi gỡ khỏi chỗ chạy (trước mốc không có kho này); mất: {lost}")
    for snap in p["supersede"]:
        lines.append(f"- {snap.name}: đổi tên thành {snap.name}.superseded-<thời điểm> (giữ lại, lần nâng sau chụp mới)")
    lines.append(f"Mọi kho bị đụng được chép sang <tên>.post-{to}-<thời điểm> trước; snapshot .pre-* được giữ.")
    lines.append("kanban.sqlite3 không đổi. Reaction của agent (reactor agent:...) không còn sau khôi phục; nếu chỉ hạ "
                 "phiên bản mà không khôi phục, bản cũ hiện chúng như reaction thường.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Khôi phục các kho team-adaptive về trước một mốc (chạy khi Javis đã tắt).")
    ap.add_argument("--to", required=True, choices=sorted(MILESTONES, key=_ver),
                    help="mốc cần về trước (vd 0.154.0)")
    ap.add_argument("--state-dir", default=os.environ.get("JAVIS_STATE_DIR", ""),
                    help="thư mục state của Javis (mặc định JAVIS_STATE_DIR)")
    ap.add_argument("--yes", action="store_true", help="xác nhận Javis đã tắt và chấp nhận mất thay đổi sau lúc nâng")
    a = ap.parse_args(argv)
    if not a.state_dir:
        print("Thiếu --state-dir (hay biến JAVIS_STATE_DIR).", file=sys.stderr)
        return 2
    p = plan(a.state_dir, a.to)
    if not p["ok"]:
        print("Không khôi phục được (chưa ghi gì): " + p["error"], file=sys.stderr)
        return 1
    if not a.yes:
        print(describe(p, a.to))
        print("Chưa ghi gì. Tắt Javis trước, rồi chạy lại với --yes.", file=sys.stderr)
        return 2
    res = restore(a.state_dir, a.to)
    if not res["ok"]:
        print("Không khôi phục được: " + res["error"]
              + (". Bản sao trước khi khôi phục giữ ở: " + ", ".join(res["kept"]) if res["kept"] else ""),
              file=sys.stderr)
        return 1
    parts = []
    if res["restored"]:
        parts.append("Đã khôi phục: " + ", ".join(res["restored"]))
    if res["removed"]:
        parts.append("Đã gỡ khỏi chỗ chạy: " + ", ".join(res["removed"]))
    if res["superseded"]:
        parts.append("Snapshot của mốc sau đã đổi tên: " + ", ".join(res["superseded"]))
    if res["kept"]:
        parts.append("Bản sao trước khi khôi phục giữ ở: " + ", ".join(res["kept"]))
    print(". ".join(parts) + ". Giờ có thể chạy bản cũ hơn " + a.to + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
