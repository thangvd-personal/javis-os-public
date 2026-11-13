"""Isolated Meeting STT worker (Whisper GPU/CPU + pyannote speaker diarization).

Runs strictly in a subprocess invoked by `server/meeting_jobs.py` or
`server/optional_tools.py` and is NEVER imported by `server/main.py` at startup.

Note: Do NOT use the em dash character (U+2014) anywhere in this file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any


WHISPER_REPOS = {
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
}


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def emit_progress(stage: str, progress: int, detail: str = "") -> None:
    emit({
        "type": "progress",
        "stage": stage,
        "progress": max(0, min(100, int(progress))),
        "detail": detail,
    })


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _nearest_speaker(t0: float, t1: float, turns: list[dict[str, Any]], max_gap: float = 1.5) -> str:
    mid = 0.5 * (t0 + t1)
    best_spk = ""
    best_dist = max_gap + 1e-9
    for turn in turns:
        s0 = float(turn["start"])
        s1 = float(turn["end"])
        if s0 <= mid <= s1:
            dist = 0.0
        elif mid < s0:
            dist = s0 - mid
        else:
            dist = mid - s1
        if dist < best_dist:
            best_dist = dist
            best_spk = str(turn.get("speaker") or "")
    return best_spk


def _speaker_for_span(
    t0: float,
    t1: float,
    turns: list[dict[str, Any]],
    fallback: str = "",
) -> str:
    scores: dict[str, float] = {}
    for turn in turns:
        ov = _overlap(t0, t1, float(turn["start"]), float(turn["end"]))
        if ov > 0:
            spk = str(turn.get("speaker") or "")
            if spk:
                scores[spk] = scores.get(spk, 0.0) + ov
    if scores:
        return max(scores.items(), key=lambda kv: kv[1])[0]
    near = _nearest_speaker(t0, t1, turns)
    if near:
        return near
    return fallback


def _join_words_text(words: list[str]) -> str:
    raw = "".join(words)
    return " ".join(raw.split()).strip()


def summarize_speakers(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for seg in segments:
        spk = str(seg.get("speaker") or "").strip()
        if not spk:
            continue
        dur = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
        if spk not in stats:
            stats[spk] = {"id": spk, "duration": 0.0, "turns": 0}
            order.append(spk)
        stats[spk]["duration"] = round(stats[spk]["duration"] + dur, 2)
        stats[spk]["turns"] += 1
    return [stats[k] for k in order]


def assign_speakers_word_level(
    whisper_segments: list[dict[str, Any]],
    speaker_turns: list[dict[str, Any]],
    *,
    pause_split_sec: float = 1.6,
    max_turn_sec: float = 45.0,
) -> list[dict[str, Any]]:
    """Merge Whisper word/segment timestamps with pyannote speaker turns.

    Preserves clean conversational turns without breaking sentences on tiny
    pauses, while accurately splitting mid-segment speaker changes using word
    timestamps when available.
    """
    if not speaker_turns:
        out: list[dict[str, Any]] = []
        for seg in whisper_segments:
            txt = " ".join(str(seg.get("text") or "").split()).strip()
            if not txt:
                continue
            out.append({
                "start": round(float(seg.get("start", 0.0)), 3),
                "end": round(float(seg.get("end", 0.0)), 3),
                "speaker": "",
                "text": txt,
            })
        return out

    word_items: list[dict[str, Any]] = []
    fallback_spk = ""

    for seg in whisper_segments:
        s0 = float(seg.get("start", 0.0))
        s1 = float(seg.get("end", s0))
        seg_txt = " ".join(str(seg.get("text") or "").split()).strip()
        seg_dom = _speaker_for_span(s0, s1, speaker_turns, fallback=fallback_spk) or "SPEAKER_00"
        words = seg.get("words") or []
        if words:
            for w in words:
                w_txt = str(w.get("word") or "")
                if not w_txt.strip():
                    continue
                w0 = float(w.get("start", s0))
                w1 = float(w.get("end", w0))
                if w1 < w0:
                    w1 = w0
                spk = _speaker_for_span(w0, w1, speaker_turns, fallback=seg_dom) or seg_dom
                word_items.append({"start": w0, "end": w1, "text": w_txt, "speaker": spk})
                fallback_spk = spk
        elif seg_txt:
            word_items.append({"start": s0, "end": s1, "text": " " + seg_txt, "speaker": seg_dom})
            fallback_spk = seg_dom

    if not word_items:
        return []

    merged: list[dict[str, Any]] = []
    cur_spk = word_items[0]["speaker"]
    cur_start = word_items[0]["start"]
    cur_end = word_items[0]["end"]
    cur_words = [word_items[0]["text"]]

    for item in word_items[1:]:
        gap = item["start"] - cur_end
        dur = item["end"] - cur_start
        same_speaker = item["speaker"] == cur_spk
        should_split = (not same_speaker) or (gap > pause_split_sec) or (dur > max_turn_sec and gap > 0.35)
        if should_split:
            txt = _join_words_text(cur_words)
            if txt:
                merged.append({
                    "start": round(cur_start, 3),
                    "end": round(cur_end, 3),
                    "speaker": cur_spk,
                    "text": txt,
                })
            cur_spk = item["speaker"]
            cur_start = item["start"]
            cur_end = item["end"]
            cur_words = [item["text"]]
        else:
            w = item["text"]
            if cur_words and not cur_words[-1].endswith((" ", "\n")) and not w.startswith((" ", ".", ",", "!", "?", ";", ":", "…")):
                cur_words.append(" " + w)
            else:
                cur_words.append(w)
            cur_end = max(cur_end, item["end"])

    last_txt = _join_words_text(cur_words)
    if last_txt:
        merged.append({
            "start": round(cur_start, 3),
            "end": round(cur_end, 3),
            "speaker": cur_spk,
            "text": last_txt,
        })
    return merged


def _has_nvidia_gpu() -> bool:
    nvsmi = shutil.which("nvidia-smi") or ("/usr/bin/nvidia-smi" if Path("/usr/bin/nvidia-smi").is_file() else "")
    if not nvsmi:
        return False
    try:
        out = subprocess.check_output(
            [nvsmi, "--query-gpu=name", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=6.0,
        )
        return bool(out.strip())
    except Exception:
        return False


def _wav_duration_sec(wav_path: str) -> float:
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 16000
            return round(frames / float(rate), 3)
    except Exception:
        return 0.0


def _convert_audio_to_wav(
    input_paths: list[str],
    wav_path: str,
    combined_audio_out: str = "",
) -> tuple[bool, str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found on PATH"
    valid_inputs = [p for p in input_paths if p and Path(p).is_file()]
    if not valid_inputs:
        return False, "No valid audio input file found"

    if len(valid_inputs) == 1:
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", valid_inputs[0],
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            return False, (res.stderr or f"ffmpeg exited with {res.returncode}")[:400]
        if combined_audio_out and Path(valid_inputs[0]).resolve() != Path(combined_audio_out).resolve():
            os.makedirs(os.path.dirname(combined_audio_out), exist_ok=True)
            shutil.copy2(valid_inputs[0], combined_audio_out)
        return True, ""

    # Multi-segment inputs (e.g. separate MediaRecorder clips from Pause/Resume):
    # filter_complex concat re-encodes safely so differing WebM/Opus cluster headers never break.
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for p in valid_inputs:
        cmd.extend(["-i", p])
    inputs_tag = "".join(f"[{i}:a]" for i in range(len(valid_inputs)))
    cmd.extend([
        "-filter_complex", f"{inputs_tag}concat=n={len(valid_inputs)}:v=0:a=1[outa]",
        "-map", "[outa]", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        wav_path,
    ])
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        return False, (res.stderr or f"ffmpeg concat exited with {res.returncode}")[:400]

    if combined_audio_out:
        os.makedirs(os.path.dirname(combined_audio_out), exist_ok=True)
        cmd_m4a = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", wav_path,
            "-c:a", "aac", "-b:a", "96k",
            combined_audio_out,
        ]
        res2 = subprocess.run(cmd_m4a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res2.returncode != 0:
            shutil.copy2(wav_path, combined_audio_out)
    return True, ""


def _pick_whisper_device(requested: str) -> tuple[str, str]:
    import ctranslate2

    req = (requested or "auto").strip().lower()
    cuda_ok = False
    try:
        cuda_ok = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda_ok = False
    if req in ("auto", "cuda") and cuda_ok:
        try:
            supported = set(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:
            supported = {"float16"}
        if "float16" in supported:
            return "cuda", "float16"
        if "int8_float16" in supported:
            return "cuda", "int8_float16"
        return "cuda", "float32"
    return "cpu", "int8"


def _load_wav_float32(wav_path: str):
    """Read 16kHz mono PCM s16le WAV directly as float32 numpy array.

    Passing numpy ndarray to `WhisperModel.transcribe` bypasses `faster_whisper.audio.decode_audio`,
    avoiding PyAV `av.open(..., metadata_errors="ignore")` breakage in newer PyAV (e.g. av 19.x in local-ear/lib).
    """
    import numpy as np

    with wave.open(wav_path, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(frames, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _is_cached_locally(model_name: str, model_dir: str) -> bool:
    repo = WHISPER_REPOS.get(model_name)
    if not repo or not model_dir:
        return False
    slug = "models--" + repo.replace("/", "--")
    root = Path(model_dir) / slug
    try:
        return root.is_dir() and any(root.rglob("model.bin"))
    except OSError:
        return False


def _load_whisper_model(model_name: str, model_dir: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    local_only = _is_cached_locally(model_name, model_dir)
    try:
        return WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=model_dir,
            local_files_only=local_only,
        )
    except Exception as e:
        if device == "cuda" and ("CUDA" in str(e).upper() or "CUBLAS" in str(e).upper() or "LIBRARY" in str(e).upper()):
            return WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                download_root=model_dir,
                local_files_only=local_only,
            ), "cpu", "int8"
        raise


def _run_pyannote_diarization(
    wav_path: str,
    *,
    hf_token: str,
    device: str,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> tuple[list[dict[str, Any]], str]:
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as e:
        return [], f"pyannote_not_installed: {e}"

    try:
        try:
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
        except TypeError:
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=hf_token)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "gated" in msg.lower() or "token" in msg.lower():
            return [], f"pyannote_auth_error: {msg[:240]}"
        return [], f"pyannote_load_error: {msg[:240]}"

    if pipeline is None:
        return [], "pyannote_auth_error: Pipeline returned None (check HF_TOKEN and license acceptance for pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0)"

    if device == "cuda" and torch.cuda.is_available():
        try:
            pipeline.to(torch.device("cuda"))
        except Exception:
            pass

    kwargs: dict[str, Any] = {}
    if num_speakers and int(num_speakers) > 0:
        kwargs["num_speakers"] = int(num_speakers)
    else:
        if min_speakers and int(min_speakers) > 0:
            kwargs["min_speakers"] = int(min_speakers)
        if max_speakers and int(max_speakers) > 0:
            kwargs["max_speakers"] = int(max_speakers)

    try:
        diarization = pipeline(wav_path, **kwargs)
    except Exception as e:
        return [], f"pyannote_runtime_error: {type(e).__name__}: {str(e)[:240]}"

    # pyannote 3.1 returns Annotation directly; handle future wrappers gracefully too
    annotation = getattr(diarization, "speaker_diarization", diarization)
    turns: list[dict[str, Any]] = []
    for turn, _track, speaker in annotation.itertracks(yield_label=True):
        turns.append({
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": str(speaker),
        })
    return turns, ""


def _run_remote_diarization(
    wav_path: str,
    *,
    base_uri: str,
    api_key: str,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    timeout: float = 600.0,
) -> tuple[list[dict[str, Any]], str]:
    import urllib.request
    import urllib.error
    import uuid

    url = base_uri.rstrip("/") + "/voice-records/remote-diarize"
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    fields: dict[str, str] = {}
    if num_speakers and int(num_speakers) > 0:
        fields["num_speakers"] = str(int(num_speakers))
    if min_speakers and int(min_speakers) > 0:
        fields["min_speakers"] = str(int(min_speakers))
    if max_speakers and int(max_speakers) > 0:
        fields["max_speakers"] = str(int(max_speakers))

    for k, v in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))

    filename = Path(wav_path).name
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n".encode("utf-8"))
    with open(wav_path, "rb") as f:
        body.extend(f.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))
    if api_key:
        req.add_header("X-Speaker-Diarization-Key", api_key)
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, dict):
                return [], "remote_diarize_error: Invalid response from server"
            if not data.get("ok"):
                return [], f"remote_diarize_error: {data.get('error') or 'Unknown server error'}"
            turns = data.get("turns") or []
            return turns, ""
    except urllib.error.HTTPError as e:
        err_msg = ""
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            err_msg = err_data.get("error") or ""
        except Exception:
            pass
        return [], f"remote_diarize_http_{e.code}: {err_msg or e.reason}"
    except Exception as e:
        return [], f"remote_diarize_connection_error: {type(e).__name__}: {str(e)[:200]}"


def run_diarize_only(job_path: str) -> int:
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    wav_path = str(job.get("wav_path") or "").strip()
    hf_token = str(job.get("hf_token") or os.getenv("HF_TOKEN") or "").strip()
    device = str(job.get("device") or "auto").strip().lower()
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    turns, derr = _run_pyannote_diarization(
        wav_path,
        hf_token=hf_token,
        device=device,
        num_speakers=job.get("num_speakers"),
        min_speakers=job.get("min_speakers"),
        max_speakers=job.get("max_speakers"),
    )

    out_json = str(job.get("output_json") or "").strip()
    result = {
        "type": "result",
        "ok": not derr,
        "turns": turns,
        "error": derr if derr else "",
        "warnings": [derr] if derr else [],
    }
    if out_json:
        try:
            Path(out_json).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            result["warnings"].append(f"write_output_failed: {e}")
    emit(result)
    return 0 if not derr else 1


def run_job(job_path: str) -> int:
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    model_name = str(job.get("model") or "large-v3-turbo").strip()
    model_dir = str(job.get("model_dir") or "").strip()
    req_device = str(job.get("device") or "auto").strip().lower()
    lang = str(job.get("language") or "vi").strip().lower()
    want_diarize = bool(job.get("diarize"))
    hf_token = str(os.getenv("HF_TOKEN") or "").strip()
    input_paths = [str(p) for p in (job.get("input_paths") or []) if p]
    if not input_paths and job.get("audio_path"):
        input_paths = [str(job["audio_path"])]
    combined_audio_out = str(job.get("combined_audio_out") or "").strip()
    output_json = str(job.get("output_json") or "").strip()

    with tempfile.TemporaryDirectory(prefix="javis-meeting-") as tmpdir:
        wav_path = os.path.join(tmpdir, "audio_16k.wav")
        emit_progress("converting", 5, "ffmpeg 16kHz mono")
        ok_conv, conv_err = _convert_audio_to_wav(input_paths, wav_path, combined_audio_out=combined_audio_out)
        if not ok_conv:
            emit({"type": "result", "ok": False, "error": f"Audio conversion failed: {conv_err}"})
            return 1

        duration = _wav_duration_sec(wav_path)
        emit_progress("transcribing", 12, f"Loading Whisper {model_name}")

        dev, ctype = _pick_whisper_device(req_device)
        loaded = _load_whisper_model(model_name, model_dir, dev, ctype)
        if isinstance(loaded, tuple):
            model, dev, ctype = loaded
        else:
            model = loaded

        emit_progress("transcribing", 16, f"Whisper {model_name} ({dev}/{ctype})")
        audio_arr = _load_wav_float32(wav_path)
        fw_lang = None if lang in ("auto", "") else lang
        segments_iter, info = model.transcribe(
            audio_arr,
            language=fw_lang,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=True,
            condition_on_previous_text=False,
        )

        whisper_segments: list[dict[str, Any]] = []
        last_pct = 16
        max_pct = 65 if want_diarize else 92
        for seg in segments_iter:
            words_list = []
            if getattr(seg, "words", None):
                for w in seg.words:
                    words_list.append({
                        "start": float(w.start),
                        "end": float(w.end),
                        "word": str(w.word),
                    })
            whisper_segments.append({
                "start": float(seg.start),
                "end": float(seg.end),
                "text": str(seg.text or "").strip(),
                "words": words_list,
            })
            if duration > 0:
                pct = 16 + int(((float(seg.end) / duration) * (max_pct - 16)))
                pct = max(last_pct, min(max_pct, pct))
                if pct >= last_pct + 2:
                    last_pct = pct
                    emit_progress("transcribing", pct, f"{int(seg.end)}s / {int(duration)}s")

        # Release Whisper VRAM before loading pyannote if both use CUDA
        del model
        try:
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        warnings: list[str] = []
        speaker_turns: list[dict[str, Any]] = []
        diarized = False

        if want_diarize:
            remote_cfg = job.get("remote_diarize") or {}
            if remote_cfg.get("enabled") and remote_cfg.get("base_uri"):
                emit_progress("diarizing", 70, "Sending audio to remote speaker diarization server")
                turns, derr = _run_remote_diarization(
                    wav_path,
                    base_uri=remote_cfg["base_uri"],
                    api_key=remote_cfg.get("api_key", ""),
                    num_speakers=job.get("num_speakers"),
                    min_speakers=job.get("min_speakers"),
                    max_speakers=job.get("max_speakers"),
                )
                if derr:
                    warnings.append(derr)
                else:
                    speaker_turns = turns
                    diarized = bool(turns)
            elif not hf_token:
                warnings.append("missing_hf_token")
            else:
                emit_progress("diarizing", 70, "Running pyannote/speaker-diarization-3.1")
                turns, derr = _run_pyannote_diarization(
                    wav_path,
                    hf_token=hf_token,
                    device=dev,
                    num_speakers=job.get("num_speakers"),
                    min_speakers=job.get("min_speakers"),
                    max_speakers=job.get("max_speakers"),
                )
                if derr:
                    warnings.append(derr)
                else:
                    speaker_turns = turns
                    diarized = bool(turns)

        emit_progress("merging", 95, "Merging timestamps and speakers")
        merged_segments = assign_speakers_word_level(whisper_segments, speaker_turns)
        speakers = summarize_speakers(merged_segments)
        raw_text_lines = []
        for seg in merged_segments:
            sec = int(float(seg["start"]))
            hh, rem = divmod(sec, 3600)
            mm, ss = divmod(rem, 60)
            ts = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh > 0 else f"{mm:02d}:{ss:02d}"
            spk = seg.get("speaker") or ""
            if spk:
                raw_text_lines.append(f"[{ts} - {spk}] {seg['text']}")
            else:
                raw_text_lines.append(f"[{ts}] {seg['text']}")

        result_payload = {
            "type": "result",
            "ok": True,
            "segments": merged_segments,
            "speakers": speakers,
            "raw_text": "\n".join(raw_text_lines),
            "duration": duration,
            "detected_language": getattr(info, "language", "") or lang,
            "device_used": dev,
            "compute_type": ctype,
            "model_used": model_name,
            "diarized": diarized,
            "warnings": warnings,
        }
        if output_json:
            os.makedirs(os.path.dirname(output_json), exist_ok=True)
            Path(output_json).write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        emit_progress("done", 100, "Done")
        emit(result_payload)
        return 0


def _clean_venv_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _pip_install(py_bin: str, args: list[str], label: str) -> int:
    print(label, flush=True)
    cmd = [py_bin, "-I", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", *args]
    return subprocess.call(cmd, env=_clean_venv_env())


def _venv_health_ok(py_bin: str) -> bool:
    if not Path(py_bin).is_file():
        return False
    check_code = (
        "import torch, torchaudio, pyannote.audio, faster_whisper; "
        "assert torch.__version__.startswith('2.5.')"
    )
    return (
        subprocess.call(
            [py_bin, "-I", "-c", check_code],
            env=_clean_venv_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        == 0
    )


def _wipe_model_cache(model_dir: str, model_name: str) -> None:
    repo = WHISPER_REPOS.get(model_name, "")
    if not repo or not model_dir:
        return
    base = Path(model_dir).resolve()
    slug = "models--" + repo.replace("/", "--")
    root = base / slug
    if root.exists():
        other_refs: set[Path] = set()
        try:
            for sibling in base.glob("models--*"):
                if sibling.name == slug or not sibling.is_dir():
                    continue
                for s_item in sibling.rglob("*"):
                    if s_item.is_symlink():
                        try:
                            other_refs.add(s_item.resolve())
                        except OSError:
                            pass
        except OSError:
            pass
        targets: list[Path] = []
        try:
            for item in root.rglob("*"):
                if item.is_symlink():
                    try:
                        res = item.resolve()
                        if res != base and res.is_relative_to(base):
                            targets.append(res)
                    except OSError:
                        pass
        except OSError:
            pass
        for tgt in targets:
            if tgt in other_refs:
                continue
            try:
                if tgt.is_file() or tgt.is_symlink():
                    tgt.unlink(missing_ok=True)
                    for suffix in (".refs", ".lock", ".incomplete"):
                        Path(str(tgt) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(root, ignore_errors=True)
    locks_dir = base / ".locks" / slug
    if locks_dir.exists():
        shutil.rmtree(locks_dir, ignore_errors=True)


def _verify_model_files(model_dir: str, model_name: str) -> bool:
    repo = WHISPER_REPOS.get(model_name, "")
    if not repo or not model_dir:
        return False
    slug = "models--" + repo.replace("/", "--")
    root = Path(model_dir) / slug
    if not root.is_dir():
        return False
    if any(root.rglob("*.incomplete")):
        return False
    for mb in root.rglob("model.bin"):
        try:
            if mb.is_file() and mb.stat().st_size > 0 and (mb.parent / "config.json").is_file():
                return True
        except OSError:
            continue
    return False


def run_install(mode: str, venv_dir: str, model_dir: str, hf_home: str, model_name: str, force: bool = False) -> int:
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(hf_home, exist_ok=True)
    os.environ["HF_HOME"] = hf_home
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    if mode in ("all", "diarize"):
        vpath = Path(venv_dir)
        py_bin = str(vpath / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        if vpath.exists() and not _venv_health_ok(py_bin):
            print(f"Removing incomplete/outdated meeting-ear venv at {vpath} ...", flush=True)
            shutil.rmtree(vpath, ignore_errors=True)
        if not Path(py_bin).is_file():
            print(f"Creating isolated venv at {vpath} ...", flush=True)
            code = subprocess.call([sys.executable, "-m", "venv", str(vpath)], env=_clean_venv_env())
            if code != 0:
                emit({"type": "result", "ok": False, "error": f"venv creation failed with code {code}"})
                return 1

        _pip_install(py_bin, ["--upgrade", "pip", "setuptools", "wheel"], "Upgrading pip in meeting-ear venv ...")
        has_gpu = _has_nvidia_gpu()
        if has_gpu:
            torch_index = "https://download.pytorch.org/whl/cu124"
            print("NVIDIA GPU detected: installing pinned PyTorch CUDA 12.4 ...", flush=True)
        else:
            torch_index = "https://download.pytorch.org/whl/cpu"
            print("No NVIDIA GPU detected: installing pinned PyTorch CPU ...", flush=True)

        code = _pip_install(
            py_bin,
            ["--index-url", torch_index, "torch==2.5.1", "torchaudio==2.5.1"],
            "Installing torch==2.5.1 torchaudio==2.5.1 ...",
        )
        if code != 0:
            emit({"type": "result", "ok": False, "error": f"PyTorch install exited with {code}"})
            return 1

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as ctf:
            ctf.write("torch==2.5.1\ntorchaudio==2.5.1\nnumpy<2\nscipy<1.14\nhuggingface_hub<1.0\n")
            constraints_path = ctf.name
        try:
            code = _pip_install(
                py_bin,
                [
                    "-c",
                    constraints_path,
                    "--extra-index-url",
                    torch_index,
                    "pyannote-audio==3.1.*",
                    "faster-whisper>=1.1,<2",
                    "numpy<2",
                    "scipy<1.14",
                    "huggingface_hub<1.0",
                    "typing_extensions",
                ],
                "Installing pinned pyannote-audio==3.1.* and faster-whisper ...",
            )
        finally:
            try:
                os.unlink(constraints_path)
            except OSError:
                pass
        if code != 0:
            emit({"type": "result", "ok": False, "error": f"pyannote/faster-whisper install exited with {code}"})
            return 1

        if not _venv_health_ok(py_bin):
            emit({"type": "result", "ok": False, "error": "meeting-ear venv failed post-install import verification"})
            return 1

    if mode in ("all", "model", "whisper", "redownload"):
        if force or mode == "redownload":
            print(f"Removing cached files for Whisper model {model_name} before re-download ...", flush=True)
            _wipe_model_cache(model_dir, model_name)
        if not force and mode != "redownload" and _verify_model_files(model_dir, model_name):
            print(f"Whisper model {model_name} already cached and verified in {model_dir}.", flush=True)
        else:
            print(f"Downloading / verifying Whisper model {model_name} in {model_dir} ...", flush=True)
            venv_py = str(Path(venv_dir) / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
            dl_cmd = [sys.executable]
            dl_env = dict(os.environ)
            if Path(venv_py).is_file():
                has_fw = subprocess.call(
                    [venv_py, "-I", "-c", "import faster_whisper"],
                    env=_clean_venv_env(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if has_fw == 0:
                    dl_cmd = [venv_py, "-I"]
                    dl_env = _clean_venv_env()
                    dl_env["HF_HOME"] = hf_home
                    dl_env["HF_HUB_DISABLE_TELEMETRY"] = "1"
            dl_code = (
                "from faster_whisper import WhisperModel; "
                f"WhisperModel({model_name!r}, device='cpu', compute_type='int8', download_root={model_dir!r}, local_files_only=False)"
            )
            code = subprocess.call([*dl_cmd, "-c", dl_code], env=dl_env)
            if code != 0:
                emit({"type": "result", "ok": False, "error": f"Whisper model download exited with {code}"})
                return 1
            if not _verify_model_files(model_dir, model_name):
                emit({"type": "result", "ok": False, "error": f"Whisper model {model_name} files incomplete after download"})
                return 1

    tok = os.getenv("HF_TOKEN", "").strip()
    if mode in ("all", "diarize") and tok:
        py_exec = str(Path(venv_dir) / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        if Path(py_exec).is_file():
            print("Pre-warming pyannote/speaker-diarization-3.1 ...", flush=True)
            warm_code = (
                "import os; from pyannote.audio import Pipeline; "
                "Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', use_auth_token=os.environ['HF_TOKEN'])"
            )
            wenv = _clean_venv_env()
            wenv["HF_HOME"] = hf_home
            wenv["HF_TOKEN"] = tok
            wcode = subprocess.call([py_exec, "-I", "-c", warm_code], env=wenv)
            if wcode != 0:
                print(
                    "Warning: pyannote warm-up failed (check HF_TOKEN and Hugging Face license acceptance).",
                    flush=True,
                )

    emit({"type": "result", "ok": True})
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true")
    p.add_argument("--install-mode", default="all", choices=("all", "diarize", "model", "whisper", "redownload"))
    p.add_argument("--force", action="store_true")
    p.add_argument("--venv-dir", default="")
    p.add_argument("--model-dir", default="")
    p.add_argument("--hf-home", default="")
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--run", action="store_true")
    p.add_argument("--diarize-only", action="store_true")
    p.add_argument("--job", default="")
    a = p.parse_args(argv)
    try:
        if a.install:
            return run_install(a.install_mode, a.venv_dir, a.model_dir, a.hf_home, a.model, force=a.force)
        if a.run:
            if not a.job:
                emit({"type": "result", "ok": False, "error": "--job is required with --run"})
                return 1
            return run_job(a.job)
        if a.diarize_only:
            if not a.job:
                emit({"type": "result", "ok": False, "error": "--job is required with --diarize-only"})
                return 1
            return run_diarize_only(a.job)
        p.print_help()
        return 0
    except Exception as e:
        emit({"type": "result", "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
