"""Review T3 phần P, P2-2 (0.156.0 sửa): tín hiệu "huỷ" -0,4 chỉ từ route CHỦ huỷ việc.

    python tests/run.py t3_fix_pulse_cancel -v

Trước bản sửa `cancel_job` luôn ghi -0,4 cho người đã nộp kết quả, kể cả khi được gọi bởi lệnh `interrupt` của trưởng nhóm
(agent tạo tín hiệu cho agent, trái 6.1), lưu trữ nhóm và `cancel_idle_running`. Bộ chạy nhóm thật, bộ chiếu thật, kho nhịp
thật; không engine nào được gọi.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import tempfile
from pathlib import Path

TEMP = Path(tempfile.mkdtemp(prefix="javis-fix-cancel-"))

import agent_pulse as AP  # noqa: E402
import feedback_projector as FP  # noqa: E402
import resonance_store as RS  # noqa: E402
import sessions  # noqa: E402
import task_store  # noqa: E402
import team_runner as TR  # noqa: E402
import team_store  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


gs = RS.GoalStore(TEMP / "resonance.sqlite3")
owner = RS.Principal("owner", "owner", str(TEMP))
K = {s: gs.agent_set_enabled(owner, s, True)["agent_key"] for s in ("lan", "minh")}
ss = sessions.SessionStore(db_path=TEMP / "chat.db")
ts = task_store.TaskStore(TEMP / "kanban.sqlite3")
tm = team_store.TeamStore(TEMP / "team.sqlite3")
box = {}
pj = FP.FeedbackProjector(resonance=lambda: gs, sessions=lambda: ss, teams=lambda: tm,
                          team_view=lambda t, s, m: box["r"].team_message_view(t, s, m))
r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=lambda *a: None, publish=lambda e: None,
                  agent_info=lambda root, key: {"slug": key, "name": key, "system": ""}, projector=pj)
box["r"] = r
tid = team_store.new_team_id()
tsid = ss.create_session(brain=str(TEMP), channel=f"team:{tid}")
tm.create_team(str(TEMP), "Đội", "", tsid, team_id=tid)
for s, k in K.items():
    tm.add_member(tid, k, s)
tm.set_leader(tid, K["lan"])


def job_with_result(n):
    task = r.submit(tid, f"Việc {n}", as_job=True, client_msg_id=f"j{n}")["task_id"]
    rev = ts.get_task(task)["team_rev"]
    st = tm.add_step(task, tid, "work", K["minh"], rev, "", [])
    mid, _ = ss.append_team_message(tsid, "assistant", f"Kết quả {n}", author_kind="agent", author_ref=K["minh"],
                                    task_id=task, step_id=st["step_id"])
    tm.finish_step(st["step_id"], "done", mid, True)
    return task


def cancel_signals(task):
    return [s for k in K.values() for s in AP.signals(gs, k) if s["source_ref"] == f"cancel:{task}"]


t1 = job_with_result(1)
check("trưởng nhóm ngắt việc đã có kết quả (cancel_job từ lệnh interrupt): việc huỷ",
      r.cancel_job(t1, "người điều phối ngắt: đổi hướng") is not None)
check("trưởng nhóm ngắt: KHÔNG có tín hiệu -0,4 cho người nộp kết quả", cancel_signals(t1) == [], cancel_signals(t1))

t2 = job_with_result(2)
claimed = ts.claim_team_next(tid, "w-test")
check("chuẩn bị: việc 2 đang team_running", claimed and ts.get_task(t2)["status"] == "team_running",
      ts.get_task(t2)["status"])
check("lưu trữ nhóm / huỷ việc treo (cancel_idle_running): huỷ một việc", r.cancel_idle_running(tid, "nhóm đã lưu trữ") == 1)
check("lưu trữ nhóm: KHÔNG phạt người đã nộp kết quả", cancel_signals(t2) == [], cancel_signals(t2))

t3 = job_with_result(3)
r.cancel_job(t3, owner_cancel=True)
cc = cancel_signals(t3)
check("chủ huỷ qua route: -0,4 cho người nộp kết quả, đúng một dòng", len(cc) == 1 and cc[0]["value"] == -0.4
      and cc[0]["agent_key"] == K["minh"], cc)
api_src = (ROOT / "server" / "team_api.py").read_text(encoding="utf-8")
check("route chủ huỷ việc (team_api) là đường duy nhất truyền owner_cancel=True",
      api_src.count("owner_cancel=True") == 1 and "cancel_job(task_id, owner_cancel=True)" in api_src)
runner_src = (ROOT / "server" / "team_runner.py").read_text(encoding="utf-8")
check("bộ chạy nhóm không tự truyền owner_cancel=True ở đâu", "owner_cancel=True)" not in runner_src.replace(
    "owner_cancel: bool = False", ""))

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
