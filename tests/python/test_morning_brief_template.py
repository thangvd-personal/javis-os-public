"""Ready-made "Morning brief" on the recurring-work page (0.222.0).

    python tests/run.py morning_brief_template

The template only PRE-FILLS the form: nothing is scheduled until the person presses Save, and Save
goes through the normal POST /reminders, so with no report channel it still answers can_force.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import os
import tempfile

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-morning-")

from pathlib import Path  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import reminders  # noqa: E402

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


# A throwaway brain and a report channel the test switches on and off: never the real brain.
brain = Path(tempfile.mkdtemp(prefix="javis-morning-brain-"))
channel = {"ok": False}


def atomic_write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


async def no_send(chat_id, text):
    return True, ""


feature = reminders.RemindersFeature(reminders.RemindersDeps(
    brain_root=lambda _b: str(brain), atomic_write_text=atomic_write, send_telegram=no_send,
    build_system_prompt=lambda _b: "", aux_model=lambda: None, safe_tools=[], readonly_tools=[],
    scheduler_brains=lambda: [],
    notify_ready=lambda: (channel["ok"], "" if channel["ok"] else "Telegram is not connected")))
app = FastAPI()
app.include_router(feature.router)
cl = TestClient(app)

tpl = reminders.morning_brief_template()
check("template: daily 7:00, does the work and reports, read-only level",
      tpl["cron"] == "0 7 * * *" and tpl["mode"] == "task" and tpl["muc_quyen"] == "suggest")
check("template: asks for calendar, yesterday's talk and news, and forbids invented numbers",
      all(k in tpl["text"] for k in ("memory/conversations/",)) and len(tpl["text"]) > 100)

r = cl.get("/reminders/templates").json()
check("GET /reminders/templates lists the morning brief", [t["id"] for t in r["templates"]] == ["morning_brief"])
check("listing the templates creates nothing", cl.get("/reminders").json()["counts"]["pending"] == 0)

payload = {k: tpl[k] for k in ("text", "label", "cron", "mode", "muc_quyen")}
payload.update(brain="brain", created_by="dashboard")
r = cl.post("/reminders", json=payload).json()
check("saving with no report channel: refused with can_force (the usual precondition)",
      r.get("ok") is False and r.get("can_force") is True)
r = cl.post("/reminders", json=dict(payload, allow_no_channel=True)).json()
check("saving after the person agrees: created as a daily cron task at read-only level",
      r.get("ok") is True and r.get("cron") == "0 7 * * *" and r.get("mode") == "task" and r.get("muc_quyen") == "suggest")

channel["ok"] = True
r = cl.post("/reminders", json=payload).json()
check("with a report channel it is created straight away", r.get("ok") is True)

js = (ROOT / "dashboard" / "console.js").read_text(encoding="utf-8")
check("page: the Morning brief button pre-fills the form, it does not POST",
      'id="lpMorning"' in js and 'fetch("/reminders/templates"' in js and "openForm(null, null, tpl)" in js)

if _fails:
    print("\nFAIL:", len(_fails), _fails)
    raise SystemExit(1)
print("\nOK - morning_brief_template")
