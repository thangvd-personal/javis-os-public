"""Đo thời gian API của các route nhóm chat team (spec 2026-10-13-team-chat-design mục 12.5, 17; ca TC38).

    python tools/team_perf_bench.py                  # 50 nhóm, 5.000 tin, 20 lần mỗi route
    python tools/team_perf_bench.py --teams 50 --messages 5000 --runs 20 --out ket-qua.json

Dựng state TẠM (thư mục mới, không đụng dữ liệu thật), brain tạm có hai trợ lý, rồi gọi route thật của `main.app` qua
TestClient (trong tiến trình, không mạng). Đo THỜI GIAN PHÍA SERVER của từng lời gọi, in trung vị và p95, kèm thông tin
máy đo. Không gọi model, không chạy vòng điều phối. Số đo trên máy local KHÔNG phải số đo VPS (spec 17): kết luận về VPS
chỉ có khi chủ dự án duyệt đo riêng trên VPS.

Thời gian vẽ khung chat trên trình duyệt đo riêng (xem docs/dev/team-chat-t4.md); script này chỉ đo API.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--teams", type=int, default=50)
    ap.add_argument("--messages", type=int, default=5000, help="tổng số tin, chia đều cho các nhóm")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    state = Path(tempfile.mkdtemp(prefix="javis-team-bench-"))
    os.environ["JAVIS_STATE_DIR"] = str(state / "state")
    (state / "state").mkdir()
    brain = state / "brain"
    (brain / "agents").mkdir(parents=True)
    for slug, name in (("lan", "Lan"), ("minh", "Minh")):
        (brain / "agents" / f"{slug}.md").write_text(f"---\nname: {name}\nrole: Thành viên\n---\nBạn là {name}.\n",
                                                     encoding="utf-8", newline="\n")
    sys.path.insert(0, str(ROOT / "server"))
    import main as M  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    M._TEAM_API.deps.spawn = lambda coro: coro.close() if hasattr(coro, "close") else None
    c = TestClient(M.app, base_url="http://127.0.0.1:8080")
    q = f"?brain={brain}"

    t0 = time.perf_counter()
    ids = []
    for i in range(a.teams):
        r = c.post(f"/teams{q}", json={"name": f"Nhóm {i:02d}", "members": ["lan", "minh"], "leader_slug": "lan"})
        ids.append((r.json()["team"]["team_id"], r.json()["team"]["session_id"]))
    store = M.get_store()
    per = max(1, a.messages // max(1, a.teams))
    for tid, sid in ids:
        for k in range(per):
            kind = "owner" if k % 2 == 0 else "agent"
            store.append_team_message(sid, "user" if kind == "owner" else "assistant",
                                      f"Tin {k} của nhóm {tid}: doanh thu, kế hoạch, việc cần làm số {k}",
                                      author_kind=kind, author_ref="" if kind == "owner" else "x",
                                      client_msg_id=f"b{k}" if kind == "owner" else None)
    seed_s = time.perf_counter() - t0
    tid, sid = ids[len(ids) // 2]

    routes = {
        "GET /teams": f"/teams{q}",
        "GET /teams/{id}": f"/teams/{tid}{q}",
        "GET /teams/{id}/queue": f"/teams/{tid}/queue{q}",
        "GET /teams/{id}/messages (trang đầu 30)": f"/teams/{tid}/messages{q}",
        "GET /teams/{id}/messages (200)": f"/teams/{tid}/messages{q}&limit=200",
        "GET /teams/{id}/search": f"/teams/{tid}/search{q}&q=doanh%20thu%20ke%20hoach",
        "GET /teams/{id}/turns": f"/teams/{tid}/turns{q}",
    }
    out = {}
    for name, url in routes.items():
        c.get(url)   # làm nóng
        ms = []
        for _ in range(a.runs):
            s = time.perf_counter()
            resp = c.get(url)
            ms.append((time.perf_counter() - s) * 1000)
            assert resp.status_code == 200, (name, resp.status_code, resp.text[:200])
        ms.sort()
        out[name] = {"median_ms": round(statistics.median(ms), 2),
                     "p95_ms": round(ms[max(0, int(len(ms) * 0.95) - 1)], 2), "runs": a.runs}
    first = c.get(f"/teams/{tid}/messages{q}").json()
    report = {
        "kind": "api_server_side_local",
        "note": "Thời gian phía server qua TestClient trong tiến trình; KHÔNG phải số đo VPS, KHÔNG gồm thời gian trình duyệt.",
        "machine": {"platform": platform.platform(), "python": platform.python_version(),
                    "processor": platform.processor(), "cpu_count": os.cpu_count()},
        "dataset": {"teams": a.teams, "messages_total": per * a.teams, "messages_per_team": per},
        "seed_seconds": round(seed_s, 2),
        "open_team_loads": {"messages": len(first["messages"]), "has_more": first["has_more"]},
        "routes": out,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
