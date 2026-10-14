"""Nhóm chat team T4: script đo tools/team_perf_bench.py còn chạy được (số liệu nhỏ; không phải phép đo).

    python tests/run.py team_perf_bench -v
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import json
import subprocess
import sys
import tempfile
from pathlib import Path

out = Path(tempfile.mkdtemp(prefix="team-bench-")) / "r.json"
p = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "team_perf_bench.py"), "--teams", "2", "--messages",
                    "40", "--runs", "2", "--out", str(out)], capture_output=True, text=True, encoding="utf-8", timeout=600)
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


check("script đo chạy xong", p.returncode == 0, p.stderr[-800:])
d = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
check("ghi rõ là số đo API phía server trên máy local, kèm máy đo", d.get("kind") == "api_server_side_local"
      and "VPS" in d.get("note", "") and d.get("machine", {}).get("platform"), d)
check("đo đủ các route nhóm, có trung vị và p95", {"GET /teams", "GET /teams/{id}", "GET /teams/{id}/queue",
                                                   "GET /teams/{id}/search"} <= set(d.get("routes", {}))
      and all("median_ms" in v and "p95_ms" in v for v in d.get("routes", {}).values()), d.get("routes"))
check("mở nhóm chỉ tải trang đầu", d.get("open_team_loads", {}).get("messages") == 20, d.get("open_team_loads"))
raise SystemExit(1 if fails else 0)
