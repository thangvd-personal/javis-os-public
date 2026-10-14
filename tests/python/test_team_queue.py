"""Nhóm chat team T1, nhiệm vụ 4: làn `team` trong Kanban (spec 2026-10-13-team-chat-design mục 5.4 và 8).

    python tests/run.py team_queue

Không gọi model. Ca TC14 (A đang chạy, ưu tiên C trước B: A, C, B), TC15 (ưu tiên lặp, bỏ ưu tiên, nhiều việc ưu tiên),
TC16 (hai tab cùng đổi: CAS), TC17 (restart giữa lúc nhận việc), TC18 (việc bị chặn rời chỗ chạy), TC9 (giao việc trùng),
và mã Kanban thường không bao giờ nhặt việc nhóm.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-queue-")

import task_store as TS  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


st = TS.TaskStore(Path(os.environ["JAVIS_STATE_DIR"]) / "kanban.sqlite3")
B = "D:/brain"


def add(title, src, team="tm_1", assignee=""):
    return st.enqueue_team(B, team, title, title, source_ref=src, assignee_key=assignee, coordinator_key="ag_lan",
                           chat_id="web:s1")


def titles(rows):
    return [r["title"] for r in rows]


a, created = add("A", "msg:s1:1")
check("giao việc tạo đúng một việc team_queued", created and st.get_task(a)["status"] == "team_queued"
      and st.get_task(a)["lane"] == "team")
a2, created2 = add("A", "msg:s1:1")
check("TC9 cùng tin nguồn: trả việc cũ, không tạo thêm", a2 == a and created2 is False)
b, _ = add("Viết bài", "msg:s1:2")
c, _ = add("Viết bài", "msg:s1:3")
check("hai việc khác nhau cùng tên vẫn là hai việc (không gộp theo tiêu đề)", b != c)
check("mã Kanban thường không nhặt việc nhóm", st.next_candidate(B) is None)

got = st.claim_team_next("tm_1", "w1", lease_s=90)
check("nhận việc đầu hàng", got and got["id"] == a and st.get_task(a)["status"] == "team_running")
check("một việc chạy mỗi nhóm: không nhận thêm", st.claim_team_next("tm_1", "w1", lease_s=90) is None)
q = st.team_queue("tm_1")
check("hàng đợi: đang làm A, chờ B rồi C theo thứ tự thêm", titles(q["running"]) == ["A"]
      and [r["id"] for r in q["waiting"]] == [b, c])

# TC14: ưu tiên C khi A đang chạy
q = st.team_priority(c, True, q["version"])
check("TC14 ưu tiên C: thứ tự chờ thành C, B; A vẫn đang chạy", [r["id"] for r in q["waiting"]] == [c, b]
      and st.get_task(a)["status"] == "team_running")
v_before = q["version"]
q2 = st.team_priority(c, True, q["version"])
check("TC15 bấm Ưu tiên lần nữa: không đổi gì", [r["id"] for r in q2["waiting"]] == [c, b] and q2["version"] == v_before)
st.team_finish(a, "xong A")
nxt = st.claim_team_next("tm_1", "w1", lease_s=90)
check("TC14 A xong thì C chạy (A không chạy lại, C không chen ngang A)", nxt["id"] == c
      and st.get_task(a)["status"] == "done")
st.team_finish(c, "xong C")
check("TC14 rồi tới B", st.claim_team_next("tm_1", "w1", lease_s=90)["id"] == b)
st.team_finish(b, "xong B")

# TC15: nhiều việc ưu tiên theo thứ tự bấm; bỏ ưu tiên về chỗ cũ
d, _ = add("D", "msg:s1:4")
e, _ = add("E", "msg:s1:5")
f, _ = add("F", "msg:s1:6")
q = st.team_queue("tm_1")
q = st.team_priority(f, True, q["version"])
q = st.team_priority(e, True, q["version"])
check("TC15 hai việc ưu tiên chạy theo thứ tự bấm, trước việc thường", [r["id"] for r in q["waiting"]] == [f, e, d])
q = st.team_priority(f, False, q["version"])
check("TC15 bỏ ưu tiên: về đúng vị trí theo thứ tự thêm ban đầu", [r["id"] for r in q["waiting"]] == [e, d, f])

# TC16: hai tab cùng đổi bằng version cũ
stale = q["version"]
q = st.team_priority(d, True, stale)
try:
    st.team_priority(f, True, stale)
    check("TC16 tab thứ hai dùng version cũ: 409 kèm hàng đợi mới", False)
except TS.QueueConflict as ex:
    check("TC16 tab thứ hai dùng version cũ: 409 kèm hàng đợi mới", ex.queue["version"] == q["version"]
          and [r["id"] for r in ex.queue["waiting"]] == [e, d, f])
got = st.claim_team_next("tm_1", "w1", lease_s=90)
check("điều phối nhận việc làm đổi version (lệnh ưu tiên cũ sau đó bị từ chối)", got["id"] == e
      and st.team_queue("tm_1")["version"] > q["version"])
try:
    st.team_priority(f, True, q["version"])
    check("ưu tiên với version trước lúc nhận việc bị từ chối", False)
except TS.QueueConflict:
    check("ưu tiên với version trước lúc nhận việc bị từ chối", True)

# TC18: việc bị chặn rời chỗ chạy, hàng đi tiếp; gỡ chặn về đúng chỗ cũ
st.team_block(e, "waiting_user", "thiếu ngày quay")
q = st.team_queue("tm_1")
check("TC18 bị chặn: rời chỗ chạy, hiện ở mục bị chặn kèm lý do", [r["id"] for r in q["blocked"]] == [e]
      and q["running"] == [] and q["blocked"][0]["block_kind"] == "waiting_user")
check("TC18 hàng đi tiếp khi một việc bị chặn", st.claim_team_next("tm_1", "w1", lease_s=90)["id"] == d)
st.team_unblock(e)
q = st.team_queue("tm_1")
check("gỡ chặn: về hàng chờ với vị trí cũ (ưu tiên giữ nguyên, đứng đầu)", [r["id"] for r in q["waiting"]][0] == e)

# Huỷ, sửa yêu cầu
rev = st.team_amend(d, "thêm: làm bản tiếng Anh")
check("sửa yêu cầu tăng revision", rev == 2 and st.get_task(d)["team_rev"] == 2
      and "tiếng Anh" in st.get_task(d)["intent"])
prev = st.team_cancel(d)
check("huỷ việc đang chạy: trả trạng thái trước, việc cancelled, chỗ chạy trống", prev == "team_running"
      and st.get_task(d)["status"] == "cancelled" and st.team_queue("tm_1")["running"] == [])

# TC17: restart giữa lúc chạy, mã Kanban cũ không đụng
g = st.claim_team_next("tm_1", "w-old", lease_s=1)
st.reclaim_stale(B, set())
check("reclaim_stale của Kanban không trả việc nhóm về ready", st.get_task(g["id"])["status"] == "team_running")
check("danh sách việc nhóm đang chạy cho bộ chạy khôi phục", [t["id"] for t in st.team_running_tasks()] == [g["id"]])
taken = st.team_take_over(g["id"], "w-new", lease_s=90)
check("tiến trình mới nhận lại việc đang chạy", taken and st.get_task(g["id"])["claimed_by"] == "w-new")

# Nhóm khác độc lập
x, _ = add("X", "msg:s2:1", team="tm_2")
check("nhóm khác có hàng riêng, chạy song song được", st.claim_team_next("tm_2", "w1", lease_s=90)["id"] == x)
check("danh sách nhóm còn việc chờ", set(st.teams_with_waiting()) == {"tm_1"})
check("lịch sử việc xong, huỷ", {r["id"] for r in st.team_history("tm_1")} >= {a, b, c, d})

# Bảng Kanban cũ không thấy, không sửa, không xoá việc nhóm
k = st.enqueue(B, "Việc Kanban", "Việc Kanban")
check("bảng Việc chỉ liệt kê việc Kanban", [t["id"] for t in st.list_tasks(B)] == [k])
check("kéo thả trên bảng Việc không đụng việc nhóm", st.move(f, "ready") is False
      and st.get_task(f)["status"] == "team_queued")
check("cấp toàn quyền trên bảng Việc không đụng việc nhóm", st.grant_full(f) is False)
check("thống kê bảng Việc không đếm việc nhóm", sum(st.health(B)["counts"].values()) == 1)
st.archive_by_status(B, ("done", "cancelled"))
st.purge_terminal(B, ("done", "cancelled", "archived"))
st.clear_board(B)
check("xoá trắng, dọn, lưu trữ bảng Việc giữ nguyên việc nhóm", st.get_task(f)["status"] == "team_queued"
      and st.get_task(a)["status"] == "done" and st.get_task(k) is None)
k2 = st.enqueue(B, "F", "F")
check("gộp theo tiêu đề của Kanban không trả về việc nhóm", k2 not in {a, b, c, d, e, f})
raise SystemExit(1 if fails else 0)
