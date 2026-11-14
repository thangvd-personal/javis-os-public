"""Voice Record page backend: SQLite audit log, grammar normalization, VoiceRecord/*.md.

    python tests/run.py voice_records
"""
import json
import os
import re
import sqlite3
import sys
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

# 7. Test Background Task Queue and Remote Diarize async queuing
_curr_settings["voice"]["meeting_stt"]["remote_mode"] = "server"
_orig_venv_ready = main.meeting_stt.venv_diarize_ready
_orig_hf_token = main.meeting_stt.resolve_hf_token
_orig_py_exe = main.meeting_stt.resolve_worker_python
_orig_exec_remote = meeting_jobs._execute_remote_diarize_job

main.meeting_stt.venv_diarize_ready = lambda: True
main.meeting_stt.resolve_hf_token = lambda cfg=None: ("hf_test_token", "env")
main.meeting_stt.resolve_worker_python = lambda need_diarize=True: (sys.executable, True)
async def _fake_exec_remote(jid):
    pass
meeting_jobs._execute_remote_diarize_job = _fake_exec_remote

# Test GET /voice-records/meeting/queue
r_q = client.get("/voice-records/meeting/queue")
check("GET /voice-records/meeting/queue returns 200 with queue summary",
      r_q.status_code == 200 and r_q.json().get("ok") is True and "active" in r_q.json() and "queued" in r_q.json())

# Test POST /voice-records/remote-diarize async enqueue
dummy_upload_wav = _STATE / "test_client_audio.wav"
dummy_upload_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

with open(dummy_upload_wav, "rb") as af:
    r_remote_post = client.post(
        "/voice-records/remote-diarize",
        headers={"X-Speaker-Diarization-Key": test_srv_key},
        files={"file": ("test_client_audio.wav", af, "audio/wav")},
        data={"num_speakers": "2"},
    )
check("POST /voice-records/remote-diarize enqueues job and returns queued status",
      r_remote_post.status_code == 200
      and r_remote_post.json().get("status") in ("queued", "diarizing")
      and bool(r_remote_post.json().get("job_id"))
      and r_remote_post.json().get("queue_position") is not None)

remote_job_id = r_remote_post.json().get("job_id")

# Test GET /voice-records/remote-diarize/jobs/{job_id} polling
r_poll_nokey = client.get(f"/voice-records/remote-diarize/jobs/{remote_job_id}")
check("GET /voice-records/remote-diarize/jobs/{job_id} rejects without key",
      r_poll_nokey.status_code == 401)

r_poll_ok = client.get(
    f"/voice-records/remote-diarize/jobs/{remote_job_id}",
    headers={"X-Speaker-Diarization-Key": test_srv_key},
)
check("GET /voice-records/remote-diarize/jobs/{job_id} returns job status with key",
      r_poll_ok.status_code == 200
      and r_poll_ok.json().get("job_id") == remote_job_id
      and r_poll_ok.json().get("status") in ("queued", "diarizing", "done", "failed"))

main.meeting_stt.venv_diarize_ready = _orig_venv_ready
main.meeting_stt.resolve_hf_token = _orig_hf_token
main.meeting_stt.resolve_worker_python = _orig_py_exe
meeting_jobs._execute_remote_diarize_job = _orig_exec_remote

# 8. Test Estimation, Timestamps, and Worker Log file
est_80m = meeting_jobs.estimate_processing_time_sec(4773.81, job_type="meeting_transcribe")
check("estimate_processing_time_sec for 80m audio matches dur*2+900 (>10000s)", est_80m >= 10400)

log_test_job = store.create_job(
    brain="brain",
    brain_path=str(_BRAIN),
    title="Log Test Job",
    status="failed",
    duration_sec=120.0,
    estimated_sec=1140.0,
    started_at=1000.0,
    started_iso="2026-11-13T10:00:00+07:00",
    finished_at=1060.0,
    finished_iso="2026-11-13T10:01:00+07:00",
    elapsed_sec=60.0,
)
check("create_job preserves started/finished timestamps and elapsed_sec",
      log_test_job.get("started_at") == 1000.0 and log_test_job.get("elapsed_sec") == 60.0)

test_logs_dir = Path(main.cfgmod.STATE_DIR) / "meeting-logs"
test_logs_dir.mkdir(parents=True, exist_ok=True)
dummy_log_file = test_logs_dir / f"{log_test_job['id']}.log"
dummy_log_file.write_text("Line 1: started worker\nLine 2: error occurred\n", encoding="utf-8")

store.update_job(log_test_job["id"], log_path=str(dummy_log_file))
pub = voice_records.public_job(store.get_job(log_test_job["id"]))
check("public_job detects has_log True when log file exists", pub.get("has_log") is True)

r_log_get = client.get(f"/voice-records/meeting/jobs/{log_test_job['id']}/log")
check("GET /voice-records/meeting/jobs/{id}/log returns 200 with log text",
      r_log_get.status_code == 200 and "Line 1: started worker" in r_log_get.text)

# Test log file deletion on success simulation
dummy_log_file.unlink(missing_ok=True)
r_log_404 = client.get(f"/voice-records/meeting/jobs/{log_test_job['id']}/log")
check("GET /voice-records/meeting/jobs/{id}/log returns 404 when log file is removed",
      r_log_404.status_code == 404)

# 9. Test In-Memory Waveform Preloading for Pyannote
import wave
from meeting_ear import worker

test_worker_wav = _STATE / "test_worker_16k.wav"
with wave.open(str(test_worker_wav), "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"\x00\x00" * 1600)  # 0.1s mono 16k

check("worker._is_16k_mono_pcm16_wav returns True for 16kHz mono PCM16",
      worker._is_16k_mono_pcm16_wav(str(test_worker_wav)) is True)

w_path, w_istmp, w_err = worker._ensure_16k_mono_wav(str(test_worker_wav))
check("worker._ensure_16k_mono_wav preserves existing 16k mono wav without re-encoding",
      w_istmp is False and not w_err and w_path == str(test_worker_wav))

w_tensor, w_sr, w_lerr = worker._load_audio_waveform(str(test_worker_wav))
check("worker._load_audio_waveform loads (1, N) float32 tensor or reports missing torch",
      (w_lerr == "torch_not_installed") or (not w_lerr and w_tensor is not None and w_tensor.shape == (1, 1600) and w_sr == 16000))

# 10. Test Meeting Log Directory Structure, Artifacts, and Debug Timing Endpoint
ts_dir = test_logs_dir / "2026.11.14-09.00.00"
ts_dir.mkdir(parents=True, exist_ok=True)
exec_log = ts_dir / "execution.log"
exec_log.write_text("Worker execution line 1\nWorker execution line 2\n", encoding="utf-8")

whisper_json = ts_dir / "whisper_output.json"
whisper_json.write_text(json.dumps({"segments": [{"start": 0.0, "end": 2.5, "text": "Xin chào"}], "language": "vi"}), encoding="utf-8")

diar_json = ts_dir / "diarization_output.json"
diar_json.write_text(json.dumps({"segments": [{"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"}], "speakers": ["SPEAKER_00"]}), encoding="utf-8")

timing_json = ts_dir / "debug_timing.json"
timing_json.write_text(json.dumps({"whisper_transcription_sec": 1.25, "diarization_sec": 0.85, "whisper_rtf": 0.5}), encoding="utf-8")

art_job = store.create_job(
    brain="brain",
    brain_path=str(_BRAIN),
    title="Artifact Job",
    status="done",
    duration_sec=2.5,
    log_path=str(exec_log),
)

check("store.get_job_by_record returns None when empty", store.get_job_by_record("") is None)
store.update_job(art_job["id"], record_id="dummy_rec_123")
check("store.get_job_by_record retrieves job by record_id", store.get_job_by_record("dummy_rec_123")["id"] == art_job["id"])

# Query default log
r_art_def = client.get(f"/voice-records/meeting/jobs/{art_job['id']}/log")
check("GET job log default returns execution.log content", r_art_def.status_code == 200 and "Worker execution line 1" in r_art_def.text)

# Query file=list
r_art_list = client.get(f"/voice-records/meeting/jobs/{art_job['id']}/log?file=list")
check("GET job log?file=list returns list of files", r_art_list.status_code == 200 and r_art_list.json().get("ok") is True)
file_names = [f["name"] for f in r_art_list.json().get("files", [])]
check("file list contains execution.log, whisper_output.json, diarization_output.json, debug_timing.json",
      "execution.log" in file_names and "whisper_output.json" in file_names and "debug_timing.json" in file_names)

# Query file=whisper_output.json
r_art_w = client.get(f"/voice-records/meeting/jobs/{art_job['id']}/log?file=whisper_output.json")
check("GET job log?file=whisper_output.json returns JSON response", r_art_w.status_code == 200 and r_art_w.json().get("language") == "vi")

# Query file=debug_timing.json
r_art_t = client.get(f"/voice-records/meeting/jobs/{art_job['id']}/log?file=debug_timing.json")
check("GET job log?file=debug_timing.json returns timing JSON", r_art_t.status_code == 200 and r_art_t.json().get("whisper_transcription_sec") == 1.25)

# 11. Test Audio Conversion Probing and Reproducible FFMPEG Command
probe_info = worker._probe_audio_file(str(test_worker_wav))
check("worker._probe_audio_file extracts sample_rate and codec",
      probe_info.get("sample_rate") == 16000 and "pcm" in (probe_info.get("codec") or ""))
check("worker._probe_audio_file includes formatted duration_clock",
      bool(probe_info.get("duration_clock")))

out_converted_wav = _STATE / "test_out_conv.wav"
conv_ok, conv_err, conv_meta = worker._convert_audio_to_wav([str(test_worker_wav)], str(out_converted_wav))
check("worker._convert_audio_to_wav succeeds and returns metadata", conv_ok is True and not conv_err)
check("worker._convert_audio_to_wav includes reproducible ffmpeg_command_str",
      "ffmpeg" in conv_meta.get("ffmpeg_command_str", "") and "-ar 16000" in conv_meta.get("ffmpeg_command_str", ""))
check("worker._convert_audio_to_wav includes source and target audio specs",
      conv_meta.get("source", {}).get("total_size_bytes", 0) > 0 and conv_meta.get("target", {}).get("codec") == "pcm_s16le")

# Save audio_conversion.json in ts_dir to test API endpoint
(ts_dir / "audio_conversion.json").write_text(json.dumps(conv_meta, ensure_ascii=False, indent=2), encoding="utf-8")
r_conv_get = client.get(f"/voice-records/meeting/jobs/{art_job['id']}/log?file=audio_conversion.json")
check("GET job log?file=audio_conversion.json returns audio conversion spec",
      r_conv_get.status_code == 200 and "ffmpeg_command_str" in r_conv_get.json())

# Test Diarization parameters & speaker stats in diarization_output.json
diar_rich_data = {
    "pipeline": {
        "name": "pyannote/speaker-diarization-3.1",
        "segmentation_model": "pyannote/segmentation-3.0",
        "mode": "local",
        "device": "cuda",
    },
    "input_audio": {
        "path": str(test_worker_wav),
        "sample_rate": 16000,
        "channels": 1,
        "duration_sec": 0.1,
        "duration_clock": "00:00:00.100",
    },
    "parameters": {
        "num_speakers": 2,
        "min_speakers": 1,
        "max_speakers": 3,
        "pipeline_kwargs": {"num_speakers": 2},
    },
    "results": {
        "diarized": True,
        "turns_count": 2,
        "speakers_count": 2,
        "speakers": ["SPEAKER_00", "SPEAKER_01"],
        "speaker_statistics": {
            "SPEAKER_00": {"total_speaking_sec": 0.06, "total_speaking_clock": "00:00:00.060", "turns_count": 1, "speaking_ratio_percent": 60.0},
            "SPEAKER_01": {"total_speaking_sec": 0.04, "total_speaking_clock": "00:00:00.040", "turns_count": 1, "speaking_ratio_percent": 40.0},
        },
    },
    "timing": {
        "elapsed_sec": 0.85,
        "real_time_factor": 8.5,
    },
}
(ts_dir / "diarization_output.json").write_text(json.dumps(diar_rich_data, ensure_ascii=False, indent=2), encoding="utf-8")
r_diar_get = client.get(f"/voice-records/meeting/jobs/{art_job['id']}/log?file=diarization_output.json")
check("GET job log?file=diarization_output.json returns rich parameters and speaker stats",
      r_diar_get.status_code == 200 and r_diar_get.json().get("parameters", {}).get("num_speakers") == 2)

# Test Normalization artifact creation into the job's log directory
norm_test_rec = store.create_raw(
    brain="brain",
    brain_path=str(_BRAIN),
    file_path="VoiceRecord/test_norm.md",
    ceil_path="VoiceRecord/test_norm.md",
    raw_text="Xin chào",
    duration_sec=2.5,
    mode="meeting",
    segments=[{"start": 0.0, "end": 2.5, "text": "Xin chào", "speaker": "SPEAKER_00"}],
)
store.update_job(art_job["id"], record_id=norm_test_rec["id"])

r_norm_post = client.post(f"/voice-records/{norm_test_rec['id']}/normalize", json={"title": "Norm Test"})
check("POST /voice-records/{id}/normalize returns 200", r_norm_post.status_code == 200 and r_norm_post.json().get("ok") is True)
check("normalization_result.json created in job log_dir", (ts_dir / "normalization_result.json").is_file())
check("normalized_meeting.md created in job log_dir", (ts_dir / "normalized_meeting.md").is_file())

# 12. Test Whisper Hallucination Detection & Word-Based Segment Timestamp Clamping
check("worker._is_whisper_hallucination detects YouTube spam",
      worker._is_whisper_hallucination("Hãy subscribe cho kênh lalaschool Để không bỏ lỡ") is True)
check("worker._is_whisper_hallucination ignores normal speech",
      worker._is_whisper_hallucination("Hôm nay chúng ta họp bàn về kế hoạch quý tới") is False)

dummy_long_words = [
    {"start": 129.6, "end": 131.0, "word": "Trên"},
    {"start": 131.0, "end": 132.5, "word": "thị"},
    {"start": 132.5, "end": 134.0, "word": "trường"},
    {"start": 134.0, "end": 135.8, "word": "Việt"},
]
# Test segment stretching across 152 seconds: clamped to last word end + 0.1
split_chunks = worker._split_segment_by_words(
    seg_start=129.6,
    seg_end=282.2,
    text="Trên thị trường Việt",
    words=dummy_long_words,
)
check("worker._split_segment_by_words clamps 152s segment to actual words end (~135.9s)",
      len(split_chunks) == 1 and split_chunks[0]["end"] < 137.0 and split_chunks[0]["start"] == 129.6)

# Test segment with large gap between words: split into 2 chunks
words_with_gap = [
    {"start": 10.0, "end": 12.0, "word": "Đoạn 1"},
    {"start": 30.0, "end": 32.0, "word": "Đoạn 2"},
]
split_gap_chunks = worker._split_segment_by_words(
    seg_start=10.0,
    seg_end=32.0,
    text="Đoạn 1 Đoạn 2",
    words=words_with_gap,
    max_gap_sec=2.5,
)
check("worker._split_segment_by_words splits segment at >2.5s gap into 2 chunks",
      len(split_gap_chunks) == 2 and split_gap_chunks[0]["end"] < 13.0 and split_gap_chunks[1]["start"] == 30.0)

# 13. Test VAD Filter Configuration in meeting_stt and Job Store
import meeting_stt
st_cfg = meeting_stt.get_config()
check("meeting_stt.get_config defaults vad_filter to False", st_cfg.get("vad_filter") is False)

meeting_stt.update_config({"vad_filter": True})
st_cfg_updated = meeting_stt.get_config()
check("meeting_stt.update_config saves vad_filter True", st_cfg_updated.get("vad_filter") is True)
meeting_stt.update_config({"vad_filter": False})

vad_job = store.create_job(
    brain="brain",
    brain_path=str(_BRAIN),
    title="VAD Test Job",
    vad_filter=True,
)
check("store.create_job stores vad_filter True", vad_job.get("vad_filter") is True)
pub_vad = voice_records.public_job(store.get_job(vad_job["id"]))
check("voice_records.public_job exposes vad_filter", pub_vad.get("vad_filter") is True)

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
