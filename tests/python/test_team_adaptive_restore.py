"""Công cụ hạ các mốc team-adaptive: `tools/team_adaptive_restore_pre.py` (thiết kế 2026-10-15-team-adaptive mục 12).

    python tests/run.py team_adaptive_restore -v

Phần lớn ca dựng kho tổng hợp (bảng nhỏ, cột dấu thật) để kiểm luật của công cụ, soi theo ca R6/R9 của
`team_chat_restore_pre.py`: chạy đúng, thiếu snapshot thì dừng trước khi ghi, snapshot hỏng, lỗi giữa chừng trả MỌI kho đã
đụng, giữ bản sao `.post-*`, chạy lại, `--state-dir`. Ca cuối nâng THẬT: mã 0.151.0 (`1d1b52fa`, qua `git show`) lập ba kho,
mã nhánh này nâng (tạo snapshot thật), công cụ hạ, mã 0.151.0 mở lại được. Không gọi model. Máy không có lịch sử git của
commit đó thì ca cuối in SKIP; trên CI (biến CI) thì ĐỎ.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-ta-restore-")
TEMP = Path(tempfile.mkdtemp(prefix="ta-restore-"))

sys.path.insert(0, str(ROOT / "tools"))
import team_adaptive_restore_pre as RT  # noqa: E402

fails = []


def check(name, cond, info=None):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or info is None else f"  ({str(info)[:400]})"))
    if not cond:
        fails.append(name)


# ---------- kho tổng hợp ----------
# (kho, bảng gốc, dấu 0.154.0: ("col", bảng, cột) hay ("table", bảng))
SPEC = {
    "team.sqlite3": ("team_reactions", ("col", "team_reactions", "source_kind")),
    "conversations.db": ("messages", ("table", "dm_reactions")),
    "resonance.sqlite3": ("goals", ("table", "agent_calls")),
}
STORES = list(SPEC)


def make_db(path, base, v, marker=None):
    db = sqlite3.connect(path)
    extra = f", {marker[2]} TEXT" if marker and marker[0] == "col" else ""
    db.execute(f"CREATE TABLE {base}(id INTEGER PRIMARY KEY, v INTEGER{extra})")
    db.execute(f"INSERT INTO {base}(v) VALUES (?)", (v,))
    if marker and marker[0] == "table":
        db.execute(f"CREATE TABLE {marker[1]}(id INTEGER PRIMARY KEY)")
        db.execute(f"INSERT INTO {marker[1]}(id) VALUES (1)")
    db.commit()
    db.close()


def fixture(label, *, judge=False, pre156=False, skip=(), no_snapshot=(), absent=()):
    """Kho đã nâng (v=2, có dấu) và snapshot .pre-0.154.0 (v=1, chưa dấu). `judge`: có agent_judge.sqlite3 (v=2).
    `pre156`: có resonance.sqlite3.pre-0.156.0 (v=3, đã có dấu 0.154.0 vì chụp sau mốc đó)."""
    d = TEMP / label
    d.mkdir()
    for name, (base, marker) in SPEC.items():
        if name in skip:
            continue
        if name not in absent:
            make_db(d / name, base, 2, marker)
        if name not in no_snapshot:
            make_db(d / (name + ".pre-0.154.0"), base, 1)
    if judge:
        make_db(d / "agent_judge.sqlite3", "judgments", 2)
    if pre156:
        make_db(d / "resonance.sqlite3.pre-0.156.0", "goals", 3, ("table", "agent_calls"))
    return d


def val(p, table):
    if not p.exists():
        return None
    db = sqlite3.connect(p)
    try:
        return db.execute(f"SELECT v FROM {table}").fetchone()[0]
    finally:
        db.close()


def live(d):
    out = {name: val(d / name, SPEC[name][0]) for name in STORES}
    out["judge"] = val(d / "agent_judge.sqlite3", "judgments")
    return out


def has_marker(p, name):
    m = SPEC[name][1]
    db = sqlite3.connect(p)
    try:
        if m[0] == "col":
            return m[2] in {r[1] for r in db.execute(f"PRAGMA table_info({m[1]})").fetchall()}
        return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (m[1],)).fetchone() is not None
    finally:
        db.close()


def listing(d):
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir()}


def posts(d, ver="0.154.0"):
    return sorted(p.name.split(".post-")[0] for p in d.iterdir() if f".post-{ver}-" in p.name)


def names_with(d, part):
    return sorted(p.name for p in d.iterdir() if part in p.name)


PRE = {"team.sqlite3": 2, "conversations.db": 2, "resonance.sqlite3": 2, "judge": None}
RESTORED = {"team.sqlite3": 1, "conversations.db": 1, "resonance.sqlite3": 1, "judge": None}


def section_happy():
    d = fixture("happy")
    res = RT.restore(str(d), "0.154.0")
    check("chạy đúng --to 0.154.0: ok, ba kho về snapshot", res["ok"] and live(d) == RESTORED, (res, live(d)))
    check("chạy đúng: không còn dấu 0.154.0 ở kho nào", not any(has_marker(d / n, n) for n in STORES))
    check("chạy đúng: giữ bản sao .post-0.154.0 của cả ba kho, nội dung là bản sau nâng",
          posts(d) == sorted(STORES) and all(val(Path(k), SPEC[Path(k).name.split(".post-")[0]][0]) == 2
                                             for k in res["kept"]), (res, posts(d)))
    check("chạy đúng: snapshot .pre-0.154.0 vẫn giữ", len(names_with(d, ".pre-0.154.0")) == 3, names_with(d, ".pre-"))
    check("chạy đúng: không gỡ, không đổi tên gì", res["removed"] == [] and res["superseded"] == [], res)

    # Đã lên 0.156.0: kho phán xử bị cất, resonance lấy bản .pre-0.154.0 (không lấy .pre-0.156.0), bản 0.156.0 đổi tên.
    d = fixture("happy-156", judge=True, pre156=True)
    res = RT.restore(str(d), "0.154.0")
    check("--to 0.154.0 khi đã lên 0.156.0: ok, ba kho về .pre-0.154.0, gỡ agent_judge.sqlite3",
          res["ok"] and live(d) == RESTORED and not (d / "agent_judge.sqlite3").exists(), (res, live(d)))
    check("--to 0.154.0 khi đã lên 0.156.0: kho phán xử có bản sao .post", "agent_judge.sqlite3" in posts(d)
          and val(next(p for p in d.iterdir() if p.name.startswith("agent_judge.sqlite3.post-")), "judgments") == 2,
          posts(d))
    check("--to 0.154.0 khi đã lên 0.156.0: .pre-0.156.0 đổi tên superseded, vẫn giữ",
          not (d / "resonance.sqlite3.pre-0.156.0").exists() and len(res["superseded"]) == 1
          and Path(res["superseded"][0]).is_file() and ".superseded-" in res["superseded"][0], res)

    d = fixture("to-156", judge=True, pre156=True)
    res = RT.restore(str(d), "0.156.0")
    check("--to 0.156.0: gỡ kho phán xử, resonance về .pre-0.156.0, team và phiên không đổi",
          res["ok"] and live(d) == {"team.sqlite3": 2, "conversations.db": 2, "resonance.sqlite3": 3, "judge": None}
          and res["superseded"] == [] and len(names_with(d, ".pre-0.154.0")) == 3, (res, live(d)))
    check("--to 0.156.0: bản sao chỉ của hai kho bị đụng",
          posts(d, "0.156.0") == ["agent_judge.sqlite3", "resonance.sqlite3"], posts(d, "0.156.0"))

    d = fixture("to-156-nosnap", judge=True)
    res = RT.restore(str(d), "0.156.0")
    check("--to 0.156.0 không có .pre-0.156.0: chỉ gỡ kho phán xử, resonance để nguyên",
          res["ok"] and live(d) == {**PRE, "judge": None} and res["restored"] == [], (res, live(d)))

    d = fixture("to-156-nothing")
    before = listing(d)
    res = RT.restore(str(d), "0.156.0")
    check("--to 0.156.0 chưa từng lên 0.156.0: báo không có gì, không ghi gì",
          res["ok"] is False and "không có gì để khôi phục" in res["error"] and listing(d) == before, res)

    # Đích vốn không có (kho phiên mất, chỉ còn snapshot): khôi phục tạo kho từ snapshot.
    d = fixture("absent-target", absent=("conversations.db",))
    res = RT.restore(str(d), "0.154.0")
    check("đích vốn không có: tạo kho từ snapshot, cả bộ về bản trước nâng; không có bản sao của kho không có",
          res["ok"] and live(d) == RESTORED and "conversations.db" not in posts(d), (res, live(d), posts(d)))


def section_missing_invalid():
    for name in STORES:
        d = fixture(f"missing-{name}", judge=True, pre156=True, no_snapshot=(name,))
        before = listing(d)
        res = RT.restore(str(d), "0.154.0")
        check(f"thiếu snapshot {name}: dừng, báo rõ kho thiếu", res["ok"] is False and name in res["error"]
              and "thiếu snapshot" in res["error"], res)
        check(f"thiếu snapshot {name}: không ghi gì (không bản sao, không gỡ kho phán xử, không đổi tên)",
              listing(d) == before and res["kept"] == [], sorted(set(listing(d)) ^ set(before)))

    # Kho chưa từng được nâng và không có snapshot: không phải lỗi, chỉ là không cần đụng.
    d = fixture("never-upgraded", skip=("team.sqlite3",))
    res = RT.restore(str(d), "0.154.0")
    check("kho nhóm chưa từng có: hai kho kia vẫn khôi phục, kho nhóm không bị tạo",
          res["ok"] and not (d / "team.sqlite3").exists() and live(d)["conversations.db"] == 1, (res, live(d)))

    cases = {
        "garbage": lambda p: p.write_bytes(b"day khong phai sqlite" * 50),
        "upgraded": lambda p: (p.unlink(), make_db(p, "messages", 1, ("table", "dm_reactions"))),
        "no-base": lambda p: (p.unlink(), make_db(p, "other", 1)),
    }
    for label, spoil in cases.items():
        d = fixture(f"invalid-{label}", judge=True, pre156=True)
        spoil(d / "conversations.db.pre-0.154.0")
        before = listing(d)
        res = RT.restore(str(d), "0.154.0")
        check(f"snapshot hỏng ({label}): dừng, báo tên snapshot", res["ok"] is False
              and "conversations.db.pre-0.154.0" in res["error"], res)
        check(f"snapshot hỏng ({label}): không ghi gì", listing(d) == before, sorted(set(listing(d)) ^ set(before)))

    # Snapshot T2 của nhóm vẫn mang cột source_kind là bản sai.
    d = fixture("invalid-team-col")
    (d / "team.sqlite3.pre-0.154.0").unlink()
    make_db(d / "team.sqlite3.pre-0.154.0", "team_reactions", 1, ("col", "team_reactions", "source_kind"))
    res = RT.restore(str(d), "0.154.0")
    check("snapshot nhóm đã có cột source_kind: dừng, nói rõ không phải bản trước 0.154.0",
          res["ok"] is False and "source_kind" in res["error"] and "0.154.0" in res["error"], res)

    # Kho mới tinh của bản đã nâng (không có snapshot nào): báo rõ.
    d = fixture("fresh", no_snapshot=tuple(STORES))
    res = RT.restore(str(d), "0.154.0")
    check("kho tạo mới từ 0.154.0, không snapshot: dừng, báo rõ", res["ok"] is False and "thiếu snapshot" in res["error"],
          res)
    res = RT.restore(str(TEMP / "khong-co"), "0.154.0")
    check("thư mục state không có: báo rõ", res["ok"] is False and "không có thư mục state" in res["error"], res)
    res = RT.restore(str(d), "0.150.0")
    check("mốc lạ: báo rõ các mốc có", res["ok"] is False and "0.154.0" in res["error"] and "0.156.0" in res["error"],
          res)


def section_failures():
    original_backup = RT._backup

    def after_write(target):
        def fake(src, dst):
            original_backup(src, dst)
            if src.name == target + ".pre-0.154.0":
                raise sqlite3.OperationalError("injected disk I/O error after backup")
        return fake

    def intact(d, label):
        state = live(d)
        ok = (state == {**PRE, "judge": 2} and (d / "resonance.sqlite3.pre-0.156.0").is_file()
              and names_with(d, ".superseded-") == [] and len(names_with(d, ".pre-0.154.0")) == 3
              and sorted(posts(d)) == sorted(STORES + ["agent_judge.sqlite3"])
              and all(has_marker(d / n, n) for n in STORES))
        return ok, (label, state, names_with(d, ".superseded-"), posts(d))

    for target in STORES:
        d = fixture(f"after-{target}", judge=True, pre156=True)
        with patch.object(RT, "_backup", side_effect=after_write(target)):
            res = RT.restore(str(d), "0.154.0")
        ok, info = intact(d, target)
        check(f"lỗi SAU khi ghi {target}: ok=false, MỌI kho về đúng trước khi khôi phục, kho phán xử trở lại, giữ bản sao",
              res["ok"] is False and res["restored"] == [] and ok, (res, info))
        d = fixture(f"before-{target}", judge=True, pre156=True)
        res = RT.restore(str(d), "0.154.0", _fail_on=target)
        ok, info = intact(d, target)
        check(f"lỗi TRƯỚC khi ghi {target}: ok=false, MỌI kho về đúng trước khi khôi phục", res["ok"] is False and ok,
              (res, info))

    d = fixture("fail-judge", judge=True, pre156=True)
    res = RT.restore(str(d), "0.154.0", _fail_on="agent_judge.sqlite3")
    ok, info = intact(d, "judge")
    check("lỗi khi gỡ kho phán xử: ok=false, kho phán xử trở lại từ bản sao", res["ok"] is False and ok, (res, info))

    orig_remove = RT._remove_db
    seen = {"n": 0}

    def flaky_remove(path):
        if path.name == "agent_judge.sqlite3" and seen["n"] == 0:
            seen["n"] += 1
            raise PermissionError("file đang bị khoá")
        return orig_remove(path)
    d = fixture("lock-judge", judge=True, pre156=True)
    with patch.object(RT, "_remove_db", side_effect=flaky_remove):
        res = RT.restore(str(d), "0.154.0")
    ok, info = intact(d, "lock")
    check("kho phán xử bị khoá khi gỡ: ok=false, cả bộ nguyên", res["ok"] is False and ok, (res, info))

    # Javis chưa tắt: file kho bị giữ, không gỡ được. Kho chưa bị ghi nên tính là đã về đúng; lỗi gợi ý tắt Javis.
    def locked(path):
        if path.name == "team.sqlite3":
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return orig_remove(path)
    d = fixture("locked-team", judge=True, pre156=True)
    with patch.object(RT, "_remove_db", side_effect=locked):
        res = RT.restore(str(d), "0.154.0")
    ok, info = intact(d, "locked")
    check("kho nhóm bị tiến trình khác giữ: ok=false, kho chưa bị ghi không tính là mất, lỗi gợi ý tắt Javis",
          res["ok"] is False and ok and not res.get("unrecoverable") and "Javis còn chạy" in res["error"], (res, info))

    d = fixture("fail-rename", judge=True, pre156=True)
    res = RT.restore(str(d), "0.154.0", _fail_on="resonance.sqlite3.pre-0.156.0")
    ok, info = intact(d, "rename-before")
    check("lỗi trước khi đổi tên snapshot 0.156.0: cả bộ về như cũ", res["ok"] is False and ok, (res, info))

    orig_rename = RT._rename

    def rename_then_fail(src, dst):
        orig_rename(src, dst)
        if ".superseded-" in dst.name:
            raise OSError("injected error after rename")
    d = fixture("fail-after-rename", judge=True, pre156=True)
    with patch.object(RT, "_rename", side_effect=rename_then_fail):
        res = RT.restore(str(d), "0.154.0")
    ok, info = intact(d, "rename-after")
    check("lỗi SAU khi đổi tên snapshot 0.156.0: đổi lại tên cũ, cả bộ về như cũ", res["ok"] is False and ok,
          (res, info))

    # Bước kiểm sau khi ghi phát hiện sai: cũng trả về hết, không báo thành công.
    orig_tables = RT._tables

    def lying_tables(path):
        out = orig_tables(path)
        if path.name == "resonance.sqlite3":
            out = {**out, "goals": -1}
        return out
    d = fixture("fail-verify", judge=True, pre156=True)
    with patch.object(RT, "_tables", side_effect=lying_tables):
        res = RT.restore(str(d), "0.154.0")
    ok, info = intact(d, "verify")
    check("bước kiểm sau khi ghi thấy sai: ok=false, nói rõ, MỌI kho về như cũ",
          res["ok"] is False and "kiểm sau khi ghi" in res["error"] and ok, (res, info))

    # Đích vốn không có, lỗi sau khi ghi: kho đó lại không có.
    d = fixture("absent-fail", absent=("conversations.db",))
    with patch.object(RT, "_backup", side_effect=after_write("conversations.db")):
        res = RT.restore(str(d), "0.154.0")
    check("đích vốn không có, lỗi sau khi ghi: kho đó lại không có, hai kho kia về đúng",
          res["ok"] is False and live(d) == {**PRE, "conversations.db": None}, (res, live(d)))

    # Bản sao không chép được: dừng trước khi đụng kho nào.
    d = fixture("post-fail", judge=True)

    def post_fail(src, dst):
        if ".post-" in dst.name and src.name == "resonance.sqlite3":
            raise OSError("đĩa đầy")
        original_backup(src, dst)
    with patch.object(RT, "_backup", side_effect=post_fail):
        res = RT.restore(str(d), "0.154.0")
    check("không chép được bản sao trước khi khôi phục: dừng, mọi kho còn nguyên",
          res["ok"] is False and "bản sao" in res["error"] and live(d) == {**PRE, "judge": 2}, (res, live(d)))

    # Không trả về được: báo rõ kho, lý do, bản sao; không nói đã trả về kho đó.
    d = fixture("unrecoverable", judge=True)

    def broken(src, dst):
        if ".post-0.154.0-" in src.name and dst.name == "conversations.db":
            raise OSError("không đọc được bản sao")
        original_backup(src, dst)
        if src.name == "resonance.sqlite3.pre-0.154.0":
            raise sqlite3.OperationalError("injected disk I/O error after backup")
    with patch.object(RT, "_backup", side_effect=broken):
        res = RT.restore(str(d), "0.154.0")
    bad = res.get("unrecoverable") or []
    returned = res["error"].split("đã trả về bản trước khi khôi phục: ")[-1].split(";")[0]
    check("không trả về được: báo rõ kho, lý do và đường dẫn bản sao; bản sao còn nguyên",
          res["ok"] is False and [Path(b["store"]).name for b in bad] == ["conversations.db"]
          and "KHÔNG trả về được" in res["error"] and Path(bad[0]["copy"]).is_file()
          and "conversations.db" not in returned and "team.sqlite3" in returned, res)

    # Chạy lại sau một lần hỏng: xong cả bộ.
    d = fixture("rerun-after-fail", judge=True, pre156=True)
    RT.restore(str(d), "0.154.0", _fail_on="resonance.sqlite3")
    res = RT.restore(str(d), "0.154.0")
    check("chạy lại sau lần hỏng: khôi phục cả bộ xong, bản sao của lần hỏng vẫn còn",
          res["ok"] and live(d) == RESTORED and len(names_with(d, "team.sqlite3.post-0.154.0-")) == 2, (res, live(d)))


def section_rerun_cli():
    d = fixture("rerun", judge=True, pre156=True)
    r1 = RT.restore(str(d), "0.154.0")
    first_posts = set(names_with(d, ".post-0.154.0-"))
    r2 = RT.restore(str(d), "0.154.0")
    check("chạy lại sau khi xong: vẫn ok, kết quả y hệt, không gỡ hay đổi tên thêm",
          r1["ok"] and r2["ok"] and live(d) == RESTORED and r2["removed"] == [] and r2["superseded"] == [], r2)
    check("chạy lại: bản sao của lần đầu không bị ghi đè (tên khác nhau dù cùng giây)",
          first_posts <= set(names_with(d, ".post-0.154.0-"))
          and len(names_with(d, ".post-0.154.0-")) == len(first_posts) + 3
          and all(val(d / n, "judgments") == 2 for n in first_posts if n.startswith("agent_judge")),
          names_with(d, ".post-"))
    check("chạy lại: snapshot .pre-0.154.0 vẫn giữ", len(names_with(d, ".pre-0.154.0")) >= 3, names_with(d, ".pre-"))

    def run(argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        old = os.environ.get("JAVIS_STATE_DIR")
        if env is not None:
            os.environ["JAVIS_STATE_DIR"] = env
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = RT.main(argv)
                except SystemExit as ex:
                    code = ex.code
        finally:
            if old is None:
                os.environ.pop("JAVIS_STATE_DIR", None)
            else:
                os.environ["JAVIS_STATE_DIR"] = old
        return code, out.getvalue(), err.getvalue()

    d = fixture("cli", judge=True, pre156=True)
    before = listing(d)
    code, out, err = run(["--to", "0.154.0", "--state-dir", str(d)])
    check("CLI không --yes: mã 2, in kế hoạch, nhắc tắt Javis, không ghi gì",
          code == 2 and "agent_judge.sqlite3" in out and "conversations.db.pre-0.154.0" in out
          and "Tắt Javis" in err and listing(d) == before, (code, out, err))
    check("CLI kế hoạch nhắc reaction của agent và kho Kanban không đổi",
          "agent:" in out and "kanban.sqlite3" in out, out)
    code, out, err = run(["--to", "0.154.0", "--state-dir", str(d), "--yes"])
    check("CLI --state-dir --yes: mã 0, báo đã khôi phục và nơi giữ bản sao",
          code == 0 and live(d) == RESTORED and "Đã khôi phục" in out and ".post-0.154.0-" in out, (code, out, err))

    d = fixture("cli-env")
    code, out, err = run(["--to", "0.154.0", "--yes"], env=str(d))
    check("CLI không --state-dir: dùng JAVIS_STATE_DIR", code == 0 and live(d) == RESTORED, (code, out, err))
    code, out, err = run(["--to", "0.154.0", "--yes"], env="")
    check("CLI thiếu thư mục state: mã 2, nói rõ", code == 2 and "--state-dir" in err, (code, err))
    code, out, err = run(["--to", "0.153.0", "--state-dir", str(d), "--yes"])
    check("CLI mốc lạ: argparse từ chối (mã 2)", code == 2, (code, err))
    d = fixture("cli-missing", no_snapshot=("resonance.sqlite3",))
    before = listing(d)
    code, out, err = run(["--to", "0.154.0", "--state-dir", str(d), "--yes"])
    check("CLI thiếu snapshot: mã 1, nói rõ chưa ghi gì", code == 1 and "chưa ghi gì" in err and listing(d) == before,
          (code, err))
    for text in (out, err, Path(RT.__file__).read_text(encoding="utf-8")):
        if chr(0x2014) in text or chr(0x2013) in text:
            check("không có em dash/en dash trong công cụ và thông báo", False)
            break
    else:
        check("không có em dash/en dash trong công cụ và thông báo", True)


def section_markers_match_code():
    """Tên dấu trong bảng của công cụ phải khớp mã thật (đổi tên cột/bảng ở server thì test này đỏ)."""
    src = {n: (SERVER / n).read_text(encoding="utf-8") for n in ("team_store.py", "sessions.py", "resonance_store.py")}
    m = {e.store: e for e in RT.MILESTONES["0.154.0"]}
    check("dấu nhóm khớp mã: team_reactions.source_kind là cột T2",
          '("team_reactions", "source_kind",' in src["team_store.py"] and m["team.sqlite3"].marker.column == "source_kind")
    check("dấu phiên khớp mã: bảng dm_reactions, chụp khi có messages",
          'snapshot_before_table(self.db_path, "messages", "dm_reactions"' in src["sessions.py"]
          and m["conversations.db"].marker.table == "dm_reactions" and m["conversations.db"].marker.base == "messages")
    check("dấu Cộng hưởng khớp mã: bảng agent_calls, chụp khi có goals",
          'self._backup_before("agent_calls", T2_BACKUP_SUFFIX)' in src["resonance_store.py"]
          and '"goals" not in names' in src["resonance_store.py"] and m["resonance.sqlite3"].marker.table == "agent_calls")
    check("hậu tố snapshot của ba kho là .pre-0.154.0",
          all('".pre-0.154.0"' in s for s in src.values()))


# ---------- nâng THẬT bằng mã 0.151.0 rồi hạ ----------
OLD_SHA = "1d1b52faed35a79aa5597f1fbde85b63da2bc3ea"


def section_real_upgrade():
    try:
        srcs = {n: subprocess.check_output(["git", "-C", str(ROOT), "show", f"{OLD_SHA}:server/{n}.py"],
                                           stderr=subprocess.DEVNULL).decode("utf-8")
                for n in ("team_store", "sessions", "resonance_store")}
    except (subprocess.CalledProcessError, OSError):
        if os.environ.get("CI"):
            check(f"đọc được mã {OLD_SHA}: CI phải fetch commit này", False)
        else:
            print(f"SKIP máy này không có lịch sử git của {OLD_SHA}")
        return
    mdir = Path(tempfile.mkdtemp(prefix="old-0151-"))

    def load(name, src):
        f = mdir / f"{name}_0151.py"
        f.write_text(src, encoding="utf-8", newline="\n")
        spec = importlib.util.spec_from_file_location(f"{name}_0151", f)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod     # dataclass của mã cũ tra module theo tên
        spec.loader.exec_module(mod)
        return mod

    OTS, OSS, ORS = load("team_store", srcs["team_store"]), load("sessions", srcs["sessions"]), \
        load("resonance_store", srcs["resonance_store"])
    import resonance_store as NRS
    import sessions as NSS
    import team_store as NTS
    check("nạp đúng mã 0.151.0: chưa có dm_reactions, chưa có agent_calls",
          "dm_reactions" not in srcs["sessions"] and "agent_calls" not in srcs["resonance_store"]
          and "source_kind" not in srcs["team_store"])

    st = TEMP / "real"
    st.mkdir()
    tdb, sdb, rdb = st / "team.sqlite3", st / "conversations.db", st / "resonance.sqlite3"
    # 1. Mã 0.151.0 lập ba kho, mỗi kho một dòng cũ.
    oss = OSS.SessionStore(sdb)
    sid = oss.create_session(brain="brain")
    oss.append_message(sid, "user", "tin cũ trước nâng")
    oss.close()
    OTS.TeamStore(tdb)
    with closing(sqlite3.connect(tdb)) as c, c:
        c.execute("INSERT INTO team_reactions(session_id,message_id,reactor,team_id,agent_key,value,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?)", (sid, 1, "owner", "tm_old", "a1", "like", 1.0, 1.0))
    ORS.GoalStore(rdb)
    for p in (tdb, sdb, rdb):
        check(f"0.151.0 lập {p.name}", p.is_file() and p.stat().st_size > 0)
    # 2. Mã nhánh này nâng: mỗi kho có đúng một snapshot .pre-0.154.0.
    nss = NSS.SessionStore(sdb)
    nss.append_message(sid, "user", "tin mới sau nâng")
    NTS.TeamStore(tdb)
    with closing(sqlite3.connect(tdb)) as c, c:
        c.execute("INSERT INTO team_reactions(session_id,message_id,reactor,team_id,agent_key,value,created_at,updated_at,"
                  "source_kind) VALUES (?,?,?,?,?,?,?,?,?)", (sid, 2, "agent:a2", "tm_old", "a2", "love", 2.0, 2.0,
                                                               "agent"))
    NRS.GoalStore(rdb)
    nss.close()
    snaps = sorted(p.name for p in st.iterdir() if p.name.endswith(".pre-0.154.0"))
    check("nâng thật: ba kho có snapshot .pre-0.154.0",
          snaps == ["conversations.db.pre-0.154.0", "resonance.sqlite3.pre-0.154.0", "team.sqlite3.pre-0.154.0"], snaps)
    p = RT.plan(str(st), "0.154.0")
    check("nâng thật: công cụ thấy ba snapshot hợp lệ", p["ok"] and len(p["restore"]) == 3, p.get("error"))
    # 3. Công cụ hạ.
    res = RT.restore(str(st), "0.154.0")
    check("nâng thật rồi hạ: ok", res["ok"], res.get("error"))
    with closing(sqlite3.connect(sdb)) as c:
        msgs = [r[0] for r in c.execute("SELECT content FROM messages ORDER BY id").fetchall()]
        tbls = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    check("hạ: kho phiên còn tin cũ, mất tin sau nâng, không còn dm_reactions",
          msgs == ["tin cũ trước nâng"] and "dm_reactions" not in tbls, (msgs, sorted(tbls)))
    with closing(sqlite3.connect(tdb)) as c, c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(team_reactions)").fetchall()}
        reactors = [r[0] for r in c.execute("SELECT reactor FROM team_reactions").fetchall()]
    check("hạ: kho nhóm không còn cột source_kind, không còn reaction agent:...",
          "source_kind" not in cols and reactors == ["owner"], (sorted(cols), reactors))
    with closing(sqlite3.connect(rdb)) as c:
        tbls = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    check("hạ: kho Cộng hưởng không còn bảng T2", not (tbls & set(NRS.T2_TABLES)), sorted(tbls & set(NRS.T2_TABLES)))
    kept_sdb = next(Path(k) for k in res["kept"] if Path(k).name.startswith("conversations.db.post-"))
    with closing(sqlite3.connect(kept_sdb)) as c:
        n_new = c.execute("SELECT COUNT(*) FROM messages WHERE content='tin mới sau nâng'").fetchone()[0]
    check("bản sao .post giữ tin sau nâng để đối chiếu", n_new == 1)
    # 4. Mã 0.151.0 mở lại được ba kho.
    try:
        o = OSS.SessionStore(sdb)
        ok_old = len(o.get_messages(sid)) == 1
        o.close()
        OTS.TeamStore(tdb)
        ORS.GoalStore(rdb)
        check("mã 0.151.0 mở lại được ba kho sau khi hạ", ok_old)
    except Exception as ex:  # noqa: BLE001
        check("mã 0.151.0 mở lại được ba kho sau khi hạ", False, f"{type(ex).__name__}: {ex}")
    # 5. Nâng lại: snapshot đã có thì không chép đè.
    mt = {n: (st / n).stat().st_mtime_ns for n in snaps}
    NSS.SessionStore(sdb).close()
    NTS.TeamStore(tdb)
    NRS.GoalStore(rdb)
    check("nâng lại: không chép đè snapshot .pre-0.154.0", all((st / n).stat().st_mtime_ns == mt[n] for n in snaps))


def section_t4_index_only():
    """T4 (0.170.0) chỉ thêm chỉ mục: --to 0.170.0 không ghi gì và nói rõ không có gì để khôi phục."""
    d = fixture("t4", judge=True, pre156=True)
    before = live(d)
    r = RT.restore(str(d), "0.170.0")
    check("--to 0.170.0: không ghi gì, nói rõ mốc chỉ thêm chỉ mục", not r["ok"] and "chỉ thêm chỉ mục" in r["error"]
          and live(d) == before and not names_with(d, ".post-0.170.0-"), r)


for _name, _fn in (("T4 chỉ mục", section_t4_index_only), ("chạy đúng", section_happy), ("thiếu/hỏng", section_missing_invalid),
                   ("lỗi giữa chừng", section_failures), ("chạy lại/CLI", section_rerun_cli),
                   ("dấu khớp mã", section_markers_match_code), ("nâng thật", section_real_upgrade)):
    try:
        _fn()
    except Exception as _ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(f"{_name}: chạy hết nhóm kiểm", False, f"{type(_ex).__name__}: {_ex}")

print(f"\n{'FAIL' if fails else 'PASS'} - test_team_adaptive_restore: {len(fails)} lỗi")
raise SystemExit(1 if fails else 0)
