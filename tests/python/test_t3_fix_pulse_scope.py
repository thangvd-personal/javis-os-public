"""Review T3 phần P, P2-1 (0.156.0 sửa): ngữ cảnh ngoài phạm vi đã giao không được quét, không được nói.

    python tests/run.py t3_fix_pulse_scope -v

Trước bản sửa `_code_pass` chỉ lọc `agent_key in agents`, không lọc `deps.contexts(key)`: agent đã rời `team:T1` vẫn có lượt
quét trên `team:T1`. Nay dòng ngoài phạm vi bị gỡ lịch (sự kiện `out_of_scope`), lý do lớp 3 của nó không được phục vụ, và
trở lại phạm vi thì có lịch lại. Phần main.py (đăng tin, đề xuất, xếp việc chỉ khi nhóm hoạt động và còn là thành viên) ở
test_t3_fix_pulse_main. Kho thật, đồng hồ giả, engine GIẢ.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-fix-scope-")
os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
TEMP = Path(tempfile.mkdtemp(prefix="javis-fix-scope-w-"))

import agent_ledger as AL  # noqa: E402
import agent_pulse as AP  # noqa: E402
import agent_pulse_host as H  # noqa: E402
import resonance_store as RS  # noqa: E402

fails = []
D = 86400.0
T0 = 2_000_000_000.0


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


st = RS.GoalStore(TEMP / "resonance.sqlite3")
owner = RS.Principal("owner", "owner", str(TEMP))
key = st.agent_set_enabled(owner, "a0", True)["agent_key"]
AP.set_state(st, key, "*", "on", at=T0)
clock = Clock(T0)
scope = {"ctx": [f"dm:{key}"]}
scans, served = [], []


def engine_for(k, ctx, purpose):
    async def run_(req):
        await req["engine_started"]()
        scans.append(req["context_ref"])
        return {"text": '{"action": "none"}'}
    return "fake", "fake-model", run_


deps = H.PulseHostDeps(
    store=lambda: st, agents=lambda: [{"agent_key": key, "brain_id": str(TEMP)}], contexts=lambda k: list(scope["ctx"]),
    try_busy=lambda k, tok: True, release_busy=lambda k, tok: None,
    opportunities=lambda k, c, iv: [{"source_ref": f"task:{c}", "version": "1", "kind": "stale_job", "summary": "x"}],
    persona=lambda k: "", engine_for=engine_for, clock=clock, spawn=asyncio.ensure_future)
host = H.PulseHost(deps, budget=lambda k, p, at=None: (True, ""))
H.register_reason_handler("reaction_review", lambda _h, r: served.append(r["context_ref"]))

T1 = "team:T1"
AP.ensure_row(st, key, T1, at=T0)
with st._Tx(st) as c:
    c.execute("UPDATE agent_pulse SET next_due_at=? WHERE agent_key=? AND context_ref=?", (T0 + 5, key, T1))
AL.WakeReasons(st).add_source(key, T1, "reaction_review", "src:1", now=T0)
clock.t = T0 + D


async def tick():
    await host.tick()
    await host.wait_idle()

asyncio.run(tick())
check("agent đã rời team:T1: không có lượt quét nào trên team:T1", T1 not in scans, scans)
check("dòng ngoài phạm vi bị gỡ lịch (next_due_at NULL)", AP.row(st, key, T1)["next_due_at"] is None,
      AP.row(st, key, T1))
check("ghi sự kiện out_of_scope", AP.events(st, key, T1, kinds=("out_of_scope",)), AP.events(st, key, T1))
check("lý do lớp 3 của ngữ cảnh ngoài phạm vi không được phục vụ", served == [], served)
clock.t += 60
asyncio.run(tick())
check("nhịp sau: vẫn không lịch, không sự kiện lặp lại", AP.row(st, key, T1)["next_due_at"] is None
      and len(AP.events(st, key, T1, kinds=("out_of_scope",))) == 1)

# Trở lại phạm vi (vào lại nhóm): có lịch lại, lý do được phục vụ.
scope["ctx"] = [f"dm:{key}", T1]
clock.t += 60
asyncio.run(tick())
check("trở lại phạm vi: dòng có lịch lại", AP.row(st, key, T1)["next_due_at"] is not None, AP.row(st, key, T1))
check("trở lại phạm vi: lý do lớp 3 được phục vụ", served == [T1], served)

# Không đọc được phạm vi (lỗi tạm): bỏ qua, không gỡ lịch.
due = AP.row(st, key, T1)["next_due_at"]


def broken(k):
    raise RuntimeError("store busy")

deps.contexts = broken
clock.t = float(due) + 1
asyncio.run(tick())
check("lỗi đọc phạm vi: không quét, không gỡ lịch (fail closed, không mất lịch)",
      AP.row(st, key, T1)["next_due_at"] == due and T1 not in scans, (AP.row(st, key, T1), scans))

if fails:
    print(f"\n{len(fails)} FAIL")
    raise SystemExit(1)
print("\nOK")
