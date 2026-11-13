"""Meeting STT stack tests: word-level speaker merge, encrypted HF_TOKEN,
chunked upload & retry, stuck-job recovery, 30-day audio GC + pin,
speaker mapping, prefix-preserving AI normalization, and SRT/VTT/TXT/JSON export.

    python tests/run.py meeting_stt
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_STATE = Path(tempfile.mkdtemp(prefix="javis-mt-state-"))
_BRAIN = Path(tempfile.mkdtemp(prefix="javis-mt-brain-"))
os.environ["JAVIS_STATE_DIR"] = str(_STATE)

sys.path.insert(0, str(SERVER / "meeting_ear"))
import worker as mt_worker  # noqa: E402
import config as cfgmod  # noqa: E402
import main  # noqa: E402
import meeting_jobs  # noqa: E402
import meeting_stt  # noqa: E402
import optional_tools  # noqa: E402
import transcript_fix  # noqa: E402
import voice_records  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


# 1. Word-level merge across mid-segment speaker transition
whisper_segs = [
    {
        "start": 0.0,
        "end": 4.0,
        "text": "Chào mọi người tôi là An bắt đầu nhé",
        "words": [
            {"start": 0.0, "end": 0.5, "word": "Chào"},
            {"start": 0.5, "end": 1.0, "word": " mọi"},
            {"start": 1.0, "end": 1.6, "word": " người"},
            {"start": 2.1, "end": 2.6, "word": " bắt"},
            {"start": 2.6, "end": 3.1, "word": " đầu"},
            {"start": 3.1, "end": 3.6, "word": " nhé"},
        ],
    }
]
spk_turns = [
    {"start": 0.0, "end": 1.9, "speaker": "SPEAKER_00"},
    {"start": 2.0, "end": 4.2, "speaker": "SPEAKER_01"},
]
merged = mt_worker.assign_speakers_word_level(whisper_segs, spk_turns)
check("word-level merge splits one Whisper segment across 2 speakers",
      len(merged) == 2
      and merged[0]["speaker"] == "SPEAKER_00"
      and "Chào mọi người" in merged[0]["text"]
      and merged[1]["speaker"] == "SPEAKER_01"
      and "bắt đầu nhé" in merged[1]["text"])

# Whisper-only fallback (no speaker turns)
wo = mt_worker.assign_speakers_word_level(whisper_segs, [])
check("whisper-only fallback keeps clean segment with empty speaker",
      len(wo) == 1 and wo[0]["speaker"] == "" and "Chào mọi người" in wo[0]["text"])

# 2. Setup state: local-ear model cache detection + encrypted hf_token handling
store = voice_records.VoiceRecordStore(_STATE / "voice_records.sqlite3")
fake_snap = _STATE / "local-ear" / "models" / "models--Systran--faster-whisper-medium" / "snapshots" / "abc"
fake_snap.mkdir(parents=True, exist_ok=True)
(fake_snap / "model.bin").write_bytes(b"1234")
(fake_snap / "config.json").write_text('{"model": "medium"}', encoding="utf-8")
# Incomplete model (missing config.json) must NOT be treated as validly cached
incomplete_snap = _STATE / "local-ear" / "models" / "models--Systran--faster-whisper-small" / "snapshots" / "def"
incomplete_snap.mkdir(parents=True, exist_ok=True)
(incomplete_snap / "model.bin").write_bytes(b"1234")
(_STATE / "local-ear" / "lib" / "faster_whisper").mkdir(parents=True, exist_ok=True)
(_STATE / "local-ear" / "lib" / "ctranslate2").mkdir(parents=True, exist_ok=True)

_orig_gate = main.cfgmod.gate_active
_orig_store_get = voice_records.get_store
_orig_brain_root = main._brain_root
_orig_files_root = main._files_root
_orig_enqueue = meeting_jobs.enqueue_job
_orig_norm_api = transcript_fix._ask_normalize_api
_orig_norm_ask = transcript_fix._ASK

main.cfgmod.gate_active = lambda: False
voice_records.get_store = lambda: store
main._brain_root = lambda b: str(_BRAIN)
main._files_root = lambda b: _BRAIN.resolve()
client = TestClient(main.app, base_url="http://127.0.0.1")

r_st = client.get("/voice-records/meeting/status").json()
check("status detects cached medium model & rejects incomplete small model",
      r_st.get("ok") is True
      and r_st["cached_whisper_models"].get("medium") is True
      and r_st["cached_whisper_models"].get("small") is False
      and r_st.get("whisper_ready") is True
      and meeting_stt.SUPPORTED_WHISPER_MODELS["large-v3-turbo"]["repo"] == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
      and mt_worker.WHISPER_REPOS["large-v3-turbo"] == "mobiuslabsgmbh/faster-whisper-large-v3-turbo")

# Incomplete venv missing faster_whisper / typing_extensions must report venv_diarize_ready=False
broken_sp = _STATE / "meeting-ear" / "venv" / "lib" / "python3.12" / "site-packages"
(broken_sp / "pyannote" / "audio").mkdir(parents=True, exist_ok=True)
(broken_sp / "torch").mkdir(parents=True, exist_ok=True)
if os.name == "nt":
    (_STATE / "meeting-ear" / "venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (_STATE / "meeting-ear" / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
else:
    (_STATE / "meeting-ear" / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    (_STATE / "meeting-ear" / "venv" / "bin" / "python").write_text("", encoding="utf-8")
check("venv_diarize_ready rejects venv missing faster_whisper and typing_extensions",
      meeting_stt.venv_diarize_ready() is False)
(broken_sp / "faster_whisper").mkdir(parents=True, exist_ok=True)
(broken_sp / "typing_extensions.py").write_text("", encoding="utf-8")
check("venv_diarize_ready accepts complete venv stack",
      meeting_stt.venv_diarize_ready() is True)
shutil.rmtree(_STATE / "meeting-ear", ignore_errors=True)

secret_tok = "hf_super_secret_test_token_987654"
r_cfg = client.post("/voice-records/meeting/config", json={"hf_token": secret_tok, "whisper_model": "medium"})
d_cfg = r_cfg.json()
check("config sets has_hf_token=True without exposing secret token in API response",
      r_cfg.status_code == 200
      and d_cfg.get("has_hf_token") is True
      and secret_tok not in json.dumps(d_cfg))

raw_settings_file = (_STATE / "settings.json").read_text(encoding="utf-8")
check("hf_token is encrypted at rest in settings.json (no plaintext)",
      secret_tok not in raw_settings_file and "enc:" in raw_settings_file)

r_pub_settings = client.get("/settings").json()
check("GET /settings never exposes raw hf_token",
      secret_tok not in json.dumps(r_pub_settings))

# 2b. POST /voice-records/meeting/install works cleanly for all & model modes (regression for NameError)
_orig_chay_tai = optional_tools._chay_tai
_install_calls = []
_captured_cmds = []


async def _mock_chay_tai(cong_cu: str):
    _install_calls.append(cong_cu)
    _captured_cmds.append(optional_tools._lenh_cai(cong_cu)[0])
    optional_tools._viec.setdefault(cong_cu, {})["dang_chay"] = False
    optional_tools._viec[cong_cu]["thanh_cong"] = True
    return None


optional_tools._chay_tai = _mock_chay_tai
r_inst_all = client.post("/voice-records/meeting/install", json={"mode": "all", "model": "large-v3-turbo"})
d_inst_all = r_inst_all.json()
cmd_all = _captured_cmds[-1] if _captured_cmds else []
check("POST /voice-records/meeting/install mode=all returns 200 and builds worker --install cmd",
      r_inst_all.status_code == 200
      and d_inst_all.get("ok") is True
      and isinstance(d_inst_all.get("status"), dict)
      and "meeting_ear" in _install_calls
      and "--install" in cmd_all)

r_inst_mod = client.post("/voice-records/meeting/install", json={"mode": "model", "model": "small"})
cmd_mod = _captured_cmds[-1] if _captured_cmds else []
check("POST /voice-records/meeting/install mode=model sets pending model args",
      r_inst_mod.status_code == 200
      and r_inst_mod.json().get("ok") is True
      and "--install-mode" in cmd_mod and "model" in cmd_mod
      and "--model" in cmd_mod and "small" in cmd_mod
      and "--force" not in cmd_mod)

r_inst_redl = client.post("/voice-records/meeting/install", json={"mode": "redownload", "model": "medium"})
cmd_redl = _captured_cmds[-1] if _captured_cmds else []
check("POST /voice-records/meeting/install mode=redownload passes --force and removes model safely",
      r_inst_redl.status_code == 200
      and r_inst_redl.json().get("ok") is True
      and r_inst_redl.json()["status"].get("installing") is True
      and "--install-mode" in cmd_redl and "redownload" in cmd_redl
      and "--force" in cmd_redl and "--model" in cmd_redl and "medium" in cmd_redl)
optional_tools._chay_tai = _orig_chay_tai
optional_tools._viec.pop("meeting_ear", None)

# Test worker _wipe_model_cache removes symlinked blob cleanly without touching sibling blobs
shared_blobs = meeting_stt.models_dir() / "blobs" / "aa"
shared_blobs.mkdir(parents=True, exist_ok=True)
med_blob = shared_blobs / "medblobhash"
med_blob.write_bytes(b"corrupt_med")
link_med = _STATE / "local-ear" / "models" / "models--Systran--faster-whisper-medium" / "snapshots" / "abc" / "weight_link.bin"
link_med.symlink_to(os.path.relpath(med_blob, link_med.parent))
mt_worker._wipe_model_cache(str(meeting_stt.models_dir()), "medium")
check("_wipe_model_cache removes cached model dir and its unshared HF blob",
      not meeting_stt.is_whisper_model_cached("medium") and not med_blob.exists())
# Recreate medium fixture for later checks
fake_snap.mkdir(parents=True, exist_ok=True)
(fake_snap / "model.bin").write_bytes(b"1234")
(fake_snap / "config.json").write_text('{"model": "medium"}', encoding="utf-8")

# 2c. POST /voice-records/meeting/models/delete removes model safely and returns updated status
r_del_empty = client.post("/voice-records/meeting/models/delete", json={})
check("POST /voice-records/meeting/models/delete rejects empty model", r_del_empty.status_code == 400)

del_test_snap = _STATE / "local-ear" / "models" / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "del_test"
del_test_snap.mkdir(parents=True, exist_ok=True)
(del_test_snap / "model.bin").write_bytes(b"9999")
(del_test_snap / "config.json").write_text('{"model": "large-v3-turbo"}', encoding="utf-8")
check("large-v3-turbo cached before delete", meeting_stt.is_whisper_model_cached("large-v3-turbo") is True)

r_del = client.post("/voice-records/meeting/models/delete", json={"model": "large-v3-turbo"})
check("POST /voice-records/meeting/models/delete removes cached model and returns updated status",
      r_del.status_code == 200
      and r_del.json().get("ok") is True
      and r_del.json()["status"]["cached_whisper_models"].get("large-v3-turbo") is False
      and meeting_stt.is_whisper_model_cached("large-v3-turbo") is False)

# 3. Chunked upload, retry overwrite idempotency, assembly, and stuck job recovery
called_jobs = []
meeting_jobs.enqueue_job = lambda jid, **kw: (called_jobs.append(jid) or store.update_job(jid, status="queued", progress=1))

r_cj = client.post("/voice-records/meeting/jobs", json={
    "brain": "brain",
    "title": "Hop giao ban",
    "upload_mode": "raw_upload",
    "file_ext": ".webm",
    "model": "medium",
    "total_chunks": 2,
})
jid = r_cj.json()["job"]["id"]
check("POST /voice-records/meeting/jobs creates uploading job", r_cj.status_code == 200 and r_cj.json()["job"]["status"] == "uploading")

# Upload chunk 1, then chunk 0, then overwrite chunk 0 (retry simulation)
client.post(f"/voice-records/meeting/jobs/{jid}/chunk", data={"chunk_index": "1"}, files={"file": ("c1", b"WORLD", "application/octet-stream")})
client.post(f"/voice-records/meeting/jobs/{jid}/chunk", data={"chunk_index": "0"}, files={"file": ("c0", b"WRONG", "application/octet-stream")})
r_c0_retry = client.post(f"/voice-records/meeting/jobs/{jid}/chunk", data={"chunk_index": "0"}, files={"file": ("c0", b"HELLO_", "application/octet-stream")})
check("chunk retry overwrites atomically without inflating received_chunks",
      r_c0_retry.status_code == 200 and r_c0_retry.json()["received_chunks"] == 2)

in_paths, combined_out = meeting_jobs.assemble_uploaded_audio(store.get_job(jid))
check("raw_upload binary assembly joins slice 0 + slice 1 cleanly",
      len(in_paths) == 1 and Path(in_paths[0]).read_bytes() == b"HELLO_WORLD")

r_fin = client.post(f"/voice-records/meeting/jobs/{jid}/finish", json={"total_chunks": 2})
check("POST /finish enqueues job", r_fin.status_code == 200 and jid in called_jobs and r_fin.json()["job"]["status"] == "queued")

store.update_job(jid, status="transcribing", progress=45)
n_recovered = meeting_jobs.recover_stuck_jobs()
check("recover_stuck_jobs resets in-flight job to failed on restart",
      n_recovered >= 1 and store.get_job(jid)["status"] == "failed")

# 4. Speaker mapping + prefix-preserving AI normalization + exports + audio pin/GC
vr_dir = _BRAIN / "VoiceRecord"
vr_dir.mkdir(parents=True, exist_ok=True)
dt_now = datetime(2026, 11, 12, 20, 45, 0)
md_name = voice_records.make_record_filename(dt_now, vr_dir)
aud_dir = meeting_stt.audio_dir()
aud_dir.mkdir(parents=True, exist_ok=True)
aud_file = aud_dir / "rec-sample.webm"
aud_file.write_bytes(b"RIFF_FAKE_AUDIO")

orig_segs = [
    {"start": 2.0, "end": 6.5, "speaker": "SPEAKER_00", "text": "hôm nay chúng ta chốt kế hoạch quý 4"},
    {"start": 7.0, "end": 12.2, "speaker": "SPEAKER_01", "text": "em đã chuẩn bị báo cáo doanh thu tháng này"},
]
md_init = voice_records.render_meeting_markdown(
    created_iso=dt_now.isoformat(),
    duration_sec=12.2,
    model_used="medium",
    segments=orig_segs,
    speaker_map={},
    title="Hop Q4",
)
(_BRAIN / "VoiceRecord" / md_name).write_text(md_init, encoding="utf-8")

rec = store.create_raw(
    brain="brain",
    brain_path=str(_BRAIN.resolve()),
    raw_text="[00:02 - SPEAKER_00] hôm nay chúng ta chốt kế hoạch quý 4\n[00:07 - SPEAKER_01] em đã chuẩn bị báo cáo doanh thu tháng này",
    file_path=f"VoiceRecord/{md_name}",
    ceil_path=f"VoiceRecord/{md_name}",
    created_at=dt_now.timestamp() - (35 * 86400),  # 35 days old -> eligible for GC unless pinned
    created_iso=dt_now.isoformat(),
    mode="meeting",
    job_id=jid,
    duration_sec=12.2,
    audio_path=str(aud_file),
    segments=orig_segs,
    model_used="medium",
    device_used="cuda",
)
rid = rec["id"]

# Map SPEAKER_00 -> Anh Nam BEFORE normalization
r_spk = client.post(f"/voice-records/{rid}/speakers", json={"speaker_map": {"SPEAKER_00": "Anh Nam", "SPEAKER_01": "Chị Lan"}, "title": "Hop Q4"})
check("POST /speakers updates VoiceRecord/*.md with real names and YAML frontmatter",
      r_spk.status_code == 200 and "Anh Nam" in (_BRAIN / f"VoiceRecord/{md_name}").read_text(encoding="utf-8")
      and "SPEAKER_00" in json.dumps(store.get(rid)["segments"]))

# Normalize keeping [HH:MM:SS - Tên] prefixes 100%
async def _mock_meeting_ai(pick, prompt: str, raw_text: str):
    return (
        "[00:02 - Anh Nam] Hôm nay chúng ta chốt kế hoạch quý 4.\n"
        "[00:07 - Chị Lan] Em đã chuẩn bị báo cáo doanh thu tháng này."
    )

cfgmod.update_settings({"model": {"order": ["openai"], "openai_api_key": "sk-test"}})
transcript_fix._ask_normalize_api = _mock_meeting_ai
transcript_fix._ASK = None

r_norm = client.post(f"/voice-records/{rid}/normalize", json={"title": "Hop Q4"})
d_norm = r_norm.json()
md_after = (_BRAIN / f"VoiceRecord/{md_name}").read_text(encoding="utf-8")
check("POST /voice-records/{id}/normalize preserves [MM:SS - Speaker] prefix and fixes grammar",
      r_norm.status_code == 200
      and d_norm.get("ai") is True
      and "[00:02 - Anh Nam] Hôm nay chúng ta chốt kế hoạch quý 4." in md_after
      and "[00:07 - Chị Lan] Em đã chuẩn bị báo cáo doanh thu tháng này." in md_after)
check("immutable audit raw_text & segments remain unedited after normalize",
      "hôm nay chúng ta chốt kế hoạch quý 4" in store.get(rid)["raw_text"]
      and store.get(rid)["segments"][0]["speaker"] == "SPEAKER_00")

# Re-map speaker AFTER normalization -> must retain normalized grammar without re-calling AI!
r_remap = client.post(f"/voice-records/{rid}/speakers", json={"speaker_map": {"SPEAKER_00": "Sếp Tuấn", "SPEAKER_01": "Chị Lan"}, "title": "Hop Q4"})
md_remapped = (_BRAIN / f"VoiceRecord/{md_name}").read_text(encoding="utf-8")
check("remapping speakers after normalize retains normalized text cleanly",
      r_remap.status_code == 200
      and "[00:02 - Sếp Tuấn] Hôm nay chúng ta chốt kế hoạch quý 4." in md_remapped)

# Export formats
r_srt = client.get(f"/voice-records/{rid}/export?format=srt")
r_vtt = client.get(f"/voice-records/{rid}/export?format=vtt")
r_txt = client.get(f"/voice-records/{rid}/export?format=txt")
r_jsn = client.get(f"/voice-records/{rid}/export?format=json")
check("export SRT/VTT/TXT/JSON succeed with mapped speaker names",
      r_srt.status_code == 200 and "-->" in r_srt.text and "[Sếp Tuấn]" in r_srt.text
      and r_vtt.status_code == 200 and r_vtt.text.startswith("WEBVTT") and "<v Sếp Tuấn>" in r_vtt.text
      and r_txt.status_code == 200 and "[00:02 - Sếp Tuấn]" in r_txt.text
      and r_jsn.status_code == 200 and r_jsn.json()["segments"][0]["speaker"] == "Sếp Tuấn")

# Rediarize existing meeting record
_orig_resolve_tok = meeting_stt.resolve_hf_token
_orig_resolve_py = meeting_stt.resolve_worker_python

meeting_stt.resolve_hf_token = lambda cfg=None: ("", False)
r_redia_no_tok = client.post(f"/voice-records/{rid}/rediarize")
check("POST /voice-records/{id}/rediarize rejects when HF_TOKEN is absent",
      r_redia_no_tok.status_code == 400 and "HF_TOKEN" in r_redia_no_tok.json().get("error", ""))

meeting_stt.resolve_hf_token = lambda cfg=None: ("hf_mock_token_123", True)
meeting_stt.resolve_worker_python = lambda need_diarize=False: (sys.executable, "")

r_redia_ok = client.post(f"/voice-records/{rid}/rediarize", json={"title": "Hop Q4 Rediarize"})
d_redia = r_redia_ok.json()
check("POST /voice-records/{id}/rediarize creates job linked to existing record_id",
      r_redia_ok.status_code == 200
      and d_redia.get("ok") is True
      and d_redia["job"]["record_id"] == rid
      and d_redia["job"]["diarize"] == 1
      and d_redia["job"]["has_audio"] is True)

new_diarized_segs = [
    {"start": 2.0, "end": 6.5, "speaker": "SPEAKER_00", "text": "Hôm nay chúng ta chốt kế hoạch quý 4."},
    {"start": 7.0, "end": 12.0, "speaker": "SPEAKER_01", "text": "Em đã chuẩn bị báo cáo doanh thu tháng này."},
]
up_rec = store.update_diarization(
    rid,
    segments=new_diarized_segs,
    raw_text="raw text with diarization",
    warnings=[],
    rendered_text="[00:02 - SPEAKER_00] Hôm nay chúng ta chốt kế hoạch quý 4.\n\n[00:07 - SPEAKER_01] Em đã chuẩn bị báo cáo doanh thu tháng này.",
    duration_sec=12.2,
    model_used="large-v3-turbo",
    device_used="cuda",
)
check("update_diarization updates segments, raw_text, clears warnings, and recalculates speakers",
      up_rec is not None
      and up_rec["warnings"] == []
      and len(up_rec["speakers"]) == 2
      and up_rec["speakers"][0]["id"] == "SPEAKER_00"
      and up_rec["model_used"] == "large-v3-turbo")

meeting_stt.resolve_hf_token = _orig_resolve_tok
meeting_stt.resolve_worker_python = _orig_resolve_py

# Audio playback + Pin prevents 30-day cleanup + Unpin allows 30-day cleanup
r_aud = client.get(f"/voice-records/{rid}/audio")
check("GET /voice-records/{id}/audio serves original audio", r_aud.status_code == 200 and r_aud.content == b"RIFF_FAKE_AUDIO")

r_pin = client.post(f"/voice-records/{rid}/pin", json={"pinned": True})
check("POST /pin marks record pinned=True", r_pin.status_code == 200 and r_pin.json()["record"]["pinned"] is True)
gc_pinned = meeting_jobs.cleanup_expired_audio(now_ts=time.time())
check("30-day audio GC skips pinned audio files", gc_pinned["files"] == 0 and aud_file.is_file())

client.post(f"/voice-records/{rid}/pin", json={"pinned": False})
gc_unpinned = meeting_jobs.cleanup_expired_audio(now_ts=time.time())
check("30-day audio GC removes expired unpinned audio and updates record",
      gc_unpinned["files"] == 1 and not aud_file.exists() and client.get(f"/voice-records/{rid}/audio").status_code == 404)

# Cleanup mocks and temp dirs
main.cfgmod.gate_active = _orig_gate
voice_records.get_store = _orig_store_get
main._brain_root = _orig_brain_root
main._files_root = _orig_files_root
meeting_jobs.enqueue_job = _orig_enqueue
transcript_fix._ask_normalize_api = _orig_norm_api
transcript_fix._ASK = _orig_norm_ask
store.close()
shutil.rmtree(_STATE, ignore_errors=True)
shutil.rmtree(_BRAIN, ignore_errors=True)

if _fails:
    print(f"\nFAIL: {len(_fails)} checks: {_fails}")
    sys.exit(1)
print("\nOK - meeting_stt")
