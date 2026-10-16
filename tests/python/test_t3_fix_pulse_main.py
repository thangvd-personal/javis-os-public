"""Review T3 phần P (0.156.0 sửa): các đường đăng tin, đề xuất, xếp việc tự phát của main.py chỉ khi còn trong phạm vi.

    python tests/run.py t3_fix_pulse_main -v

- P P2-1: `_pulse_post`, `_pulse_propose`, `_pulse_enqueue`, `_learn_send` và hàm gửi của phán xử (`_judge_send`) chỉ đăng
  vào nhóm ĐANG HOẠT ĐỘNG mà agent là thành viên ĐANG THAM GIA. Trước bản sửa, agent đã rời nhóm (hay nhóm tạm dừng, lưu
  trữ) vẫn đăng được tin chủ động vào nhóm đó.

Không model nào được gọi: chốt chặn làm test DỪNG NGAY nếu có đường nào dựng engine thật.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fix-pm-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
BR = Path(tempfile.mkdtemp(prefix="javis-fix-pm-brain-"))
(BR / "agents").mkdir()
(BR / "agents" / "lan.md").write_text("---\nname: Lan\nrole: Trợ lý\n---\nBạn là Lan.\n", encoding="utf-8", newline="\n")

import main  # noqa: E402
import resonance_store as RS  # noqa: E402
import team_store  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class RealEngineUsed(AssertionError):
    pass


def _no_real_engine(*a, **k):
    raise RealEngineUsed("test tried to build a REAL engine")


main.team_engine.build = _no_real_engine
main.claude_engine = _no_real_engine
main.CodexCLI = _no_real_engine
main._pulse_engine_for = _no_real_engine
main._team_engine_for = _no_real_engine
main._TEAM_API.deps.spawn = lambda coro: coro.close()

rs = main._resonance_store()
BK = main._brain_key(str(BR))
owner = RS.Principal("owner", "owner", BK)
lan = rs.agent_set_enabled(owner, "lan", True)
KEY = lan["agent_key"]

teams = main._TEAM_API.teams()
TID = team_store.new_team_id()
TSID = main.get_store().create_session(brain=BK, channel=f"team:{TID}")
teams.create_team(BK, "Đội A", "", TSID, team_id=TID)
teams.add_member(TID, KEY, "lan")
CTX = f"team:{TID}"
ss = main.get_store()


def n_msgs():
    return len(ss.team_messages_after(TSID, 0, 500))


ok = main._pulse_post(KEY, CTX, "Tin chủ động khi còn là thành viên", {"proactive": True})
check("còn là thành viên của nhóm đang hoạt động: đăng được", ok and ok.get("message_id"), ok)

# ═════ rời nhóm ═════
teams.remove_member(TID, KEY)
base = n_msgs()
check("rời nhóm: _pulse_post không đăng", main._pulse_post(KEY, CTX, "x", {"proactive": True}) is None)
check("rời nhóm: _pulse_propose không đăng", main._pulse_propose(KEY, CTX, {"title": "Việc", "brief": "b"}, "task:1")
      is None)
check("rời nhóm: _pulse_enqueue không xếp việc", main._pulse_enqueue(KEY, CTX, {"title": "Việc", "brief": "b"},
                                                                     "task:1") is None)
check("rời nhóm: _learn_send không đăng", main._learn_send(KEY, CTX, "Bản sửa", {"reply_to": 1, "review": "revise"})
      is None)
check("rời nhóm: hàm gửi của phán xử không đăng",
      main._judge_send({"agent_key": KEY, "context_ref": CTX, "decision_id": 1}, {"draft": "Nháp"}, "send") is None)
check("rời nhóm: không có tin mới nào trong nhóm", n_msgs() == base, n_msgs() - base)
check("rời nhóm: không được enqueue (can_enqueue)", main._pulse_can_enqueue(KEY, CTX) is False)

# ═════ còn là thành viên nhưng nhóm tạm dừng / lưu trữ ═════
teams.add_member(TID, KEY, "lan")
for status in ("paused", "archived"):
    teams.update(TID, status=status)
    base = n_msgs()
    check(f"nhóm {status}: _pulse_post không đăng", main._pulse_post(KEY, CTX, "x", {"proactive": True}) is None)
    check(f"nhóm {status}: _learn_send không đăng",
          main._learn_send(KEY, CTX, "Bản sửa", {"reply_to": 1, "review": "revise"}) is None)
    check(f"nhóm {status}: không có tin mới", n_msgs() == base)
teams.update(TID, status="active")
check("nhóm hoạt động trở lại: đăng được", main._pulse_post(KEY, CTX, "y", {"proactive": True}) is not None)

# ═════ P P3: việc do lượt quét xếp có dấu vết quản trị như open_job ═════
teams.set_leader(TID, KEY)
tid9 = main._pulse_enqueue(KEY, CTX, {"title": "Rà việc cũ", "brief": "b"}, "task:9")
evs = [e for e in teams.events(TID) if e.get("kind") == "job_opened"]
sysm = [m for m in ss.team_messages_after(TSID, 0, 500) if m.get("author_kind") == "system"
        and (m.get("meta") or {}).get("kind") == "job_opened"]
check("lượt quét xếp việc: ghi team_events job_opened (nguồn pulse) và tin hệ thống", tid9 and evs and sysm
      and "Rà việc cũ" in sysm[-1]["content"], (tid9, evs[-1:], sysm[-1:]))

# ═════ L P2-2: bản sửa ở chat riêng vào ĐÚNG phiên nguồn, kèm reply_to và dấu lượt xem xét ═════
SRC = ss.create_session(brain=BK, channel="agent:lan")
REPORT = ss.create_session(brain=BK, channel="agent:lan")
main._pulse_dm_session = lambda key: (REPORT, "lan")
rmid = ss.append_message(SRC, "assistant", "Câu trả lời bị phản hồi")
got = main._learn_send(KEY, f"dm:{KEY}", "Bản sửa ngắn hơn", {"session_id": SRC, "reply_to": rmid, "review": "revise",
                                                              "source_ref": "wr:5", "decision_id": 9})
check("bản sửa chat riêng vào đúng phiên nguồn (không phải phiên báo cáo)", got and got["session_id"] == SRC, got)
tm_row = ss.team_message(SRC, int(got["message_id"])) if got else None
check("bản sửa chat riêng có reply_to tới tin bị phản hồi", tm_row and int(tm_row.get("reply_to") or 0) == rmid, tm_row)
check("bản sửa chat riêng mang dấu lượt xem xét (meta review, decision_id)",
      tm_row and (tm_row.get("meta") or {}).get("review") == "revise" and (tm_row.get("meta") or {}).get("decision_id") == 9,
      tm_row)
FOREIGN = ss.create_session(brain=BK, channel="web")
got2 = main._learn_send(KEY, f"dm:{KEY}", "Bản sửa 2", {"session_id": FOREIGN, "reply_to": 1, "review": "revise"})
check("phiên nguồn không phải chat riêng của trợ lý: về phiên báo cáo, không đăng vào phiên lạ",
      got2 and got2["session_id"] == REPORT, got2)

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
