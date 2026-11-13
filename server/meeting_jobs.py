"""Chunked audio assembly, single-concurrency meeting STT queue, timeout watchdog,
and 30-day unpinned audio cleanup.

Architecture notes:
  - Runs at most 1 heavy worker subprocess at a time (`_RUN_LOCK`) to stay safe
    within a single RTX A4500 16GB GPU or modest server RAM.
  - Supports two upload topologies cleanly:
      1) `raw_upload`: slices (`Blob.slice`) of a SINGLE audio container uploaded
         in parts `chunk_00000.part`, `chunk_00001.part`, ... assembled sequentially.
      2) `multi_segment`: multiple standalone recorded clips (e.g. separate
         MediaRecorder clips from Pause/Resume) concatenated via ffmpeg
         `filter_complex concat` inside the worker.
  - Crashed/restarted server jobs stuck in non-terminal states are marked
    `failed` on startup (`recover_stuck_jobs`).
  - Original audio files live in `STATE_DIR/meeting-audio/` so users can play
    them back beside the transcript; unpinned audio older than `keep_audio_days`
    (default 30) is pruned during the server's periodic media GC pass.

Note: Do NOT use the em dash character (U+2014) anywhere in this file.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import brain_ident
import config as cfgmod
import localefmt
import meeting_stt
import transcript_fix
import voice_records
import winproc

MAX_CHUNK_BYTES = 32 * 1024 * 1024  # 32 MB ceiling per HTTP chunk
MIN_WORKER_TIMEOUT_SEC = 1800.0     # 30 minutes floor
MAX_WORKER_TIMEOUT_SEC = 10800.0    # 3 hours ceiling
UPLOAD_STAGING_TTL_SEC = 3 * 86400  # clean abandoned partial upload dirs after 3 days

_ALLOWED_EXTS = {".webm", ".mp3", ".wav", ".m4a", ".ogg", ".opus", ".mp4", ".aac", ".flac"}

_QUEUE_LOCK: asyncio.Lock | None = None
_ACTIVE_TASKS: dict[str, asyncio.Task] = {}


def _get_lock() -> asyncio.Lock:
    global _QUEUE_LOCK
    if _QUEUE_LOCK is None:
        _QUEUE_LOCK = asyncio.Lock()
    return _QUEUE_LOCK


def sanitize_ext(ext_or_name: str) -> str:
    raw = str(ext_or_name or "").strip().lower()
    if "." in raw:
        raw = "." + raw.rsplit(".", 1)[-1]
    elif raw and not raw.startswith("."):
        raw = "." + raw
    raw = re.sub(r"[^.a-z0-9]", "", raw)
    return raw if raw in _ALLOWED_EXTS else ".webm"


def job_upload_dir(job_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(job_id or ""))
    return meeting_stt.uploads_dir() / safe_id


def chunk_file_path(job_id: str, chunk_index: int) -> Path:
    return job_upload_dir(job_id) / f"chunk_{int(chunk_index):05d}.part"


def list_chunks(job_id: str) -> list[Path]:
    d = job_upload_dir(job_id)
    if not d.is_dir():
        return []
    files = []
    for p in d.iterdir():
        if p.is_file() and p.name.startswith("chunk_") and p.name.endswith(".part"):
            files.append(p)
    files.sort(key=lambda x: x.name)
    return files


async def save_chunk_stream(job_id: str, chunk_index: int, upload_file) -> int:
    """Save one upload slice atomically so retries overwrite cleanly without duplication."""
    if chunk_index < 0 or chunk_index >= 10000:
        raise ValueError("Invalid chunk_index")
    dest = chunk_file_path(job_id, chunk_index)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.tmp")
    size = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                buf = await upload_file.read(1024 * 1024)
                if not buf:
                    break
                size += len(buf)
                if size > MAX_CHUNK_BYTES:
                    raise ValueError(f"Chunk exceeds maximum allowed size ({MAX_CHUNK_BYTES // (1024 * 1024)} MB)")
                f.write(buf)
        if size == 0:
            raise ValueError("Empty audio chunk")
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    chunks = list_chunks(job_id)
    voice_records.get_store().update_job(job_id, received_chunks=len(chunks))
    return len(chunks)


def save_chunk_bytes(job_id: str, chunk_index: int, data: bytes) -> int:
    if chunk_index < 0 or chunk_index >= 10000:
        raise ValueError("Invalid chunk_index")
    if not data:
        raise ValueError("Empty audio chunk")
    if len(data) > MAX_CHUNK_BYTES:
        raise ValueError("Chunk exceeds maximum allowed size")
    dest = chunk_file_path(job_id, chunk_index)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    chunks = list_chunks(job_id)
    voice_records.get_store().update_job(job_id, received_chunks=len(chunks))
    return len(chunks)


def assemble_uploaded_audio(job: dict[str, Any]) -> tuple[list[str], str]:
    """Prepare audio input path(s) and persistent `meeting-audio/` destination path.

    For `raw_upload` (slices of ONE file/recording): binary-concats chunk slices
    into `<STATE_DIR>/meeting-audio/<job_id><ext>` cleanly in O(file_size) I/O.
    For `multi_segment` (separate clips from Pause/Resume): keeps individual clip
    paths so ffmpeg concat inside the worker safely re-encodes across headers,
    writing the unified playback file to `<STATE_DIR>/meeting-audio/<job_id>.m4a`.
    """
    jid = job["id"]
    chunks = list_chunks(jid)
    if not chunks:
        prev = str(job.get("audio_path") or "").strip()
        if prev and Path(prev).is_file():
            return [prev], prev
        raise ValueError(localefmt.chu("Chưa nhận được đoạn audio nào.", "No audio chunks received."))
    expected = int(job.get("total_chunks") or 0)
    if expected > 0:
        missing = [i for i in range(expected) if not chunk_file_path(jid, i).is_file()]
        if missing:
            raise ValueError(
                localefmt.chu(
                    f"Thiếu {len(missing)} phần upload (đầu tiên: #{missing[0]}).",
                    f"Missing {len(missing)} uploaded chunk(s) (first: #{missing[0]}).",
                )
            )
        chunks = [chunk_file_path(jid, i) for i in range(expected)]

    aud_dir = meeting_stt.audio_dir()
    aud_dir.mkdir(parents=True, exist_ok=True)
    mode = str(job.get("upload_mode") or "raw_upload")
    ext = sanitize_ext(job.get("file_ext") or ".webm")

    if mode == "multi_segment" and len(chunks) > 1:
        dest = aud_dir / f"{jid}.m4a"
        return [str(p) for p in chunks], str(dest)

    dest = aud_dir / f"{jid}{ext}"
    tmp_dest = aud_dir / f"{jid}{ext}.tmp"
    with open(tmp_dest, "wb") as out_f:
        for ch in chunks:
            with open(ch, "rb") as in_f:
                shutil.copyfileobj(in_f, out_f, 1024 * 1024)
    os.replace(tmp_dest, dest)
    return [str(dest)], str(dest)


def _estimate_worker_timeout(input_paths: list[str]) -> float:
    total_bytes = 0
    for p in input_paths:
        try:
            total_bytes += Path(p).stat().st_size
        except OSError:
            pass
    mb = total_bytes / (1024 * 1024)
    est = MIN_WORKER_TIMEOUT_SEC + mb * 6.0
    return max(MIN_WORKER_TIMEOUT_SEC, min(MAX_WORKER_TIMEOUT_SEC, est))


async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


async def _execute_job(
    job_id: str,
    *,
    brain_root_fn: Callable[[str], Path | str],
    files_root_fn: Callable[[str], Path | str],
    files_rel_fn: Callable[[Path | str, Path | str], str],
) -> None:
    store = voice_records.get_store()
    job = store.get_job(job_id)
    if not job:
        return

    async with _get_lock():
        job = store.get_job(job_id)
        if not job or job["status"] in ("done", "failed"):
            return
        try:
            input_paths, final_audio_path = await asyncio.to_thread(assemble_uploaded_audio, job)
            store.update_job(job_id, status="converting", progress=3, audio_path=final_audio_path, stage_detail="Preparing audio")

            cfg = cfgmod.read_settings()
            mc = meeting_stt.get_config(cfg)
            tok, _ = meeting_stt.resolve_hf_token(cfg)
            diarize_req = bool(job.get("diarize"))
            is_remote_diarize = (
                mc.get("remote_mode") == "client"
                and bool(mc.get("client_base_uri"))
                and bool(mc.get("client_api_key"))
            )
            need_local_diarize = diarize_req and bool(tok) and not is_remote_diarize
            py_exe, py_path = meeting_stt.resolve_worker_python(need_diarize=need_local_diarize)
            if not py_exe:
                raise RuntimeError(
                    localefmt.chu(
                        "Chưa cài thư viện Whisper/Meeting STT trên máy. Hãy bấm Cài đặt ở trang Ghi âm.",
                        "Whisper / Meeting STT libraries are not installed yet. Click Install on the Record page.",
                    )
                )

            up_dir = job_upload_dir(job_id)
            up_dir.mkdir(parents=True, exist_ok=True)
            job_json = up_dir / "worker_job.json"
            out_json = up_dir / "worker_out.json"
            worker_payload = {
                "input_paths": input_paths,
                "combined_audio_out": final_audio_path,
                "model": job.get("model") or mc["whisper_model"],
                "model_dir": str(meeting_stt.models_dir()),
                "hf_home": str(meeting_stt.hf_home()),
                "device": job.get("device") or mc["device"],
                "language": job.get("language") or mc["language"],
                "diarize": diarize_req,
                "remote_diarize": {
                    "enabled": is_remote_diarize,
                    "base_uri": mc.get("client_base_uri") or "",
                    "api_key": mc.get("client_api_key") or "",
                },
                "num_speakers": int(job.get("num_speakers") or 0) or None,
                "min_speakers": int(job.get("min_speakers") or 0) or None,
                "max_speakers": int(job.get("max_speakers") or 0) or None,
                "output_json": str(out_json),
            }
            job_json.write_text(json.dumps(worker_payload, ensure_ascii=False), encoding="utf-8")

            env = meeting_stt.worker_env(cfg, extra_pythonpath=py_path)
            timeout_sec = _estimate_worker_timeout(input_paths)
            proc = await asyncio.create_subprocess_exec(
                py_exe,
                "-u",
                str(meeting_stt.worker_script()),
                "--run",
                "--job",
                str(job_json),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=str(meeting_stt.meeting_ear_root() if meeting_stt.meeting_ear_root().exists() else up_dir),
                **winproc.kwargs_no_window(),
            )

            last_result: dict[str, Any] | None = None
            tail_lines: list[str] = []

            async def _read_stream() -> None:
                nonlocal last_result
                assert proc.stdout is not None
                while True:
                    raw_line = await proc.stdout.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    tail_lines.append(line)
                    if len(tail_lines) > 25:
                        tail_lines.pop(0)
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(evt, dict):
                        continue
                    etype = evt.get("type")
                    if etype == "progress":
                        st = str(evt.get("stage") or "transcribing")
                        if st not in ("converting", "transcribing", "diarizing", "merging", "done"):
                            st = "transcribing"
                        store.update_job(
                            job_id,
                            status=st if st != "done" else "merging",
                            progress=int(evt.get("progress") or 0),
                            stage_detail=str(evt.get("detail") or "")[:160],
                        )
                    elif etype == "result":
                        last_result = evt

            try:
                await asyncio.wait_for(asyncio.gather(_read_stream(), proc.wait()), timeout=timeout_sec)
            except TimeoutError:
                await _terminate_proc(proc)
                raise RuntimeError(
                    localefmt.chu(
                        f"Tiến trình xử lý âm thanh vượt quá giới hạn ({int(timeout_sec // 60)} phút) nên đã dừng.",
                        f"Meeting audio worker exceeded timeout ({int(timeout_sec // 60)} minutes) and was stopped.",
                    )
                )

            if last_result is None and out_json.is_file():
                try:
                    last_result = json.loads(out_json.read_text(encoding="utf-8"))
                except Exception:
                    last_result = None

            if proc.returncode != 0 or not last_result or not last_result.get("ok"):
                err = (last_result or {}).get("error") or (tail_lines[-1] if tail_lines else f"Worker exited with code {proc.returncode}")
                raise RuntimeError(str(err)[:500])

            segments = last_result.get("segments") or []
            raw_text = str(last_result.get("raw_text") or "").strip()
            duration_sec = float(last_result.get("duration") or 0.0)
            warnings = [str(w) for w in (last_result.get("warnings") or []) if w]
            model_used = str(last_result.get("model_used") or worker_payload["model"])
            device_used = str(last_result.get("device_used") or "")

            now_dt = localefmt.now()
            broot = Path(brain_root_fn(job["brain"])).resolve()
            vr_dir = broot / "VoiceRecord"
            vr_dir.mkdir(parents=True, exist_ok=True)

            existing_rec = None
            if job.get("record_id"):
                existing_rec = store.get(job["record_id"])

            if existing_rec:
                brain_rel = str(existing_rec.get("file_path") or "").strip()
                if brain_rel:
                    target_file = (broot / brain_rel).resolve()
                else:
                    fname = voice_records.make_record_filename(now_dt, vr_dir)
                    target_file = vr_dir / fname
                    brain_rel = f"VoiceRecord/{fname}"
                try:
                    ceil_rel = files_rel_fn(files_root_fn(job["brain"]), target_file)
                except Exception:
                    ceil_rel = brain_rel

                title = job.get("title") or ""
                if not title and target_file.is_file():
                    try:
                        for line in target_file.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if line.startswith("# "):
                                title = line[2:].strip()
                                break
                    except Exception:
                        pass

                md_text = voice_records.render_meeting_markdown(
                    created_iso=existing_rec.get("created_iso") or now_dt.isoformat(),
                    duration_sec=duration_sec or float(existing_rec.get("duration_sec") or 0.0),
                    model_used=model_used or str(existing_rec.get("model_used") or ""),
                    segments=segments,
                    speaker_map=existing_rec.get("speaker_map") or {},
                    normalized_segments=existing_rec.get("normalized_segments") or [],
                    title=title,
                )
                await asyncio.to_thread(_atomic_write_text, target_file, md_text)

                store.update_diarization(
                    existing_rec["id"],
                    segments=segments,
                    raw_text=raw_text or md_text,
                    warnings=warnings,
                    rendered_text=md_text,
                    duration_sec=duration_sec or float(existing_rec.get("duration_sec") or 0.0),
                    model_used=model_used or str(existing_rec.get("model_used") or ""),
                    device_used=device_used or str(existing_rec.get("device_used") or ""),
                )
                store.update_job(
                    job_id,
                    status="done",
                    progress=100,
                    stage_detail="Completed",
                    audio_path=final_audio_path if Path(final_audio_path).is_file() else "",
                    duration_sec=duration_sec or float(existing_rec.get("duration_sec") or 0.0),
                    record_id=existing_rec["id"],
                    warnings=warnings,
                    error="",
                )
                return

            fname = voice_records.make_record_filename(now_dt, vr_dir)
            target_file = vr_dir / fname
            brain_rel = f"VoiceRecord/{fname}"
            try:
                ceil_rel = files_rel_fn(files_root_fn(job["brain"]), target_file)
            except ValueError:
                ceil_rel = brain_rel

            md_text = voice_records.render_meeting_markdown(
                created_iso=now_dt.isoformat(),
                duration_sec=duration_sec,
                model_used=model_used,
                segments=segments,
                speaker_map={},
                title=job.get("title") or "",
            )
            await asyncio.to_thread(_atomic_write_text, target_file, md_text)

            rec = await asyncio.to_thread(
                lambda: store.create_raw(
                    brain=job["brain"],
                    brain_path=brain_ident.canonical(str(broot)),
                    raw_text=raw_text or md_text,
                    file_path=brain_rel,
                    ceil_path=ceil_rel,
                    created_at=now_dt.timestamp(),
                    created_iso=now_dt.isoformat(),
                    mode="meeting",
                    job_id=job_id,
                    duration_sec=duration_sec,
                    audio_path=final_audio_path if Path(final_audio_path).is_file() else "",
                    pinned=bool(job.get("pinned")),
                    segments=segments,
                    warnings=warnings,
                    model_used=model_used,
                    device_used=device_used,
                )
            )
            # Also store initial rendered md into normalized_text so list views/fallback always have preview,
            # while keeping status="raw_saved" until AI grammar normalization runs.
            store.mark_normalized(
                rec["id"],
                normalized_text=md_text,
                source="meeting_stt",
                file_path=brain_rel,
                ceil_path=ceil_rel,
                status="raw_saved",
            )

            store.update_job(
                job_id,
                status="done",
                progress=100,
                stage_detail="Completed",
                audio_path=final_audio_path if Path(final_audio_path).is_file() else "",
                duration_sec=duration_sec,
                record_id=rec["id"],
                warnings=warnings,
                error="",
            )
        except asyncio.CancelledError:
            store.update_job(job_id, status="failed", error="Cancelled")
            raise
        except Exception as e:
            store.update_job(job_id, status="failed", error=f"{type(e).__name__}: {e}"[:500])
        finally:
            try:
                shutil.rmtree(job_upload_dir(job_id), ignore_errors=True)
            except Exception:
                pass


def enqueue_job(
    job_id: str,
    *,
    brain_root_fn: Callable[[str], Path | str],
    files_root_fn: Callable[[str], Path | str],
    files_rel_fn: Callable[[Path | str, Path | str], str],
) -> dict[str, Any] | None:
    store = voice_records.get_store()
    job = store.update_job(job_id, status="queued", progress=1, stage_detail="Queued", error="")
    if not job:
        return None
    if job_id not in _ACTIVE_TASKS:
        task = asyncio.create_task(
            _execute_job(
                job_id,
                brain_root_fn=brain_root_fn,
                files_root_fn=files_root_fn,
                files_rel_fn=files_rel_fn,
            )
        )
        _ACTIVE_TASKS[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: _ACTIVE_TASKS.pop(jid, None))
    return job


def recover_stuck_jobs() -> int:
    """Mark jobs interrupted in-flight during a previous server run as `failed`."""
    try:
        return voice_records.get_store().fail_stuck_jobs(
            localefmt.chu(
                "Server khởi động lại khi phiên xử lý đang chạy dở.",
                "Server restarted while the transcription job was in progress.",
            )
        )
    except Exception:
        return 0


def cleanup_expired_audio(now_ts: float | None = None) -> dict[str, int]:
    """Delete unpinned audio files older than `keep_audio_days` and stale upload dirs."""
    now = float(now_ts if now_ts is not None else time.time())
    cfg = cfgmod.read_settings()
    days = int(meeting_stt.get_config(cfg)["keep_audio_days"])
    cutoff = now - (days * 86400)
    store = voice_records.get_store()

    removed_files = 0
    freed_bytes = 0
    for rec in store.expired_unpinned_audio_records(cutoff):
        ap = str(rec.get("audio_path") or "")
        if ap:
            p = Path(ap)
            if p.is_file():
                try:
                    freed_bytes += p.stat().st_size
                    p.unlink()
                    removed_files += 1
                except OSError:
                    continue
        store.clear_audio_path(rec["id"])

    # Sweep abandoned partial chunk upload directories older than 3 days
    up_root = meeting_stt.uploads_dir()
    if up_root.is_dir():
        for d in up_root.iterdir():
            if not d.is_dir():
                continue
            try:
                if (now - d.stat().st_mtime) > UPLOAD_STAGING_TTL_SEC:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    return {"files": removed_files, "bytes": freed_bytes}
