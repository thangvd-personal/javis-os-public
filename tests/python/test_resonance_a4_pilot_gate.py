"""Resonance A4: cổng của bộ chạy pilot (review cổng pilot tại 1719303d, ba P2), chạy MÃ THẬT của bộ chạy.

    python tests/run.py resonance_a4_pilot_gate -v

Nạp bằng AST đúng các phần của `tests/python/test_resonance_a4_pilot.py` (file đó tự thoát khi không đặt biến pilot, nên
không import được): lớp `ChatLedger`, hàm `audit_notices`, và các hàm trong `run_case` (`check`, `cut`, `ws_chat`,
`chat`), cùng đoạn điều phối THẬT từ câu kiểm bytes bản 1 tới lời gọi lượt chat 2. Chỉ nguồn khung WebSocket là giả: không
server, không mạng, không xác thực, không model.

P2-1: chỉ `engine_status == "ok"` và `turn_status == "completed"` là lượt thành công; lỗi engine, không rõ, huỷ, thất bại,
thiếu trạng thái, không có turn_done đều giữ lượt đã tính và đóng sổ, lượt sau không được gửi.
P2-2: kiểm bắt buộc hỏng thì không giữ chỗ, không gửi lượt nữa (cổng nằm trong `reserve`); token không đạt trước lượt
cũng vậy; đoạn điều phối thật dừng trước lượt 2 với từng tiền điều kiện hỏng.
P2-3: phiên rỗng, tin revision cũ, tin mục tiêu khác, thiếu biên nhận, outbox đã gửi mà phiên không có tin, khoá lặp, tin
chờ gửi đều không đạt; ca hợp lệ đạt.
Sau pilot real-1: thẻ `turn:` host đặt sau lượt chat được nhận khi biên nhận khớp chính tin, phiên, mục tiêu và nối được
sự kiện lập hay sửa mục tiêu cùng revision; không bao giờ thay tin outbox bắt buộc. Fixture là ba thẻ thật của lần chạy.
"""
import ast
import asyncio
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch

from _paths import ROOT, SERVER  # noqa: F401

SRC = Path(__file__).with_name("test_resonance_a4_pilot.py")
TREE = ast.parse(SRC.read_text(encoding="utf-8"))
RUN = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "run_case")
FNS = {n.name: n for n in RUN.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
TOP = {n.name: n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}

_fails = []


def ok_(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or not extra else f"  {extra}"))
    if not cond:
        _fails.append(name)


def load(extra: dict) -> dict:
    ns = {"Path": Path, "os": os, "json": json, "time": time, "asyncio": asyncio, **extra}
    # `audit_notices` chưa có ở mã trước bản sửa: thiếu thì các ca P2-3 tương ứng báo hỏng, không làm sập test (để chạy
    # đối chứng trên head cũ).
    body = [TOP["ChatLedger"], *([TOP[n] for n in ("audit_notices", "read_notice_evidence") if n in TOP]),
            FNS["check"], FNS["cut"], FNS["ws_chat"], FNS["chat"]]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SRC), "exec"), ns)
    return ns


class FakeWS:
    """Nguồn khung thay WebSocket: khung chào, vài khung giữa lượt, rồi khung kết thúc (hoặc đóng kết nối)."""
    opened = 0

    def __init__(self, end):
        FakeWS.opened += 1
        self.frames = [{"type": "hello"}, {"type": "status"}, {"type": "response", "content": "..."}]
        if end is not None:
            self.frames.append({"type": "turn_done", **end})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def recv(self):
        if not self.frames:
            raise ConnectionError("đóng kết nối trước turn_done")
        return json.dumps(self.frames.pop(0))

    async def send(self, _x):
        pass


def world(tmp: Path, name: str, end, mode="real", fails=None):
    fails = [] if fails is None else fails
    ns = load({"MODE": mode, "PORT": 0, "origin": "http://fake", "sid": "s", "TURN_WALL_S": 2, "limit_env": 2,
               "scen": "main", "brain": str(tmp), "rep": {"turns": {}}, "fails": fails, "log": [],
               "G": types.SimpleNamespace(snapshot_files=lambda b: {}, snapshot_diff=lambda a, b: {})})
    led = ns["ChatLedger"].create(tmp / f"{name}.json", 2, name)
    led.gate = lambda: fails[0] if fails else ""
    ns["led"] = led
    ws_mod = types.SimpleNamespace(connect=lambda *a, **k: FakeWS(end))
    return ns, led, ws_mod


with tempfile.TemporaryDirectory(prefix="a4-pilot-gate-") as td:
    tmp = Path(td)

    # ───────────── P2-1: kết cục lượt từ khung turn_done thật ─────────────
    ENDS = {
        "ok": ({"engine_status": "ok", "engine_error": None, "turn_status": "completed"}, True, "ok"),
        "engine_error": ({"engine_status": "error", "engine_error": {"source": "exception", "kind": "auth_refresh_race"},
                          "turn_status": "completed"}, False, "engine_error"),
        "unknown": ({"engine_status": "unknown", "engine_error": {"source": "no_final"}, "turn_status": "completed"},
                    False, "engine_unknown"),
        "cancelled": ({"engine_status": "ok", "turn_status": "cancelled"}, False, "cancelled"),
        "failed": ({"engine_status": "ok", "turn_status": "failed"}, False, "engine_error"),
        "missing_status": ({}, False, "no_status"),
        "only_engine": ({"engine_status": "ok"}, False, "no_status"),
        "no_turn_done": (None, False, "transport_error"),
    }
    for name, (end, want_ok, want_status) in ENDS.items():
        ns, led, ws_mod = world(tmp, "r1-" + name, end)
        with patch.dict(sys.modules, {"websockets": ws_mod}):
            got_ok, status = ns["chat"](1, "tin")
            opened_before = FakeWS.opened
            got2 = ns["chat"](2, "tin 2")
            sent2 = FakeWS.opened - opened_before
        turn = ns["rep"]["turns"].get("turn_1", {})
        if want_ok:
            ok_(f"P2-1 {name}: lượt thành công, sổ ok, lượt 2 được gửi",
                got_ok and status == "ok" and not led.halted() and got2[0] and sent2 == 1 and led.used() == 2,
                (got_ok, status, led.halted(), got2, sent2))
        else:
            ok_(f"P2-1 {name}: KHÔNG thành công ({want_status}), lượt 1 vẫn tính, sổ đóng, lượt 2 không giữ chỗ và "
                "không gửi",
                not got_ok and status == want_status and led.used() == 1 and bool(led.halted())
                and not got2[0] and sent2 == 0, (got_ok, status, led.halted(), led.used(), got2, sent2))
        ok_(f"P2-1 {name}: dấu vết giữ nguyên siêu dữ liệu turn_done", end is None and turn.get("turn_done") is None
            or (end is not None and turn.get("turn_done") == {k: end[k] for k in end}), turn.get("turn_done"))

    # Dry không bao giờ gửi tin qua /ws (sự cố 11/10/2026).
    ns, led, ws_mod = world(tmp, "dry-ws", ENDS["ok"][0], mode="dry")
    try:
        with patch.dict(sys.modules, {"websockets": ws_mod}):
            asyncio.run(ns["ws_chat"]("tin"))
        refused = False
    except RuntimeError as e:
        refused = "dry_never_sends_ws" in str(e)
    ok_("dry: ws_chat từ chối gửi tin qua /ws", refused)

    # ───────────── P2-2: cổng trong reserve ─────────────
    fails = []
    ns, led, ws_mod = world(tmp, "gate", ENDS["ok"][0], fails=fails)
    with patch.dict(sys.modules, {"websockets": ws_mod}):
        a = ns["chat"](1, "tin")
        fails.append("bytes file đích khớp sha bản nháp chủ đã duyệt")
        before = (led.path.read_bytes(), FakeWS.opened)
        b = ns["chat"](2, "tin 2")
    ok_("P2-2 kiểm bắt buộc hỏng: lượt 2 bị từ chối trong reserve, sổ không đổi, không mở WebSocket",
        a[0] and not b[0] and b[1].startswith("contract:") and (led.path.read_bytes(), FakeWS.opened) == before
        and led.used() == 1, (a, b))

    ns, led, ws_mod = world(tmp, "token", ENDS["ok"][0])
    calls = []
    led.token_check = lambda: (calls.append(led.used()) or (led.used() < 1, "token còn 100 giây"))
    with patch.dict(sys.modules, {"websockets": ws_mod}):
        a = ns["chat"](1, "tin")
        opened = FakeWS.opened
        b = ns["chat"](2, "tin 2")
    ok_("P2-2 token kiểm TRƯỚC MỖI lượt: lượt 2 bị từ chối, không giữ chỗ, không gửi",
        a[0] and not b[0] and b[1].startswith("token:") and calls == [0, 1] and led.used() == 1
        and FakeWS.opened == opened, (a, b, calls))

    # Đoạn điều phối THẬT từ câu kiểm bytes bản 1 tới lượt chat 2: mỗi tiền điều kiện hỏng phải dừng trước lượt 2.
    flow = next(n for n in RUN.body if isinstance(n, ast.Try) and len(n.body) > 20)
    start = next(i for i, n in enumerate(flow.body) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and n.value.args and isinstance(n.value.args[0], ast.Constant)
                 and str(n.value.args[0].value).startswith("bytes file đích"))
    stop = next(i for i, n in enumerate(flow.body) if i > start and isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "chat")
    slice_ = ast.Module(body=flow.body[start:stop + 1], type_ignores=[])
    target = tmp / "out.md"
    target.write_text("# Tóm tắt\n\n## Kết luận\n", encoding="utf-8")
    VARIANTS = {
        "valid": {},
        "hash": {"dr": {"sha256": "khac"}},
        "not_published": {"s1": {"status": "publishing", "adopted_at": None}},
        "not_adopted": {"s1": {"status": "published", "adopted_at": None}},
        "store_calls": {"store_calls": lambda: 1},
        "evidence": {"G": types.SimpleNamespace(preserve_artifact=lambda *a: {"ok": False})},
        "earlier_soft_fail": {"fails": ["một kiểm mềm trước đó"]},
    }
    for name, over in VARIANTS.items():
        sent = []
        base = load({})
        base.update(fails=[], log=[], deliv=target, dr={"sha256": "thuc"}, s1={"status": "published", "adopted_at": 1},
                    _sha_file=lambda f: "thuc", store_calls=lambda: 0, calls0=0, out_dir=tmp, scen="main", rep={},
                    FROZEN={"content_v1": [], "turn_2": "tin 2"}, ChatLedger=base["ChatLedger"],
                    G=types.SimpleNamespace(preserve_artifact=lambda *a: {"ok": True, "sha256": "thuc"}))
        base.update(over)
        base["chat"] = lambda n, m: (sent.append(n) or (True, "ok"))
        stopped = ""
        try:
            exec(compile(slice_, str(SRC), "exec"), base)
        except SystemExit as e:
            stopped = str(e)
        if name == "valid":
            ok_("P2-2 điều phối thật, đủ tiền điều kiện: lượt 2 được gọi", sent == [2] and not stopped, (sent, stopped))
        else:
            ok_(f"P2-2 điều phối thật, {name} hỏng: dừng trước lượt 2, không gọi lượt 2", sent == [] and bool(stopped),
                (sent, stopped))

    # ───────────── P2-3: tin báo về đúng phiên ─────────────
    audit = load({}).get("audit_notices") or (lambda *a: {"matched_ok": None, "required_ok": None,
                                                         "error": "không có hàm audit_notices"})
    NK = ("goal.succeeded", "goal.blocked", "goal.waiting_human")
    row = lambda i, kind="goal.succeeded", rev=2, d=True: {"id": i, "kind": kind, "revision": rev, "delivered": d}
    msg = lambda key, goal="g1", rc=True: {"key": key, "goal_id": goal, "receipt": rc}
    CASES = {
        "valid": ([row(3), {"id": 1, "kind": "goal.created", "revision": 1, "delivered": True}], [msg("outbox:3")],
                  True, True),
        "empty_session": ([row(3)], [], False, False),
        "no_notice_at_all": ([], [], False, False),
        "only_old_revision": ([row(3, rev=1)], [msg("outbox:3")], True, False),
        "other_goal": ([row(3)], [msg("outbox:3", goal="g2")], False, False),
        "no_receipt": ([row(3)], [msg("outbox:3", rc=False)], False, False),
        "delivered_without_message": ([row(3), row(4)], [msg("outbox:3")], False, False),
        "duplicate": ([row(3)], [msg("outbox:3"), msg("outbox:3")], False, False),
        "stranger_key": ([row(3)], [msg("outbox:3"), msg("outbox:99")], False, False),
        "pending": ([row(3), row(5, d=False)], [msg("outbox:3")], False, False),
    }
    for name, (rows, msgs, want_match, want_req) in CASES.items():
        r = audit(rows, msgs, 2, "g1", NK)
        ok_(f"P2-3 {name}: matched={want_match}, required={want_req}",
            r["matched_ok"] == want_match and r["required_ok"] == want_req, r)
    # Câu kiểm của bộ chạy trên danh sách khoá phiên: rỗng không còn đạt.
    flow_notice = next(n for n in flow.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                       and n.value.args and isinstance(n.value.args[0], ast.Constant)
                       and str(n.value.args[0].value).startswith("sau khởi động lại: mỗi tin báo"))
    for keys, want in (([], False), ([("outbox:1", True)], True), ([("outbox:1", False)], False),
                       ([("outbox:1", True), ("outbox:1", True)], False)):
        base = load({})
        base.update(fails=[], log=[], keys=keys)
        exec(compile(ast.Module(body=[flow_notice], type_ignores=[]), str(SRC), "exec"), base)
        ok_(f"P2-3 câu kiểm khoá phiên {keys}: {'đạt' if want else 'không đạt'}", (not base["fails"]) == want)

# ═══════════ Review cổng pilot vòng 2: hậu kiểm lượt engine của dry và dừng cả đợt ═══════════
# Chạy đúng các nút AST của bộ chạy: khối đọc sổ usage trong `finally` của run_case (và câu kiểm ngay sau), đoạn giữ
# bằng chứng, và vòng điều phối dry (chỉ thay subprocess.run). Không server, không model.
FINALLY = next(n for n in RUN.body if isinstance(n, ast.Try) and n.finalbody).finalbody
USAGE = next(i for i, n in enumerate(FINALLY) if isinstance(n, ast.Try) and "engine_turns_logged" in ast.unparse(n))
# Mã trước bản sửa không có bước giữ bằng chứng: KEEP_AT là None và các ca tương ứng báo hỏng (để chạy đối chứng).
KEEP_AT = next((i for i, n in enumerate(FINALLY) if isinstance(n, ast.Assign) and ast.unparse(n).startswith("keep =")),
               None)
DRIVER = next(n for n in TREE.body if isinstance(n, ast.For) and "DRY_CASES.items" in ast.unparse(n.iter))

with tempfile.TemporaryDirectory(prefix="a4-pilot-gate-r2-") as td:
    tmp = Path(td)
    COUNTS = {
        # tên: (usage.json, usage-events.jsonl hay None, quyền đọc bị từ chối, số mong đợi)
        "zero": ('{"days": {}, "total": {}}', "", False, 0),
        # Vòng 3: sổ từng lượt đã tạo sẵn từ đầu ca; mất sổ là KHÔNG xác minh được, không phải 0.
        "zero_no_events_file": ('{"days": {}, "total": {}}', None, False, None),
        "missing_turns": ('{"total": {"cli": {"in": 100, "out": 10, "cost": 0.1}}}', "", False, None),
        "missing_turns_next_to_valid": ('{"total": {"a": {"turns": 0}, "b": {"in": 5}}}', "", False, None),
        "one": ('{"total": {"cli|x": {"in": 9, "out": 1, "turns": 1, "cost": 0.1}}}', '{"model": "x"}\n', False, 1),
        "truncated": ('{"total":', "", False, None),
        "permission": ('{"total": {"cli|x": {"turns": 1}}}', '{"m": 1}\n', True, None),
        "not_object": ("[]", "", False, None),
        "total_list": ('{"total": []}', "", False, None),
        "turns_text": ('{"total": {"a": {"turns": "1"}}}', "", False, None),
        "turns_negative": ('{"total": {"a": {"turns": -1}}}', "", False, None),
        "turns_bool": ('{"total": {"a": {"turns": true}}}', "", False, None),
        "missing_usage": (None, "", False, None),
        "events_disagree": ('{"days": {}, "total": {}}', '{"model": "lot"}\n', False, None),
    }
    for name, (usage, events, denied, want) in COUNTS.items():
        st = tmp / ("c-" + name)
        st.mkdir()
        if usage is not None:
            (st / "usage.json").write_text(usage, encoding="utf-8")
        if events is not None:
            (st / "usage-events.jsonl").write_text(events, encoding="utf-8")
        checks = []
        ns = {"state": st, "json": json, "rep": {}, "MODE": "dry",
              "check": lambda label, cond, **kw: checks.append(bool(cond))}
        code = compile(ast.Module(body=FINALLY[USAGE:USAGE + 2], type_ignores=[]), str(SRC), "exec")
        crash = ""
        try:
            if denied:
                with patch.object(Path, "read_text", side_effect=PermissionError("bị khoá")):
                    exec(code, ns)
            else:
                exec(code, ns)
        except Exception as e:  # noqa: BLE001 - mã cũ văng lỗi với dữ liệu sai cấu trúc: tính là hỏng, không sập test
            crash = f"{type(e).__name__}: {e}"
        got = ns["rep"].get("engine_turns_logged")
        status = ns["rep"].get("engine_turns_status", "")
        ok_(f"vòng 2 sổ usage {name}: báo {want!r}, câu kiểm dry {'đạt' if want == 0 else 'không đạt'}",
            not crash and got == want and (type(got) is int if want is not None else got is None)
            and checks == [want == 0], crash or ns["rep"])
        ok_(f"vòng 2 sổ usage {name}: nhãn {'verified' if want is not None else 'unverified + lý do'}",
            status == "verified" if want is not None else status.startswith("unverified: "), crash or ns["rep"])

    # Bằng chứng phía ca: chưa xác minh được 0 thì GIỮ thư mục ca (kể cả tệp hỏng), đã xác minh 0 thì dọn.
    for etl, want_kept in ((0, False), (1, True), (None, True)):
        if KEEP_AT is None:
            ok_(f"vòng 2 bằng chứng ca, lượt engine {etl!r}: có bước giữ bằng chứng", False, "không có `keep =`")
            continue
        base = tmp / f"ca-{etl}"
        (base / "state").mkdir(parents=True)
        (base / "state" / "usage.json").write_text('{"total":', encoding="utf-8")
        printed = []
        ns = {"rep": {"engine_turns_logged": etl}, "os": os, "base": base, "json": json, "MODE": "dry",
              "shutil": __import__("shutil"), "print": lambda *a, **k: printed.append(" ".join(map(str, a)))}
        body = FINALLY[KEEP_AT:KEEP_AT + 2] + [n for n in FINALLY[KEEP_AT + 2:] if isinstance(n, ast.If)
                                                and "rmtree" in ast.unparse(n)]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JAVIS_RESONANCE_A4_KEEP", None)
            exec(compile(ast.Module(body=body, type_ignores=[]), str(SRC), "exec"), ns)
        kept = base.exists() and (base / "state" / "usage.json").read_text(encoding="utf-8") == '{"total":'
        ok_(f"vòng 2 bằng chứng ca, lượt engine {etl!r}: {'giữ nguyên thư mục và tệp hỏng' if want_kept else 'dọn'}",
            kept == want_kept and (any(p.startswith("A4_PILOT_KEPT ") for p in printed) == want_kept), printed)

    # Vòng điều phối dry THẬT, chỉ thay subprocess.run: dừng cả đợt ngay khi ca có lượt engine hay không xác minh được.
    def drive(first: dict, dry_dir=None, kept_dir=None):
        sent = []

        def fake_run(*a, **kw):
            sc = kw["env"]["JAVIS_RESONANCE_A4_SCENARIO"]
            sent.append(sc)
            rep = {"acceptance": "stopped", "model_turns": {"chat_simulated": 0, "chat_real": 0},
                   "engine_turns_logged": 0, "engine_turns_status": "verified"}
            out = "dừng như mong đợi\n"
            if sc == "first":
                if first.get("no_report"):
                    return types.SimpleNamespace(stdout="vỡ giữa chừng\n", stderr="", returncode=1)
                rep.update(first)
                if kept_dir:
                    out += f"A4_PILOT_KEPT {kept_dir}\n"
            return types.SimpleNamespace(stdout=out + "A4_PILOT " + json.dumps(rep), stderr="", returncode=1)
        ns = {"os": os, "sys": sys, "HERE": SRC, "ROOT": ROOT, "json": json, "Path": Path, "bad": [],
              "subprocess": types.SimpleNamespace(run=fake_run), "print": lambda *a, **k: None,
              "DRY_CASES": {"first": ({}, "stopped", "dừng như mong đợi"), "second": ({}, "stopped", "dừng như mong đợi")},
              "DRY_TURNS": {"first": 0, "second": 0}}
        env = {"JAVIS_RESONANCE_A4_DRY_DIR": str(dry_dir)} if dry_dir else {}
        with patch.dict(os.environ, env, clear=True):
            exec(compile(ast.Module(body=[DRIVER], type_ignores=[]), str(SRC), "exec"), ns)
        return sent, ns["bad"]

    sent, bad = drive({})
    ok_("vòng 2 điều phối: các ca âm đúng kỳ vọng đã xác minh 0 lượt thì chạy tiếp đủ", sent == ["first", "second"]
        and bad == [], (sent, bad))
    for name, first in (("một lượt engine", {"engine_turns_logged": 1}),
                        ("số đếm không xác minh được", {"engine_turns_logged": None,
                                                         "engine_turns_status": "unverified: JSONDecodeError"}),
                        ("số đếm sai kiểu", {"engine_turns_logged": "0"}),
                        ("có lượt thật", {"model_turns": {"chat_simulated": 0, "chat_real": 1}}),
                        ("tiến trình ca không có báo cáo", {"no_report": True})):
        sent, bad = drive(first)
        ok_(f"vòng 2 điều phối, {name}: dừng cả đợt sau ca đầu, không mở ca thứ hai", sent == ["first"]
            and bad == ["first"], (sent, bad))

    # Bằng chứng được chép nguyên vẹn vào hồ sơ dry trước khi đợt dừng.
    kept_dir = tmp / "giu-lai"
    (kept_dir / "state").mkdir(parents=True)
    (kept_dir / "state" / "usage.json").write_text('{"total":', encoding="utf-8")
    (kept_dir / "server-1.log").write_text("nhật ký server\n", encoding="utf-8")
    dry_dir = tmp / "ho-so-dry"
    sent, bad = drive({"engine_turns_logged": None, "engine_turns_status": "unverified: JSONDecodeError"},
                      dry_dir=dry_dir, kept_dir=kept_dir)
    inc = dry_dir / "incident-first"
    ok_("vòng 2 điều phối: bằng chứng ca sự cố được chép nguyên vẹn (tệp hỏng, log server) rồi mới dừng",
        sent == ["first"] and (inc / "state" / "usage.json").read_text(encoding="utf-8") == '{"total":'
        and (inc / "server-1.log").is_file() and (dry_dir / "a4-pilot-dry-first.txt").is_file(), sent)

# ═══════════ Review cổng pilot vòng 3: hai phản ví dụ đi trọn chuỗi unverified, không đạt, giữ bằng chứng, dừng đợt ═══════════
with tempfile.TemporaryDirectory(prefix="a4-pilot-gate-r3-") as td:
    tmp = Path(td)
    for name, usage, events in (("mất sổ từng lượt", '{"days": {}, "total": {}}', None),
                                ("thiếu turns", '{"total": {"cli": {"in": 100, "out": 10, "cost": 0.1}}}', "")):
        base = tmp / name.replace(" ", "-")
        st = base / "state"
        st.mkdir(parents=True)
        (st / "usage.json").write_text(usage, encoding="utf-8")
        if events is not None:
            (st / "usage-events.jsonl").write_text(events, encoding="utf-8")
        checks, printed = [], []
        ns = {"state": st, "json": json, "rep": {}, "MODE": "dry", "base": base, "os": os,
              "shutil": __import__("shutil"), "print": lambda *a, **k: printed.append(" ".join(map(str, a))),
              "check": lambda label, cond, **kw: checks.append(bool(cond))}
        body = FINALLY[USAGE:USAGE + 2]
        if KEEP_AT is not None:
            body = body + FINALLY[KEEP_AT:KEEP_AT + 2] + [n for n in FINALLY[KEEP_AT + 2:] if isinstance(n, ast.If)
                                                          and "rmtree" in ast.unparse(n)]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JAVIS_RESONANCE_A4_KEEP", None)
            exec(compile(ast.Module(body=body, type_ignores=[]), str(SRC), "exec"), ns)
        rep = ns["rep"]
        ok_(f"vòng 3 {name}: số đếm None, nhãn unverified, câu kiểm dry không đạt",
            rep.get("engine_turns_logged") is None and str(rep.get("engine_turns_status", "")).startswith("unverified: ")
            and checks == [False], rep)
        # Mã cũ coi là 0 đã xác minh nên xoá thư mục ca: tính là hỏng, không sập test (để chạy đối chứng).
        kept = (st / "usage.json").is_file() and (st / "usage.json").read_text(encoding="utf-8") == usage
        ok_(f"vòng 3 {name}: giữ nguyên thư mục ca và sổ như lúc phát hiện",
            kept and (events is None) == (not (st / "usage-events.jsonl").exists())
            and any(p.startswith("A4_PILOT_KEPT ") for p in printed), printed or "thư mục ca đã bị xoá")
        sent, bad = drive({"engine_turns_logged": rep.get("engine_turns_logged"),
                           "engine_turns_status": rep.get("engine_turns_status")})
        ok_(f"vòng 3 {name}: bộ điều phối dừng cả đợt sau ca đầu", sent == ["first"] and bad == ["first"], (sent, bad))

# ═══════════ Sau pilot real-1 (11/10): thẻ turn của host trong đối chiếu tin báo ═══════════
# Lần chạy thật duy nhất tại 5305687a báo technical_failed vì `audit_notices` đòi tập khoá phiên bằng đúng tập outbox,
# trong khi host còn đặt thẻ `turn:<message_ref>` sau mỗi lượt chat lập hay sửa mục tiêu. Dry mô phỏng lượt chat nên
# không có thẻ này. Fixture dưới đây là ĐÚNG ba thẻ của lần chạy đó (khoá, mục tiêu, revision, id tin, nội dung khối).
RSID, RGID = "aa2c8660c01540a2b2e78487c4e3173d", "g_8163e17553dc"
T1, T2 = f"turn:msg:{RSID}:1", f"turn:msg:{RSID}:4"
REAL_CARDS = [  # (id tin, khoá, revision, câu)
    (3, T1, 1, "Javis đã lập mục tiêu từ việc này."),
    (6, T2, 2, "Javis đã cập nhật cách hiểu mục tiêu."),
    (7, "outbox:3", 2, "Đã xong phần kiểm được của mục tiêu: ..."),
]
REAL_OUTBOX = [(1, "goal.created", 1), (2, "goal.revised", 2), (3, "goal.waiting_human", 2)]
REAL_EVENTS = [(1, "created", f"msg:{RSID}:1"), (1, "submission_received", ""), (2, "reframe", f"msg:{RSID}:4"),
               (2, "artifact_adopted", f"msg:{RSID}:4")]

import sqlite3

import resonance as R

NS = load({})
# Mã trước bản sửa không có read_notice_evidence: phần hàm thuần vẫn chạy bằng audit_notices cũ (để chạy đối chứng),
# phần đọc SQLite báo hỏng.
audit, reader = NS["audit_notices"], NS.get("read_notice_evidence")
NK = R.NOTIFY_KINDS

# 1) Hàm thuần: thẻ turn chỉ được thêm khi đủ chứng thực, không thay outbox bắt buộc.
orow = lambda i, kind, rev, d=True: {"id": i, "kind": kind, "revision": rev, "delivered": d}
ROWS = [orow(i, k, r) for i, k, r in REAL_OUTBOX]
card = lambda key, rev, goal=RGID, rc=True, ev=None: {
    "key": key, "goal_id": goal, "revision": rev, "receipt": rc,
    "turn_event": (key.startswith("turn:") if ev is None else ev)}
REAL = [card(T1, 1), card(T2, 2), card("outbox:3", 2)]
PURE = {
    "ba thẻ thật (turn r1, turn r2, outbox r2)": (ROWS, REAL, True, True),
    "chỉ có turn, không có outbox": (ROWS, [card(T1, 1), card(T2, 2)], False, False),
    "chỉ có turn, outbox rỗng": ([], [card(T1, 1)], False, False),
    "turn không biên nhận": (ROWS, [card(T1, 1, rc=False), card(T2, 2), card("outbox:3", 2)], False, False),
    "turn có biên nhận của tin khác (receipt không phải True)": (
        ROWS, [card(T1, 1, rc=None), card(T2, 2), card("outbox:3", 2)], False, False),
    "turn sai mục tiêu": (ROWS, [card(T1, 1, goal="g_khac"), card(T2, 2), card("outbox:3", 2)], False, False),
    "turn không nối được sự kiện lập/sửa mục tiêu": (
        ROWS, [card(T1, 1, ev=False), card(T2, 2), card("outbox:3", 2)], False, False),
    "turn không có revision": (ROWS, [card(T1, None), card(T2, 2), card("outbox:3", 2)], False, False),
    "turn revision vượt revision cuối": (ROWS, [card(T1, 1), card(T2, 3), card("outbox:3", 2)], False, False),
    "turn lặp": (ROWS, [card(T1, 1), card(T1, 1), card(T2, 2), card("outbox:3", 2)], False, False),
    "khoá lạ": (ROWS, REAL + [card("note:1", 2)], False, False),
    "outbox lạ (tin không có trong outbox)": (ROWS, REAL + [card("outbox:99", 2)], False, False),
    "outbox bắt buộc chỉ ở revision cũ": ([orow(3, "goal.waiting_human", 1)],
                                         [card(T1, 1), card(T2, 2), card("outbox:3", 1)], True, False),
    "thẻ outbox ghi sai revision của dòng outbox": (ROWS, [card(T1, 1), card(T2, 2), card("outbox:3", 1)],
                                                   False, False),
    "outbox còn tin chờ gửi": (ROWS + [orow(4, "goal.succeeded", 2, d=False)], REAL, False, False),
    "outbox đã gửi mà phiên không có tin": (ROWS + [orow(5, "goal.succeeded", 2)], REAL, False, False),
}
for name, (rows, msgs, want_match, want_req) in PURE.items():
    r = audit(rows, msgs, 2, RGID, NK)
    ok_(f"real-1 {name}: matched={want_match}, required={want_req}",
        r["matched_ok"] == want_match and r["required_ok"] == want_req, r)

if reader is None:
    ok_("real-1: bộ chạy có read_notice_evidence (đọc chỉ đọc, kiểm biên nhận và sự kiện của thẻ turn)", False)
else:
    # 2) Đọc dữ liệu: SQLite dựng theo đúng lược đồ và dữ liệu của lần chạy, mở chỉ đọc; từng biến thể làm hỏng một
    #    mắt xích chứng thực của thẻ turn.
    def build(td, name, mutate=None):
        d = Path(td) / name
        d.mkdir()
        conv, goal = d / "conversations.db", d / "resonance.sqlite3"
        s = sqlite3.connect(str(conv))
        s.executescript("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);"
                        "CREATE TABLE report_receipts (session_id TEXT, report_key TEXT, goal_id TEXT, message_id "
                        "INTEGER, PRIMARY KEY(session_id, report_key, goal_id));")
        s.execute("INSERT INTO messages VALUES (1, ?, 'user', 'lượt 1')", (RSID,))
        s.execute("INSERT INTO messages VALUES (2, ?, 'assistant', 'trả lời lượt 1')", (RSID,))
        s.execute("INSERT INTO messages VALUES (4, ?, 'user', 'lượt 2')", (RSID,))
        s.execute("INSERT INTO messages VALUES (5, ?, 'assistant', 'trả lời lượt 2')", (RSID,))
        for mid, key, rev, text in REAL_CARDS:
            block = json.dumps({"goal_id": RGID, "revision": rev, "report": key})
            s.execute("INSERT INTO messages VALUES (?, ?, 'assistant', ?)",
                      (mid, RSID, f"{text}\n<!-- JAVIS_RESONANCE: {block} -->"))
            s.execute("INSERT INTO report_receipts VALUES (?, ?, ?, ?)", (RSID, key, RGID, mid))
        g = sqlite3.connect(str(goal))
        g.executescript("CREATE TABLE outbox (id INTEGER PRIMARY KEY, goal_id TEXT, kind TEXT, payload_json TEXT, "
                        "delivered_at REAL);"
                        "CREATE TABLE goal_events (id INTEGER PRIMARY KEY, goal_id TEXT, revision INTEGER, kind TEXT, "
                        "message_ref TEXT NOT NULL DEFAULT '');")
        for i, kind, rev in REAL_OUTBOX:
            g.execute("INSERT INTO outbox VALUES (?, ?, ?, ?, ?)", (i, RGID, kind, json.dumps({"revision": rev}), 1.0))
        for rev, kind, ref in REAL_EVENTS:
            g.execute("INSERT INTO goal_events(goal_id, revision, kind, message_ref) VALUES (?, ?, ?, ?)",
                      (RGID, rev, kind, ref))
        try:
            if mutate:
                mutate(s, g)
            s.commit()
            g.commit()
        finally:
            s.close()
            g.close()
        return conv, goal

    VARIANTS = {
        "dữ liệu thật": (None, True, True),
        "biên nhận turn r1 trỏ tin khác (tin 2)": (
            lambda s, g: s.execute("UPDATE report_receipts SET message_id=2 WHERE report_key=?", (T1,)), False, False),
        "biên nhận turn r2 ở phiên khác": (
            lambda s, g: s.execute("UPDATE report_receipts SET session_id='phien-khac' WHERE report_key=?", (T2,)),
            False, False),
        "turn r2 mất biên nhận": (
            lambda s, g: s.execute("DELETE FROM report_receipts WHERE report_key=?", (T2,)), False, False),
        "sự kiện sửa mục tiêu của turn r2 ở revision khác": (
            lambda s, g: g.execute("UPDATE goal_events SET revision=1 WHERE kind='reframe'"), False, False),
        "sự kiện của turn r1 không phải lập/sửa mục tiêu": (
            lambda s, g: g.execute("UPDATE goal_events SET kind='submission_received' WHERE kind='created'"),
            False, False),
        "thẻ turn trỏ message_ref của phiên khác": (
            lambda s, g: (s.execute("UPDATE messages SET content=replace(content, ?, ?) WHERE id=3",
                                    (T1, "turn:msg:phien-khac:1")),
                          s.execute("UPDATE report_receipts SET report_key='turn:msg:phien-khac:1' WHERE report_key=?",
                                    (T1,)),
                          g.execute("UPDATE goal_events SET message_ref='msg:phien-khac:1' WHERE kind='created'")),
            False, False),
        "thẻ turn mang mục tiêu khác": (
            lambda s, g: (s.execute("UPDATE messages SET content=replace(content, ?, 'g_khac') WHERE id=6", (RGID,)),
                          s.execute("UPDATE report_receipts SET goal_id='g_khac' WHERE report_key=?", (T2,))),
            False, False),
        "thêm một thẻ turn lặp khoá": (
            lambda s, g: s.execute("INSERT INTO messages VALUES (8, ?, 'assistant', (SELECT content FROM messages "
                                   "WHERE id=3))", (RSID,)), False, False),
        "outbox:3 mất tin trong phiên, chỉ còn turn": (
            lambda s, g: (s.execute("DELETE FROM messages WHERE id=7"),
                          s.execute("DELETE FROM report_receipts WHERE report_key='outbox:3'")), False, False),
    }
    with tempfile.TemporaryDirectory(prefix="a4-pilot-gate-real1-") as td:
        for i, (name, (mut, want_match, want_req)) in enumerate(VARIANTS.items()):
            conv, goal = build(td, f"v{i}", mut)
            before = (conv.read_bytes(), goal.read_bytes())
            rows, msgs = reader(conv, goal, RSID, RGID, R.parse_goal_blocks)
            r = audit(rows, msgs, 2, RGID, NK)
            ok_(f"real-1 đọc SQLite, {name}: matched={want_match}, required={want_req}",
                r["matched_ok"] == want_match and r["required_ok"] == want_req, (r, msgs))
            ok_(f"real-1 đọc SQLite, {name}: chỉ đọc, không đổi byte nào", (conv.read_bytes(), goal.read_bytes()) == before)
        conv, goal = build(td, "keys")
        _rows, msgs = reader(conv, goal, RSID, RGID, R.parse_goal_blocks)
        ok_("real-1 đọc SQLite: đúng ba thẻ, biên nhận khớp chính tin, hai thẻ turn nối sự kiện",
            [(m["key"], m["revision"], m["message_id"], m["receipt"], m["turn_event"]) for m in msgs]
            == [(T1, 1, 3, True, True), (T2, 2, 6, True, True), ("outbox:3", 2, 7, True, False)], msgs)

    # 3) Bộ chạy đọc qua hàm chỉ đọc mới, không còn tự dò phiên bằng report_receipt (không tách được tin).
    na = ast.unparse(FNS["notice_audit"])
    ok_("real-1: notice_audit dùng read_notice_evidence và audit_notices",
        "read_notice_evidence(" in na and "audit_notices(" in na and "report_receipt(" not in na, na)

if _fails:
    print(f"\n{len(_fails)} FAIL:", _fails)
    sys.exit(1)
print("\nOK")
