"""Voice Record page backend: SQLite audit log, grammar normalization, VoiceRecord/*.md.

    python tests/run.py voice_records
"""
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_STATE = Path(tempfile.mkdtemp(prefix="javis-vr-state-"))
_BRAIN = Path(tempfile.mkdtemp(prefix="javis-vr-brain-"))
_OTHER_BRAIN = Path(tempfile.mkdtemp(prefix="javis-vr-other-"))
os.environ["JAVIS_STATE_DIR"] = str(_STATE)

import config as cfgmod
import main
import transcript_fix
import ui_targets
import voice_records
from fastapi.testclient import TestClient

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


# 1. Store location and filename format
store = voice_records.VoiceRecordStore(_STATE / "voice_records.sqlite3")
check("sqlite audit file created in STATE_DIR", (_STATE / "voice_records.sqlite3").is_file())

dt = datetime(2026, 11, 12, 17, 49, 4)
tmp_vr = _BRAIN / "VoiceRecord"
tmp_vr.mkdir(parents=True, exist_ok=True)
f1 = voice_records.make_record_filename(dt, tmp_vr)
check("filename matches YYYY.MM.DD-HH.MM.SS.md", f1 == "2026.11.12-17.49.04.md")
(tmp_vr / f1).write_text("existing", encoding="utf-8")
f2 = voice_records.make_record_filename(dt, tmp_vr)
check("collision appends suffix -1.md", f2 == "2026.11.12-17.49.04-1.md")

# 2. Direct unit test of normalize_voice_record with mocked API
import asyncio
_orig_norm_api = transcript_fix._ask_normalize_api
_orig_norm_ask = transcript_fix._ASK


async def _fake_norm_api(pick, prompt: str, raw_text: str):
    return "Hôm nay chúng ta họp lúc 9 giờ.\n\nNội dung chính gồm hai phần."


transcript_fix._ask_normalize_api = _fake_norm_api
transcript_fix._ASK = None
cfg_mock = {"model": {"order": ["openai"], "openai_api_key": "sk-test"}}
res = asyncio.run(transcript_fix.normalize_voice_record(cfg_mock, "hôm nay chúng ta họp lúc 9 giờ\nnội dung chính gồm hai phần"))
check("normalize_voice_record returns normalized AI text with paragraphs preserved",
      res["ai"] is True and "\n\n" in res["text"] and "Hôm nay" in res["text"])
check("normalize_voice_record never returns em dash", "\u2014" not in res["text"])
transcript_fix._ask_normalize_api = _orig_norm_api
transcript_fix._ASK = _orig_norm_ask

# 3. ui_targets routing check
check("ui_targets PAGES places record right after chat",
      list(ui_targets.PAGES[:3]) == ["home", "chat", "record"])
check("ui_targets resolve_page('Ghi âm') == 'record'",
      ui_targets.resolve_page("Ghi âm") == "record")

# 4. FastAPI route tests with mocked _brain_root / _files_root / cfgmod
_orig_gate = main.cfgmod.gate_active
main.cfgmod.gate_active = lambda: False
client = TestClient(main.app, base_url="http://127.0.0.1")

_orig_brain_root = main._brain_root
_orig_files_root = main._files_root
_orig_cfg_read = main.cfgmod.read_settings
_orig_norm = transcript_fix.normalize_voice_record
_orig_store_get = voice_records.get_store

main._brain_root = lambda b: str(_OTHER_BRAIN if b == "other" else _BRAIN)
main._files_root = lambda b: (_OTHER_BRAIN if b == "other" else _BRAIN).resolve()
main.cfgmod.read_settings = lambda: {"model": {"openai_api_key": "sk-test", "order": ["openai"]}}
voice_records.get_store = lambda: store

# Clean VoiceRecord dir so route proves auto-creation
import shutil
shutil.rmtree(tmp_vr, ignore_errors=True)
check("VoiceRecord folder absent before route call", not tmp_vr.exists())

r_empty = client.post("/voice-records/normalize", json={"brain": "brain", "raw_text": "   "})
if r_empty.status_code != 400:
    print("DEBUG r_empty:", r_empty.status_code, r_empty.text)
check("POST /voice-records/normalize rejects empty raw_text with 400", r_empty.status_code == 400 and r_empty.json().get("ok") is False)


async def _mock_normalize_ok(cfg, text):
    return {"text": "Văn bản đã được chuẩn hóa đúng ngữ pháp.", "ai": True, "source": "mock"}


transcript_fix.normalize_voice_record = _mock_normalize_ok
raw_sample = "văn bản thô chưa có dấu chấm câu nói liên tục từ mic"
r_ok = client.post("/voice-records/normalize", json={"brain": "brain", "raw_text": raw_sample})
d_ok = r_ok.json()
if r_ok.status_code != 200 or not d_ok.get("ok"):
    print("DEBUG r_ok:", r_ok.status_code, r_ok.text)
check("POST /voice-records/normalize returns 200 ok=True", r_ok.status_code == 200 and d_ok.get("ok") is True)
rec = d_ok["record"]
check("normalized file uses VoiceRecord/YYYY.MM.DD-HH.MM.SS.md pattern",
      bool(re.match(r"^VoiceRecord/\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}(?:-\d+)?\.md$", rec["file_path"])))
saved_md = _BRAIN / rec["file_path"]
check("VoiceRecord folder auto-created and .md file written in active brain",
      tmp_vr.is_dir() and saved_md.is_file() and saved_md.read_text(encoding="utf-8") == "Văn bản đã được chuẩn hóa đúng ngữ pháp.")
check("audit record keeps original raw_text, created_at, brain, normalized_text",
      rec["raw_text"] == raw_sample and rec["brain"] == "brain" and rec["status"] == "normalized" and rec["created_at"] > 0)

# Prove raw text is persisted BEFORE normalization runs even if normalization raises
async def _mock_normalize_boom(cfg, text):
    raise RuntimeError("simulated llm failure")


transcript_fix.normalize_voice_record = _mock_normalize_boom
r_boom = client.post("/voice-records/normalize", json={"brain": "brain", "raw_text": "bản thô trước khi lỗi"})
check("when normalization throws, endpoint returns 500", r_boom.status_code == 500)
conn = sqlite3.connect(str(_STATE / "voice_records.sqlite3"))
rows = conn.execute("SELECT raw_text, status, file_path FROM voice_records WHERE raw_text = ?", ("bản thô trước khi lỗi",)).fetchall()
conn.close()
check("raw text was ALREADY audited in SQLite before normalization threw",
      len(rows) == 1 and rows[0][1] == "raw_saved" and rows[0][2].startswith("VoiceRecord/"))

# List per brain isolation
transcript_fix.normalize_voice_record = _mock_normalize_ok
client.post("/voice-records/normalize", json={"brain": "other", "raw_text": "dữ liệu brain khác"})

lst_default = client.get("/voice-records?brain=brain").json()
lst_other = client.get("/voice-records?brain=other").json()
check("GET /voice-records filters by active brain only",
      lst_default["ok"] and len(lst_default["records"]) == 2
      and len(lst_other["records"]) == 1 and lst_other["records"][0]["raw_text"] == "dữ liệu brain khác")

# 5. Remote Speaker Diarization tests
r_gen = client.post("/voice-records/meeting/generate-diarize-key")
d_gen = r_gen.json()
check("generate-diarize-key returns random key with sdk_live_ prefix",
      r_gen.status_code == 200 and d_gen.get("ok") and str(d_gen.get("key", "")).startswith("sdk_live_"))

test_srv_key = d_gen["key"]
# Update config with server mode
_curr_settings = {
    "model": {"openai_api_key": "sk-test", "order": ["openai"]},
    "voice": {
        "meeting_stt": {
            "remote_mode": "server",
            "server_api_key": test_srv_key,
            "server_base_uri": "http://192.168.1.50:8000",
        }
    }
}
main.cfgmod.read_settings = lambda: _curr_settings

# Ping when server mode enabled but missing key -> 401
r_ping_nokey = client.get("/voice-records/remote-diarize/ping")
check("remote-diarize/ping rejects request without key with 401", r_ping_nokey.status_code == 401)

# Ping with invalid key -> 401
r_ping_badkey = client.get("/voice-records/remote-diarize/ping", headers={"X-Speaker-Diarization-Key": "wrong-key"})
check("remote-diarize/ping rejects wrong key with 401", r_ping_badkey.status_code == 401)

# Ping with valid key via X-Speaker-Diarization-Key -> 200
r_ping_ok = client.get("/voice-records/remote-diarize/ping", headers={"X-Speaker-Diarization-Key": test_srv_key})
check("remote-diarize/ping accepts valid X-Speaker-Diarization-Key", r_ping_ok.status_code == 200 and r_ping_ok.json().get("ok") is True)

# Ping with valid key via Authorization Bearer -> 200
r_ping_bearer = client.get("/voice-records/remote-diarize/ping", headers={"Authorization": f"Bearer {test_srv_key}"})
check("remote-diarize/ping accepts valid Authorization Bearer token", r_ping_bearer.status_code == 200 and r_ping_bearer.json().get("ok") is True)

# GET /voice-records/meeting/remote-server-key returns plaintext key
r_get_key = client.get("/voice-records/meeting/remote-server-key")
check("GET /voice-records/meeting/remote-server-key returns server key for admin",
      r_get_key.status_code == 200 and r_get_key.json().get("key") == test_srv_key)

# When server mode is off -> ping returns 403
_curr_settings["voice"]["meeting_stt"]["remote_mode"] = "off"
r_ping_off = client.get("/voice-records/remote-diarize/ping", headers={"X-Speaker-Diarization-Key": test_srv_key})
check("remote-diarize/ping returns 403 when remote_mode is not server", r_ping_off.status_code == 403)

# 6. Test timeout estimation, worker logging, and failed record recovery
import meeting_jobs

# Mock audio duration probe
_orig_probe = meeting_jobs.probe_audio_duration_sec
meeting_jobs.probe_audio_duration_sec = lambda p: 4800.0  # 80 mins
to_est = meeting_jobs._estimate_worker_timeout(["/dummy/meeting.m4a"])
check("estimate_worker_timeout factors in duration (80 mins -> >160 mins timeout)",
      to_est >= 4800.0 * 2.0)
meeting_jobs.probe_audio_duration_sec = _orig_probe

# Test _ensure_failed_record creates voice record and .md file with audio intact
dummy_aud = _STATE / "test_failed_meeting.m4a"
dummy_aud.write_bytes(b"dummy audio content")
dummy_job = store.create_job(
    brain="brain",
    brain_path=str(_BRAIN),
    title="Buổi họp thử nghiệm lỗi",
    upload_mode="raw_upload",
    audio_path=str(dummy_aud),
    status="failed",
    created_at=datetime(2026, 11, 13, 15, 30, 0).timestamp(),
    created_iso="2026-11-13T15:30:00+07:00",
)
store.update_job(
    dummy_job["id"],
    error="RuntimeError: Tiến trình xử lý âm thanh vượt quá giới hạn (37 phút) nên đã dừng.",
    log_tail="line 1\nline 2\nline 3",
)
check("meeting_jobs stores log_tail",
      store.get_job(dummy_job["id"]).get("log_tail") == "line 1\nline 2\nline 3")

failed_rec = meeting_jobs._ensure_failed_record(
    dummy_job["id"],
    "Tiến trình xử lý âm thanh vượt quá giới hạn (37 phút) nên đã dừng.",
    ["worker_timeout"],
    brain_root_fn=lambda b: str(_BRAIN),
    files_root_fn=lambda b: _BRAIN.resolve(),
    files_rel_fn=lambda r, t: f"VoiceRecord/{t.name}",
)
check("_ensure_failed_record creates voice record", failed_rec is not None and bool(failed_rec.get("id")))
check("failed record has_audio is True", failed_rec.get("has_audio") is True)
check("failed record contains worker_timeout warning", "worker_timeout" in (failed_rec.get("warnings") or []))
check("failed job is linked to record_id",
      store.get_job(dummy_job["id"]).get("record_id") == failed_rec["id"])
target_md = _BRAIN / failed_rec["file_path"]
check("failed record created markdown file in VoiceRecord/", target_md.is_file())
if target_md.is_file():
    md_content = target_md.read_text(encoding="utf-8")
    check("markdown file contains warning block and guidance",
          "[!WARNING]" in md_content and "Tách lại người nói" in md_content)

# Restore
main.cfgmod.gate_active = _orig_gate
main._brain_root = _orig_brain_root
main._files_root = _orig_files_root
main.cfgmod.read_settings = _orig_cfg_read
transcript_fix.normalize_voice_record = _orig_norm
voice_records.get_store = _orig_store_get

if _fails:
    print("\nFAIL:", len(_fails), _fails)
    raise SystemExit(1)
print("\nOK - voice_records")
