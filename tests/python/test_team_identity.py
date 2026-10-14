"""Nhóm chat team T1, nhiệm vụ 1: danh tính bền cho mọi thành viên, không cần bật Cộng hưởng; tạo agent trùng slug
không ghi đè âm thầm (spec 2026-10-13-team-chat-design mục 5.1).

    python tests/run.py team_identity
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-id-")
BR = Path(tempfile.mkdtemp(prefix="javis-team-id-brain-"))
(BR / "agents").mkdir()

import main  # noqa: E402
import resonance_store as RS  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


store = RS.GoalStore()
root = str(BR.resolve())
owner = RS.Principal("owner", "owner", root)

a = store.agent_register(owner, "lan")
check("đăng ký: có mã bền, KHÔNG bật Cộng hưởng", a["agent_key"].startswith("ag_") and a["enabled"] is False
      and a["status"] == "active", str(a))
b = store.agent_register(owner, "lan")
check("đăng ký lần hai: cùng mã, không đổi version", b["agent_key"] == a["agent_key"]
      and b["config_version"] == a["config_version"])
on = store.agent_set_enabled(owner, "lan", True)
check("bật sau đó dùng đúng mã đã đăng ký", on["agent_key"] == a["agent_key"] and on["enabled"] is True)
again = store.agent_register(owner, "lan")
check("đăng ký khi đã bật: không tắt, không đổi gì", again["enabled"] is True and again["agent_key"] == a["agent_key"])
store.agent_retire(owner, "lan")
new = store.agent_register(owner, "lan")
check("đã nghỉ thì đăng ký lại cấp mã MỚI, tắt", new["agent_key"] != a["agent_key"] and new["enabled"] is False)
try:
    store.agent_register(RS.Principal("agent", "ag_x", root), "khac")
    check("agent không tự đăng ký được", False)
except PermissionError:
    check("agent không tự đăng ký được", True)

# ───────────── tạo agent trùng slug không ghi đè ─────────────
c = TestClient(main.app, base_url="http://127.0.0.1:8080")
r = c.post("/agents", data={"name": "Bống Work", "role": "vai cũ", "prompt": "lời nhắc cũ", "brain": str(BR)})
check("tạo mới: 200", r.status_code == 200 and r.json().get("slug") == "bong-work", r.text)
f = BR / "agents" / "bong-work.md"
before = f.read_bytes()
r = c.post("/agents", data={"name": "Bống Work", "role": "vai mới", "prompt": "lời nhắc mới", "brain": str(BR)})
check("tạo trùng slug (không gửi slug): 409, có mã lỗi và gợi ý slug khác", r.status_code == 409
      and r.json().get("code") == "slug_taken" and r.json().get("suggest") == "bong-work-2", r.text)
check("file cũ giữ nguyên từng byte", f.read_bytes() == before)
r = c.post("/agents", data={"name": "Bống Work", "slug": "bong-work", "role": "vai sửa", "brain": str(BR)})
check("sửa (có gửi slug): 200 như cũ", r.status_code == 200 and "vai sửa" in f.read_text(encoding="utf-8"), r.text)
raise SystemExit(1 if fails else 0)
