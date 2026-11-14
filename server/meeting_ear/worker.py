"""Isolated Meeting STT worker (Whisper GPU/CPU + pyannote speaker diarization).

Runs strictly in a subprocess invoked by `server/meeting_jobs.py` or
`server/optional_tools.py` and is NEVER imported by `server/main.py` at startup.

Note: Do NOT use the em dash character (U+2014) anywhere in this file.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime
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


def _fmt_clock_sec(seconds: float) -> str:
    sec = max(0.0, float(seconds or 0.0))
    m, s = divmod(sec, 60.0)
    h, m = divmod(m, 60.0)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def _human_size(size_bytes: int | float) -> str:
    s = float(size_bytes or 0)
    for u in ["B", "KB", "MB", "GB"]:
        if s < 1024.0 or u == "GB":
            return f"{s:.2f} {u}" if u != "B" else f"{int(s)} B"
        s /= 1024.0
    return f"{s:.2f} GB"


def _probe_audio_file(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {
            "path": str(path),
            "file_name": p.name,
            "exists": False,
            "size_bytes": 0,
            "size_human": "0 B",
            "format_name": "",
            "codec": "",
            "sample_rate": 0,
            "channels": 0,
            "channel_layout": "",
            "duration_sec": 0.0,
            "duration_clock": "00:00:00.000",
            "bit_rate": 0,
        }

    size_bytes = p.stat().st_size
    info: dict[str, Any] = {
        "path": str(p),
        "file_name": p.name,
        "exists": True,
        "size_bytes": size_bytes,
        "size_human": _human_size(size_bytes),
        "format_name": p.suffix.lstrip(".").lower() or "unknown",
        "codec": "",
        "sample_rate": 0,
        "channels": 0,
        "channel_layout": "",
        "duration_sec": 0.0,
        "duration_clock": "00:00:00.000",
        "bit_rate": 0,
    }

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            cmd = [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels,channel_layout,duration,bit_rate:format=format_name,duration,size,bit_rate",
                "-of", "json",
                str(p),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                streams = data.get("streams") or []
                fmt = data.get("format") or {}
                if streams:
                    s0 = streams[0]
                    info["codec"] = str(s0.get("codec_name") or "")
                    if s0.get("sample_rate"):
                        try:
                            info["sample_rate"] = int(s0["sample_rate"])
                        except Exception:
                            pass
                    if s0.get("channels"):
                        try:
                            info["channels"] = int(s0["channels"])
                        except Exception:
                            pass
                    info["channel_layout"] = str(s0.get("channel_layout") or "")
                    if s0.get("duration"):
                        try:
                            info["duration_sec"] = round(float(s0["duration"]), 3)
                        except Exception:
                            pass
                    if s0.get("bit_rate"):
                        try:
                            info["bit_rate"] = int(s0["bit_rate"])
                        except Exception:
                            pass
                if fmt:
                    if fmt.get("format_name"):
                        info["format_name"] = str(fmt["format_name"])
                    if info["duration_sec"] <= 0 and fmt.get("duration"):
                        try:
                            info["duration_sec"] = round(float(fmt["duration"]), 3)
                        except Exception:
                            pass
                    if not info["bit_rate"] and fmt.get("bit_rate"):
                        try:
                            info["bit_rate"] = int(fmt["bit_rate"])
                        except Exception:
                            pass
                    if not info["size_bytes"] and fmt.get("size"):
                        try:
                            info["size_bytes"] = int(fmt["size"])
                            info["size_human"] = _human_size(info["size_bytes"])
                        except Exception:
                            pass
        except Exception:
            pass

    # Fallback to wave module for WAV files if ffprobe didn't get details
    if p.suffix.lower() == ".wav" and (info["duration_sec"] <= 0 or not info["codec"]):
        try:
            with wave.open(str(p), "rb") as wf:
                sr = wf.getframerate() or 16000
                frames = wf.getnframes()
                info["sample_rate"] = sr
                info["channels"] = wf.getnchannels()
                info["codec"] = "pcm_s16le" if wf.getsampwidth() == 2 else f"pcm_{wf.getsampwidth()*8}bit"
                info["duration_sec"] = round(frames / float(sr), 3)
        except Exception:
            pass

    info["duration_clock"] = _fmt_clock_sec(info["duration_sec"])
    return info


def _get_system_debug_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
    }
    try:
        import torch
        info["torch_version"] = torch.__version__
        info["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            info["cuda_version"] = getattr(torch.version, "cuda", "") or ""
            info["device_count"] = torch.cuda.device_count()
            info["device_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["vram_total_mb"] = round(props.total_memory / (1024 * 1024), 1)
            info["vram_allocated_mb"] = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
            info["vram_reserved_mb"] = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 1)
    except Exception:
        pass
    return info


def _log_debug(stage: str, msg: str, elapsed: float | None = None) -> None:
    now_iso = datetime.now().astimezone().isoformat()
    el_str = f" in {elapsed:.3f}s" if elapsed is not None else ""
    print(f"[{now_iso}] [{stage}] {msg}{el_str}", flush=True)


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


def _is_whisper_hallucination(text: str) -> bool:
    t = (text or "").lower().strip()
    if not t:
        return False
    hallucination_phrases = [
        "subscribe cho kênh",
        "đăng ký kênh",
        "subscribe kênh",
        "like và subscribe",
        "like and subscribe",
        "hẹn gặp lại trong các video",
        "hẹn gặp lại các bạn",
        "cảm ơn các bạn đã theo dõi",
        "cảm ơn quý vị đã theo dõi",
        "lalaschool",
        "thank you for watching",
        "thanks for watching",
    ]
    for p in hallucination_phrases:
        if p in t:
            return True
    return False


def _split_segment_by_words(
    seg_start: float,
    seg_end: float,
    text: str,
    words: list[dict[str, Any]],
    max_gap_sec: float = 2.5,
    max_segment_len_sec: float = 30.0,
) -> list[dict[str, Any]]:
    """Clean and split Whisper segment using word timestamps.

    Fixes Whisper bug where seg.end stretches across long silence (e.g. 150s with only 10s of words).
    Splits segments when gaps between words exceed max_gap_sec or chunk length exceeds max_segment_len_sec.
    """
    if not words:
        clamped_end = min(seg_end, seg_start + max_segment_len_sec)
        return [{
            "start": round(seg_start, 3),
            "end": round(clamped_end, 3),
            "text": text.strip(),
            "words": [],
        }]

    chunks: list[dict[str, Any]] = []
    current_words: list[dict[str, Any]] = []

    for w in words:
        if not current_words:
            current_words.append(w)
            continue

        prev_w = current_words[-1]
        gap = float(w["start"]) - float(prev_w["end"])
        curr_span = float(w["end"]) - float(current_words[0]["start"])

        # Split if gap between words > max_gap_sec OR current chunk exceeds max_segment_len_sec
        if gap > max_gap_sec or (curr_span > max_segment_len_sec and gap > 0.8):
            chunk_text = "".join(cw["word"] for cw in current_words).strip()
            if not chunk_text:
                chunk_text = text.strip()
            w_s = float(current_words[0]["start"])
            w_e = float(current_words[-1]["end"]) + 0.1
            chunks.append({
                "start": round(w_s, 3),
                "end": round(w_e, 3),
                "text": chunk_text,
                "words": current_words,
            })
            current_words = [w]
        else:
            current_words.append(w)

    if current_words:
        chunk_text = "".join(cw["word"] for cw in current_words).strip()
        if not chunk_text:
            chunk_text = text.strip()
        w_s = float(current_words[0]["start"])
        w_e = float(current_words[-1]["end"]) + 0.1
        chunks.append({
            "start": round(w_s, 3),
            "end": round(w_e, 3),
            "text": chunk_text,
            "words": current_words,
        })

    return chunks


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
) -> tuple[bool, str, dict[str, Any]]:
    ffmpeg = shutil.which("ffmpeg")
    empty_meta: dict[str, Any] = {
        "ok": False,
        "error": "",
        "ffmpeg_parameters": {},
        "ffmpeg_command": [],
        "ffmpeg_command_str": "",
        "source": {},
        "target": {},
    }
    if not ffmpeg:
        empty_meta["error"] = "ffmpeg not found on PATH"
        return False, "ffmpeg not found on PATH", empty_meta
    valid_inputs = [p for p in input_paths if p and Path(p).is_file()]
    if not valid_inputs:
        empty_meta["error"] = "No valid audio input file found"
        return False, "No valid audio input file found", empty_meta

    source_probes = [_probe_audio_file(p) for p in valid_inputs]
    total_source_bytes = sum(s.get("size_bytes", 0) for s in source_probes)
    total_source_dur = sum(s.get("duration_sec", 0.0) for s in source_probes)
    primary_src = source_probes[0]

    ffmpeg_params: dict[str, Any] = {
        "executable": ffmpeg,
        "overwrite": "-y",
        "hide_banner": "-hide_banner",
        "log_level": "-loglevel error",
        "disable_video": "-vn",
        "sample_rate": "16000 (-ar 16000)",
        "channels": "1 (-ac 1)",
        "audio_codec": "pcm_s16le (-c:a pcm_s16le)",
    }

    combined_cmd_str = ""
    if len(valid_inputs) == 1:
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", valid_inputs[0],
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err = (res.stderr or f"ffmpeg exited with {res.returncode}")[:400]
            empty_meta["error"] = err
            empty_meta["ffmpeg_command"] = cmd
            empty_meta["ffmpeg_command_str"] = shlex.join(cmd)
            return False, err, empty_meta
        if combined_audio_out and Path(valid_inputs[0]).resolve() != Path(combined_audio_out).resolve():
            os.makedirs(os.path.dirname(combined_audio_out), exist_ok=True)
            shutil.copy2(valid_inputs[0], combined_audio_out)
    else:
        # Multi-segment inputs (e.g. separate MediaRecorder clips from Pause/Resume):
        # filter_complex concat re-encodes safely so differing WebM/Opus cluster headers never break.
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for p in valid_inputs:
            cmd.extend(["-i", p])
        inputs_tag = "".join(f"[{i}:a]" for i in range(len(valid_inputs)))
        filter_str = f"{inputs_tag}concat=n={len(valid_inputs)}:v=0:a=1[outa]"
        cmd.extend([
            "-filter_complex", filter_str,
            "-map", "[outa]", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path,
        ])
        ffmpeg_params["filter_complex"] = filter_str
        ffmpeg_params["map"] = "[outa]"

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err = (res.stderr or f"ffmpeg concat exited with {res.returncode}")[:400]
            empty_meta["error"] = err
            empty_meta["ffmpeg_command"] = cmd
            empty_meta["ffmpeg_command_str"] = shlex.join(cmd)
            return False, err, empty_meta

        if combined_audio_out:
            os.makedirs(os.path.dirname(combined_audio_out), exist_ok=True)
            cmd_m4a = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", wav_path,
                "-c:a", "aac", "-b:a", "96k",
                combined_audio_out,
            ]
            combined_cmd_str = shlex.join(cmd_m4a)
            res2 = subprocess.run(cmd_m4a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res2.returncode != 0:
                shutil.copy2(wav_path, combined_audio_out)

    target_probe = _probe_audio_file(wav_path)
    conversion_meta: dict[str, Any] = {
        "ok": True,
        "error": "",
        "source": {
            "inputs_count": len(valid_inputs),
            "files": source_probes,
            "format": primary_src.get("format_name") or "",
            "codec": primary_src.get("codec") or "",
            "sample_rate": primary_src.get("sample_rate") or 0,
            "channels": primary_src.get("channels") or 0,
            "channel_layout": primary_src.get("channel_layout") or "",
            "total_size_bytes": total_source_bytes,
            "total_size_human": _human_size(total_source_bytes),
            "duration_sec": round(total_source_dur, 3),
            "duration_clock": _fmt_clock_sec(total_source_dur),
        },
        "target": {
            "path": wav_path,
            "format": "wav",
            "codec": target_probe.get("codec") or "pcm_s16le",
            "sample_rate": target_probe.get("sample_rate") or 16000,
            "channels": target_probe.get("channels") or 1,
            "channel_layout": target_probe.get("channel_layout") or "mono",
            "bit_depth": "16-bit",
            "size_bytes": target_probe.get("size_bytes") or 0,
            "size_human": target_probe.get("size_human") or "0 B",
            "duration_sec": target_probe.get("duration_sec") or 0.0,
            "duration_clock": target_probe.get("duration_clock") or "00:00:00.000",
        },
        "ffmpeg_parameters": ffmpeg_params,
        "ffmpeg_command": cmd,
        "ffmpeg_command_str": shlex.join(cmd),
    }
    if combined_audio_out:
        conversion_meta["combined_audio"] = {
            "path": combined_audio_out,
            "ffmpeg_command_str": combined_cmd_str,
        }
    return True, "", conversion_meta


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


def _is_16k_mono_pcm16_wav(path: str) -> bool:
    try:
        with wave.open(path, "rb") as wf:
            return wf.getnchannels() == 1 and wf.getframerate() == 16000 and wf.getsampwidth() == 2
    except Exception:
        return False


def _ensure_16k_mono_wav(audio_path: str, tmp_dir: str | None = None) -> tuple[str, bool, str]:
    """Ensure audio file is 16kHz mono PCM16 WAV. If already so, returns (audio_path, False, '').

    Otherwise, converts using ffmpeg to a temporary WAV in tmp_dir and returns (converted_path, True, '').
    """
    if _is_16k_mono_pcm16_wav(audio_path):
        return audio_path, False, ""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "", False, "ffmpeg not found on PATH"

    if not tmp_dir:
        tmp_dir = tempfile.gettempdir()
    os.makedirs(tmp_dir, exist_ok=True)
    out_wav = os.path.join(tmp_dir, f"converted_16k_{os.getpid()}_{abs(hash(audio_path)) & 0xffffffff}.wav")

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", audio_path,
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        out_wav,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        return "", False, (res.stderr or f"ffmpeg exited with {res.returncode}")[:400]
    return out_wav, True, ""


def _load_audio_waveform(wav_path: str) -> tuple[Any, int, str]:
    """Load WAV into memory as (1, N) float32 torch.Tensor and sample_rate.

    Prefers `soundfile` for maximum speed, with fallback to torchaudio and standard wave/numpy.
    """
    try:
        import torch
    except ImportError:
        return None, 0, "torch_not_installed"

    # 1. Try soundfile (fastest for uncompressed WAV)
    try:
        import soundfile as sf
        data, sr = sf.read(wav_path, dtype="float32")
        if data.ndim == 1:
            tensor = torch.from_numpy(data).unsqueeze(0)
        elif data.ndim == 2:
            mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
            tensor = torch.from_numpy(mono).unsqueeze(0)
        else:
            return None, 0, f"Unsupported audio dimensions from soundfile: {data.ndim}"
        return tensor, int(sr), ""
    except Exception:
        pass

    # 2. Try torchaudio.load
    try:
        import torchaudio
        tensor, sr = torchaudio.load(wav_path)
        if tensor.ndim == 2 and tensor.shape[0] > 1:
            tensor = tensor.mean(dim=0, keepdim=True)
        return tensor.to(torch.float32), int(sr), ""
    except Exception:
        pass

    # 3. Fallback using standard library wave + numpy
    try:
        import numpy as np
        with wave.open(wav_path, "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
            sampwidth = wf.getsampwidth()
            n_channels = wf.getnchannels()

        if sampwidth == 2:
            pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            pcm = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            pcm = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0

        if n_channels > 1:
            pcm = pcm.reshape(-1, n_channels).mean(axis=1)
        tensor = torch.from_numpy(pcm).unsqueeze(0)
        return tensor, int(sr), ""
    except Exception as e:
        return None, 0, f"Failed to load audio into memory: {e}"


def _run_pyannote_diarization(
    wav_path: str,
    *,
    hf_token: str,
    device: str,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
    stage_name: str = "diarizing",
    progress_base: int = 70,
    progress_span: int = 25,
) -> tuple[list[dict[str, Any]], str]:
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as e:
        return [], f"pyannote_not_installed: {e}"

    # 1. Convert to 16kHz mono WAV once if needed
    norm_wav, is_temp, conv_err = _ensure_16k_mono_wav(wav_path)
    if conv_err:
        return [], f"audio_conversion_failed: {conv_err}"

    waveform = None
    audio_dict = None
    try:
        # 2. Pre-load WAV into RAM as float32 tensor to bypass torchcodec / StreamingMediaDecoder
        waveform, sr, load_err = _load_audio_waveform(norm_wav)
        if load_err or waveform is None:
            return [], f"audio_load_failed: {load_err}"

        audio_dict = {
            "waveform": waveform,
            "sample_rate": sr,
            "uri": Path(wav_path).stem or "audio",
        }

        # 3. Load pipeline
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

        # Progress hook to track segmentation and embedding stages
        last_reported_pct = progress_base
        def _pyannote_hook(step_name, step_artifact=None, file=None, completed=None, total=None, **_):
            nonlocal last_reported_pct
            step_str = str(step_name or "").lower()
            if "segment" in step_str:
                w_start, w_end = 0.05, 0.45
            elif "embed" in step_str:
                w_start, w_end = 0.45, 0.88
            else:
                w_start, w_end = 0.88, 0.98

            if completed is not None and total is not None and total > 0:
                frac = min(1.0, max(0.0, float(completed) / float(total)))
                sub_pct = w_start + frac * (w_end - w_start)
                detail = f"{step_name}: {completed}/{total} ({int(frac * 100)}%)"
            else:
                sub_pct = w_start
                detail = str(step_name)

            curr_pct = progress_base + int(sub_pct * progress_span)
            curr_pct = max(progress_base, min(progress_base + progress_span, curr_pct))
            if curr_pct >= last_reported_pct + 1:
                last_reported_pct = curr_pct
                emit_progress(stage_name, curr_pct, detail)

        kwargs: dict[str, Any] = {"hook": _pyannote_hook}
        if num_speakers and int(num_speakers) > 0:
            kwargs["num_speakers"] = int(num_speakers)
        else:
            if min_speakers and int(min_speakers) > 0:
                kwargs["min_speakers"] = int(min_speakers)
            if max_speakers and int(max_speakers) > 0:
                kwargs["max_speakers"] = int(max_speakers)

        try:
            diarization = pipeline(audio_dict, **kwargs)
        except TypeError as e:
            if "hook" in str(e) and "hook" in kwargs:
                del kwargs["hook"]
                diarization = pipeline(audio_dict, **kwargs)
            else:
                raise
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
    finally:
        if is_temp and norm_wav and os.path.isfile(norm_wav):
            try:
                os.unlink(norm_wav)
            except OSError:
                pass
        try:
            del waveform
            del audio_dict
        except Exception:
            pass
        try:
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


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
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, dict):
                return [], "remote_diarize_error: Invalid response from server"
            if not data.get("ok"):
                return [], f"remote_diarize_error: {data.get('error') or 'Unknown server error'}"

            # If server returned turns synchronously
            if "turns" in data:
                return data.get("turns") or [], ""

            job_id = data.get("job_id")
            if not job_id:
                return [], "remote_diarize_error: Missing job_id from server"

        # Background queue mode: poll until job completes
        poll_url = base_uri.rstrip("/") + f"/voice-records/remote-diarize/jobs/{job_id}"
        poll_req = urllib.request.Request(poll_url, method="GET")
        if api_key:
            poll_req.add_header("X-Speaker-Diarization-Key", api_key)
            poll_req.add_header("Authorization", f"Bearer {api_key}")

        import time
        start_time = time.time()
        max_poll_time = float(timeout or 14400.0)
        last_progress_emit = 0

        while (time.time() - start_time) < max_poll_time:
            time.sleep(2.5)
            try:
                with urllib.request.urlopen(poll_req, timeout=30.0) as poll_resp:
                    pdata = json.loads(poll_resp.read().decode("utf-8"))
                    if not isinstance(pdata, dict) or not pdata.get("ok"):
                        err_txt = (pdata or {}).get("error") or "Polling failed"
                        return [], f"remote_diarize_error: {err_txt}"

                    status = str(pdata.get("status") or "")
                    if status == "done":
                        emit_progress("diarizing", 95, "Máy chủ đã hoàn tất phân tách")
                        return pdata.get("turns") or [], ""
                    if status == "failed":
                        return [], f"remote_diarize_error: {pdata.get('error') or 'Job failed on server'}"

                    q_pos = int(pdata.get("queue_position") or 0)
                    prog = int(pdata.get("progress") or 0)
                    detail = str(pdata.get("stage_detail") or "")
                    if status == "queued":
                        msg = f"Đang chờ trong hàng đợi máy chủ (thứ tự: #{q_pos})" if q_pos > 0 else "Đang chờ máy chủ xử lý..."
                        emit_progress("diarizing", 70, msg)
                    elif status in ("diarizing", "processing", "converting"):
                        calc_pct = 70 + int(prog * 0.24)
                        if calc_pct >= last_progress_emit + 1:
                            last_progress_emit = calc_pct
                            emit_progress("diarizing", calc_pct, f"Máy chủ đang tách: {detail or f'{prog}%'}")
            except urllib.error.HTTPError as he:
                if he.code in (401, 403, 404):
                    return [], f"remote_diarize_http_{he.code}"
                continue
            except Exception:
                continue

        return [], f"remote_diarize_timeout: Quá thời gian chờ máy chủ ({int(max_poll_time // 60)} phút)"
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

    t_start = time.time()
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    log_dir_str = str(job.get("log_dir") or "").strip()
    log_dir = Path(log_dir_str) if log_dir_str else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    wav_path = str(job.get("wav_path") or "").strip()
    hf_token = str(job.get("hf_token") or os.getenv("HF_TOKEN") or "").strip()
    device = str(job.get("device") or "auto").strip().lower()
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    _log_debug("JOB_START", f"Starting diarize-only worker on {device}")
    t_diarize_start = time.time()
    turns, derr = _run_pyannote_diarization(
        wav_path,
        hf_token=hf_token,
        device=device,
        num_speakers=job.get("num_speakers"),
        min_speakers=job.get("min_speakers"),
        max_speakers=job.get("max_speakers"),
        stage_name="diarizing",
        progress_base=15,
        progress_span=75,
    )
    t_diarize_end = time.time()
    diarize_dur = t_diarize_end - t_diarize_start
    _log_debug("STAGE:DIARIZE", f"Diarize-only finished ({len(turns)} turns)", diarize_dur)

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

    total_dur = time.time() - t_start
    if log_dir:
        wav_probe = _probe_audio_file(wav_path)
        wav_dur = float(wav_probe.get("duration_sec") or 0.0)
        rtf_diarize = diarize_dur / max(0.1, wav_dur)

        speaker_stats: dict[str, dict[str, Any]] = {}
        for t in turns:
            spk = t.get("speaker") or "UNKNOWN"
            t_len = max(0.0, float(t.get("end", 0)) - float(t.get("start", 0)))
            if spk not in speaker_stats:
                speaker_stats[spk] = {
                    "speaker": spk,
                    "total_speaking_sec": 0.0,
                    "turns_count": 0,
                }
            speaker_stats[spk]["total_speaking_sec"] += t_len
            speaker_stats[spk]["turns_count"] += 1

        total_spoken_sec = sum(s["total_speaking_sec"] for s in speaker_stats.values())
        for spk, st in speaker_stats.items():
            st["total_speaking_sec"] = round(st["total_speaking_sec"], 3)
            st["total_speaking_clock"] = _fmt_clock_sec(st["total_speaking_sec"])
            st["speaking_ratio_percent"] = round((st["total_speaking_sec"] / max(0.001, total_spoken_sec)) * 100, 1)

        unique_speakers = sorted(speaker_stats.keys())

        pipeline_kwargs_record: dict[str, Any] = {}
        if job.get("num_speakers") and int(job["num_speakers"]) > 0:
            pipeline_kwargs_record["num_speakers"] = int(job["num_speakers"])
        if job.get("min_speakers") and int(job["min_speakers"]) > 0:
            pipeline_kwargs_record["min_speakers"] = int(job["min_speakers"])
        if job.get("max_speakers") and int(job["max_speakers"]) > 0:
            pipeline_kwargs_record["max_speakers"] = int(job["max_speakers"])

        # Also save audio_conversion.json for diarize-only
        (log_dir / "audio_conversion.json").write_text(
            json.dumps({
                "ok": True,
                "note": "Input audio passed directly to diarize-only worker",
                "source": {
                    "inputs_count": 1,
                    "files": [wav_probe],
                    "format": wav_probe.get("format_name") or "wav",
                    "codec": wav_probe.get("codec") or "pcm_s16le",
                    "sample_rate": wav_probe.get("sample_rate") or 16000,
                    "channels": wav_probe.get("channels") or 1,
                    "channel_layout": wav_probe.get("channel_layout") or "mono",
                    "total_size_bytes": wav_probe.get("size_bytes") or 0,
                    "total_size_human": wav_probe.get("size_human") or "0 B",
                    "duration_sec": wav_dur,
                    "duration_clock": wav_probe.get("duration_clock") or "00:00:00.000",
                },
                "target": {
                    "path": wav_path,
                    "format": "wav",
                    "codec": wav_probe.get("codec") or "pcm_s16le",
                    "sample_rate": wav_probe.get("sample_rate") or 16000,
                    "channels": wav_probe.get("channels") or 1,
                    "channel_layout": wav_probe.get("channel_layout") or "mono",
                    "bit_depth": "16-bit",
                    "size_bytes": wav_probe.get("size_bytes") or 0,
                    "size_human": wav_probe.get("size_human") or "0 B",
                    "duration_sec": wav_dur,
                    "duration_clock": wav_probe.get("duration_clock") or "00:00:00.000",
                },
                "ffmpeg_parameters": {
                    "note": "Pre-converted WAV was provided directly",
                },
                "ffmpeg_command": [],
                "ffmpeg_command_str": f"ffmpeg -y -hide_banner -loglevel error -i {shlex.quote(wav_path)} -vn -ar 16000 -ac 1 -c:a pcm_s16le output_16k.wav",
            }, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        diarize_data = {
            "pipeline": {
                "name": "pyannote/speaker-diarization-3.1",
                "segmentation_model": "pyannote/segmentation-3.0",
                "mode": "local",
                "remote_endpoint": None,
                "device": device,
                "hf_token_configured": bool(hf_token),
            },
            "input_audio": {
                "path": wav_path,
                "format": "wav",
                "codec": wav_probe.get("codec") or "pcm_s16le",
                "sample_rate": wav_probe.get("sample_rate") or 16000,
                "channels": wav_probe.get("channels") or 1,
                "channel_layout": wav_probe.get("channel_layout") or "mono",
                "duration_sec": wav_dur,
                "duration_clock": wav_probe.get("duration_clock") or "00:00:00.000",
                "in_memory_tensor_preloaded": True,
            },
            "parameters": {
                "num_speakers": job.get("num_speakers"),
                "min_speakers": job.get("min_speakers"),
                "max_speakers": job.get("max_speakers"),
                "pipeline_kwargs": pipeline_kwargs_record,
            },
            "results": {
                "diarized": bool(turns),
                "turns_count": len(turns),
                "speakers_count": len(unique_speakers),
                "speakers": unique_speakers,
                "speaker_statistics": speaker_stats,
                "total_spoken_sec": round(total_spoken_sec, 3),
                "total_spoken_clock": _fmt_clock_sec(total_spoken_sec),
                "turns": turns,
                "error": derr if derr else "",
                "warnings": [derr] if derr else [],
            },
            "timing": {
                "elapsed_sec": round(diarize_dur, 3),
                "real_time_factor": round(rtf_diarize, 4),
                "real_time_factor_str": f"{rtf_diarize:.3f}x realtime",
            },
            "device": device,
            "elapsed_sec": round(diarize_dur, 3),
            "real_time_factor": round(rtf_diarize, 4),
            "turns_count": len(turns),
            "speakers_count": len(unique_speakers),
            "turns": turns,
            "error": derr if derr else "",
            "warnings": [derr] if derr else [],
        }
        (log_dir / "diarization_output.json").write_text(json.dumps(diarize_data, ensure_ascii=False, indent=2), encoding="utf-8")
        timeline_header = [
            "=== THONG SO DAU VAO PHAN TACH GIONG NOI ===",
            "Pipeline: pyannote/speaker-diarization-3.1 (diarize-only)",
            f"Device: {device}",
            f"Audio duration: {_fmt_clock_sec(wav_dur)} ({wav_dur:.2f}s)",
            f"Parameters: num_speakers={job.get('num_speakers')}, min_speakers={job.get('min_speakers')}, max_speakers={job.get('max_speakers')}",
            f"Processing time: {diarize_dur:.3f}s (RTF: {rtf_diarize:.3f}x)",
            "",
            "=== TONG KET THEO NGUOI NOI ===",
        ]
        for spk in unique_speakers:
            s_info = speaker_stats[spk]
            timeline_header.append(
                f"- {spk}: {s_info['turns_count']} turns, {s_info['total_speaking_clock']} ({s_info['speaking_ratio_percent']}%)"
            )
        timeline_header.append(f"Tong so nguoi noi: {len(unique_speakers)}")
        timeline_header.append("")
        timeline_header.append("=== DONG THOI GIAN CHI TIET (TIMELINE) ===")

        timeline_lines = timeline_header + [
            f"[{_fmt_clock_sec(t['start'])} -> {_fmt_clock_sec(t['end'])}] {t.get('speaker', '')}"
            for t in turns
        ]
        (log_dir / "diarization_timeline.txt").write_text("\n".join(timeline_lines), encoding="utf-8")

        debug_timing = {
            "job_id": job.get("job_id") or "",
            "timestamp": datetime.now().astimezone().isoformat(),
            "total_elapsed_sec": round(total_dur, 3),
            "stages": {
                "speaker_diarization": {
                    "elapsed_sec": round(diarize_dur, 3),
                    "real_time_factor": round(rtf_diarize, 4),
                    "turns_count": len(turns),
                    "speakers_count": len(unique_speakers),
                    "device": device,
                    "num_speakers": job.get("num_speakers"),
                    "min_speakers": job.get("min_speakers"),
                    "max_speakers": job.get("max_speakers"),
                }
            },
            "system": _get_system_debug_info(),
        }
        (log_dir / "debug_timing.json").write_text(json.dumps(debug_timing, ensure_ascii=False, indent=2), encoding="utf-8")
        _log_debug("ARTIFACT", f"Saved diarization artifacts, audio_conversion.json, and debug_timing.json to {log_dir}")

    emit(result)
    return 0 if not derr else 1


def run_job(job_path: str) -> int:
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass

    t_run_start = time.time()
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    log_dir_str = str(job.get("log_dir") or "").strip()
    log_dir = Path(log_dir_str) if log_dir_str else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, dict[str, Any]] = {}
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

    _log_debug("JOB_START", f"Starting meeting worker for job {job.get('job_id', 'unknown')}")

    with tempfile.TemporaryDirectory(prefix="javis-meeting-") as tmpdir:
        wav_path = os.path.join(tmpdir, "audio_16k.wav")
        emit_progress("converting", 5, "ffmpeg 16kHz mono")

        t_conv_start = time.time()
        _log_debug("STAGE:CONVERT", f"Starting audio conversion with ffmpeg (inputs: {len(input_paths)})...")
        ok_conv, conv_err, conv_meta = _convert_audio_to_wav(input_paths, wav_path, combined_audio_out=combined_audio_out)
        t_conv_end = time.time()
        conv_dur = t_conv_end - t_conv_start
        if not ok_conv:
            _log_debug("STAGE:CONVERT_FAILED", f"Audio conversion failed: {conv_err}", conv_dur)
            emit({"type": "result", "ok": False, "error": f"Audio conversion failed: {conv_err}"})
            return 1

        src_meta = conv_meta.get("source") or {}
        tgt_meta = conv_meta.get("target") or {}
        _log_debug(
            "STAGE:CONVERT",
            f"Conversion completed: Source ({src_meta.get('format')}/{src_meta.get('codec')}, {src_meta.get('total_size_human')}, {src_meta.get('duration_clock')}) -> Target ({tgt_meta.get('format')}/{tgt_meta.get('codec')}, {tgt_meta.get('size_human')}, {tgt_meta.get('duration_clock')})",
            conv_dur,
        )
        _log_debug("FFMPEG_CMD", f"FFMPEG reproducible command: {conv_meta.get('ffmpeg_command_str')}")
        timings["audio_conversion"] = {
            "elapsed_sec": round(conv_dur, 3),
            "inputs_count": len(input_paths),
            "source_format": src_meta.get("format"),
            "source_codec": src_meta.get("codec"),
            "source_size_human": src_meta.get("total_size_human"),
            "source_duration_clock": src_meta.get("duration_clock"),
            "target_format": tgt_meta.get("format"),
            "target_codec": tgt_meta.get("codec"),
            "target_size_human": tgt_meta.get("size_human"),
            "target_duration_clock": tgt_meta.get("duration_clock"),
            "ffmpeg_command_str": conv_meta.get("ffmpeg_command_str"),
        }
        if log_dir:
            (log_dir / "audio_conversion.json").write_text(
                json.dumps(conv_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _log_debug("ARTIFACT", f"Saved audio_conversion.json to {log_dir}")

        duration = _wav_duration_sec(wav_path)
        _log_debug("AUDIO_PROBE", f"WAV duration: {duration:.2f}s ({_fmt_clock_sec(duration)})")
        emit_progress("transcribing", 12, f"Loading Whisper {model_name}")

        t_load_w_start = time.time()
        _log_debug("STAGE:WHISPER_LOAD", f"Loading Whisper model {model_name} on {req_device}...")
        dev, ctype = _pick_whisper_device(req_device)
        loaded = _load_whisper_model(model_name, model_dir, dev, ctype)
        if isinstance(loaded, tuple):
            model, dev, ctype = loaded
        else:
            model = loaded
        t_load_w_end = time.time()
        load_w_dur = t_load_w_end - t_load_w_start
        _log_debug("STAGE:WHISPER_LOAD", f"Whisper {model_name} loaded on {dev}/{ctype}", load_w_dur)
        timings["whisper_model_load"] = {
            "elapsed_sec": round(load_w_dur, 3),
            "model": model_name,
            "device": dev,
            "compute_type": ctype,
        }

        t_transcribe_start = time.time()
        want_vad = bool(job.get("vad_filter", False))
        beam_size = int(job.get("beam_size") or 5)
        condition_on_previous_text = bool(job.get("condition_on_previous_text", False))
        no_speech_threshold = float(job.get("no_speech_threshold") or 0.6)
        compression_ratio_threshold = float(job.get("compression_ratio_threshold") or 2.4)
        log_prob_threshold = float(job.get("log_prob_threshold") or -1.0)
        vad_params = {"min_silence_duration_ms": 500} if want_vad else None

        _log_debug(
            "STAGE:WHISPER_TRANSCRIBE",
            f"Transcribing audio with Whisper (duration: {duration:.2f}s, vad_filter={want_vad}, beam_size={beam_size})...",
        )
        emit_progress("transcribing", 16, f"Whisper {model_name} ({dev}/{ctype}, VAD={'on' if want_vad else 'off'})")
        audio_arr = _load_wav_float32(wav_path)
        fw_lang = None if lang in ("auto", "") else lang
        transcribe_kwargs: dict[str, Any] = {
            "language": fw_lang,
            "beam_size": beam_size,
            "vad_filter": want_vad,
            "word_timestamps": True,
            "condition_on_previous_text": condition_on_previous_text,
            "no_speech_threshold": no_speech_threshold,
            "compression_ratio_threshold": compression_ratio_threshold,
            "log_prob_threshold": log_prob_threshold,
        }
        if want_vad and vad_params:
            transcribe_kwargs["vad_parameters"] = vad_params

        segments_iter, info = model.transcribe(audio_arr, **transcribe_kwargs)

        whisper_segments: list[dict[str, Any]] = []
        last_pct = 16
        max_pct = 65 if want_diarize else 92
        raw_seg_count = 0
        hallucination_count = 0
        for seg in segments_iter:
            raw_seg_count += 1
            seg_text = str(seg.text or "").strip()
            if not seg_text:
                continue

            if _is_whisper_hallucination(seg_text):
                hallucination_count += 1
                _log_debug("WHISPER_HALLUCINATION_DROPPED", f"Dropped hallucination segment: '{seg_text}' at [{seg.start:.2f}s - {seg.end:.2f}s]")
                continue

            words_list = []
            if getattr(seg, "words", None):
                for w in seg.words:
                    words_list.append({
                        "start": float(w.start),
                        "end": float(w.end),
                        "word": str(w.word),
                    })

            sub_segs = _split_segment_by_words(
                seg_start=float(seg.start),
                seg_end=float(seg.end),
                text=seg_text,
                words=words_list,
                max_gap_sec=2.5,
                max_segment_len_sec=30.0,
            )
            whisper_segments.extend(sub_segs)

            if duration > 0:
                pct = 16 + int(((float(seg.end) / duration) * (max_pct - 16)))
                pct = max(last_pct, min(max_pct, pct))
                if pct >= last_pct + 2:
                    last_pct = pct
                    emit_progress("transcribing", pct, f"{int(seg.end)}s / {int(duration)}s")

        t_transcribe_end = time.time()
        transcribe_dur = t_transcribe_end - t_transcribe_start
        rtf_whisper = transcribe_dur / max(0.1, duration)
        total_words_count = sum(len(s.get("words") or []) for s in whisper_segments)
        _log_debug(
            "STAGE:WHISPER_TRANSCRIBE",
            f"Whisper transcribe completed: {len(whisper_segments)} segments (from {raw_seg_count} raw, {hallucination_count} hallucinations dropped), {total_words_count} words, RTF: {rtf_whisper:.3f}",
            transcribe_dur,
        )
        timings["whisper_transcription"] = {
            "elapsed_sec": round(transcribe_dur, 3),
            "audio_duration_sec": round(duration, 2),
            "real_time_factor": round(rtf_whisper, 4),
            "segments_count": len(whisper_segments),
            "raw_segments_count": raw_seg_count,
            "words_count": total_words_count,
            "vad_filter": want_vad,
            "beam_size": beam_size,
        }

        if log_dir:
            whisper_data = {
                "model": model_name,
                "device": dev,
                "compute_type": ctype,
                "language": getattr(info, "language", "") or lang,
                "duration_sec": round(duration, 2),
                "elapsed_sec": round(transcribe_dur, 3),
                "real_time_factor": round(rtf_whisper, 4),
                "parameters": {
                    "vad_filter": want_vad,
                    "vad_parameters": vad_params,
                    "beam_size": beam_size,
                    "condition_on_previous_text": condition_on_previous_text,
                    "word_timestamps": True,
                    "no_speech_threshold": no_speech_threshold,
                    "compression_ratio_threshold": compression_ratio_threshold,
                    "log_prob_threshold": log_prob_threshold,
                },
                "segments_count": len(whisper_segments),
                "raw_segments_count": raw_seg_count,
                "hallucinations_dropped_count": hallucination_count,
                "words_count": total_words_count,
                "segments": whisper_segments,
            }
            (log_dir / "whisper_output.json").write_text(json.dumps(whisper_data, ensure_ascii=False, indent=2), encoding="utf-8")
            raw_w_text = " ".join(seg["text"] for seg in whisper_segments if seg.get("text")).strip()
            (log_dir / "whisper_raw.txt").write_text(raw_w_text, encoding="utf-8")
            _log_debug("ARTIFACT", f"Saved whisper_output.json ({len(whisper_segments)} segments, {total_words_count} words) and whisper_raw.txt to {log_dir}")

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

        t_diarize_start = time.time()
        if want_diarize:
            remote_cfg = job.get("remote_diarize") or {}
            if remote_cfg.get("enabled") and remote_cfg.get("base_uri"):
                emit_progress("diarizing", 70, "Sending audio to remote speaker diarization server")
                _log_debug("STAGE:DIARIZE", f"Sending audio to remote diarization server at {remote_cfg.get('base_uri')}...")
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
                _log_debug("STAGE:DIARIZE", "Skipping diarization: missing HF_TOKEN")
            else:
                emit_progress("diarizing", 70, "Running pyannote/speaker-diarization-3.1")
                _log_debug("STAGE:DIARIZE", "Starting local pyannote speaker diarization...")
                turns, derr = _run_pyannote_diarization(
                    wav_path,
                    hf_token=hf_token,
                    device=dev,
                    num_speakers=job.get("num_speakers"),
                    min_speakers=job.get("min_speakers"),
                    max_speakers=job.get("max_speakers"),
                    stage_name="diarizing",
                    progress_base=70,
                    progress_span=23,
                )
                if derr:
                    warnings.append(derr)
                else:
                    speaker_turns = turns
                    diarized = bool(turns)

        t_diarize_end = time.time()
        diarize_dur = t_diarize_end - t_diarize_start
        rtf_diarize = diarize_dur / max(0.1, duration)

        speaker_stats: dict[str, dict[str, Any]] = {}
        for t in speaker_turns:
            spk = t.get("speaker") or "UNKNOWN"
            t_len = max(0.0, float(t.get("end", 0)) - float(t.get("start", 0)))
            if spk not in speaker_stats:
                speaker_stats[spk] = {
                    "speaker": spk,
                    "total_speaking_sec": 0.0,
                    "turns_count": 0,
                }
            speaker_stats[spk]["total_speaking_sec"] += t_len
            speaker_stats[spk]["turns_count"] += 1

        total_spoken_sec = sum(s["total_speaking_sec"] for s in speaker_stats.values())
        for spk, st in speaker_stats.items():
            st["total_speaking_sec"] = round(st["total_speaking_sec"], 3)
            st["total_speaking_clock"] = _fmt_clock_sec(st["total_speaking_sec"])
            st["speaking_ratio_percent"] = round((st["total_speaking_sec"] / max(0.001, total_spoken_sec)) * 100, 1)

        unique_speakers = sorted(speaker_stats.keys())
        remote_cfg = job.get("remote_diarize") or {}
        is_remote = bool(remote_cfg.get("enabled") and remote_cfg.get("base_uri"))

        pipeline_kwargs_record: dict[str, Any] = {}
        if job.get("num_speakers") and int(job["num_speakers"]) > 0:
            pipeline_kwargs_record["num_speakers"] = int(job["num_speakers"])
        if job.get("min_speakers") and int(job["min_speakers"]) > 0:
            pipeline_kwargs_record["min_speakers"] = int(job["min_speakers"])
        if job.get("max_speakers") and int(job["max_speakers"]) > 0:
            pipeline_kwargs_record["max_speakers"] = int(job["max_speakers"])

        _log_debug("STAGE:DIARIZE", f"Speaker diarization finished: {len(speaker_turns)} turns across {len(unique_speakers)} speakers, RTF: {rtf_diarize:.3f}", diarize_dur)
        timings["speaker_diarization"] = {
            "elapsed_sec": round(diarize_dur, 3),
            "real_time_factor": round(rtf_diarize, 4),
            "turns_count": len(speaker_turns),
            "speakers_count": len(unique_speakers),
            "diarized": diarized,
            "device": dev,
            "num_speakers": job.get("num_speakers"),
            "min_speakers": job.get("min_speakers"),
            "max_speakers": job.get("max_speakers"),
        }

        if log_dir and want_diarize:
            diarize_data = {
                "pipeline": {
                    "name": "pyannote/speaker-diarization-3.1",
                    "segmentation_model": "pyannote/segmentation-3.0",
                    "mode": "remote" if is_remote else "local",
                    "remote_endpoint": remote_cfg.get("base_uri") if is_remote else None,
                    "device": dev,
                    "hf_token_configured": bool(hf_token),
                },
                "input_audio": {
                    "path": wav_path,
                    "format": "wav",
                    "codec": "pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                    "channel_layout": "mono",
                    "duration_sec": round(duration, 3),
                    "duration_clock": _fmt_clock_sec(duration),
                    "in_memory_tensor_preloaded": True,
                },
                "parameters": {
                    "num_speakers": job.get("num_speakers"),
                    "min_speakers": job.get("min_speakers"),
                    "max_speakers": job.get("max_speakers"),
                    "pipeline_kwargs": pipeline_kwargs_record,
                },
                "results": {
                    "diarized": diarized,
                    "turns_count": len(speaker_turns),
                    "speakers_count": len(unique_speakers),
                    "speakers": unique_speakers,
                    "speaker_statistics": speaker_stats,
                    "total_spoken_sec": round(total_spoken_sec, 3),
                    "total_spoken_clock": _fmt_clock_sec(total_spoken_sec),
                    "turns": speaker_turns,
                    "warnings": warnings,
                },
                "timing": {
                    "elapsed_sec": round(diarize_dur, 3),
                    "real_time_factor": round(rtf_diarize, 4),
                    "real_time_factor_str": f"{rtf_diarize:.3f}x realtime",
                },
                "enabled": want_diarize,
                "diarized": diarized,
                "device": dev,
                "duration_sec": round(duration, 2),
                "elapsed_sec": round(diarize_dur, 3),
                "real_time_factor": round(rtf_diarize, 4),
                "turns_count": len(speaker_turns),
                "speakers_count": len(unique_speakers),
                "turns": speaker_turns,
                "warnings": warnings,
            }
            (log_dir / "diarization_output.json").write_text(json.dumps(diarize_data, ensure_ascii=False, indent=2), encoding="utf-8")
            timeline_header = [
                "=== THONG SO DAU VAO PHAN TACH GIONG NOI ===",
                f"Pipeline: pyannote/speaker-diarization-3.1 ({'remote' if is_remote else 'local'})",
                f"Device: {dev}",
                f"Audio duration: {_fmt_clock_sec(duration)} ({duration:.2f}s)",
                f"Parameters: num_speakers={job.get('num_speakers')}, min_speakers={job.get('min_speakers')}, max_speakers={job.get('max_speakers')}",
                f"Processing time: {diarize_dur:.3f}s (RTF: {rtf_diarize:.3f}x)",
                "",
                "=== TONG KET THEO NGUOI NOI ===",
            ]
            for spk in unique_speakers:
                s_info = speaker_stats[spk]
                timeline_header.append(
                    f"- {spk}: {s_info['turns_count']} turns, {s_info['total_speaking_clock']} ({s_info['speaking_ratio_percent']}%)"
                )
            timeline_header.append(f"Tong so nguoi noi: {len(unique_speakers)}")
            timeline_header.append("")
            timeline_header.append("=== DONG THOI GIAN CHI TIET (TIMELINE) ===")

            timeline_lines = timeline_header + [
                f"[{_fmt_clock_sec(t['start'])} -> {_fmt_clock_sec(t['end'])}] {t.get('speaker', '')}"
                for t in speaker_turns
            ]
            (log_dir / "diarization_timeline.txt").write_text("\n".join(timeline_lines), encoding="utf-8")
            _log_debug("ARTIFACT", f"Saved diarization_output.json and diarization_timeline.txt to {log_dir}")

        emit_progress("merging", 95, "Merging timestamps and speakers")
        t_merge_start = time.time()
        _log_debug("STAGE:MERGE", "Merging Whisper words with speaker turns...")
        merged_segments = assign_speakers_word_level(whisper_segments, speaker_turns)
        speakers = summarize_speakers(merged_segments)
        t_merge_end = time.time()
        merge_dur = t_merge_end - t_merge_start
        _log_debug("STAGE:MERGE", f"Merge completed ({len(merged_segments)} segments across {len(speakers)} speakers)", merge_dur)
        timings["speaker_merging"] = {
            "elapsed_sec": round(merge_dur, 3),
            "merged_segments_count": len(merged_segments),
            "speakers_count": len(speakers),
        }

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

        if log_dir:
            (log_dir / "merged_segments.json").write_text(json.dumps(merged_segments, ensure_ascii=False, indent=2), encoding="utf-8")
            (log_dir / "meeting_raw.txt").write_text("\n".join(raw_text_lines), encoding="utf-8")
            _log_debug("ARTIFACT", f"Saved merged_segments.json and meeting_raw.txt to {log_dir}")

        total_dur = time.time() - t_run_start
        _log_debug("JOB_DONE", f"Worker finished successfully in {total_dur:.2f}s")
        timings["total_processing"] = {
            "elapsed_sec": round(total_dur, 3),
            "overall_rtf": round(total_dur / max(0.1, duration), 4),
        }

        if log_dir:
            debug_timing = {
                "job_id": job.get("job_id") or "",
                "timestamp": datetime.now().astimezone().isoformat(),
                "audio_duration_sec": round(duration, 2),
                "total_elapsed_sec": round(total_dur, 3),
                "overall_rtf": round(total_dur / max(0.1, duration), 4),
                "stages": timings,
                "system": _get_system_debug_info(),
            }
            (log_dir / "debug_timing.json").write_text(json.dumps(debug_timing, ensure_ascii=False, indent=2), encoding="utf-8")
            _log_debug("ARTIFACT", f"Saved debug_timing.json to {log_dir}")

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
            "timings": timings,
        }
        if output_json:
            os.makedirs(os.path.dirname(output_json), exist_ok=True)
            Path(output_json).write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        emit_progress("done", 100, "Done")
        emit(result_payload)
        return 0

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
