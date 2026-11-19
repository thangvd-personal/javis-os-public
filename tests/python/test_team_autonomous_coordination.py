"""Kiểm tra bộ quy tắc điều phối tự quản cả team (Level 3):
1. Khi cộng sự làm xong việc, kết quả được chuyển giao về người điều phối (bước synthesize được thêm ngay cả khi chỉ có 1 kết quả).
2. Người điều phối nếu không giao thêm việc cho cộng sự nào thì phải báo cáo lại cho chủ nhóm (gắn cờ asks_owner và gửi notify).
3. Nếu người điều phối mở việc mới (open_job), việc mới được xếp hàng và chuỗi tự vận hành tiếp tục.
4. Ở nhóm quyền giới hạn (không tự quản), việc 1 bước vẫn kết thúc trực tiếp để tương thích ngược.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-auton-")
TEMP = Path(tempfile.mkdtemp(prefix="javis-team-auton-w-"))

import sessions
import task_store
import team_runner as TR
import team_store

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def directive(**op):
    return f"\n<!-- JAVIS_TEAM: {json.dumps(op, ensure_ascii=False)} -->"


AGENTS = {"lan": "Lan Điều Phối", "minh": "Minh Cộng Sự"}


class Fake:
    def __init__(self):
        self.script = {}
        self.calls = []

    def say(self, agent, role, *answers):
        self.script.setdefault((agent, role), []).extend(answers)

    def engine_for(self, team, agent_key, role):
        async def run_(req):
            self.calls.append(req)
            q = self.script.get((agent_key, role)) or []
            return {"text": q.pop(0) if q else f"{agent_key} {role} xong"}
        return "aux", "fake-model", run_


_n = [0]


def world(label, autonomous=True):
    _n[0] += 1
    d = TEMP / f"{_n[0]:02d}-{label}"
    d.mkdir()
    ss = sessions.SessionStore(db_path=d / "chat.db")
    ts = task_store.TaskStore(d / "kanban.sqlite3")
    tm = team_store.TeamStore(d / "team.sqlite3")
    fake = Fake()

    def agent_info(root, key):
        slug = key[3:] if key.startswith("ag_") else ""
        if slug not in AGENTS:
            return None
        return {"slug": slug, "name": AGENTS[slug], "system": f"Bạn là {AGENTS[slug]}.", "model": "", "model_provider": ""}

    r = TR.TeamRunner(teams=tm, tasks=ts, sessions=ss, engine_for=fake.engine_for, agent_info=agent_info)
    tid = team_store.new_team_id()
    sid = ss.create_session(brain=str(d), channel=f"team:{tid}")
    tm.create_team(str(d), label, "", sid, team_id=tid)
    for s in AGENTS:
        tm.add_member(tid, f"ag_{s}", s)
    tm.set_leader(tid, "ag_lan")

    if autonomous:
        # Bật Toàn quyền Level 3 (Tự quản cả team: can_interrupt = True)
        grant = {
            "caps": {},
            "ceilings": {k: {"unlimited": True} for k in TR.AUTH_CAPS},
            "can_interrupt": True,
            "providers_unlimited": [],
            "billing": "subscription"
        }
        tm.grant_authority(tid, grant, by="owner")

    return r, tm, ts, ss, fake, tm.get(tid)


def run(coro):
    return asyncio.run(coro)


def reply(r, t, text, cid, mentions=None):
    return run(r.run_replies(r.submit(t["team_id"], text, client_msg_id=cid, mentions=mentions or [])))


def msgs(ss, t):
    return ss.get_team_messages_page(t["session_id"], limit=200)["messages"]


def jobs(ts, t):
    q = ts.team_queue(t["team_id"])
    return [x for k in ("running", "waiting", "blocked") for x in (q.get(k) or [])]


# ═════ 1. Nhóm Tự Quản: Cộng sự làm xong -> Bàn giao Điều phối -> Báo cáo Chủ nhóm ═════
r, tm, ts, ss, fake, T = world("auton-report-to-owner", autonomous=True)
check("team is autonomous (Level 3)", r._is_team_autonomous(tm.get(T["team_id"])))

# Kịch bản:
# - Leader nhận lệnh mở việc cho Minh
# - Leader plan: giao việc cho ag_minh
# - ag_minh work: làm xong, in báo cáo kết quả
# - Leader synthesize: tổng kết và báo cáo chủ nhóm (không mở việc mới)
fake.say("ag_lan", "leader_reply", "Mở việc cho Minh." + directive(op="open_job", title="Chạy test e2e", brief="Chạy 5 test Playwright"))
fake.say("ag_lan", "plan", "Minh làm test." + directive(op="assign", id="a", agent="ag_minh", brief="Chạy test Playwright"))
fake.say("ag_minh", "work", "Đã chạy 5/5 test Playwright PASS 100%.")
fake.say("ag_lan", "synthesize", "Báo cáo anh toàn bộ test Playwright đã đạt 100% không lỗi.")

reply(r, T, "Mở việc chạy test giúp anh", "c1")
for _ in range(8):
    run(r.dispatch_once())
    if not jobs(ts, T):
        break

roles = [c["role"] for c in fake.calls]
check("called roles include synthesize by leader after member work",
      roles == ["leader_reply", "plan", "work", "synthesize"], roles)

all_msgs = msgs(ss, T)
synth_msg = next((m for m in all_msgs if (m.get("meta") or {}).get("role") == "synthesize"), None)
check("leader synthesize message was posted", synth_msg is not None)
check("control gate flagged asks_owner=True because no new job was opened",
      synth_msg and (synth_msg.get("meta") or {}).get("asks_owner") is True,
      synth_msg.get("meta") if synth_msg else {})
check("final job result tag is on synthesize message",
      synth_msg and (synth_msg.get("meta") or {}).get("job_result") is True)


# ═════ 2. Nhóm Tự Quản: Điều phối mở việc tiếp theo (không báo dừng vội) ═════
r, tm, ts, ss, fake, T = world("auton-open-next-job", autonomous=True)
fake.say("ag_lan", "leader_reply", "Mở việc." + directive(op="open_job", title="Giai đoạn 1", brief="Làm việc 1"))
fake.say("ag_lan", "plan", "Minh làm 1." + directive(op="assign", id="a", agent="ag_minh", brief="Làm việc 1"))
fake.say("ag_minh", "work", "Việc 1 đã xong mỹ mãn.")
# Trong synthesize, leader quyết định mở tiếp Giai đoạn 2
fake.say("ag_lan", "synthesize", "Việc 1 đã xong, tôi mở tiếp việc 2 cho Minh." +
         directive(op="open_job", title="Giai đoạn 2", brief="Làm việc 2"))

reply(r, T, "Bắt đầu dự án", "c2")
# Dispatch đợt 1 để hoàn thành Giai đoạn 1
for _ in range(8):
    run(r.dispatch_once())
    # Nếu giai đoạn 1 xong và giai đoạn 2 đã được mở
    if any(j["title"] == "Giai đoạn 2" for j in jobs(ts, T)):
        break

js = jobs(ts, T)
check("next job 'Giai đoạn 2' was enqueued autonomously", any(j["title"] == "Giai đoạn 2" for j in js), [j["title"] for j in js])

all_msgs = msgs(ss, T)
synth_msg_1 = next((m for m in all_msgs if (m.get("meta") or {}).get("role") == "synthesize"), None)
check("asks_owner is NOT flagged prematurely when coordinator opened another job",
      synth_msg_1 and not (synth_msg_1.get("meta") or {}).get("asks_owner"),
      synth_msg_1.get("meta") if synth_msg_1 else {})


# ═════ 3. Nhóm không Tự Quản (Level 1): Giữ nguyên tương thích ngược (không thêm synthesize khi 1 kết quả) ═════
r, tm, ts, ss, fake, T = world("non-auton-legacy", autonomous=False)
check("team is not autonomous", not r._is_team_autonomous(tm.get(T["team_id"])))

fake.say("ag_lan", "leader_reply", "Mở việc." + directive(op="open_job", title="Việc đơn", brief="Việc đơn"))
fake.say("ag_lan", "plan", "Minh làm." + directive(op="assign", id="a", agent="ag_minh", brief="Làm việc đơn"))
fake.say("ag_minh", "work", "Kết quả việc đơn.")

reply(r, T, "Làm việc đơn", "c3")
for _ in range(8):
    run(r.dispatch_once())
    if not jobs(ts, T):
        break

roles_legacy = [c["role"] for c in fake.calls]
check("non-autonomous team finishes 1-step work without extra synthesize (backward compatible)",
      roles_legacy == ["leader_reply", "plan", "work"], roles_legacy)


if fails:
    print(f"\n{len(fails)} TEST(S) FAILED:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
else:
    print("\nALL AUTONOMOUS COORDINATION TESTS PASSED!")
