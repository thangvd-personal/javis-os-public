"""Host thật cho test T4 (0.170.0): `main.app` qua TestClient, state và brain tạm, engine GIẢ cho mọi loại lượt.

Chốt chặn engine thật dùng chung với tools/team_adaptive_bench.py (`install_guards`): engine nhóm, Claude, Codex, engine
Cộng hưởng, engine phán xử (`_agent_judge_ask`, chốt `JAVIS_NO_REAL_JUDGE_ENGINE` trong `Judge._run_engine`: engine giả
được đánh dấu bằng `agent_judge.mark_test_fake`), engine quét của nhịp, engine lượt học (`_learn_engine_for`,
`JAVIS_NO_REAL_REVIEW_ENGINE`) và sandbox của engine chỉ chữ đều ném lỗi NGAY nếu bị dựng. Việc nền (lượt trả lời, lô phán xử, điều phối) gom lại rồi chạy trong test.

Không phải file test (tên bắt đầu bằng `_`).
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-t4-host-")
os.environ["JAVIS_NO_REAL_JUDGE_ENGINE"] = "1"   # chốt trung tâm (tests/run.py, _paths.py đặt sẵn; đặt lại cho chắc)
os.environ["JAVIS_NO_REAL_REVIEW_ENGINE"] = "1"
os.environ["JAVIS_NO_REAL_TEAM_ENGINE"] = "1"
os.environ["JAVIS_PULSE_TICK_PAUSED"] = "1"
os.environ["JAVIS_RESONANCE_TICK_PAUSED"] = "1"
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
BR = Path(tempfile.mkdtemp(prefix="javis-t4-host-brain-"))
(BR / "agents").mkdir()
SLUGS = ("lan", "minh", "hoa", "tuan")
for _slug in SLUGS:
    (BR / "agents" / f"{_slug}.md").write_text(
        f"---\nname: {_slug.capitalize()}\nrole: Thành viên phụ trách {_slug}\n---\nBạn là {_slug.capitalize()}.\n",
        encoding="utf-8", newline="\n")

sys.path.insert(0, str(Path(ROOT) / "tools"))
import main  # noqa: E402
import team_adaptive_bench as TAB  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fakes = TAB.install_guards(main)
api = main._TEAM_API
sent, spawned = [], []


async def _publish(ev):
    sent.append(ev)


main._CHAT_RUNTIME.publish = _publish
api.deps.spawn = spawned.append
api.deps.notify = lambda session_id, text: None
c = TestClient(main.app, base_url="http://127.0.0.1:8080")
Q = f"?brain={BR}"
fails: list = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:500]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def drain(tolerate=False):
    """Chạy hết việc nền đã gom, kể cả việc nền do việc nền sinh ra, rồi chờ việc nền của bộ chạy. `tolerate`: lỗi của
    một việc nền (vd. gửi gói WS khi socket đã đóng) được nuốt như event loop thật làm với tác vụ nền, trả số lỗi."""
    errors = []

    async def go():
        api.runner()._bind_loop()
        for _ in range(50):
            while spawned:
                job = spawned.pop(0)
                if asyncio.iscoroutine(job):
                    try:
                        await job
                    except Exception as ex:  # noqa: BLE001
                        if not tolerate:
                            raise
                        errors.append(ex)
            await api.runner().wait_jobs()
            if not spawned:
                break
    asyncio.run(go())
    return errors


def dispatch():
    asyncio.run(api.runner().dispatch_once(wait=True))
    drain()


def new_team(name, members=("lan", "minh", "hoa"), leader="lan"):
    r = c.post(f"/teams{Q}", json={"name": name, "members": list(members), "leader_slug": leader})
    assert r.status_code == 200, r.text
    t = r.json()["team"]
    c.post(f"/teams/{t['team_id']}/caps{Q}", json={"caps": {"daily_turns": 1000}})
    return t, {m["slug"]: m["agent_key"] for m in t["members"]}


def discard_spawned():
    """Bỏ việc nền đã gom mà không chạy (vd. lần đánh thức bộ điều phối khi test muốn việc còn nằm ở hàng chờ)."""
    while spawned:
        job = spawned.pop(0)
        if asyncio.iscoroutine(job):
            job.close()


def send(team_id, text, mentions=(), as_job=False, cm=None, run=True):
    """Chủ gửi tin qua route. `run=False`: không chạy việc nền (việc nhóm còn ở hàng chờ, lượt trả lời chưa chạy)."""
    r = c.post(f"/teams/{team_id}/messages{Q}", json={"text": text, "mentions": list(mentions), "as_job": as_job,
                                                       "client_msg_id": cm or f"cm-{len(sent)}-{text[:10]}"})
    assert r.status_code == 200, r.text
    if run:
        drain()
    else:
        discard_spawned()
    return r.json()


def toggle_auto(slug, on):
    r = c.post(f"/resonance/agents/toggle{Q}", json={"slug": slug, "enabled": bool(on)})
    assert r.status_code == 200, r.text
    return r.json()


def guards_ok():
    """Chốt còn nguyên VÀ chưa có đường nào thử dựng engine thật (chốt đếm mỗi lần bị gọi)."""
    return not TAB.guards_hold(main) and not TAB.REAL_ATTEMPTS


def finish():
    check("không engine thật nào được dựng hay gọi (chốt còn nguyên)", guards_ok(),
          (TAB.guards_hold(main), len(TAB.REAL_ATTEMPTS)))
    if fails:
        print(f"\n{len(fails)} FAIL")
        raise SystemExit(1)
    print("\nOK")
    raise SystemExit(0)
