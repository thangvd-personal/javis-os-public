"""Ma trận nghiệm thu T1-T4 (GOAL mục 12, thiết kế 2026-10-15 mục 13): mỗi ca 1-28 và hàng thêm (lỗi kho, lệch đồng hồ).

    python tests/run.py team_acceptance_matrix -v           # kiểm ma trận
    python tests/python/test_team_acceptance_matrix.py --write   # chạy các test được trỏ tới, ghi lại bảng

Mỗi ca: (a) file test chuyên trách nêu ở thiết kế mục 13 phải CÓ và chứa đúng các tên kiểm then chốt (chuỗi trong
`check(...)`), hoặc (b) một kịch bản gọn chạy qua host thật (`main.app`, TestClient, engine giả, chốt chặn engine thật).
Ca phụ thuộc phần T3 chưa ghép (phần L: bài học có phạm vi, góp ý đồng đội, tự rà soát) được ghi `pending` kèm tên file
còn thiếu, KHÔNG tính là đạt và KHÔNG làm đỏ test: đó là phần chưa có, không phải lỗi hành vi. File được thiết kế nêu mà
thiếu khi phần sở hữu nó ĐÃ ghép thì là lỗi.

Bảng docs/dev/team-chat-acceptance-matrix.md được sinh từ MATRIX dưới đây (`--write`); test kiểm bảng còn khớp ma trận.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PY = Path(ROOT) / "tests" / "python"
JS = Path(ROOT) / "tests" / "js"
DOC = Path(ROOT) / "docs" / "dev" / "team-chat-acceptance-matrix.md"
WRITE = "--write" in sys.argv
# Môi trường gốc, chụp TRƯỚC khi kịch bản host đặt state tạm và cờ dừng nhịp: test con chạy như `tests/run.py` chạy nó.
BASE_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

# Phần T3 nào đã ghép: phát hiện bằng module có mặt (không đoán).
PARTS = {
    "P": (Path(SERVER) / "agent_pulse_host.py").is_file(),
    "J": (Path(SERVER) / "agent_judge.py").is_file(),
    "L": (Path(SERVER) / "agent_lessons.py").is_file() and (Path(SERVER) / "agent_review.py").is_file(),
}

# (ca, mốc, yêu cầu, mã chính, [(file test, [tên kiểm then chốt])], phần T3 sở hữu file còn thiếu, kịch bản host)
MATRIX = [
    ("1", "T1/T3", "Cùng agent ở hai nhóm: vai, ngữ cảnh, bài học riêng; đổi tag không đổi tư cách thành viên",
     "team_store, agent_judge (ngữ cảnh), agent_lessons (L)",
     [("test_team_t1_goal_host.py", ["H7 đổi tag: thành viên, vai, trạng thái"]),
      ("test_agent_judge_scope.py", ["prompt lô nhóm B không chứa ca hay lịch sử của nhóm A",
                                     "ca của nhóm A chỉ đọc được trong nhóm A"]),
      ("test_agent_lessons.py", ["lượt nhóm A nạp team:A + * (không team:B, không dm, không trợ lý khác)",
                                 "chat riêng nạp dm:<agent_key> + * (không bài học nhóm nào)"])], "L", None),
    ("2", "T3", "Gọi trực tiếp được trả lời; tự tham gia khi phù hợp, im khi không đóng góp; không ép cả nhóm nói",
     "agent_judge.judge_team_message, team_runner.run_message",
     [("test_agent_judge_team.py", ["được @ trực tiếp: một quyết định address=certain",
                                    "K=1: chỉ người điểm cao nhất tự lên tiếng",
                                    "không tín hiệu (không vai khớp, không câu hỏi chung): silent/no_signal"])],
     None, "direct_and_judge"),
    ("3", "T1/T3", "Trưởng nhóm phân công, thành viên phản hồi, người làm sửa đúng bản; chủ chốt/chặn, nhóm chờ đúng",
     "team_runner (bước, bản), agent_peer_feedback (L)",
     [("test_team_t1_review.py", ["R7 kế hoạch: yêu cầu mới KHÔNG bị chốt bằng kết quả bản cũ"]),
      ("test_team_queue.py", ["TC18 bị chặn: rời chỗ chạy"]),
      ("test_team_peer_feedback.py", ["trưởng nhóm góp ý: host gán nguồn leader",
                                      "góp ý ở nhóm 1 không hiện ở nhóm 2"])], "L", None),
    ("4", "T2", "Sáu reaction ở chat riêng và nhóm: chọn/đổi/bỏ, hash nội dung, nguồn, nhiều tab, restart",
     "sessions.dm_reactions, team_store.team_reactions, dashboard/reactions.js",
     [("test_dm_reactions.py", ["khuôn R đúng hợp đồng", "seen_sha lệch: stale true, vẫn nhận",
                                "giữ qua mở lại kho (restart)"]),
      ("test_team_reactions.py", ["đúng sáu giá trị, không gộp", "giữ qua mở lại kho (restart)"]),
      ("test_reactions_ui.js", ["đúng sáu giá trị theo hợp đồng"]),
      ("test_team_t4_ops.py", ["tab B đổi sang wow: tab A và tab B đều NHẬN gói team_message"])], None, "six_reactions"),
    ("5", "T2/T3", "Auto bật: phản hồi mở lần xem xét có tác động; tắt: chỉ lưu, không học nền",
     "feedback_projector, agent_ledger.WakeReasons",
     [("test_feedback_wake.py", ["Cộng hưởng tắt: KHÔNG lý do thức nào, không lượt model",
                                 "6 lần bấm trên 6 tin: MỘT lý do chờ gom 6 nguồn"]),
      ("test_t3_integration.py", ["reaction trên tin của trợ lý bật Auto tạo lý do reaction_review"])], None, None),
    ("6", "T3", "Phản hồi hữu ích lặp lại đổi nhịp dài hạn có giới hạn; ít nhu cầu giảm nhịp; im đúng không bị phạt",
     "agent_pulse (pulse.v1)",
     [("test_agent_pulse_policy.py", ["5 ❤️/3 ngày: đúng một lần lên bậc",
                                      "im lâu sau đợt cuối thì chậm dần lại (low_demand)"])], None, None),
    ("7", "T3", "Agent ngủ được gọi thì thức lại cùng ký ức; tắt Auto không tự bật; việc đang nhận không bị quên",
     "agent_pulse_host, team_runner",
     [("test_agent_pulse_host.py", ["tắt Cộng hưởng: lời gọi trực tiếp không tự bật lại nhịp",
                                    "tạm dừng và ngủ đông: không nhận lịch chủ động"]),
      ("test_team_t4_ops.py", ["việc của agent đang ngủ đông vẫn chạy xong (nghĩa vụ giữ)"])], None, None),
    ("8", "T3", "Phản hồi kết quả cuối ghi nhận đóng góp có bằng chứng; không đếm trùng, không tự thưởng",
     "agent_pulse.record_contrib, feedback_projector._synthesis_inputs",
     [("test_pulse_contrib.py", ["chiếu lại: không đếm trùng", "record_contrib từ chối người phản hồi là agent"])],
     None, None),
    ("9", "T2/T3", "Thay/bỏ reaction, xoá nguồn đối soát theo chính sách; không tác động kép",
     "feedback_projector, agent_pulse.supersede_signal",
     [("test_dm_reactions.py", ["nhật ký trước/sau của lần bỏ"]),
      ("test_feedback_projector.py", ["bỏ hết: nguồn gỡ, lý do không còn nguồn thì superseded"]),
      ("test_pulse_contrib.py", ["đổi reaction: tín hiệu và phân bổ cũ superseded"])], None, None),
    ("10", "T3", "Phản hồi qua lại, sự kiện dồn dập không thành vòng gọi model vô hạn; chờ chủ không quay vòng",
     "agent_judge (độ sâu, vòng), agent_pulse_host (waiting_owner)",
     [("test_related_review.py", ["A->B->A: Minh (được Lan đánh thức) nhờ lại Lan thì bị chặn (loop)",
                                  "độ sâu > 2: Minh (độ sâu 2) nhờ Hoa thì bị chặn depth_limit"]),
      ("test_agent_pulse_host.py", ["chờ chủ: nhắc đúng một lần sau 24 giờ"])], None, None),
    ("11", "T1", "Ưu tiên A đang làm, B/C chờ: thứ tự A, C, B; không ngắt A; hai kick không vượt trần pool",
     "task_store (team_prio), team_runner._claim_batch",
     [("test_team_t1_goal.py", ["G2 thứ tự engine của nhóm: A, C, B",
                                "G2 kick thứ hai lúc A chạy không mở thêm việc"])], None, None),
    ("12", "T1", "Sửa yêu cầu, bỏ thành viên, đổi trưởng nhóm, thu hồi quyền, huỷ ở các biên; bản cũ không chốt bản mới",
     "team_runner (accept_lock, authority_pin)",
     [("test_team_t1_goal.py", ["G1a thu hồi rồi cấp lại giữa lượt"]),
      ("test_team_t1_review.py", ["R3 bỏ thành viên trong khoá chấp nhận"]),
      ("test_team_authority.py", ["TC34 thu hồi giữa lượt"]),
      ("test_team_t4_ops.py", ["tin không @ do trưởng nhóm MỚI trả lời"])], None, None),
    ("13", "T1/T3", "Lỗi engine trước/sau mốc, lỗi kho, huỷ lặp, restart giữ đúng số lượt, lịch thức, bằng chứng",
     "agent_ledger.CallLedger, team_runner, feedback_projector",
     [("test_agent_calls_ledger.py", ["đã qua mốc engine thì không hoàn được",
                                      "đối soát idempotent: lần hai không đổi gì"]),
      ("test_feedback_projector.py", ["lỗi kho resonance: project không ném, reaction vẫn lưu, dòng giữ pending"]),
      ("test_team_t1_review.py", ["R4 phục hồi sau restart: lượt chưa tới mốc hoàn đúng một lần"])], None, None),
    ("14", "T1/T3", "Khôi phục nhiều kho lỗi trước/trong/sau ghi vẫn nhất quán hoặc báo rõ cần cứu hộ",
     "tools/team_adaptive_restore_pre.py, tools/team_chat_restore_pre.py",
     [("test_team_adaptive_restore.py", ["lỗi SAU khi ghi {target}: ok=false, MỌI kho về đúng trước khi khôi phục"]),
      ("test_team_rollback.py", ["R6 hỏng giữa chừng"])], None, None),
    ("15", "T3", "Heartbeat mục tiêu, lịch chủ động, lease không tạo ba lượt cho một lý do; nghĩa vụ vẫn chạy",
     "agent_ledger (định tuyến 3.3, chỉ mục từng phần)",
     [("test_agent_calls_ledger.py", ["một lý do chỉ một lượt đang chạy: lượt thứ hai bị chặn"]),
      ("test_feedback_wake.py", ["tin gắn goal: KHÔNG lý do lớp 3 (một lý do, không ba)"])], None, None),
    ("16", "T3", "Đồng hồ giả ngày/tuần/tháng: giảm nhịp, ngủ đông, thức lại không cần thời gian thực",
     "agent_pulse",
     [("test_agent_pulse_policy.py", ["5 ❤️ rải 2 tuần: giữ B3 suốt đợt",
                                      "thang bậc: B0 15 phút ... B7 1 tháng, trên B7 ngủ đông"]),
      ("test_agent_pulse_host.py", ["cùng agent sau 30 ngày: hoạt động ở nhóm A"])], None, None),
    ("17", "T3", "Phán xử và tự rà soát có sổ lượt, nguồn, phạm vi thay đổi, quay lại",
     "agent_judge (nhãn, changes), agent_review (L)",
     [("test_agent_judge_labels.py", ["nhật ký changes ghi trước/sau của lần dịch"]),
      ("test_agent_judge_review.py", ["sổ lượt: purpose review, obligation 0, giữ chỗ trước engine",
                                      "lỗi phán xử tăng quá 0,15"])], "L", None),
    ("18", "T3/T4", "Desktop, màn hẹp, hai ngôn ngữ, số đo hiệu năng, trạng thái hiển thị khớp kho thật",
     "dashboard/teams.js, agent-judge-ui.js, agent-pulse.js, tools/team_adaptive_bench.py",
     [("test_agent_judge_ui.js", []), ("test_teams_ui.js", []), ("test_team_t4_perf.py", []),
      ("test_team_perf_bench.py", [])], None, None),
    ("19", "T3", "Phán xử có hồ sơ riêng từng agent; hai vai khác nhau chọn khác nhau cho cùng một tin",
     "agent_judge (eagerness, offset theo agent)",
     [("test_agent_judge_team.py", ["hồ sơ riêng: hăng hái cao (ngưỡng 0.45) nói với điểm 0.6",
                                    "hồ sơ của agent này không đổi hồ sơ agent khác"])], None, None),
    ("20", "T2/T3", "Chat riêng: hỏi trực tiếp vẫn được trả lời; chủ động chỉ khi phù hợp; lỗi phán xử không mất câu hỏi",
     "agent_judge.judge_proactive, main._agent_turn_system_prompt",
     [("test_agent_judge_dm.py", ["{label}: quyết định im vẫn nằm trong lịch sử (không mất dấu)",
                                  "lượt chat riêng của chủ vẫn dựng prompt và trả lời như cũ"])], None, None),
    ("21", "T3", "Ca im sửa thành 'đáng lẽ nên nói', ca nói sửa thành 'chen ngang'; lần sau đổi đúng chính sách",
     "agent_judge.label",
     [("test_agent_judge_labels.py", ["chen ngang nâng độ lệch ngưỡng +0.08 x 1.5 = +0.12",
                                      "không đổi ước lượng nội dung"])], None, None),
    ("22", "T3", "Cá nhân hoá đủ/thiếu mẫu, emoji về sự việc, reaction hiếm, đổi/bỏ, chưa xem; không thưởng/phạt mù",
     "agent_pulse (k cá nhân hoá, seen_ignored)",
     [("test_agent_pulse_policy.py", ["k: không mẫu thì đúng 1", "k: emoji hiếm tối đa 1.25",
                                      "lý do thắng emoji"])], None, None),
    ("23", "T3", "Một agent hoạt động ở nhóm A, ngủ ở nhóm B phục vụ đúng cả hai; không rò dữ liệu/quyền",
     "agent_pulse_host (ngữ cảnh)",
     [("test_agent_pulse_host.py", ["lượt quét của B không chứa nội dung của A",
                                    "tín hiệu của A không vào ngữ cảnh B"])], None, None),
    ("24", "T3", "Lần thức tìm việc hữu ích trong phạm vi, đúng đường quyền; không tự tạo chuỗi việc",
     "agent_pulse_host._act (propose/enqueue)",
     [("test_agent_pulse_host.py", ["không có quyền: enqueue đổi thành propose",
                                    "quét giới hạn không có nguồn: không tạo việc"]),
      ("test_team_t4_ops.py", ["sau thu hồi: lượt quét chỉ còn đề xuất (không xếp việc)"])], None, None),
    ("25", "T2/T3", "Bật lại Auto không phát lại phản hồi cũ; tắt vẫn nhận việc được giao, không mở vòng học",
     "feedback_projector (resumed_at)",
     [("test_feedback_wake.py", ["bật lại KHÔNG phát lại"]),
      ("test_team_t4_ops.py", ["bật lại Auto: phản hồi cũ không bị phát lại (vẫn stored_only)"])], None, None),
    ("26", "T3", "Tự áp một điều chỉnh được phép, lưu trước/sau, nhận phản hồi, quay lại; vòng học khép kín",
     "agent_review.maybe_self_review (L), agent_lessons (L), agent_pulse.apply_policy_change",
     [("test_agent_judge_review.py", ["tự rà soát chạy và áp 1 thay đổi",
                                      "quay lại: độ lệch về trước thay đổi"]),
      ("test_agent_lessons.py", ["thu hồi: revoked, không nạp nữa"]),
      ("test_t3_integration.py", ["lượt xem xét qua main.py: chốt chặn engine thật"])], "L", None),
    ("27", "T3", "Tín hiệu đồng đội mở đúng lượt xem xét; tag trong trích dẫn không mở lượt; chờ có sự kiện gỡ chờ",
     "agent_judge (related_review, release_waits)",
     [("test_related_review.py", ["trích dẫn bị bỏ trước khi phán xử",
                                  "chữ @Hoa trong văn bản không mở lượt nào cho Hoa"]),
      ("test_agent_judge_waits.py", ["tin mới của chủ gỡ lần chờ owner_message"])], None, None),
    ("28", "T3", "Kết quả tổng hợp không nhân điểm qua nhiều tầng; agent không tự nhận công; trưởng nhóm không nhận hết",
     "agent_pulse.attribute",
     [("test_pulse_contrib.py", ["tổng hợp nhiều tầng: tầng trước tính như MỘT đầu vào của tác giả",
                                 "trưởng nhóm không mặc nhiên nhận hết"])], None, None),
    ("+", "T2/T3/T4", "Lỗi kho và outbox, lệch đồng hồ",
     "feedback_projector (outbox), agent_pulse (tuổi âm kẹp)",
     [("test_feedback_projector.py", ["nhịp sau khi kho hết lỗi: chiếu xong, đúng một lý do"]),
      ("test_agent_pulse_policy.py", ["lệch đồng hồ: tuổi âm kẹp về 0"]),
      ("test_team_t4_ops.py", ["dòng nhật ký giữ pending để chiếu lại",
                               "đồng hồ lùi 1 giờ: đánh giá nhịp không lỗi"])], None, None),
    ("T3.fix", "T3", "Sửa sau ba vòng review T3: chốt engine thật trong Judge, huỷ và phạm vi của nhịp, học, phán xử",
     "agent_judge, agent_pulse_host, agent_review, server/io_offload.py",
     [(f, []) for f in ("test_t3_fix_judge_guard.py", "test_t3_fix_judge_main.py", "test_t3_fix_judge_p3.py",
                        "test_t3_fix_judge_retry.py", "test_t3_fix_pulse_cancel.py", "test_t3_fix_pulse_judge.py",
                        "test_t3_fix_pulse_main.py", "test_t3_fix_pulse_p3.py", "test_t3_fix_pulse_scope.py",
                        "test_t3_fix_review_learn.py", "test_t3_fix_judge_team_wait.py")], None, None),
    ("T4.a", "T4", "Lịch sử dài: nhóm 5.000 tin giữ yêu cầu, quyết định, nguồn đóng góp, phản hồi; số đọc kho có trần",
     "team_long_memory, team_runner._older_summary",
     [("test_team_t4_long_history.py", ["số lần đọc kho mỗi lượt KHÔNG tăng theo độ dài lịch sử",
                                        "nhóm 5.000 tin: còn nguồn đóng góp của kết quả gần nhất"])], None, None),
    ("T4.b", "T4", "Vận hành: nhiều tab, mất kết nối, restart, huỷ, retry, rời nhóm, đổi trưởng nhóm, thu hồi; nhóm "
     "bị chặn hay ngủ không chiếm suất", "team_runner, team_api, feedback_projector",
     [("test_team_t4_ops.py", ["pool một chỗ, hai nhóm vừa bị chặn trong lượt: nhóm C có việc vẫn được nhận và chạy",
                               "Thử lại: đúng một lượt mới, kết cục done"])], None, None),
]

# Trạng thái review và smoke theo MỐC (dữ liệu, không suy ra từ code). Hàng của ma trận ghép từ các mốc trong cột Mốc.
# Sửa ở đây khi một mốc có review hay smoke mới, rồi chạy --write để sinh lại bảng.
REVIEWS = {
    "T1": "T1 đã review (3 vòng, kết luận cuối PASS)",
    "T2": "T2 đã review (vòng 2 PASS WITH CHANGES, các P3 đã sửa)",
    "T3": "T3 đã review (ba review, một vòng sửa, xác minh vòng 2 và 3, kết luận cuối PASS)",
    "T4": "T4 đã review (review độc lập PASS WITH CHANGES ở bd6de2fd, đã sửa ở vòng này)",
}
SMOKES = {
    "T1": "T1: không có ghi nhận smoke trình duyệt trong gói T1-T4 này",
    "T2": ("T2: smoke trình duyệt trên sandbox có chốt (reaction chat riêng và nhóm, F5, hai tab, màn 375px, khởi động "
           "lại)"),
    "T3": ("T3: smoke trình duyệt trên sandbox có chốt (khung nhịp, lịch sử phán xử với nút sửa và cập nhật ngưỡng, bài "
           "học theo đúng hàng nhóm, chữ trạng thái trống, nền tối); chưa thử điện thoại thật"),
    "T4": "T4: không có smoke trình duyệt (chỉ test host, engine giả, bench)",
}


def milestones(ms):
    return [m for m in str(ms).split("/") if m in REVIEWS]


def test_path(name):
    return (JS if name.endswith(".js") else PY) / name


def file_checks(name, keys):
    p = test_path(name)
    if not p.is_file():
        return False, [f"thiếu file {name}"]
    text = p.read_text(encoding="utf-8")
    miss = [k for k in keys if k not in text]
    return not miss, [f"{name}: thiếu kiểm '{k}'" for k in miss]


fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:500]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def evaluate():
    """Trả {ca: {"status": coded|tested|pending, "missing": [...], "pending_on": part|None}}."""
    out = {}
    for case, ms, req, code, tests, owner, scen in MATRIX:
        missing, pend = [], None
        for name, keys in tests:
            ok, why = file_checks(name, keys)
            if ok:
                continue
            if not test_path(name).is_file() and owner and not PARTS.get(owner, False):
                pend = owner
                continue
            missing.extend(why)
        status = "pending" if pend else ("tested" if not missing else "broken")
        out[case] = {"status": status, "missing": missing, "pending_on": pend, "scenario": scen}
    return out


# ───────────── kịch bản host gọn ─────────────


def scenario_direct_and_judge(h):
    t, k = h.new_team("Ma trận ca 2", members=("lan", "minh", "hoa"), leader="lan")
    calls0, judge0 = h.fakes.team_calls, h.fakes.judge_calls
    h.send(t["team_id"], "Minh viết giúp mô tả ngắn", mentions=[k["minh"]], cm="mx2-direct")
    msgs = h.c.get(f"/teams/{t['team_id']}/messages{h.Q}").json()["messages"]
    answered = any(m["author_kind"] == "agent" and m["author_ref"] == k["minh"] for m in msgs)
    h.send(t["team_id"], "Hôm nay trời đẹp quá", cm="mx2-chat")
    judge_after_chat = h.fakes.judge_calls
    h.send(t["team_id"], "Cả nhóm ơi, ai xem giúp kế hoạch nhập hàng được không?", cm="mx2-question")
    return {"ca 2: được @ thì trả lời (engine giả)": answered and h.fakes.team_calls > calls0,
            "ca 2: câu hỏi chung mở lô phán xử (một lời gọi engine phán xử giả)":
                h.fakes.judge_calls >= judge_after_chat + 1 and h.fakes.judge_calls > judge0}


def scenario_six_reactions(h):
    t, k = h.new_team("Ma trận ca 4", members=("lan", "minh"), leader="lan")
    h.send(t["team_id"], "Minh trả lời giúp", mentions=[k["minh"]], cm="mx4")
    msgs = h.c.get(f"/teams/{t['team_id']}/messages{h.Q}").json()["messages"]
    mid = [m for m in msgs if m["author_kind"] == "agent"][-1]["id"]
    team_ok = all(h.c.post(f"/teams/{t['team_id']}/messages/{mid}/reaction{h.Q}",
                           json={"value": v, "nonce": f"mx4-t-{v}"}).json()["reaction"]["value"] == v
                  for v in ("like", "love", "haha", "wow", "sad", "angry"))
    st = h.main.get_store()
    sid = st.create_session(brain=str(h.BR), channel="agent:minh")
    st.append_message(sid, "user", "Hỏi")
    amid = st.append_message(sid, "assistant", "Đáp")
    dm_ok = all(h.c.post(f"/sessions/{sid}/messages/{amid}/reaction{h.Q}",
                         json={"value": v, "nonce": f"mx4-d-{v}"}).json()["reaction"]["value"] == v
                for v in ("like", "love", "haha", "wow", "sad", "angry"))
    cleared = h.c.post(f"/teams/{t['team_id']}/messages/{mid}/reaction{h.Q}",
                       json={"value": None, "nonce": "mx4-clear"}).json()["reaction"] is None
    return {"ca 4: sáu reaction ở nhóm qua route": team_ok, "ca 4: sáu reaction ở chat riêng qua route": dm_ok,
            "ca 4: bỏ hết thì reaction null": cleared}


SCENARIOS = {"direct_and_judge": scenario_direct_and_judge, "six_reactions": scenario_six_reactions}


def run_scenarios():
    spec = importlib.util.spec_from_file_location("_t4_host", PY / "_t4_host.py")
    h = importlib.util.module_from_spec(spec)
    sys.modules["_t4_host"] = h
    spec.loader.exec_module(h)
    out = {}
    for case, *_rest, scen in MATRIX:
        if scen:
            out.update(SCENARIOS[scen](h))
    out["chốt chặn engine thật còn nguyên sau mọi kịch bản"] = h.guards_ok()
    return out


# ───────────── bảng ─────────────


def run_tests_for_results():
    names = sorted({n for row in MATRIX for n, _ in row[4] if test_path(n).is_file()})
    res = {}
    for n in names:
        cmd = (["node", str(test_path(n))] if n.endswith(".js") else [sys.executable, "-X", "utf8", str(test_path(n))])
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=1200, env=BASE_ENV,
                               cwd=str(ROOT))
            res[n] = "ok" if p.returncode == 0 else "ĐỎ"
        except subprocess.TimeoutExpired:
            res[n] = "quá giờ"
    return res


def status_label(case, ms, ev):
    if ev["status"] == "pending":
        return f"đã code một phần, chờ phần {ev['pending_on']} của T3"
    if ev["status"] == "broken":
        return "thiếu test"
    rev = "; ".join(REVIEWS[m] for m in milestones(ms)) or "chưa review"
    return f"đã code, đã test, {rev}; chưa pilot"


def smoke_label(ms):
    return "; ".join(SMOKES[m] for m in milestones(ms)) or "không có"


def render(ev, results, sha, when):
    lines = ["# Ma trận nghiệm thu nhóm chat thích nghi (T1-T4)", "",
             "Sinh tự động bởi `python tests/python/test_team_acceptance_matrix.py --write` từ `MATRIX` trong file test đó; "
             "đừng sửa tay. Ca = GOAL mục 12 (1-28), hàng thêm \"+\" = lỗi kho/outbox và lệch đồng hồ, T4.a/T4.b = "
             "phần T4 của thiết kế mục 11.", "",
             f"Lần chạy cuối: {when}, head `{sha}`. Kết quả là của engine GIẢ qua host thật; chưa ca nào được pilot trên "
             "model thật, chưa đo VPS.", "",
             "Phần T3 đã ghép lúc sinh bảng: " + ", ".join(f"{k} {'có' if v else 'CHƯA'}" for k, v in PARTS.items()) + ".",
             "", "| Ca | Mốc | Yêu cầu | Mã | Test | Kết quả lần cuối | Smoke | Trạng thái |",
             "|---|---|---|---|---|---|---|---|"]
    for case, ms, req, code, tests, owner, scen in MATRIX:
        names = [n for n, _ in tests]
        if scen:
            names.append(f"kịch bản host `{scen}`")
        res = ", ".join(f"{n}: {results.get(n, 'thiếu file' if not test_path(n).is_file() else '?')}"
                        for n, _ in tests)
        lines.append(f"| {case} | {ms} | {req} | {code} | {', '.join(names)} | {res} | {smoke_label(ms)} | "
                     f"{status_label(case, ms, ev[case])} |")
    pend = [c for c, e in ev.items() if e["status"] == "pending"]
    lines += ["", "## Còn chờ", "",
              (f"- Ca {', '.join(pend)}: chờ phần L của T3 (bài học có phạm vi `agent_lessons`, góp ý đồng đội, tự rà "
               "soát `agent_review`, `agent_budget`)." if pend else "- Không ca nào chờ phần T3 (P, J, L đã ghép)."),
              "- Smoke trình duyệt: T2 và T3 đã làm trên sandbox có chốt (chưa thử điện thoại thật); T4 không có smoke "
              "trình duyệt riêng, chi phí vẽ chỉ đo bằng hàm vẽ thuần dưới node (tools/team_adaptive_ui_bench.js, hai tab "
              "là mô phỏng).",
              "- Mọi ca: chưa pilot trên model thật (gói pilot: docs/dev/team-chat-pilot-package.md, chưa chạy).", ""]
    return "\n".join(lines)


ev = evaluate()
for case, e in ev.items():
    if e["status"] == "pending":
        print(f"PEND ca {case}: chờ phần {e['pending_on']} của T3 (file thiết kế nêu chưa có)")
    else:
        check(f"ca {case}: file test chuyên trách có đủ tên kiểm then chốt", e["status"] == "tested", e["missing"])
for part, has in PARTS.items():
    if has:
        owned = [r[0] for r in MATRIX if r[5] == part]
        check(f"phần {part} đã ghép: không ca nào của nó còn pending", all(ev[c]["status"] != "pending" for c in owned))
check("ma trận phủ đủ ca 1-28 và hàng thêm", {str(i) for i in range(1, 29)} | {"+"} <= {r[0] for r in MATRIX})
check("mọi hàng có trạng thái review và smoke lấy từ dữ liệu theo mốc", all(milestones(r[1]) for r in MATRIX),
      [r[0] for r in MATRIX if not milestones(r[1])])

for name, ok in run_scenarios().items():
    check(name, ok)

if WRITE:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    results = run_tests_for_results()
    DOC.write_text(render(ev, results, sha, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")) + "\n",
                   encoding="utf-8", newline="\n")
    (DOC.with_suffix(".json")).write_text(json.dumps({"sha": sha, "results": results}, ensure_ascii=False, indent=1)
                                          + "\n", encoding="utf-8", newline="\n")
    print(f"đã ghi {DOC}")
else:
    doc = DOC.read_text(encoding="utf-8") if DOC.is_file() else ""
    check("bảng docs/dev/team-chat-acceptance-matrix.md có mặt", bool(doc))
    for case, ms, req, *_ in MATRIX:
        check(f"bảng có hàng ca {case} khớp yêu cầu của ma trận", f"| {case} | {ms} | {req} |" in doc)

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
raise SystemExit(0)
