"""Resonance A4: pilot nộp sản phẩm qua hợp đồng host (thiết kế chốt D1), engine THẬT chỉ ở lượt chat của trợ lý.

    JAVIS_RESONANCE_A4_PILOT=dry  python tests/python/test_resonance_a4_pilot.py         # KHÔNG gọi model, mọi ca
    JAVIS_RESONANCE_A4_PILOT=real JAVIS_RESONANCE_PILOT_SETTINGS=<settings.json thật> \\
        JAVIS_RESONANCE_A4_OUT=<thư mục hồ sơ, chưa tồn tại> python tests/python/test_resonance_a4_pilot.py
                                                                                        # cần người dùng duyệt riêng

Không đặt biến thì bỏ qua (CI và tests/run.py không gọi gì).

Điều pilot cần chứng minh bằng model thật (phần còn lại đã có test và smoke engine giả):
1. Lượt chat của TRỢ LÝ với đích mới: bộ não tự lập mục tiêu và nộp bản qua công cụ chung `javis_submit_deliverable`;
   host GIỮ bản nháp, xin quyền trên thẻ, KHÔNG có file đích nào trước khi chủ cho phép.
2. Chủ dự án bấm Cho phép (API như nút thẻ, gửi đúng yêu cầu, bản nháp, sha): host đăng ĐÚNG bytes đã giữ, tiếp nhận,
   KHÔNG gọi thêm model.
3. Lượt chat thứ hai (phạm vi đã cấp): bộ não sửa mục tiêu và nộp bản 2; host tự đăng, không hỏi lại quyền.
4. Giết và dựng lại server: không gọi thêm, không báo lặp, kho nhất quán; cuối cùng tạm dừng mục tiêu.

Hạn mức, hai lớp, không thay nhau:
- Sổ LƯỢT CHAT thật (ChatLedger): tối đa 2 lượt, giữ chỗ và fsync TRƯỚC mỗi lần gửi, không đặt lại, không hoàn. Lượt
  lỗi, hết giờ, không có turn_done: sổ ĐÓNG, không gửi lượt nào nữa, không tự thử lại. Sổ đã có từ lần chạy trước thì
  từ chối chạy (không bao giờ đặt về 0).
- Trần KHO `JAVIS_RESONANCE_CALL_CEILING=0` cho mọi tiến trình server: KHÔNG lượt việc nền hay phép thử nào được gọi
  engine (kho chặn trước lời gọi, giữ qua khởi động lại).
Đơn vị là lượt engine cấp host: một lượt chat có thể gồm nhiều request nội bộ của SDK, không phải số request hay token.

dry chạy mỗi ca bằng một tiến trình con (ca chính và các ca âm: cổng chi phí, cổng xác thực, quyền (thẻ cũ 409), biên
nhận đăng không thành, lỗi engine, bộ não không nộp, bộ não tự ghi file đích, thiếu bằng chứng, sổ có lượt giữ chỗ
không kết quả). Lượt chat ở dry là MÔ PHỎNG trong tiến trình bằng đúng các hàm công cụ dùng; mọi bước khác (server
thật, API thẻ, giết và dựng lại) như real.
"""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

MODE = os.environ.get("JAVIS_RESONANCE_A4_PILOT", "").strip().lower()
SCEN = os.environ.get("JAVIS_RESONANCE_A4_SCENARIO", "").strip()
if MODE not in ("dry", "real"):
    print("pilot A4: bỏ qua (đặt JAVIS_RESONANCE_A4_PILOT=dry hoặc real để chạy)")
    print("\nOK")
    sys.exit(0)

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
import _e2e_pilot_guard as G

CHAT_LIMIT = 2
STORE_CEILING = 0
TOKEN_MIN_S = 600
TURN_WALL_S = 900
PORT = int(os.environ.get("JAVIS_RESONANCE_A4_PORT", "7792"))
SLUG = "tro-ly-tom-tat"

# ───────────── Đầu vào ĐÓNG BĂNG: không chọn dữ liệu lúc chạy ─────────────
FROZEN = {
    "agent_md": ("---\nname: Trợ lý tóm tắt\ndescription: Soạn bản tóm tắt cuộc họp\ngroup: Content\n---\n"
                 "Bạn soạn bản tóm tắt cuộc họp ngắn gọn, đúng nội dung chủ dự án đưa.\n"),
    "deliverable": "Inbox/tom-tat-hop.md",
    "turn_1": ("(Dữ liệu mô phỏng để thử nghiệm.) Em soạn giúp anh bản tóm tắt cuộc họp sáng nay, ghi vào "
               "Inbox/tom-tat-hop.md, phải có mục Kết luận. Nội dung họp: chốt ra mắt sản phẩm ngày 20/11; chị Lan "
               "lo kế hoạch truyền thông; anh Minh kiểm lại ngân sách quảng cáo; cả nhóm họp lại thứ Hai tuần sau."),
    "turn_2": ("(Dữ liệu mô phỏng để thử nghiệm.) Em thêm vào bản tóm tắt Inbox/tom-tat-hop.md một mục Việc tiếp "
               "theo, mỗi việc một dòng có người phụ trách: chị Lan gửi kế hoạch truyền thông trước thứ Sáu, anh Minh "
               "gửi bảng ngân sách trước thứ Năm."),
    # Hợp đồng nội dung ĐỘC LẬP với tiêu chí bộ não đề xuất (chỉ hỗ trợ người review, không tự nghiệm thu).
    "content_v1": ["Kết luận", "20/11", "Lan", "Minh"],
    "content_v2": ["Việc tiếp theo", "Lan", "thứ Sáu", "Minh", "thứ Năm"],
}
# Hai bộ đầu vào, mỗi bộ ghim bằng hash; real BẮT BUỘC chọn đúng bộ đã duyệt (JAVIS_RESONANCE_A4_INPUTS), không mặc định.
# v1: lời giao gốc ("soạn giúp"), hash 48a31ab9... Theo hướng dẫn của host ("việc xong ngay trong lượt thì làm luôn"), lời
# này có thể được làm ngay trong lượt mà không lập mục tiêu; sự cố 11/10 (một lượt Claude Code lọt vào dry) cho thấy đúng
# như vậy. v2: lời giao giao trách nhiệm sau lượt ("lo tới khi anh duyệt, góp ý thì sửa tiếp"), hash 7762a2d7... Chỉ khác
# câu lượt 1; chờ review và anh chọn.
TURN_1_V2 = ("(Dữ liệu mô phỏng để thử nghiệm.) Em nhận lo giúp anh bản tóm tắt cuộc họp sáng nay cho tới khi anh duyệt: "
             "soạn vào Inbox/tom-tat-hop.md, phải có mục Kết luận; anh xem rồi góp ý thì em sửa tiếp. Nội dung họp: chốt "
             "ra mắt sản phẩm ngày 20/11; chị Lan lo kế hoạch truyền thông; anh Minh kiểm lại ngân sách quảng cáo; cả "
             "nhóm họp lại thứ Hai tuần sau.")
INPUTS = os.environ.get("JAVIS_RESONANCE_A4_INPUTS", "").strip().lower()
INPUT_SHA = {"v1": "48a31ab95b3c6be873440c1e7e600f98ddd9cf597f9b09d5afb64b456d83693c",
             "v2": "7762a2d7005ad79a4e596605f898d01ee06ee59b34a42ff02a03d32f03693613"}
if INPUTS == "v2":
    FROZEN = {**FROZEN, "turn_1": TURN_1_V2}
FROZEN_SHA = hashlib.sha256(json.dumps(FROZEN, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _sha(b) -> str:
    return hashlib.sha256(b if isinstance(b, bytes) else str(b).encode("utf-8")).hexdigest()


def _sha_file(p) -> str:
    return _sha(Path(p).read_bytes()) if p and Path(p).is_file() else ""


# ───────────── Sổ lượt chat thật ─────────────

class ChatLedger:
    """Sổ lượt chat, ghi xuống đĩa (fsync rồi os.replace) TRƯỚC mỗi lần gửi. `create()` chỉ dùng ở đầu một lần chạy mới
    và từ chối khi sổ đã có. Mọi lỗi đọc, sai cấu trúc, lượt giữ chỗ chưa có kết quả hay kết quả không phải `ok` đều
    ĐÓNG sổ: không gửi thêm.

    Hai cổng kiểm NGAY trong `reserve` (review cổng pilot, P2-2), nên không chỗ gọi nào quên được:
    - `gate()`: lý do dừng khác rỗng khi một kiểm kỹ thuật hay hợp đồng bắt buộc đã hỏng; khi đó không cấp lượt nào nữa.
    - `token_check()`: (được, lý do) về đăng nhập gói thuê bao và hạn token, chạy trước MỖI lượt (không gọi model)."""

    OK = "ok"
    # Kết cục một lượt: chỉ `ok` (engine_status ok VÀ turn_status completed) là thành công (review cổng pilot, P2-1).
    STATUSES = ("reserved", "ok", "engine_error", "engine_unknown", "cancelled", "no_status", "timeout", "no_turn_done",
                "transport_error")

    def __init__(self, path: Path, limit: int):
        self.path, self.limit = Path(path), int(limit)
        self.gate = None
        self.token_check = None

    @classmethod
    def create(cls, path: Path, limit: int, run_id: str) -> "ChatLedger":
        led = cls(path, limit)
        if led.path.exists():
            raise FileExistsError("sổ lượt chat đã có từ lần chạy trước; không đặt lại")
        led._write({"run_id": run_id, "limit": int(limit), "turns": []})
        return led

    def _write(self, st: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(st, ensure_ascii=False, indent=1))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def _load(self) -> tuple:
        try:
            st = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return None, f"ledger_unreadable: {type(e).__name__}"
        turns = st.get("turns") if isinstance(st, dict) else None
        if not isinstance(turns, list) or st.get("limit") != self.limit:
            return None, "ledger_bad_shape"
        for i, t in enumerate(turns, 1):
            if not isinstance(t, dict) or t.get("n") != i or t.get("status") not in self.STATUSES:
                return None, f"ledger_bad_turn_{i}"
        return st, ""

    def halted(self) -> str:
        st, err = self._load()
        if err:
            return err
        for t in st["turns"]:
            if t["status"] == "reserved":
                return "reserved_without_outcome"
            if t["status"] != self.OK:
                return f"turn_{t['n']}_{t['status']}"
        return ""

    def used(self) -> int:
        st, _ = self._load()
        return len(st["turns"]) if st else self.limit

    def reserve(self, label: str) -> tuple:
        h = self.halted()
        if h:
            return False, f"halted: {h}"
        st, _ = self._load()
        if len(st["turns"]) >= self.limit:
            return False, "limit"
        stop = self.gate() if self.gate else ""
        if stop:
            return False, f"contract: {stop}"
        if self.token_check:
            ok, why = self.token_check()
            if not ok:
                return False, f"token: {why}"
        st["turns"].append({"n": len(st["turns"]) + 1, "label": label, "status": "reserved", "at": time.time()})
        self._write(st)
        return True, ""

    def settle(self, label: str, status: str) -> None:
        st, err = self._load()
        if err:
            return
        for t in st["turns"]:
            if t["label"] == label and t["status"] == "reserved":
                t["status"], t["settled_at"] = status, time.time()
        self._write(st)


def audit_notices(rows: list, msgs: list, final_revision: int, goal_id: str, notify_kinds) -> dict:
    """Đối chiếu tin báo của MỘT mục tiêu (review cổng pilot, P2-3; sửa sau pilot real-1). Hàm thuần để test.

    rows: dòng outbox của mục tiêu `{"id", "kind", "revision", "delivered"}`; msgs: thẻ có khoá báo cáo trong phiên trợ
    lý `{"key", "goal_id", "receipt", "revision"?, "turn_event"?}` (đọc bằng `read_notice_evidence`). Chỉ xét loại tin
    host thật sự gửi (`notify_kinds`); tin nội bộ không được gửi nên không đòi.
    Phiên có HAI loại khoá host đặt, kiểm riêng:
    - `outbox:<id>`: tin báo. Có ít nhất một tin đã gửi (danh sách rỗng không đạt), không còn tin chờ gửi; mỗi tin đã gửi
      có ĐÚNG một tin trong phiên, đúng mục tiêu, có biên nhận của host (và đúng revision của dòng outbox khi thẻ ghi).
    - `turn:<message_ref>`: thẻ "Em đang hướng tới" host đặt sau lượt chat lập hay sửa mục tiêu (`_resonance_push_card`).
      Được có thêm, nhưng phải đúng mục tiêu, có biên nhận khớp CHÍNH tin đó, và nối được tới sự kiện lập hay sửa mục
      tiêu cùng message_ref, cùng revision (`turn_event`). Thẻ turn không bao giờ thay tin outbox bắt buộc.
    Khoá khác, khoá lặp đều không đạt. Pilot real-1 (11/10) cho thấy bản cũ đòi tập khoá bằng đúng tập outbox nên loại
    nhầm thẻ turn hợp lệ; dry mô phỏng lượt chat nên không có thẻ này.
    - matched_ok: cả hai nhóm đạt, không khoá lạ, không lặp.
    - required_ok: matched_ok VÀ có ít nhất một tin outbox đã gửi ở đúng revision cuối của mục tiêu."""
    rows = [n for n in rows if n["kind"] in notify_kinds]
    pending = [n["id"] for n in rows if not n["delivered"]]
    delivered = {f"outbox:{n['id']}": n for n in rows if n["delivered"]}
    keys = [m["key"] for m in msgs]
    outbox_msgs = [m for m in msgs if str(m["key"]).startswith("outbox:")]
    turn_msgs = [m for m in msgs if str(m["key"]).startswith("turn:")]
    strangers = [k for k in keys if not str(k).startswith(("outbox:", "turn:"))]
    outbox_ok = (bool(delivered) and not pending and {m["key"] for m in outbox_msgs} == set(delivered)
                 and all(m["goal_id"] == goal_id and m["receipt"] is True
                         and m.get("revision") in (None, delivered[m["key"]]["revision"])
                         for m in outbox_msgs if m["key"] in delivered))
    turn_ok = all(m["goal_id"] == goal_id and m["receipt"] is True and m.get("turn_event") is True
                  and type(m.get("revision")) is int and 1 <= m["revision"] <= final_revision for m in turn_msgs)
    matched = outbox_ok and turn_ok and not strangers and len(keys) == len(set(keys))
    at_rev = [k for k, n in delivered.items() if n["revision"] == final_revision]
    return {"final_revision": final_revision,
            "outbox": [{k: n[k] for k in ("id", "kind", "revision", "delivered")} for n in rows],
            "pending": pending, "session_keys": sorted(keys), "messages_with_report": len(msgs),
            "turn_keys": sorted(m["key"] for m in turn_msgs), "outbox_ok": bool(outbox_ok), "turn_ok": bool(turn_ok),
            "stranger_keys": sorted(strangers),
            "required_keys": sorted(at_rev), "matched_ok": bool(matched), "required_ok": bool(matched and at_rev)}


def read_notice_evidence(conv_db, goal_db, sid: str, goal_id: str, parse_blocks) -> tuple:
    """Đọc CHỈ ĐỌC (SQLite `mode=ro`) dữ liệu cho `audit_notices`: dòng outbox của mục tiêu và các thẻ có khoá báo cáo
    trong phiên trợ lý. Dùng chung cho bộ chạy (server đang chạy) và hậu kiểm offline trên bản sao sandbox: không dựng
    server, không gọi model, không ghi gì.

    - receipt: bảng biên nhận có ĐÚNG một dòng cho (khoá, mục tiêu), và dòng đó trỏ đúng phiên và đúng CHÍNH tin này
      (biên nhận của tin khác, phiên khác không tính).
    - turn_event (thẻ `turn:<message_ref>`): message_ref thuộc chính phiên này, và kho mục tiêu có sự kiện lập hay sửa
      mục tiêu (`created`, `reframe`, `continued`, đúng các loại `route_request` dùng để đặt thẻ) của mục tiêu này với
      cùng message_ref và cùng revision ghi trên thẻ."""
    import sqlite3
    from pathlib import Path as _P

    def _ro(p):
        return sqlite3.connect(_P(p).resolve().as_uri() + "?mode=ro", uri=True)

    g = _ro(goal_db)
    try:
        rows = [{"id": r[0], "kind": r[1], "delivered": r[2] is not None,
                 "revision": int((json.loads(r[3] or "{}") or {}).get("revision") or 0)}
                for r in g.execute("SELECT id, kind, delivered_at, payload_json FROM outbox WHERE goal_id=? ORDER BY id",
                                   (goal_id,))]
        events = g.execute("SELECT revision, kind, message_ref FROM goal_events WHERE goal_id=? AND message_ref<>''",
                           (goal_id,)).fetchall()
    finally:
        g.close()
    s = _ro(conv_db)
    try:
        msgs = []
        for mid, role, content in s.execute("SELECT id, role, content FROM messages WHERE session_id=? ORDER BY id",
                                            (sid,)).fetchall():
            if role != "assistant":
                continue
            for b in parse_blocks(content or ""):
                key = b.get("report")
                if not key:
                    continue
                gid, rev = str(b.get("goal_id") or ""), b.get("revision")
                rc = s.execute("SELECT session_id, message_id FROM report_receipts WHERE report_key=? AND goal_id=?",
                               (str(key), gid)).fetchall()
                ref = str(key)[len("turn:"):] if str(key).startswith("turn:") else ""
                linked = (bool(ref) and ref.startswith(f"msg:{sid}:") and gid == goal_id
                          and any(e[0] == rev and e[1] in ("created", "reframe", "continued") and e[2] == ref
                                  for e in events))
                msgs.append({"key": key, "goal_id": gid, "revision": rev, "message_id": mid,
                             "receipt": [tuple(x) for x in rc] == [(sid, mid)], "turn_event": linked})
    finally:
        s.close()
    return rows, msgs


# ───────────── Ca chạy (một tiến trình mỗi ca) ─────────────

def run_case(scen: str) -> int:
    """Một ca trọn: dựng, hai lượt chat (thật hay mô phỏng), Cho phép, giết và dựng lại, đối soát. Trả mã thoát."""
    base = Path(tempfile.mkdtemp(prefix="rsa4p-", dir=os.environ.get("TEMP") or None)).resolve()
    state, brains = base / "state", base / "brains"
    brain = brains / "Brain Default"
    for d in (state, brain / "Inbox", brain / "agents", brain / "Notes"):
        d.mkdir(parents=True, exist_ok=True)
    (brain / "agents" / f"{SLUG}.md").write_text(FROZEN["agent_md"], encoding="utf-8", newline="\n")
    keep = brain / "Notes" / "ghi-chu-cu.md"
    keep.write_text("Ghi chú cũ, không được đụng tới.\n", encoding="utf-8", newline="\n")
    keep_sha = _sha_file(keep)
    deliv = brain / FROZEN["deliverable"]
    if scen == "receipt":
        deliv.write_text("Bản anh tự viết trước đó.\n", encoding="utf-8", newline="\n")

    out_dir = Path(os.environ.get("JAVIS_RESONANCE_A4_OUT") or (base / "out"))
    if MODE == "real":
        if INPUTS not in INPUT_SHA or FROZEN_SHA != INPUT_SHA[INPUTS]:
            print("FAIL pilot: real cần JAVIS_RESONANCE_A4_INPUTS=v1 hay v2 (bộ đầu vào đã duyệt) và hash phải khớp")
            return 1
        if not os.environ.get("JAVIS_RESONANCE_A4_OUT"):
            print("FAIL pilot: chế độ real cần JAVIS_RESONANCE_A4_OUT (thư mục hồ sơ, chưa tồn tại)")
            return 1
        if out_dir.exists():
            print("FAIL pilot: JAVIS_RESONANCE_A4_OUT đã tồn tại; mỗi lần chạy thật một thư mục mới")
            return 1
        src = os.environ.get("JAVIS_RESONANCE_PILOT_SETTINGS", "")
        if not src or not Path(src).is_file():
            print("FAIL pilot: thiếu JAVIS_RESONANCE_PILOT_SETTINGS trỏ tới settings.json thật")
            return 1
        _m = (json.loads(Path(src).read_text(encoding="utf-8")).get("model") or {})
        model = {k: _m[k] for k in ("auxiliary", "main", "engine", "claude_model") if k in _m}
    else:
        model = {"auxiliary": {"provider": "grok-cli", "model": "grok-dry"}}
    out_dir.mkdir(parents=True, exist_ok=True)
    assert not any(k in model for k in ("claude_auth", "anthropic_api_key")), "settings pilot không có chế độ API key"
    (state / "settings.json").write_text(json.dumps({"model": model, "setup_done": True}, ensure_ascii=False),
                                         encoding="utf-8", newline="\n")
    # Sổ lượt engine của server sandbox (usage_store) tạo SẴN, rỗng và hợp lệ, trước khi dựng server nào: "chưa có file"
    # không bao giờ bị hiểu là 0 lượt. Chỉ là state tạm của ca, không đụng kho usage của máy thật (review cổng pilot vòng 2).
    (state / "usage.json").write_text(json.dumps({"days": {}, "total": {}}), encoding="utf-8", newline="\n")
    (state / "usage-events.jsonl").write_text("", encoding="utf-8", newline="\n")

    os.environ["JAVIS_STATE_DIR"] = str(state)
    os.environ["BRAINS_DIR"] = str(brains)
    import luot_dang_chay
    import resonance as R
    import resonance_store as RS
    import sessions as SSm
    import turn_context

    key = str(brain.resolve())
    owner = RS.Principal("owner", "owner", key)
    host = RS.Principal("agent", "javis", key)
    origin = f"http://127.0.0.1:{PORT}"
    approved = (json.loads(os.environ["JAVIS_RESONANCE_A4_APPROVED"]) if os.environ.get("JAVIS_RESONANCE_A4_APPROVED")
                else {} if MODE == "real" else {"aux": {"provider": "grok-cli"}})
    pilot_vars = ("JAVIS_RESONANCE_TICK_PAUSED", "JAVIS_RESONANCE_CALL_CEILING", "JAVIS_CLAUDE_CLI", "JAVIS_CLAUDE_BIN",
                  "JAVIS_STATE_DIR", "BRAINS_DIR", "JAVIS_PORT")
    env0 = {k: v for k, v in G.clean_env(dict(os.environ)).items() if k not in pilot_vars}
    fails, log = [], []

    def check(name, cond, hard=False):
        """Ghi một kiểm. `hard`: kiểm là tiền điều kiện của bước có tác động phía sau (lượt chat, Cho phép, chuyển giai
        đoạn); hỏng thì DỪNG ngay, chỉ còn lưu bằng chứng và dọn (review cổng pilot, P2-2). Kiểm mềm hỏng vẫn chặn mọi lượt
        chat sau đó, vì sổ lượt hỏi `fails` trong chính `reserve`."""
        print(("ok   " if cond else "FAIL ") + name)
        log.append({"check": name, "ok": bool(cond)})
        if not cond:
            fails.append(name)
            if hard:
                raise SystemExit(f"dừng: kiểm bắt buộc hỏng: {name}")
        return bool(cond)

    def store():
        return RS.GoalStore(state / "resonance.sqlite3")

    def sess():
        return SSm.SessionStore(state / "conversations.db")

    # Trợ lý bật qua kho như chủ dự án bật; phiên của trợ lý tạo sẵn (kênh agent:<slug>) để lượt chat là lượt trợ lý.
    agent = store().agent_set_enabled(owner, SLUG, True)
    sid = sess().create_session(brain=key, engine="pilot", model="pilot", channel=f"agent:{SLUG}")

    rep = {"mode": MODE, "scenario": scen, "inputs": INPUTS or "v1", "frozen_sha256": FROZEN_SHA, "chat_limit": CHAT_LIMIT,
           "store_ceiling": STORE_CEILING, "unit": "lượt engine cấp host (không phải request nội bộ hay token)",
           "steps": {}, "turns": {}}
    run_id = f"a4-{int(time.time())}-{os.getpid()}"
    ledger_path = out_dir / "chat-ledger.json"
    if scen == "reserved":
        # Lần chạy trước chết giữa lượt: sổ còn dòng giữ chỗ không có kết quả.
        ChatLedger.create(ledger_path, CHAT_LIMIT, "trước").reserve("turn_1")
    try:
        led = ChatLedger.create(ledger_path, CHAT_LIMIT, run_id)
    except FileExistsError as e:
        led = ChatLedger(ledger_path, CHAT_LIMIT)
        rep["ledger_refused"] = str(e)
    limit_env = int(os.environ.get("JAVIS_RESONANCE_A4_MAX_CALLS", str(CHAT_LIMIT)))

    def resolve_cli() -> str:
        cp = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'server'); "
                             "from claude_cli import tim_binary; print(tim_binary('claude') or '')"],
                            cwd=str(ROOT), env=env0, capture_output=True, text=True, timeout=60)
        return (cp.stdout or "").strip().splitlines()[-1] if (cp.stdout or "").strip() else ""

    def resolve_engines() -> dict:
        code = ("import json, sys; sys.path.insert(0, 'server'); import aux_engine, config; "
                "m = (config.read_settings().get('model') or {}); "
                "print(json.dumps({'main': aux_engine.main_spec(), 'aux': aux_engine.read_spec(), "
                "'claude_model': m.get('claude_model')}))")
        cp = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env={**env0, "JAVIS_STATE_DIR": str(state)},
                            capture_output=True, text=True, timeout=120)
        try:
            return json.loads((cp.stdout or "").strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return {}

    def auth_gate(cli: str) -> dict:
        """Không gọi model: engine sẽ chạy đúng cấu hình đã duyệt; `claude auth status` bằng đúng binary, cwd (brain),
        môi trường đã lọc là gói thuê bao Anthropic gốc; settings không có apiKeyHelper hay env chọn nhà cung cấp;
        không có settings do quản trị đặt. Chỉ ghi siêu dữ liệu, không ghi khoá hay token."""
        out = {"ok": False, "why": "", "engines": resolve_engines(), "approved": approved}
        if MODE == "dry":
            st = ({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"} if scen == "auth"
                  else {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                        "subscriptionType": "max"})
            ok, why = G.check_auth_status(st)
            out.update(ok=ok, why=why, auth={"dry_simulated": G.auth_metadata(st)})
            return out
        ok_e, why_e = G.check_engines(out["engines"], approved)
        if not approved or not ok_e:
            out["why"] = "chưa có cấu hình engine đã duyệt" if not approved else f"engine khác đã duyệt: {why_e}"
            return out
        if not cli:
            out["why"] = "không tìm thấy binary claude"
            return out
        try:
            st = json.loads(subprocess.run([cli, "auth", "status", "--json"], cwd=str(brain), env=env0,
                                           capture_output=True, text=True, timeout=60).stdout or "{}")
        except Exception as e:  # noqa: BLE001
            out["why"] = f"không chạy được auth status: {type(e).__name__}"
            return out
        out["auth"] = G.auth_metadata(st)
        ok, why = G.check_auth_status(st)
        if not ok:
            out["why"] = why
            return out
        cfg_dir = Path(st.get("configDirectory") or (Path.home() / ".claude"))
        paths = G.settings_paths(cfg_dir, brain)
        out["settings"] = G.scan_settings(paths)
        out["managed_sources"] = G.managed_sources(cfg_dir)
        out["not_assessed"] = G.ancillary_sources(paths)
        if out["settings"]["risky"]:
            out["why"] = "nguồn settings có apiKeyHelper hay env chọn nhà cung cấp/khoá"
        elif out["managed_sources"]:
            out["why"] = "có nguồn settings do quản trị đặt; môi trường này pilot chưa hỗ trợ"
        else:
            out["ok"] = True
        return out

    class Server:
        def __init__(self, cli: str):
            self.n, self.proc, self.cli, self.started = 0, None, cli, []

        def start(self, tick_paused: bool, wait_s: int = 150) -> float:
            self.n += 1
            env = {**env0, "JAVIS_PORT": str(PORT), "JAVIS_STATE_DIR": str(state), "BRAINS_DIR": str(brains),
                   "JAVIS_REQUIRE_LOGIN": "0", "PYTHONUTF8": "1", "JAVIS_RESONANCE_CALL_CEILING": str(STORE_CEILING)}
            if self.cli:
                env["JAVIS_CLAUDE_CLI"] = self.cli
            if tick_paused:
                env["JAVIS_RESONANCE_TICK_PAUSED"] = "1"
            self.started.append({"n": self.n, "tick_paused": tick_paused, "call_ceiling": STORE_CEILING})
            logf = open(base / f"server-{self.n}.log", "w", encoding="utf-8")
            self.proc = subprocess.Popen([sys.executable, "server/main.py"], cwd=str(ROOT), env=env, stdout=logf,
                                         stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            import httpx
            t0 = time.time()
            while time.time() - t0 < wait_s:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"server thoát sớm (server-{self.n}.log)")
                try:
                    if httpx.get(f"{origin}/health", timeout=2).status_code == 200:
                        return time.time() - t0
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1)
            raise RuntimeError("server không lên kịp")

        def kill(self) -> None:
            if self.proc is None:
                return
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            else:
                self.proc.kill()
            self.proc.wait(timeout=30)
            self.proc = None
            time.sleep(2)

    def http(method, path, **kw):
        import httpx
        r = httpx.request(method, f"{origin}{path}", params={"brain": "brain"}, headers={"Origin": origin},
                          timeout=60, **kw)
        try:
            return r.status_code, r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, {}

    def store_calls() -> int:
        import sqlite3
        c = sqlite3.connect(str(state / "resonance.sqlite3"))
        n = int(c.execute("SELECT COALESCE(SUM(calls_used),0) FROM goals").fetchone()[0])
        try:
            n += int(c.execute("SELECT COUNT(*) FROM call_ledger WHERE status='used'").fetchone()[0])
        except Exception:  # noqa: BLE001
            pass
        c.close()
        return n

    def goals_of_session() -> list:
        import sqlite3
        c = sqlite3.connect(str(state / "resonance.sqlite3"))
        ids = [r[0] for r in c.execute("SELECT id FROM goals WHERE brain_id=? AND session_id=?", (key, sid))]
        c.close()
        return [store().get(owner, i) for i in ids]

    def notices(goal_id) -> list:
        import sqlite3
        c = sqlite3.connect(str(state / "resonance.sqlite3"))
        rows = c.execute("SELECT id, kind, delivered_at, payload_json FROM outbox WHERE goal_id=? ORDER BY id",
                         (goal_id,)).fetchall()
        c.close()
        return [{"id": r[0], "kind": r[1], "delivered": r[2] is not None,
                 "revision": int((json.loads(r[3] or "{}") or {}).get("revision") or 0)} for r in rows]

    def notice_audit(goal_id) -> dict:
        """Đọc outbox và phiên trợ lý (chỉ đọc, `read_notice_evidence`) rồi đối chiếu bằng `audit_notices`."""
        g_now = store().get(owner, goal_id)
        rows, msgs = read_notice_evidence(state / "conversations.db", state / "resonance.sqlite3", sid, goal_id,
                                          R.parse_goal_blocks)
        return {"final_status": g_now.status,
                **audit_notices(rows, msgs, g_now.revision, goal_id, R.NOTIFY_KINDS)}

    def reports() -> list:
        """(khoá báo cáo, có biên nhận) của mọi thẻ tin báo trong phiên trợ lý."""
        ss, out = sess(), []
        for m in ss.get_messages(sid):
            if m["role"] != "assistant":
                continue
            for b in R.parse_goal_blocks(m.get("content") or ""):
                rk = b.get("report") or ""
                if rk:
                    out.append((rk, bool(ss.report_receipt(sid, rk, b["goal_id"]))))
        return out

    def cut(v, n=3000):
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        return s if len(s) <= n else s[:n] + f"...(+{len(s) - n})"

    async def ws_chat(message: str):
        """Gửi MỘT tin như dashboard, đọc khung tới `turn_done`. Trả (loại khung, khung công cụ, câu trả lời, khung kết
        thúc). Khung kết thúc giữ NGUYÊN siêu dữ liệu host ghi (`engine_status`, `engine_error`, `turn_status`) để phân
        loại kết cục; thiếu `turn_done` thì là None (review cổng pilot, P2-1)."""
        if MODE != "real":
            # Dry KHÔNG BAO GIỜ gửi tin qua /ws: server sandbox vẫn chọn bộ não theo luật runtime, và một lượt ở đó có thể
            # là lượt model thật (sự cố 11/10/2026: settings dry chỉ đặt provider API nên runtime lùi về Claude Code).
            raise RuntimeError("dry_never_sends_ws")
        import websockets
        frames, tools, answer, done = [], [], [], None
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws", origin=origin, max_size=None) as ws:
            await asyncio.wait_for(ws.recv(), 20)
            await ws.send(json.dumps({"message": message, "brain": "brain", "session_id": sid}))
            t0 = time.time()
            while time.time() - t0 < TURN_WALL_S:
                o = json.loads(await asyncio.wait_for(ws.recv(), TURN_WALL_S))
                t = o.get("type")
                frames.append(t)
                if t in ("tool_call", "tool", "tool_result"):
                    tools.append({k: cut(o.get(k)) for k in ("type", "tool", "name", "detail", "content", "input")
                                  if o.get(k) not in (None, "")})
                if t in ("response", "stream", "text") and o.get("content"):
                    answer.append(str(o.get("content")))
                if t == "turn_done":
                    done = {k: o.get(k) for k in ("engine_status", "engine_error", "turn_status", "engine", "model")
                            if k in o}
                    break
        return frames, tools, "".join(answer)[-6000:], done

    def sim_turn(n: int, message: str) -> dict:
        """Lượt chat MÔ PHỎNG (dry) bằng đúng các hàm công cụ dùng, trong ngữ cảnh lượt như run_turn dựng."""
        ss = sess()
        mid = ss.append_message(sid, "user", message)
        ref = R.message_ref(sid, mid)
        ag = store().agent(key, SLUG)
        p_agent = RS.Principal("agent", ag["agent_key"], key)
        st = store()
        deps = R.GoalDeps(engine_factory=lambda s, t: (None, {"blocked": "dry"}), budget=R.CallBudget(0),
                          clock=time.time, store=st, principal=host, brain_root=key)
        if scen == "engine" and n == 1:
            raise RuntimeError("engine_error (mô phỏng)")
        tok = turn_context.bind(turn_context.make(
            "dashboard", chat_id=sid, la_chu=True, session_id=sid, message_id=mid,
            agent={"key": ag["agent_key"], "slug": SLUG, "config_version": ag["config_version"]},
            authority_seq=st.authority_seq()))
        k = luot_dang_chay.bat_dau(f"web:{sid}", key, msg_id=mid, user_text=message)
        crit = [{"description": "Có mục Kết luận", "evaluator": "artifact_contract",
                 "params": {"path": FROZEN["deliverable"], "must_contain": ["Kết luận"]}}]
        body = ("# Tóm tắt cuộc họp\n\n- Ra mắt sản phẩm 20/11.\n- Chị Lan: kế hoạch truyền thông.\n"
                "- Anh Minh: ngân sách quảng cáo.\n\n## Kết luận\n\nHọp lại thứ Hai tuần sau.\n")
        if n == 2:
            body += "\n## Việc tiếp theo\n\n- Chị Lan gửi kế hoạch truyền thông trước thứ Sáu.\n" \
                    "- Anh Minh gửi bảng ngân sách trước thứ Năm.\n"
        try:
            if n == 1:
                g = asyncio.run(R.form_goal(ref, {
                    "principal": p_agent, "brain_root": key, "session_id": sid, "message_id": mid,
                    "user_text": message, "constraints": [], "budget_calls": 4,
                    "proposal": {"understanding": "Bản tóm tắt cuộc họp sáng nay", "relevant_quote": message[40:100],
                                 "criteria": crit, "horizon": {"kind": "review", "at_iso": "2027-01-20T09:00:00+07:00"},
                                 "stage": "delivery", "mode": "achieve"},
                    "hold_until": time.time() + R.HANDOFF_HOLD_S, "authority_seq": st.authority_seq(),
                    "agent_key": ag["agent_key"], "agent_version": ag["config_version"]},
                    R.GoalDeps(engine_factory=lambda s, t: (None, {}), budget=R.CallBudget(0), store=st)))
            else:
                g0 = goals_of_session()[0]
                g = R.revise_goal(st, p_agent, g0.id, g0.revision,
                                  {"understanding": "Bản tóm tắt cuộc họp sáng nay, thêm Việc tiếp theo",
                                   "relevant_quote": message[40:100], "criteria": crit},
                                  {"message_ref": ref, "session_id": sid, "message_id": mid, "user_text": message,
                                   "constraints": [], "user_unsure": False, "reason": "góp ý",
                                   "hold_until": time.time() + R.HANDOFF_HOLD_S, "authority_seq": st.authority_seq(),
                                   "agent_key": ag["agent_key"], "agent_version": ag["config_version"]})[0]
            if scen == "native" and n == 1:
                deliv.write_text(body, encoding="utf-8", newline="\n")
            if not (scen == "nosubmit" and n == 1):
                R.submit_deliverable(turn_context.current(), {"path": FROZEN["deliverable"], "content": body}, deps)
        finally:
            luot_dang_chay.ket_thuc(k)
            turn_context.reset(tok)
        R.handoff_after_turn(g.id, ref, deps)
        ss.append_message(sid, "assistant", "Em đã nhận việc.\n" + R.goal_block(g.id, g.revision))
        return {"frames": {"simulated": 1}, "tools": [], "answer": "(mô phỏng)"}

    def chat(n: int, message: str) -> tuple:
        """Một lượt chat qua sổ: giữ chỗ TRƯỚC khi gửi (sổ tự từ chối khi một kiểm bắt buộc đã hỏng hay token không
        đạt), chốt kết quả sau. Không thử lại.

        Thành công CHỈ khi khung `turn_done` của host báo `engine_status == "ok"` VÀ `turn_status == "completed"`. Lỗi
        engine (kể cả lỗi làm mới OAuth), không rõ, thiếu trạng thái, bị huỷ, hết giờ hay không có `turn_done`: lượt vẫn
        tính là đã dùng, sổ đóng, không gửi tiếp. Đây là lỗi KỸ THUẬT, không phải bằng chứng bộ não định tuyến sai
        (review cổng pilot, P2-1)."""
        label = f"turn_{n}"
        if led.used() + 1 > limit_env:
            return False, "cost_gate: vượt hạn mức lượt chat đã duyệt"
        ok, why = led.reserve(label)
        if not ok:
            return False, why
        before = G.snapshot_files(brain)
        t0 = time.time()
        try:
            if MODE == "real":
                frames, tools, answer, done = asyncio.run(ws_chat(message))
                if done is None:
                    status = "no_turn_done"
                elif "engine_status" not in done or "turn_status" not in done:
                    status = "no_status"
                elif done["turn_status"] != "completed":
                    status = "cancelled" if done["turn_status"] == "cancelled" else "engine_error"
                elif done["engine_status"] == "ok":
                    status = "ok"
                else:
                    status = "engine_unknown" if done["engine_status"] == "unknown" else "engine_error"
                res = {"frames": {k: frames.count(k) for k in sorted(set(f for f in frames if f))},
                       "frame_order": [f for f in frames if f][-40:], "turn_done": done, "tools": tools,
                       "answer": answer}
            else:
                res = sim_turn(n, message)
                status = "ok"
        except TimeoutError:
            status, res = "timeout", {}
        except RuntimeError as e:
            status, res = ("engine_error" if "engine_error" in str(e) else "transport_error"), {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            status, res = "transport_error", {"error": f"{type(e).__name__}: {e}"}
        led.settle(label, status)
        res.update(status=status, seconds=round(time.time() - t0, 1),
                   brain_files=G.snapshot_diff(before, G.snapshot_files(brain)))
        rep["turns"][label] = res
        return status == "ok", status

    def token_gate() -> tuple:
        """Trước MỖI lượt chat (không gọi model): đăng nhập gói thuê bao gốc bằng đúng binary, cwd và môi trường đã lọc,
        và hạn token còn ít nhất TOKEN_MIN_S (chỉ đọc `expiresAt`, không đọc hay ghi giá trị token). Giảm, không loại hẳn,
        rủi ro token hết hạn giữa lượt (pilot e2e lần 4)."""
        if MODE != "real":
            if scen == "token" and led.used() >= 1:
                return False, "token còn 100 giây (giả lập)"
            return True, ""
        try:
            st = json.loads(subprocess.run([cli, "auth", "status", "--json"], cwd=str(brain), env=env0,
                                           capture_output=True, text=True, timeout=60).stdout or "{}")
        except Exception as e:  # noqa: BLE001
            return False, f"không chạy được auth status: {type(e).__name__}"
        ok, why = G.check_auth_status(st)
        if not ok:
            return False, why
        import claude_token_gate
        exp = claude_token_gate.han_token()
        left = round(exp - time.time()) if exp else None
        rep.setdefault("token_checks", []).append({"before_turn": led.used() + 1, "seconds_left": left})
        return (left is not None and left >= TOKEN_MIN_S), f"token còn {left} giây"

    # Hai cổng nằm TRONG thao tác giữ lượt (review cổng pilot, P2-2): kiểm nào đã hỏng thì không cấp thêm lượt chat.
    led.gate = lambda: fails[0] if fails else ""
    led.token_check = token_gate

    cli = resolve_cli() if MODE == "real" else ""
    srv = Server(cli)
    t_start = time.time()
    g = None
    try:
        # ───────────── Bước 0: cổng chi phí và xác thực, TRƯỚC mọi lượt chat (không gọi model) ─────────────
        if rep.get("ledger_refused"):
            raise SystemExit("dừng: " + rep["ledger_refused"])
        if not check(f"cổng chi phí: lượt chat dự kiến ({CHAT_LIMIT}) trong hạn mức đã duyệt ({limit_env})",
                     CHAT_LIMIT <= limit_env):
            raise SystemExit("dừng trước khi gửi tin: hạn mức đã duyệt nhỏ hơn số lượt kịch bản cần")
        rep["steps"]["start_A_s"] = round(srv.start(tick_paused=True), 1)
        gate = auth_gate(cli)
        rep["auth_gate"] = gate
        if not check(f"cổng xác thực: gói thuê bao Anthropic gốc, đúng engine đã duyệt ({gate.get('why') or 'đạt'})",
                     gate["ok"]):
            raise SystemExit("dừng trước khi gửi tin: không chứng minh được chỉ dùng gói thuê bao")

        # ───────────── Bước 1: lượt chat 1, đích mới: giữ bản nháp, xin quyền, không có file ─────────────
        ok, why = chat(1, FROZEN["turn_1"])
        if not ok:
            rep["failure_class"] = "technical" if why in ChatLedger.STATUSES else "gate"
        if not check(f"lượt chat 1 kết thúc bình thường ({why})", ok):
            raise SystemExit(f"dừng: lượt chat 1 {why}; không thử lại (lỗi kỹ thuật, không phải bằng chứng định tuyến)"
                             if why in ChatLedger.STATUSES else f"dừng: lượt chat 1 bị từ chối trước khi gửi ({why})")
        gs = goals_of_session()
        if not check("bộ não lập ĐÚNG MỘT mục tiêu gắn phiên trợ lý", len(gs) == 1):
            rep["failure_class"] = "routing"
            raise SystemExit("dừng: không có mục tiêu để theo dõi (ghi nhận, không thử lại)")
        g = gs[0]
        check("mục tiêu thuộc trợ lý của phiên (host gắn, không từ lời model)", g.agent_key == agent["agent_key"],
              hard=True)
        check(f"đích của mục tiêu đúng file người dùng nêu ({FROZEN['deliverable']})",
              R._deliverable_rel(g) == FROZEN["deliverable"], hard=True)
        sc = store().scope_state(owner, g.id)
        drafts = store().submissions(owner, g.id, limit=5, statuses=("awaiting_scope",), revision=g.revision)
        rep["after_turn_1"] = {"scope": sc.get("state"), "drafts": [{k: d[k] for k in ("source", "status", "sha256",
                                                                                         "size")} for d in drafts]}
        check("D1: phạm vi CHỜ chủ cho phép, lời chat không cấp quyền", sc.get("state") == "pending", hard=True)
        if not check("bộ não nộp bản qua công cụ chung: host giữ bản nháp chờ quyền (submit_tool)",
                     bool(drafts) and drafts[0]["source"] == "submit_tool"):
            raise SystemExit("dừng: không có bản nháp để chủ cho phép (ghi nhận như kết quả pilot)")
        if not check("không có file đích trước khi chủ cho phép (không ghi native)", not deliv.is_file()
                     if scen != "receipt" else deliv.read_text(encoding="utf-8") == "Bản anh tự viết trước đó.\n"):
            raise SystemExit("dừng: file đích xuất hiện trước khi chủ cho phép")
        check("lượt chat không mở lượt việc nền hay gọi engine cấp kho", store_calls() == 0, hard=True)

        # ───────────── Bước 2: giết A, dựng B; chủ bấm Cho phép qua API ─────────────
        # B vẫn tạm dừng nhịp: mục tiêu không được kết thúc giữa hai lượt chat (lượt 2 cần mục tiêu còn mở để sửa). Nhịp
        # chạy lại ở C, cùng trần kho 0.
        srv.kill()
        rep["steps"]["start_B_s"] = round(srv.start(tick_paused=True), 1)
        code, body = http("GET", f"/goals/{g.id}")
        v = body.get("goal") or {}
        scv = v.get("scope") or {}
        dr = scv.get("draft") or {}
        check("thẻ sau khởi động lại vẫn xin quyền đúng đường, kèm bản nháp (mã, sha)",
              code == 200 and scv.get("state") == "pending" and scv.get("path") == FROZEN["deliverable"]
              and dr.get("sha256") == drafts[0]["sha256"], hard=True)
        req = {"command": "approve_scope", "expected_revision": v.get("revision"),
               "request_id": str((scv.get("request") or {}).get("id") or ""), "path": scv.get("path"),
               "submission_id": dr.get("submission_id"), "sha256": dr.get("sha256")}
        if scen == "permission":
            # Thẻ cũ: "tab khác" đã bấm Không trước; Cho phép trên thẻ cũ phải 409 và không ghi gì.
            http("POST", f"/goals/{g.id}/commands", json={"command": "deny_scope",
                                                          "expected_revision": v.get("revision"),
                                                          "request_id": req["request_id"]})
        calls0 = store_calls()
        check("không có kiểm bắt buộc nào hỏng trước khi bấm Cho phép", not fails, hard=True)
        code, ab = http("POST", f"/goals/{g.id}/commands", json=req)
        rep["approve"] = {"code": code, "status": ab.get("status"), "publish": ab.get("publish")}
        if not check("Cho phép: 200, đăng ĐÚNG bản nháp đã giữ (publish succeeded)",
                     code == 200 and ab.get("publish") == "succeeded"):
            raise SystemExit(f"dừng: Cho phép trả {code}/{ab.get('publish') or ab.get('error')}")
        if scen == "contract":
            # Tiền điều kiện hỏng trước lượt 2: bytes file đích không còn là bản đã duyệt.
            deliv.write_text("Bị sửa sau khi đăng.\n", encoding="utf-8", newline="\n")
        s1 = store().submission(owner, ab.get("submission_id") or "") or {}
        check("bytes file đích khớp sha bản nháp chủ đã duyệt, bản nộp đã đăng và đã tiếp nhận",
              _sha_file(deliv) == dr.get("sha256") and s1.get("status") == "published" and bool(s1.get("adopted_at")),
              hard=True)
        check("Cho phép không gọi thêm model", store_calls() == calls0, hard=True)
        saved1 = G.preserve_artifact(deliv, out_dir / "a4-deliverable-v1.md") if scen != "evidence" else {"ok": False}
        rep["deliverable_v1"] = {"sha256": _sha_file(deliv), "saved": saved1,
                                 "content_candidate": [k for k in FROZEN["content_v1"]
                                                       if k.lower() not in deliv.read_text(encoding="utf-8").lower()]}
        if not check("bản 1 lưu NGUYÊN VẸN ra hồ sơ cho người review (hash khớp)",
                     saved1.get("ok") is True and saved1.get("sha256") == _sha_file(deliv)):
            raise SystemExit("dừng: thiếu bằng chứng sản phẩm bản 1")

        # ───────────── Bước 3: lượt chat 2, phạm vi đã cấp: tự đăng bản 2, không hỏi lại ─────────────
        check("không có kiểm bắt buộc nào hỏng trước lượt chat 2", not fails, hard=True)
        ok, why = chat(2, FROZEN["turn_2"])
        if not ok:
            rep["failure_class"] = "technical" if why in ChatLedger.STATUSES else "gate"
        if not check(f"lượt chat 2 kết thúc bình thường ({why})", ok):
            raise SystemExit(f"dừng: lượt chat 2 {why}; không thử lại (lỗi kỹ thuật)"
                             if why in ChatLedger.STATUSES else f"dừng: lượt chat 2 bị từ chối trước khi gửi ({why})")
        g2 = store().get(owner, g.id)
        subs = store().submissions(owner, g.id, limit=20)
        latest = next((s for s in subs if s["revision"] == g2.revision and s["source"] == "submit_tool"), None)
        sc2 = store().scope_state(owner, g.id)
        rep["after_turn_2"] = {"revision": g2.revision, "scope": sc2.get("state"),
                               "subs": [(s["source"], s["status"], s["revision"]) for s in subs]}
        check("lượt 2 sửa mục tiêu (revision mới) và nộp bản 2 qua công cụ chung", g2.revision > g.revision
              and latest is not None, hard=True)
        check("phạm vi đã cấp: không có yêu cầu quyền mới", sc2.get("state") == "granted" and not sc2.get("request"),
              hard=True)
        check("bản 2 được host tự đăng, bytes khớp sha bản nộp, đã tiếp nhận",
              latest is not None and latest["status"] == "published" and _sha_file(deliv) == latest["sha256"]
              and bool(latest.get("adopted_at")), hard=True)
        saved2 = G.preserve_artifact(deliv, out_dir / "a4-deliverable-v2.md")
        rep["deliverable_v2"] = {"sha256": _sha_file(deliv), "saved": saved2,
                                 "content_candidate": [k for k in FROZEN["content_v2"]
                                                       if k.lower() not in deliv.read_text(encoding="utf-8").lower()]}
        check("bản 2 lưu NGUYÊN VẸN ra hồ sơ cho người review", saved2.get("ok") is True, hard=True)

        # ───────────── Bước 4: giết B, dựng C: không gọi thêm, không báo lặp; tạm dừng ─────────────
        calls1 = store_calls()
        srv.kill()
        rep["steps"]["start_C_s"] = round(srv.start(tick_paused=False), 1)
        time.sleep(75 if MODE == "real" else 35)
        check("sau khởi động lại: trần kho 0 giữ, không lượt engine cấp kho nào", store_calls() == calls1 == 0)
        if scen == "notice":
            import sqlite3
            c = sqlite3.connect(str(state / "resonance.sqlite3"))
            c.execute("INSERT INTO outbox(goal_id,kind,payload_json,created_at,idem,delivered_at) VALUES(?,?,?,?,?,?)",
                      (g.id, "goal.succeeded", json.dumps({"revision": store().get(owner, g.id).revision}),
                       time.time(), "pilot-dry-notice", time.time()))
            c.commit()
            c.close()
        ns = notices(g.id)
        keys = reports()
        # Phiên rỗng không còn được tính là đạt (review cổng pilot, P2-3); đối chiếu đầy đủ ở `notice_audit` ngay dưới.
        check("sau khởi động lại: mỗi tin báo một khoá báo cáo, có biên nhận của host, không lặp",
              bool(keys) and len(keys) == len(set(k for k, _r in keys)) and all(r for _k, r in keys))
        a1 = notice_audit(g.id)
        rep["notices_after_C"] = a1
        check("tin báo bắt buộc: có tin trạng thái của mục tiêu ở đúng revision cuối, đã gửi về đúng phiên có biên nhận",
              a1["required_ok"])
        check("mọi tin outbox đã gửi của mục tiêu có ĐÚNG một tin trong phiên, đúng mục tiêu, có biên nhận; không có khoá "
              "lạ, không còn tin chờ gửi", a1["matched_ok"])
        # Dựng lại lần nữa (D): tin đã gửi không được gửi lại, không thêm khoá.
        srv.kill()
        rep["steps"]["start_D_s"] = round(srv.start(tick_paused=False), 1)
        time.sleep(40 if MODE == "real" else 35)
        a2 = notice_audit(g.id)
        rep["notices_after_D"] = a2
        check("sau khởi động lại lần hai: cùng tập tin báo, không thêm bản sao, vẫn 0 lượt engine cấp kho",
              a2["session_keys"] == a1["session_keys"] and a2["messages_with_report"] == a1["messages_with_report"]
              and store_calls() == 0)
        g3 = store().get(owner, g.id)
        if g3.status == "active":
            code, _b = http("POST", f"/goals/{g.id}/commands", json={"command": "pause",
                                                                      "expected_revision": g3.revision})
            check("kết thúc: tạm dừng qua API, không còn việc nền", code == 200 and store().get(owner, g.id).paused)
        rep["final"] = {"status": store().get(owner, g.id).status, "notices": ns}
        check("ghi chú cũ còn nguyên", _sha_file(keep) == keep_sha)
    except SystemExit as e:
        print(f"DỪNG: {e}")
        rep["stopped"] = str(e)
    except Exception as e:  # noqa: BLE001
        check(f"bộ chạy không lỗi ({type(e).__name__}: {e})", False)
    finally:
        srv.kill()
        rep["ledger"] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
        rep["model_turns"] = {"chat_real": led.used() if MODE == "real" else 0,
                              "chat_simulated": led.used() if MODE == "dry" else 0,
                              "store": store_calls() if (state / "resonance.sqlite3").exists() else 0}
        # Bộ đếm lượt engine của chính server sandbox (usage.json): dry phải là 0, không thì có lượt model lọt qua.
        # HẬU KIỂM, không phải chốt chặn: usage_store chỉ ghi lượt có token và ghi kiểu cố gắng. Chốt chặn của dry là
        # `ws_chat` từ chối gửi tin. Ở đây chỉ báo 0 khi ĐÃ XÁC MINH: hai sổ (tổng và từng lượt) đọc được, đúng cấu trúc,
        # khớp nhau. Đọc lỗi, JSON hỏng, sai cấu trúc, thiếu một trong hai sổ (cả hai đã tạo sẵn từ đầu ca) hay hai sổ
        # lệch nhau: None kèm lý do, KHÔNG đổi thành 0 (review cổng pilot vòng 2 và 3).
        try:
            usage = json.loads((state / "usage.json").read_text(encoding="utf-8"))
            total = usage.get("total") if isinstance(usage, dict) else None
            # Mỗi mục BẮT BUỘC có `turns` là số nguyên không âm (không nhận bool, không mặc định 0 cho trường thiếu);
            # `total = {}` là số đếm rỗng hợp lệ (review cổng pilot vòng 3).
            if not isinstance(total, dict) or not all(
                    isinstance(v, dict) and "turns" in v and type(v["turns"]) is int and v["turns"] >= 0
                    for v in total.values()):
                raise ValueError("usage.json sai cấu trúc")
            turns = sum(v["turns"] for v in total.values())
            # Sổ từng lượt đã tạo sẵn từ đầu ca nên BẮT BUỘC còn và đọc được: mất sổ là không xác minh được (vòng 3).
            events = [ln for ln in (state / "usage-events.jsonl").read_text(encoding="utf-8").splitlines()
                      if ln.strip()]
            if len(events) != turns:
                raise ValueError(f"hai sổ lệch nhau: usage.json {turns} lượt, usage-events.jsonl {len(events)} dòng")
            rep["engine_turns_logged"], rep["engine_turns_status"] = turns, "verified"
        except (OSError, ValueError) as e:
            rep["engine_turns_logged"] = None
            rep["engine_turns_status"] = f"unverified: {type(e).__name__}: {str(e)[:120]}"
        if MODE == "dry":
            check(f"dry: đã xác minh server sandbox không ghi lượt engine nào ({rep.get('engine_turns_status')}, "
                  f"{rep['engine_turns_logged']})", rep["engine_turns_logged"] == 0)
        check(f"tổng lượt chat thật trong hạn mức {CHAT_LIMIT}", rep["model_turns"]["chat_real"] <= CHAT_LIMIT)
        rep["servers"] = srv.started
        rep["seconds_total"] = round(time.time() - t_start, 1)
        rep["acceptance"] = ("stopped" if rep.get("stopped") else "technical_failed" if fails else
                             "pending_content_review" if MODE == "real" else "dry_ok")
        rep["checks"] = log
        try:
            rep["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                                           text=True, timeout=10).stdout.strip()
            rep["server_dirty"] = bool(subprocess.run(["git", "status", "--porcelain", "--", "server", "system",
                                                       "dashboard"], cwd=str(ROOT), capture_output=True, text=True,
                                                      timeout=10).stdout.strip())
        except Exception:  # noqa: BLE001
            rep["commit"], rep["server_dirty"] = "", None
        txt = json.dumps(rep, ensure_ascii=True, indent=2) + "\n"
        for secret in (str(base), str(Path.home())):
            txt = txt.replace(json.dumps(secret)[1:-1], "<path>")
        (out_dir / f"a4-pilot-{MODE}-{scen or 'main'}.json").write_text(txt, encoding="utf-8", newline="\n")
        keep = rep.get("engine_turns_logged") != 0 or bool(os.environ.get("JAVIS_RESONANCE_A4_KEEP"))
        if keep and rep.get("engine_turns_logged") != 0:
            # Sự cố hay không xác minh được: GIỮ nguyên thư mục ca (state, sổ usage, log server, brain) làm bằng chứng,
            # không xoá tệp hỏng; bộ cha chép nó vào hồ sơ rồi dừng cả đợt.
            print("A4_PILOT_KEPT " + str(base))
        print("A4_PILOT " + json.dumps({k: rep.get(k) for k in ("mode", "scenario", "acceptance", "model_turns",
                                                                 "engine_turns_logged", "engine_turns_status")},
                                       ensure_ascii=False))
        if not keep:
            shutil.rmtree(base, ignore_errors=True)
    return 0 if rep["acceptance"] in ("dry_ok", "pending_content_review") else 1


# ───────────── dry: mọi ca, mỗi ca một tiến trình con; real: đúng một ca chính ─────────────

DRY_CASES = {
    # ca: (biến môi trường thêm, kết luận mong đợi, câu kiểm phải có trong output)
    "main": ({}, "dry_ok", "bản 2 được host tự đăng"),
    "cost": ({"JAVIS_RESONANCE_A4_MAX_CALLS": "1"}, "stopped", "dừng trước khi gửi tin: hạn mức"),
    "auth": ({}, "stopped", "dừng trước khi gửi tin: không chứng minh"),
    "permission": ({}, "stopped", "Cho phép trả 409"),
    "receipt": ({}, "stopped", "Cho phép trả 200/conflict"),
    "engine": ({}, "stopped", "lượt chat 1 engine_error; không thử lại"),
    "nosubmit": ({}, "stopped", "không có bản nháp để chủ cho phép"),
    "native": ({}, "stopped", "file đích xuất hiện trước khi chủ cho phép"),
    "evidence": ({}, "stopped", "thiếu bằng chứng sản phẩm bản 1"),
    "reserved": ({}, "stopped", "sổ lượt chat đã có từ lần chạy trước"),
    # Review cổng pilot: tiền điều kiện hỏng trước lượt 2, token trước lượt 2, tin báo. Kết cục lượt (engine lỗi, không
    # rõ, huỷ, thiếu trạng thái) kiểm qua chat() thật với nguồn khung giả trong test_resonance_a4_pilot_gate.py; dry
    # không gửi tin qua /ws.
    "contract": ({}, "stopped", "kiểm bắt buộc hỏng: bytes file đích khớp sha"),
    "token": ({}, "stopped", "lượt chat 2 bị từ chối trước khi gửi (token: token còn 100 giây"),
    "notice": ({}, "technical_failed", "FAIL mọi tin outbox đã gửi của mục tiêu có ĐÚNG một tin trong phiên"),
}
# Số lượt đã giữ trong sổ phải đúng như sau (lượt bị từ chối ở cổng không được giữ chỗ).
DRY_TURNS = {"main": 2, "cost": 0, "auth": 0, "permission": 1, "receipt": 1, "engine": 1, "nosubmit": 1, "native": 1,
             "evidence": 1, "reserved": None, "contract": 1, "token": 1, "notice": 2}

if SCEN:
    sys.exit(run_case(SCEN))
if MODE == "real":
    sys.exit(run_case("main"))

bad = []
for name, (extra, want, needle) in DRY_CASES.items():
    env = {**os.environ, "JAVIS_RESONANCE_A4_SCENARIO": name, "PYTHONIOENCODING": "utf-8", **extra}
    env.pop("JAVIS_RESONANCE_A4_OUT", None)
    cp = subprocess.run([sys.executable, str(HERE)], cwd=str(ROOT), env=env, capture_output=True, text=True,
                        encoding="utf-8", timeout=900)
    if os.environ.get("JAVIS_RESONANCE_A4_DRY_DIR"):
        # Bằng chứng từng ca: output đầy đủ của tiến trình con (không có khoá hay đường dẫn cá nhân).
        dd = Path(os.environ["JAVIS_RESONANCE_A4_DRY_DIR"])
        dd.mkdir(parents=True, exist_ok=True)
        (dd / f"a4-pilot-dry-{name}.txt").write_text(cp.stdout + cp.stderr[-4000:], encoding="utf-8", newline="\n")
    line = next((x for x in cp.stdout.splitlines() if x.startswith("A4_PILOT ")), "")
    got = json.loads(line[len("A4_PILOT "):]) if line else {}
    turns = (got.get("model_turns") or {}).get("chat_simulated")
    ok = got.get("acceptance") == want and needle in cp.stdout and \
        (got.get("model_turns") or {}).get("chat_real", 1) == 0 and \
        (DRY_TURNS[name] is None or turns == DRY_TURNS[name]) and got.get("engine_turns_logged") == 0
    print(("ok   " if ok else "FAIL ") + f"ca {name}: kết luận {got.get('acceptance')} (mong {want}), "
          f"lượt đã giữ trong sổ {turns} (mong {DRY_TURNS[name]}), "
          f"lượt thật {(got.get('model_turns') or {}).get('chat_real')}, "
          f"lượt engine server ghi {got.get('engine_turns_logged')}")
    if not ok:
        bad.append(name)
        print("\n".join("    " + x for x in cp.stdout.splitlines()[-25:]))
    # Sự cố hay không xác minh được (lượt engine khác 0, số đếm không hợp lệ, lượt thật, thiếu báo cáo): DỪNG CẢ ĐỢT
    # ngay, không mở ca kế tiếp, chép bằng chứng ca đó vào hồ sơ. Ca âm đúng kỳ vọng mà đã xác minh 0 lượt thì vẫn chạy
    # tiếp (review cổng pilot vòng 2).
    etl = got.get("engine_turns_logged")
    if type(etl) is not int or etl != 0 or (got.get("model_turns") or {}).get("chat_real", 1) != 0:
        kept = next((x[len("A4_PILOT_KEPT "):].strip() for x in cp.stdout.splitlines()
                     if x.startswith("A4_PILOT_KEPT ")), "")
        print(f"DỪNG CẢ ĐỢT: ca {name} có lượt engine {etl!r} ({got.get('engine_turns_status') or 'không có báo cáo'}); "
              "không mở ca kế tiếp, không thử lại.")
        if kept and os.environ.get("JAVIS_RESONANCE_A4_DRY_DIR"):
            import shutil as _shutil
            dest = Path(os.environ["JAVIS_RESONANCE_A4_DRY_DIR"]) / f"incident-{name}"
            _shutil.copytree(kept, dest, dirs_exist_ok=True)
            print(f"Bằng chứng (thư mục ca, gồm sổ usage và log server) đã chép vào {dest}")
        elif kept:
            print(f"Bằng chứng giữ nguyên tại {kept}")
        if name not in bad:
            bad.append(name)
        break
if bad:
    print(f"\n{len(bad)} FAIL:", bad)
    sys.exit(1)
print("\nOK")
