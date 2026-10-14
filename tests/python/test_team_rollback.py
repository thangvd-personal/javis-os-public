"""Nhóm chat team T1, nhiệm vụ 8: nâng từ 0.139.0, hạ về bằng snapshot (spec 2026-10-13-team-chat-design mục 18; ca TC39).

    python tests/run.py team_rollback -v

Nạp `server/task_store.py` và `server/sessions.py` của commit phát hành 0.139.0 (`be1f8aa2` trên main, bản cuối trước nhóm
chat) qua `git show`:
1. mã 0.139.0 THẬT lập kho Kanban có một việc và kho phiên có một tin;
2. mã T1 mở hai kho: mỗi kho có đúng một snapshot `.pre-0.140.0`, chụp TRƯỚC khi thêm cột; việc và tin cũ còn nguyên;
3. hạ KHÔNG khôi phục: mã 0.139.0 mở được hai kho, KHÔNG nhặt việc nhóm (không chạy bằng aux); phiên `team:` hiện như
   phiên thường (giới hạn đã công bố);
4. công cụ khôi phục giữ bản kho hiện tại `post-0.140.0-*` rồi ghi snapshot; mã 0.139.0 mở được, không còn cột hay việc
   nhóm;
5. nâng lại: snapshot đã có thì không chép lại.
Không gọi model. Máy không có lịch sử git của commit đó thì in SKIP; trên CI (biến CI) thì ĐỎ.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-rb-")

import sessions as NEW_SS  # noqa: E402
import task_store as NEW_TS  # noqa: E402
import team_migration as TM  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import team_chat_restore_pre as RESTORE  # noqa: E402

OLD_SHA = "be1f8aa215395471c4640e5e8e109d44e02dc190"
fails = []


def check(name, cond, info=None):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or info is None else f"  ({str(info)[:300]})"))
    if not cond:
        fails.append(name)


def _show(path):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{OLD_SHA}:{path}"],
                                   stderr=subprocess.DEVNULL).decode("utf-8")


try:
    srcs = {n: _show(f"server/{n}.py") for n in ("task_store", "sessions")}
except (subprocess.CalledProcessError, OSError):
    if os.environ.get("CI"):
        print(f"FAIL không đọc được mã của {OLD_SHA}: CI phải fetch commit này trước")
        sys.exit(1)
    print(f"SKIP máy này không có lịch sử git của {OLD_SHA}")
    sys.exit(0)

_dir = Path(tempfile.mkdtemp(prefix="old-0109-"))


def _load(name, src):
    f = _dir / f"{name}.py"
    f.write_text(src, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(name, f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OLD_TS = _load("task_store_0109", srcs["task_store"])
OLD_SS = _load("sessions_0109", srcs["sessions"])
check("nạp đúng mã 0.139.0: chưa có làn team, chưa có tin có người gửi",
      not hasattr(OLD_TS.TaskStore, "enqueue_team") and not hasattr(OLD_SS.SessionStore, "append_team_message"))


def cols(db, table):
    with sqlite3.connect(str(db)) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


state = Path(tempfile.mkdtemp(prefix="state-team-rb-"))
kdb, sdb = state / "kanban.sqlite3", state / "conversations.db"
B = "D:/brain"

# 1. Mã 0.139.0 lập hai kho.
ot = OLD_TS.TaskStore(kdb)
k_old = ot.enqueue(B, "Việc cũ", "Việc cũ", status="ready")
ot.close()
os_ = OLD_SS.SessionStore(db_path=sdb)
s_old = os_.create_session(brain="B", channel="web")
os_.append_message(s_old, "user", "tin cũ")
os_._conn.close()

# 2. Mã T1 mở: snapshot một lần, chụp trước khi thêm cột.
nt = NEW_TS.TaskStore(kdb)
ns = NEW_SS.SessionStore(db_path=sdb)
ksnap, ssnap = kdb.with_name(kdb.name + TM.SNAPSHOT_SUFFIX), sdb.with_name(sdb.name + TM.SNAPSHOT_SUFFIX)
check("nâng lên: kanban có snapshot .pre-0.140.0, chụp TRƯỚC khi thêm cột làn team",
      ksnap.is_file() and "lane" not in cols(ksnap, "tasks") and "lane" in cols(kdb, "tasks"))
check("nâng lên: kho phiên có snapshot .pre-0.140.0, chụp TRƯỚC khi thêm cột người gửi",
      ssnap.is_file() and "author_kind" not in cols(ssnap, "messages") and "author_kind" in cols(sdb, "messages"))
check("việc cũ và tin cũ còn nguyên sau nâng", nt.get_task(k_old)["status"] == "ready"
      and [m["content"] for m in ns.get_messages(s_old)] == ["tin cũ"])
tid, _ = nt.enqueue_team(B, "tm_1", "Việc nhóm", "Việc nhóm", source_ref="msg:s:1")
team_sid = ns.create_session(brain="B", channel="team:tm_1", title="Nhóm")
ns.append_team_message(team_sid, "user", "tin nhóm", author_kind="owner", client_msg_id="c1")
k_mtime, s_mtime = ksnap.stat().st_mtime_ns, ssnap.stat().st_mtime_ns
nt.close()
ns._conn.close()

# 3. Hạ KHÔNG khôi phục: mã 0.139.0 trên kho đã nâng.
ot = OLD_TS.TaskStore(kdb)
cand = ot.next_candidate(B)
check("hạ không khôi phục: mã 0.139.0 mở được kho, chỉ nhặt việc Kanban, không bao giờ nhặt việc nhóm",
      cand is not None and cand["id"] == k_old and ot.claim(tid, "w-old") is None)
ot.reclaim_stale(B, set())
check("hạ không khôi phục: thu hồi của mã cũ không đụng việc nhóm", ot.get_task(tid)["status"] == "team_queued")
check("hạ không khôi phục: bảng Việc của mã cũ thấy dòng team_queued (giới hạn đã công bố)",
      any(t["id"] == tid and t["status"] == "team_queued" for t in ot.list_tasks(B)))
ot.close()
os_ = OLD_SS.SessionStore(db_path=sdb)
check("hạ không khôi phục: mã 0.139.0 đọc được tin nhóm như tin thường, phiên team: hiện như phiên thường",
      [m["content"] for m in os_.get_messages(team_sid)] == ["tin nhóm"]
      and team_sid in {s["id"] for s in os_.list_sessions(limit=50)})
os_._conn.close()

# 4. Công cụ khôi phục.
r = RESTORE.restore(str(state))
check("công cụ khôi phục: ok, giữ bản hiện tại post-0.140.0 của cả hai kho", r["ok"] and len(r["kept"]) == 2
      and all(Path(p).is_file() for p in r["kept"]) and all(".post-0.140.0-" in p for p in r["kept"]), r)
kept_k = next(Path(p) for p in r["kept"] if "kanban" in p)
with sqlite3.connect(str(kept_k)) as c:
    kept_team = c.execute("SELECT COUNT(*) FROM tasks WHERE lane='team'").fetchone()[0]
check("bản giữ lại còn việc nhóm để tra cứu", kept_team == 1)
check("khôi phục: không còn cột mới", "lane" not in cols(kdb, "tasks") and "author_kind" not in cols(sdb, "messages"))
ot = OLD_TS.TaskStore(kdb)
os_ = OLD_SS.SessionStore(db_path=sdb)
check("khôi phục rồi chạy 0.139.0: việc cũ còn, không còn việc nhóm hay phiên nhóm", ot.get_task(k_old) is not None
      and ot.get_task(tid) is None and os_.get_session(team_sid) is None
      and [m["content"] for m in os_.get_messages(s_old)] == ["tin cũ"])
ot.close()
os_._conn.close()

# 5. Nâng lại: snapshot đã có thì không chép lại.
nt = NEW_TS.TaskStore(kdb)
ns = NEW_SS.SessionStore(db_path=sdb)
check("nâng lại: không chép đè snapshot", ksnap.stat().st_mtime_ns == k_mtime and ssnap.stat().st_mtime_ns == s_mtime
      and "lane" in cols(kdb, "tasks"))
nt.close()
ns._conn.close()

# Kho mới tinh thì không chụp gì.
fresh = Path(tempfile.mkdtemp(prefix="state-fresh-"))
NEW_TS.TaskStore(fresh / "kanban.sqlite3").close()
NEW_SS.SessionStore(db_path=fresh / "conversations.db")._conn.close()
check("kho mới tinh: không có snapshot", not list(fresh.glob("*" + TM.SNAPSHOT_SUFFIX)))
check("công cụ báo rõ khi không có snapshot", RESTORE.restore(str(fresh))["ok"] is False)


# ═════════════ R6 (review T1 vòng 1): vòng hạ, nâng với NHÓM THẬT, cả bộ ba kho ═════════════
import asyncio  # noqa: E402

import team_runner as NEW_TR  # noqa: E402
import team_store as NEW_TEAM  # noqa: E402


def fake_engine_for(team, key, role):
    async def run_(req):
        return {"text": f"{key} trả lời"}
    return "fake", "fake", run_


def old_state(prefix):
    """Mã 0.139.0 thật lập kho Kanban (một việc) và kho phiên (một tin)."""
    st = Path(tempfile.mkdtemp(prefix=prefix))
    o = OLD_TS.TaskStore(st / "kanban.sqlite3")
    kid = o.enqueue(B, "Việc cũ", "Việc cũ", status="ready")
    o.close()
    oss = OLD_SS.SessionStore(db_path=st / "conversations.db")
    sid = oss.create_session(brain="B", channel="web")
    oss.append_message(sid, "user", "tin cũ")
    oss._conn.close()
    return st, kid, sid


def t1_world(st):
    ss = NEW_SS.SessionStore(db_path=st / "conversations.db")
    ts = NEW_TS.TaskStore(st / "kanban.sqlite3")
    tm = NEW_TEAM.TeamStore(st / "team.sqlite3")
    r = NEW_TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=fake_engine_for,
                          agent_info=lambda root, key: {"slug": key, "name": key, "system": ""})
    return ss, ts, tm, r


def t1_team(ss, tm, r, name="Nhóm thật"):
    """Một nhóm thật: phiên team:, hai thành viên, trưởng nhóm, một lượt trò chuyện và một việc nhóm đã xong."""
    tid = NEW_TEAM.new_team_id()
    sid = ss.create_session(brain="B", channel=f"team:{tid}", title=name)
    tm.create_team(B, name, "", sid, team_id=tid)
    for k in ("ag_a", "ag_b"):
        tm.add_member(tid, k, k)
    tm.set_leader(tid, "ag_a")
    asyncio.run(r.run_replies(r.submit(tid, "Chào nhóm", client_msg_id="hello")))
    job = r.submit(tid, "Việc nhóm", mentions=["ag_b"], as_job=True, client_msg_id="job")["task_id"]
    asyncio.run(r.dispatch_once())
    return tid, sid, job


def close(ss, ts):
    ss._conn.close()
    ts.close()


def count(db, sql):
    with sqlite3.connect(str(db)) as c:
        return c.execute(sql).fetchone()[0]


# R6.1 Vòng đầy đủ: 0.139.0 -> T1 có nhóm thật -> khôi phục -> 0.139.0 -> T1 lại.
st, k6, s6 = old_state("state-r6-round-")
ss, ts, tm, r = t1_world(st)
tid6, tsid6, job6 = t1_team(ss, tm, r)
check("R6 dựng nhóm thật trên kho đã nâng: có tin trả lời và việc nhóm xong",
      ts.get_task(job6)["status"] == "done" and len(ss.get_team_messages_page(tsid6, limit=50)["messages"]) >= 4)
close(ss, ts)
res = RESTORE.restore(str(st))
team_db = st / "team.sqlite3"
kept_team = [Path(p) for p in res["kept"] if Path(p).name.startswith("team.sqlite3.post-0.140.0-")]
check("R6 khôi phục cả bộ: ok, gỡ team.sqlite3 khỏi chỗ chạy, giữ bản sao của cả ba kho",
      res["ok"] and not team_db.exists() and str(team_db) in res.get("removed", []) and len(res["kept"]) == 3
      and len(kept_team) == 1, res)
check("R6 bản sao kho nhóm còn nguyên nhóm, thành viên, sổ lượt để đối chiếu",
      kept_team and count(kept_team[0], "SELECT COUNT(*) FROM teams") == 1
      and count(kept_team[0], "SELECT COUNT(*) FROM team_members") == 2
      and count(kept_team[0], "SELECT COUNT(*) FROM team_turns WHERE status='ok'") >= 2)
ot = OLD_TS.TaskStore(st / "kanban.sqlite3")
os_ = OLD_SS.SessionStore(db_path=st / "conversations.db")
check("R6 sau khôi phục, mã 0.139.0: việc và tin cũ còn, không còn việc, phiên nhóm",
      ot.get_task(k6) is not None and ot.get_task(job6) is None and os_.get_session(tsid6) is None
      and [m["content"] for m in os_.get_messages(s6)] == ["tin cũ"])
ot.close()
os_._conn.close()
ss, ts, tm, r = t1_world(st)
check("R6 nâng lại: không có nhóm nào sống trỏ vào phiên đã mất", tm.get(tid6) is None and tm.list_teams(B) == [])
try:
    r.submit(tid6, "Gửi lại", client_msg_id="again")
    err6 = None
except Exception as ex:  # noqa: BLE001
    err6 = type(ex).__name__
check("R6 gửi vào nhóm cũ sau nâng lại: lỗi nghiệp vụ rõ (TeamError), không phải lỗi khoá ngoại", err6 == "TeamError",
      err6)
tid6b, tsid6b, job6b = t1_team(ss, tm, r, "Nhóm mới sau nâng lại")
check("R6 nâng lại: tạo nhóm mới, trò chuyện, chạy việc bình thường", ts.get_task(job6b)["status"] == "done")
close(ss, ts)

# R6.2 Kho nhóm còn mà kho phiên đã về bản cũ (công cụ khôi phục cũ, hay chép tay): nhóm mồ côi bị lưu trữ, không sống.
st, k7, s7 = old_state("state-r6-orphan-")
ss, ts, tm, r = t1_world(st)
tid7, tsid7, _ = t1_team(ss, tm, r)
open7 = r.submit(tid7, "Việc còn chờ", as_job=True, client_msg_id="wait")["task_id"]
close(ss, ts)
for name in ("kanban.sqlite3", "conversations.db"):
    RESTORE._backup(st / (name + TM.SNAPSHOT_SUFFIX), st / name)   # đúng hành vi công cụ cũ: để nguyên team.sqlite3
ss, ts, tm, r = t1_world(st)
check("R6 tình huống mồ côi tái hiện: nhóm còn trong kho nhóm, phiên đã mất", tm.get(tid7) is not None
      and ss.get_session(tsid7) is None)
try:
    r.submit(tid7, "Hello", client_msg_id="orphan")
    err7 = None
except Exception as ex:  # noqa: BLE001
    err7 = type(ex).__name__
check("R6 nhóm mồ côi: gửi tin trả TeamError, không IntegrityError", err7 == "TeamError", err7)
import team_api as NEW_API  # noqa: E402

api = NEW_API.TeamApi(NEW_API.TeamApiDeps(
    brain_key=lambda b: b, session_store=lambda: ss, task_store=lambda: ts, resonance_store=lambda: None,
    agents_meta=lambda root: [], agent_info=lambda root, key: None, engine_for=fake_engine_for,
    spawn=lambda coro: coro.close() if asyncio.iscoroutine(coro) else None, store_path=st / "team.sqlite3"))
api.prime()
asyncio.run(api.tick())
t7 = api.teams().get(tid7)
check("R6 khởi động có nhóm mồ côi: vòng nền lưu trữ nhóm, ghi sự kiện session_missing, huỷ việc còn mở",
      t7["status"] == "archived" and any(e["kind"] == "session_missing" for e in api.teams().events(tid7))
      and (ts.get_task(open7) is None or ts.get_task(open7)["status"] == "cancelled"), t7)
check("R6 dữ liệu nhóm mồ côi giữ nguyên để đối chiếu (không xoá)", len(api.teams().members(tid7)) == 2
      and len(api.teams().turns(tid7)) >= 2)
close(ss, ts)

# R6.3 Thiếu snapshot của một kho đã nâng: không đụng kho nào, không báo thành công.
st, k8, s8 = old_state("state-r6-missing-")
ss, ts, tm, r = t1_world(st)
tid8, _, _ = t1_team(ss, tm, r)
close(ss, ts)
(st / ("kanban.sqlite3" + TM.SNAPSHOT_SUFFIX)).unlink()
before = sorted(p.name for p in st.iterdir())
res = RESTORE.restore(str(st))
check("R6 thiếu snapshot kanban: không khôi phục, báo rõ kho thiếu", res["ok"] is False and "kanban" in res["error"]
      and res["restored"] == [], res)
check("R6 thiếu snapshot: không ghi gì (không bản sao, không gỡ kho nhóm, kho vẫn là bản đã nâng)",
      sorted(p.name for p in st.iterdir()) == before and (st / "team.sqlite3").is_file()
      and "author_kind" in cols(st / "conversations.db", "messages") and "lane" in cols(st / "kanban.sqlite3", "tasks"))

# R6.4 Hỏng giữa chừng: trả mọi kho đã đụng về bản trước khi khôi phục, báo lỗi; chạy lại thì xong.
st, k9, s9 = old_state("state-r6-partial-")
ss, ts, tm, r = t1_world(st)
tid9, tsid9, job9 = t1_team(ss, tm, r)
close(ss, ts)
try:
    res = RESTORE.restore(str(st), _fail_on="conversations.db")
except TypeError:   # công cụ không có chỗ giả lập hỏng: không kiểm được đường trả về
    res = {"ok": None, "restored": None, "error": "không có chỗ giả lập hỏng"}
check("R6 hỏng khi ghi kho thứ hai: báo lỗi, không báo thành công", res["ok"] is False and res["restored"] == []
      and "khôi phục dở" in res["error"], res)
ss, ts, tm, r = t1_world(st)
check("R6 hỏng giữa chừng: kanban đã ghi được trả về bản sau nâng (còn việc nhóm), kho nhóm trả về chỗ, nhóm vẫn chạy",
      "lane" in cols(st / "kanban.sqlite3", "tasks") and ts.get_task(job9) is not None
      and tm.get(tid9) is not None and ss.get_session(tsid9) is not None)
close(ss, ts)
res = RESTORE.restore(str(st))
check("R6 chạy lại sau lần hỏng: khôi phục cả bộ xong", res["ok"] and not (st / "team.sqlite3").exists()
      and "lane" not in cols(st / "kanban.sqlite3", "tasks"), res)
raise SystemExit(1 if fails else 0)
