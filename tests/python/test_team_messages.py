"""Nhóm chat team T1, nhiệm vụ 3: tin có người gửi trong kho phiên (spec 2026-10-13-team-chat-design mục 5.3).

    python tests/run.py team_messages

Không gọi model. Cột người gửi, chống gửi trùng bằng `client_msg_id`, phân trang, ẩn kênh nhóm khỏi lịch sử chat chính,
và chat thường không đổi.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import sqlite3
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-msg-")

import sessions  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


db = Path(os.environ["JAVIS_STATE_DIR"]) / "s.db"
st = sessions.SessionStore(db_path=db)
web = st.create_session(brain="B", channel="web")
team = st.create_session(brain="B", channel="team:tm_1", title="Ra mắt")
mate = st.create_session(brain="B", channel="teammate:tm_1:ag_lan")

mid, created = st.append_team_message(team, "user", "Giao việc: kế hoạch", author_kind="owner", author_ref="",
                                      client_msg_id="c-1", meta={"mentions": ["ag_minh"], "as_job": True})
check("ghi tin của chủ có mã client", created and mid > 0)
mid2, created2 = st.append_team_message(team, "user", "Giao việc: kế hoạch", author_kind="owner", author_ref="",
                                        client_msg_id="c-1")
check("gửi lại cùng client_msg_id: trả đúng tin cũ, không tạo dòng mới", (mid2, created2) == (mid, False)
      and len(st.get_messages(team)) == 1)
a1, _ = st.append_team_message(team, "assistant", "Đã chia việc", author_kind="agent", author_ref="ag_lan",
                               reply_to=mid, task_id="t_1", step_id="st_1", meta={"role": "plan"})
page = st.get_team_messages_page(team, limit=10)
m0, m1 = page["messages"]
check("trang tin nhóm mang đủ người gửi, việc, bước, meta", m1["author_kind"] == "agent" and m1["author_ref"] == "ag_lan"
      and m1["task_id"] == "t_1" and m1["step_id"] == "st_1" and m1["reply_to"] == mid and m1["meta"]["role"] == "plan"
      and m0["author_kind"] == "owner" and m0["meta"]["mentions"] == ["ag_minh"], str(page))
check("tra tin theo client_msg_id", st.message_by_client_id(team, "c-1")["id"] == mid
      and st.message_by_client_id(team, "khong-co") is None)
check("cùng client_msg_id ở phiên KHÁC là tin khác", st.append_team_message(mate, "user", "x", author_kind="owner",
                                                                          client_msg_id="c-1")[1] is True)
for i in range(35):
    st.append_team_message(team, "assistant", f"tin {i}", author_kind="agent", author_ref="ag_minh")
p1 = st.get_team_messages_page(team, limit=30)
oldest = p1["messages"][0]
p2 = st.get_team_messages_page(team, limit=30, before=(oldest["ts"], oldest["id"]))
check("phân trang theo con trỏ: không trùng, không sót", p1["has_more"] and not p2["has_more"]
      and len(p1["messages"]) + len(p2["messages"]) == 37
      and not ({m["id"] for m in p1["messages"]} & {m["id"] for m in p2["messages"]}))
ids = {s["id"] for s in st.list_sessions(limit=50)}
check("lịch sử chat chính không hiện phiên nhóm hay mạch thành viên", web in ids and team not in ids and mate not in ids)
st.append_message(web, "user", "chat thường")
plain = st.get_messages_page(web, limit=5)["messages"][0]
check("chat thường không đổi hình dạng", plain["content"] == "chat thường" and "author_kind" not in plain)
st.append_message(web, "user", "từ khoá chung xyzzy")
st.append_team_message(team, "user", "từ khoá chung xyzzy trong nhóm", author_kind="owner", client_msg_id="s-1")
hits = {h["session_id"] for h in st.search("xyzzy", brain="B")}
check("tìm kiếm chung không trả phiên nhóm (mở ra là chat vào nhóm bằng bộ não chính)", web in hits and team not in hits,
      str(hits))
with sqlite3.connect(db) as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
check("cột mới cho phép null", {"author_kind", "author_ref", "client_msg_id", "reply_to", "task_id", "step_id",
                                 "meta_json"} <= cols)
raise SystemExit(1 if fails else 0)
