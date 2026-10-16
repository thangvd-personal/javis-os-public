"""T4 (0.170.0): hồi quy hiệu năng bằng engine GIẢ (tools/team_adaptive_bench.py, tools/team_adaptive_ui_bench.js).

    python tests/run.py team_t4_perf -v

Không phải phép đo (số đo ở docs/dev/team-chat-t4-bench.md). Test này chạy bench ở cỡ nhỏ, hai lần với độ dài lịch sử khác
nhau, và kiểm bằng SỐ ĐẾM (ổn định trên mọi máy) thay vì thời gian: số câu SQL đọc của trang tin và trang nhóm không tăng
theo số tin, mỗi thao tác có trần câu SQL, không engine thật nào được dựng, lượt model giả đúng loại. Thời gian chỉ dùng
ngưỡng rộng cho độ trễ event loop (p95 của bộ hẹn giờ 10 ms) ở pha chỉ có request, đo trên hàng trăm mẫu, không sleep cố
định. Không nới ngưỡng cũ nào.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import json
import subprocess
import sys
import tempfile
from pathlib import Path

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:600]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def bench(messages):
    out = Path(tempfile.mkdtemp(prefix="t4-perf-")) / "r.json"
    p = subprocess.run([sys.executable, "-X", "utf8", str(Path(ROOT) / "tools" / "team_adaptive_bench.py"), "--teams", "3",
                        "--messages", str(messages), "--reactions", "60", "--agents", "6", "--runs", "12",
                        "--concurrency", "4", "--judge-messages", "6", "--pulse-ticks", "2", "--no-ui", "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", timeout=1200)
    check(f"bench {messages} tin mỗi nhóm chạy xong", p.returncode == 0, p.stderr[-1500:])
    return json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}


small = bench(60)
big = bench(600)

for label, d in (("nhỏ", small), ("lớn", big)):
    check(f"bench {label}: không engine thật nào được dựng (chốt còn nguyên, chốt đếm 0 lần thử dựng)",
          d.get("real_engine_guard", {}).get("unguarded") == []
          and d.get("real_engine_guard", {}).get("real_engine_build_attempts") == 0, d.get("real_engine_guard"))
    calls = d.get("concurrent_phase", {}).get("fake_model_calls", {})
    check(f"bench {label}: tin không @ mở lô phán xử giả và lượt nhóm giả (đúng loại lượt)",
          calls.get("judge_batches", 0) >= 1 and calls.get("team_turns", 0) >= 1, calls)
    check(f"bench {label}: ghi rõ không phải số đo VPS, không phải trình duyệt", "VPS" in d.get("note", ""))

io_s, io_b = small.get("store_io_per_op", {}), big.get("store_io_per_op", {})
for op in ("GET /teams/{id}/messages (trang tin)", "GET /teams/{id} (trang nhóm)"):
    check(f"{op}: số câu SQL đọc không tăng theo số tin (60 và 600 tin mỗi nhóm)",
          io_s.get(op, {}).get("sql_reads_max") == io_b.get(op, {}).get("sql_reads_max"),
          (io_s.get(op), io_b.get(op)))
# Trần rộng (khoảng gấp đôi số đo lúc viết test), để bắt hồi quy N+1 chứ không bắt dao động.
CAPS = {"GET /teams/{id}/messages (trang tin)": (20, 0), "GET /teams/{id} (trang nhóm)": (60, 0),
        "POST reaction nhóm": (60, 20), "POST reaction chat riêng": (60, 20), "GET /agents/{slug}/pulse (khung nhịp)":
        (45, 0)}   # khung nhịp: 17 câu lúc viết test, 29 sau khi phần L thêm mục bài học (vẫn hằng số)
for op, (rmax, wmax) in CAPS.items():
    got = io_b.get(op, {})
    check(f"{op}: câu SQL đọc <= {rmax}, ghi <= {wmax}", got and got["sql_reads_max"] <= rmax
          and got["sql_writes_max"] <= wmax, got)
op = "GET /agents/{slug}/pulse (khung nhịp)"
check(f"{op}: số câu SQL đọc không tăng theo số tin (lệch tối đa 2)",
      abs(io_s.get(op, {}).get("sql_reads_max", -99) - io_b.get(op, {}).get("sql_reads_max", 99)) <= 2,
      (io_s.get(op), io_b.get(op)))
lag = big.get("isolated_load_lag", {}).get("requests_only", {})
check("pha chỉ có request: p95 độ trễ event loop < 50 ms (bộ hẹn giờ 10 ms, hàng trăm mẫu)",
      lag.get("samples", 0) >= 50 and lag.get("lag_p95_ms", 1e9) < 50, lag)

# Vẽ thuần dưới node (không phải trình duyệt).
p = subprocess.run(["node", str(Path(ROOT) / "tools" / "team_adaptive_ui_bench.js"), "--teams", "12", "--messages", "120",
                    "--runs", "5"], capture_output=True, text=True, encoding="utf-8", timeout=600)
ui = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else {}
check("bench vẽ thuần chạy được, ghi rõ không phải trình duyệt", ui.get("kind") == "pure_render_node_no_browser", p.stderr)
tw = ui.get("two_tabs_ws_reactions_simulated", {})
check("mô phỏng hai tab nhận 20 gói reaction chạy xong, ghi rõ là mô phỏng (không gọi onTeamEvent)",
      tw.get("events_per_run") == 20 and tw.get("tabs") == 2 and "mô phỏng" in tw.get("note", "")
      and tw.get("p95_ms", 1e9) < 2000, tw)
check("trang 120 tin vẽ dưới 250 ms (p95, ngưỡng rộng)", ui.get("message_page", {}).get("p95_ms", 1e9) < 250,
      ui.get("message_page"))

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
raise SystemExit(0)
