"""T4 (0.170.0): kịch bản đo VPS `tools/team_adaptive_vps_measure.py` chạy đúng đường trên một máy chủ HTTP GIẢ ở 127.0.0.1.

    python tests/run.py team_t4_vps_script -v

Không bao giờ chạm VPS: máy chủ giả là `http.server` trong tiến trình test, trả khuôn giống route thật (`/teams` có
`session_id`, `faces`, `counts` không có số tin). Kiểm: chọn nhóm NHIỀU TIN NHẤT (đếm chỉ đọc trong `conversations.db`, hay
gần đúng theo trang tin khi không có kho), đo cả `GET /agents/{slug}/pulse` với slug của `faces[0]`, chỉ gửi GET, không ghi
gì vào kho.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import hashlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:500]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


spec = importlib.util.spec_from_file_location("vps_measure", Path(ROOT) / "tools" / "team_adaptive_vps_measure.py")
VM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(VM)

TEAMS = [
    {"team_id": "tm_small", "session_id": "s_small", "name": "Nhỏ", "counts": {"running": 0, "waiting": 0, "blocked": 0},
     "faces": [{"slug": "nho", "name": "Nhỏ", "avatar": ""}], "member_count": 1},
    {"team_id": "tm_big", "session_id": "s_big", "name": "Lớn", "counts": {"running": 1, "waiting": 0, "blocked": 0},
     "faces": [{"slug": "lan", "name": "Lan", "avatar": ""}, {"slug": "minh", "name": "Minh", "avatar": ""}],
     "member_count": 2},
]
PAGE = {"tm_small": 3, "tm_big": 40}
hits = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Server-Timing", "app;dur=1.5")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        hits.append(("GET", self.path.split("?")[0]))
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send({"ok": True})
        if path == "/teams":
            return self._send({"ok": True, "teams": TEAMS})
        parts = path.strip("/").split("/")
        if parts[0] == "teams" and len(parts) == 2:
            return self._send({"ok": True, "team": next(t for t in TEAMS if t["team_id"] == parts[1])})
        if parts[0] == "teams" and len(parts) == 3 and parts[2] == "messages":
            return self._send({"ok": True, "messages": [{"id": i} for i in range(PAGE[parts[1]])], "has_more": False})
        if parts[0] == "agents" and len(parts) == 3 and parts[2] == "pulse":
            return self._send({"ok": True, "slug": parts[1]})
        return self._send({"ok": False}, 404)

    def do_POST(self):  # noqa: N802
        hits.append(("POST", self.path))
        self._send({"ok": False}, 405)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

state = Path(tempfile.mkdtemp(prefix="t4-vps-stub-"))
with sqlite3.connect(state / "conversations.db") as c:
    c.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
    c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, content TEXT)")
    c.execute("CREATE TABLE dm_reactions(session_id TEXT)")
    c.executemany("INSERT INTO messages(session_id, content) VALUES(?, ?)",
                  [("s_small", "x")] * 50 + [("s_big", "y")] * 900)
digest = hashlib.sha256((state / "conversations.db").read_bytes()).hexdigest()


def run(args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = VM.main(args)
    out = buf.getvalue()
    return code, (json.loads(out) if code == 0 and out.strip() else {})


code, rep = run(["--owner-approved", "test-stub", "--base-url", BASE, "--state-dir", str(state), "--runs", "2",
                 "--readers", "1"])
check("chạy được trên máy chủ giả ở 127.0.0.1 (mã 0)", code == 0, code)
mt = rep.get("measured_team", {})
check("chọn nhóm nhiều tin nhất theo kho chỉ đọc (900 tin, không phải nhóm đầu danh sách)",
      mt.get("team_id") == "tm_big" and mt.get("messages") == 900 and "chỉ đọc" in mt.get("how", ""), mt)
check("slug lấy từ faces[0] của nhóm đó", mt.get("pulse_slug") == "lan", mt)
check("đo cả GET /agents/{slug}/pulse", "GET /agents/{slug}/pulse" in rep.get("routes", {})
      and ("GET", "/agents/lan/pulse") in hits, list(rep.get("routes", {})))
check("đo trang nhóm và trang tin của đúng nhóm đó", ("GET", "/teams/tm_big") in hits
      and ("GET", "/teams/tm_big/messages") in hits and ("GET", "/teams/tm_small") not in hits)
check("có Server-Timing trong kết quả", rep.get("routes", {}).get("GET /teams", {}).get("server_timing_p50_ms") == 1.5)
check("chỉ gửi GET, không POST nào", hits and all(m == "GET" for m, _ in hits), hits[-5:])
check("không ghi gì vào kho (nội dung conversations.db y nguyên, không sinh WAL)",
      hashlib.sha256((state / "conversations.db").read_bytes()).hexdigest() == digest
      and not (state / "conversations.db-wal").exists())
check("đếm dòng các kho chỉ đọc có mặt trong kết quả", rep.get("stores", {}).get("conversations.db", {}).get("messages")
      == 950, rep.get("stores"))

hits.clear()
code, rep2 = run(["--owner-approved", "test-stub", "--base-url", BASE, "--runs", "1", "--readers", "1"])
mt2 = rep2.get("measured_team", {})
check("không có kho: chọn theo trang tin qua GET (gần đúng), vẫn đúng nhóm lớn và đo pulse",
      code == 0 and mt2.get("team_id") == "tm_big" and "gần đúng" in mt2.get("how", "") and mt2.get("pulse_slug") == "lan",
      mt2)
check("hàm thuần: không faces thì không đo pulse", VM.first_slug({"faces": []}) == ""
      and VM.first_slug({"members": [{"slug": "x"}]}) == "")
srv.shutdown()

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
raise SystemExit(0)
