"""Luu ban tho ghi am (audit SQLite trong STATE_DIR) va ten tep chuan hoa trong brain.

Thiet ke:
  - Du lieu audit (thoi gian, brain, raw_text, segments_json goc, lien ket file
    chuan hoa) nam trong SQLite `<STATE_DIR>/voice_records.sqlite3` (khong nam
    trong brain) de tach biet ban tho audit voi file Markdown nguoi dung sua.
  - Ban tho va segments_json goc la bat bien; moi lan map ten nguoi noi
    (speaker_map) hay chuan hoa van pham chi cap nhat ban hien thi / render
    lai file `.md` trong `<brain>/VoiceRecord/YYYY.MM.DD-HH.MM.SS.md`.
  - Ho tro bang `meeting_jobs` quan ly phien ghi am cuoc hop dai (chunk upload,
    tien trinh worker GPU/CPU, giu/don file audio sau 30 ngay).

Ghi chu: KHONG dung ky tu em dash (U+2014) o bat ky dau trong file nay.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import brain_ident
import config as cfgmod

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_records (
    id                      TEXT PRIMARY KEY,
    created_at              REAL NOT NULL,
    created_iso             TEXT NOT NULL,
    brain                   TEXT NOT NULL,
    brain_path              TEXT NOT NULL DEFAULT '',
    raw_text                TEXT NOT NULL,
    normalized_text         TEXT NOT NULL DEFAULT '',
    file_path               TEXT NOT NULL DEFAULT '',
    ceil_path               TEXT NOT NULL DEFAULT '',
    status                  TEXT NOT NULL DEFAULT 'raw_saved',
    source                  TEXT NOT NULL DEFAULT '',
    updated_at              REAL NOT NULL,
    job_id                  TEXT NOT NULL DEFAULT '',
    mode                    TEXT NOT NULL DEFAULT 'dictate',
    duration_sec            REAL NOT NULL DEFAULT 0,
    audio_path              TEXT NOT NULL DEFAULT '',
    pinned                  INTEGER NOT NULL DEFAULT 0,
    segments_json           TEXT NOT NULL DEFAULT '[]',
    normalized_segments_json TEXT NOT NULL DEFAULT '[]',
    speaker_map_json        TEXT NOT NULL DEFAULT '{}',
    warnings_json           TEXT NOT NULL DEFAULT '[]',
    model_used              TEXT NOT NULL DEFAULT '',
    device_used             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_vr_brain_created ON voice_records(brain_path, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_vr_created ON voice_records(created_at DESC);

CREATE TABLE IF NOT EXISTS meeting_jobs (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    created_iso     TEXT NOT NULL,
    updated_at      REAL NOT NULL,
    brain           TEXT NOT NULL,
    brain_path      TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'uploading',
    progress        INTEGER NOT NULL DEFAULT 0,
    stage_detail    TEXT NOT NULL DEFAULT '',
    upload_mode     TEXT NOT NULL DEFAULT 'raw_upload',
    file_ext        TEXT NOT NULL DEFAULT '.webm',
    model           TEXT NOT NULL DEFAULT 'large-v3-turbo',
    device          TEXT NOT NULL DEFAULT 'auto',
    language        TEXT NOT NULL DEFAULT 'vi',
    diarize         INTEGER NOT NULL DEFAULT 1,
    num_speakers    INTEGER NOT NULL DEFAULT 0,
    min_speakers    INTEGER NOT NULL DEFAULT 0,
    max_speakers    INTEGER NOT NULL DEFAULT 0,
    total_chunks    INTEGER NOT NULL DEFAULT 0,
    received_chunks INTEGER NOT NULL DEFAULT 0,
    audio_path      TEXT NOT NULL DEFAULT '',
    duration_sec    REAL NOT NULL DEFAULT 0,
    pinned          INTEGER NOT NULL DEFAULT 0,
    record_id       TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    warnings_json   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_mj_created ON meeting_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_mj_brain_created ON meeting_jobs(brain_path, created_at DESC);
"""

_VR_EXTRA_COLS = (
    ("job_id", "TEXT NOT NULL DEFAULT ''"),
    ("mode", "TEXT NOT NULL DEFAULT 'dictate'"),
    ("duration_sec", "REAL NOT NULL DEFAULT 0"),
    ("audio_path", "TEXT NOT NULL DEFAULT ''"),
    ("pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("segments_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("normalized_segments_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("speaker_map_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("warnings_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("model_used", "TEXT NOT NULL DEFAULT ''"),
    ("device_used", "TEXT NOT NULL DEFAULT ''"),
)


def _db_path() -> Path:
    return Path(cfgmod.STATE_DIR) / "voice_records.sqlite3"


def make_record_filename(dt: datetime, vr_dir: Path | None = None) -> str:
    """Ten tep theo dinh dang YYYY.MM.DD-HH.MM.SS.md (tranh ghi de neu goi trung giay)."""
    stem = dt.strftime("%Y.%m.%d-%H.%M.%S")
    name = f"{stem}.md"
    if vr_dir is None or not (vr_dir / name).exists():
        return name
    idx = 1
    while (vr_dir / f"{stem}-{idx}.md").exists():
        idx += 1
    return f"{stem}-{idx}.md"


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (list, dict)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return default
    try:
        val = json.loads(raw)
        return val if isinstance(val, type(default)) else default
    except Exception:
        return default


def format_ts(sec: float, force_hours: bool = False) -> str:
    total = max(0, int(float(sec or 0)))
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    if hh > 0 or force_hours:
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    return f"{mm:02d}:{ss:02d}"


def format_srt_ts(sec: float) -> str:
    ms_total = max(0, int(round(float(sec or 0) * 1000)))
    hh, rem = divmod(ms_total, 3600000)
    mm, rem = divmod(rem, 60000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def format_vtt_ts(sec: float) -> str:
    ms_total = max(0, int(round(float(sec or 0) * 1000)))
    hh, rem = divmod(ms_total, 3600000)
    mm, rem = divmod(rem, 60000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"


def resolve_speaker_name(speaker_id: str, speaker_map: dict[str, str] | None) -> str:
    sid = str(speaker_id or "").strip()
    if not sid:
        return ""
    if isinstance(speaker_map, dict):
        mapped = str(speaker_map.get(sid) or "").strip()
        if mapped:
            return mapped
    return sid


def effective_segments(
    segments: list[dict[str, Any]],
    speaker_map: dict[str, str] | None = None,
    normalized_segments: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Combine immutable raw segments with optional normalized text and speaker mapping."""
    base = list(segments or [])
    norm_by_idx: dict[int, str] = {}
    if isinstance(normalized_segments, list):
        for i, ns in enumerate(normalized_segments):
            if isinstance(ns, dict) and str(ns.get("text") or "").strip():
                idx = int(ns.get("idx", i))
                norm_by_idx[idx] = str(ns.get("text") or "").strip()

    out: list[dict[str, Any]] = []
    for i, seg in enumerate(base):
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("speaker") or "").strip()
        disp = resolve_speaker_name(sid, speaker_map)
        txt = norm_by_idx.get(i) or str(seg.get("text") or "").strip()
        out.append({
            "idx": i,
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "speaker_id": sid,
            "speaker": disp,
            "text": txt,
        })
    return out


def summarize_speakers(
    segments: list[dict[str, Any]],
    speaker_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("speaker_id") or seg.get("speaker") or "").strip()
        if not sid:
            continue
        dur = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
        if sid not in stats:
            stats[sid] = {
                "id": sid,
                "name": resolve_speaker_name(sid, speaker_map),
                "duration": 0.0,
                "turns": 0,
            }
            order.append(sid)
        stats[sid]["duration"] = round(stats[sid]["duration"] + dur, 2)
        stats[sid]["turns"] += 1
    return [stats[k] for k in order]


def render_meeting_markdown(
    *,
    created_iso: str,
    duration_sec: float,
    model_used: str,
    segments: list[dict[str, Any]],
    speaker_map: dict[str, str] | None = None,
    normalized_segments: list[dict[str, Any]] | None = None,
    title: str = "",
) -> str:
    """Render VoiceRecord Markdown with YAML frontmatter and `[MM:SS - Speaker] text` turns."""
    eff = effective_segments(segments, speaker_map, normalized_segments=normalized_segments)
    spk_summary = summarize_speakers(segments, speaker_map)
    has_hours = float(duration_sec or 0) >= 3600 or any(float(s.get("start", 0)) >= 3600 for s in eff)
    diarized = bool(spk_summary)

    lines = [
        "---",
        "type: voice-record",
        "mode: meeting",
        f"created_at: {json.dumps(str(created_iso or ''))}",
        f"duration_sec: {round(float(duration_sec or 0), 2)}",
        f"model: {json.dumps(str(model_used or ''))}",
        f"diarized: {'true' if diarized else 'false'}",
    ]
    if spk_summary:
        lines.append("speakers:")
        for sp in spk_summary:
            lines.append(f"  {sp['id']}: {json.dumps(sp['name'], ensure_ascii=False)}")
    lines.append("---")
    lines.append("")
    heading = str(title or "").strip() or "Bien ban ghi am cuoc hop"
    lines.append(f"# {heading}")
    lines.append("")
    for s in eff:
        ts = format_ts(s["start"], force_hours=has_hours)
        if s["speaker"]:
            lines.append(f"[{ts} - {s['speaker']}] {s['text']}")
        else:
            lines.append(f"[{ts}] {s['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_meeting_transcript(
    *,
    segments: list[dict[str, Any]],
    speaker_map: dict[str, str] | None,
    normalized_segments: list[dict[str, Any]] | None,
    fmt: str,
    duration_sec: float = 0.0,
) -> tuple[str, str, str]:
    """Return `(content, media_type, extension)` for `srt | vtt | txt | json`."""
    kind = str(fmt or "txt").strip().lower()
    eff = effective_segments(segments, speaker_map, normalized_segments=normalized_segments)
    has_hours = float(duration_sec or 0) >= 3600 or any(float(s.get("start", 0)) >= 3600 for s in eff)

    if kind == "srt":
        out: list[str] = []
        for idx, s in enumerate(eff, 1):
            t0 = format_srt_ts(s["start"])
            t1 = format_srt_ts(max(s["start"] + 0.2, s["end"]))
            prefix = f"[{s['speaker']}] " if s["speaker"] else ""
            out.append(f"{idx}\n{t0} --> {t1}\n{prefix}{s['text']}\n")
        return "\n".join(out), "application/x-subrip; charset=utf-8", "srt"

    if kind == "vtt":
        out = ["WEBVTT", ""]
        for s in eff:
            t0 = format_vtt_ts(s["start"])
            t1 = format_vtt_ts(max(s["start"] + 0.2, s["end"]))
            if s["speaker"]:
                out.append(f"{t0} --> {t1}\n<v {s['speaker']}>{s['text']}\n")
            else:
                out.append(f"{t0} --> {t1}\n{s['text']}\n")
        return "\n".join(out), "text/vtt; charset=utf-8", "vtt"

    if kind == "json":
        payload = {
            "duration_sec": round(float(duration_sec or 0), 3),
            "speaker_map": speaker_map or {},
            "speakers": summarize_speakers(segments, speaker_map),
            "segments": eff,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), "application/json; charset=utf-8", "json"

    lines = []
    for s in eff:
        ts = format_ts(s["start"], force_hours=has_hours)
        if s["speaker"]:
            lines.append(f"[{ts} - {s['speaker']}] {s['text']}")
        else:
            lines.append(f"[{ts}] {s['text']}")
    return "\n".join(lines) + ("\n" if lines else ""), "text/plain; charset=utf-8", "txt"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = set(row.keys())
    segs = _loads(row["segments_json"], []) if "segments_json" in keys else []
    n_segs = _loads(row["normalized_segments_json"], []) if "normalized_segments_json" in keys else []
    smap = _loads(row["speaker_map_json"], {}) if "speaker_map_json" in keys else {}
    warns = _loads(row["warnings_json"], []) if "warnings_json" in keys else []
    audio_path = str(row["audio_path"]) if "audio_path" in keys else ""
    audio_exists = bool(audio_path and Path(audio_path).is_file())
    return {
        "id": row["id"],
        "created_at": float(row["created_at"]),
        "created_iso": row["created_iso"],
        "brain": row["brain"],
        "brain_path": row["brain_path"],
        "raw_text": row["raw_text"],
        "normalized_text": row["normalized_text"],
        "file_path": row["file_path"],
        "ceil_path": row["ceil_path"],
        "status": row["status"],
        "source": row["source"],
        "updated_at": float(row["updated_at"]),
        "job_id": row["job_id"] if "job_id" in keys else "",
        "mode": row["mode"] if "mode" in keys else "dictate",
        "duration_sec": float(row["duration_sec"]) if "duration_sec" in keys else 0.0,
        "has_audio": audio_exists,
        "audio_path": audio_path,
        "pinned": bool(row["pinned"]) if "pinned" in keys else False,
        "segments": segs,
        "normalized_segments": n_segs,
        "speaker_map": smap,
        "speakers": summarize_speakers(segs, smap),
        "warnings": warns,
        "model_used": row["model_used"] if "model_used" in keys else "",
        "device_used": row["device_used"] if "device_used" in keys else "",
    }


def public_record(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(rec, dict):
        return rec
    out = dict(rec)
    out.pop("audio_path", None)
    return out


def public_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return job
    out = dict(job)
    ap = str(out.pop("audio_path", "") or "")
    out["has_audio"] = bool(ap and Path(ap).is_file())
    return out


def _job_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": float(row["created_at"]),
        "created_iso": row["created_iso"],
        "updated_at": float(row["updated_at"]),
        "brain": row["brain"],
        "brain_path": row["brain_path"],
        "title": row["title"],
        "status": row["status"],
        "progress": int(row["progress"]),
        "stage_detail": row["stage_detail"],
        "upload_mode": row["upload_mode"],
        "file_ext": row["file_ext"],
        "model": row["model"],
        "device": row["device"],
        "language": row["language"],
        "diarize": bool(row["diarize"]),
        "num_speakers": int(row["num_speakers"]),
        "min_speakers": int(row["min_speakers"]),
        "max_speakers": int(row["max_speakers"]),
        "total_chunks": int(row["total_chunks"]),
        "received_chunks": int(row["received_chunks"]),
        "audio_path": row["audio_path"],
        "duration_sec": float(row["duration_sec"]),
        "pinned": bool(row["pinned"]),
        "record_id": row["record_id"],
        "error": row["error"],
        "warnings": _loads(row["warnings_json"], []),
    }


class VoiceRecordStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._ensure_columns()

    def _ensure_columns(self) -> None:
        existing = {
            str(r["name"])
            for r in self._db.execute("PRAGMA table_info(voice_records)").fetchall()
        }
        for col, ddl in _VR_EXTRA_COLS:
            if col not in existing:
                self._db.execute(f"ALTER TABLE voice_records ADD COLUMN {col} {ddl}")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    def create_raw(
        self,
        *,
        brain: str,
        brain_path: str,
        raw_text: str,
        file_path: str,
        ceil_path: str,
        created_at: float | None = None,
        created_iso: str = "",
        mode: str = "dictate",
        job_id: str = "",
        duration_sec: float = 0.0,
        audio_path: str = "",
        pinned: bool = False,
        segments: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
        model_used: str = "",
        device_used: str = "",
    ) -> dict[str, Any]:
        rid = uuid.uuid4().hex
        now_ts = float(created_at if created_at is not None else time.time())
        with self._lock:
            self._db.execute(
                "INSERT INTO voice_records "
                "(id, created_at, created_iso, brain, brain_path, raw_text, normalized_text, "
                "file_path, ceil_path, status, source, updated_at, job_id, mode, duration_sec, "
                "audio_path, pinned, segments_json, normalized_segments_json, speaker_map_json, "
                "warnings_json, model_used, device_used) "
                "VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 'raw_saved', '', ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?, ?, ?)",
                (
                    rid,
                    now_ts,
                    str(created_iso or ""),
                    str(brain or ""),
                    str(brain_path or ""),
                    str(raw_text or ""),
                    str(file_path or ""),
                    str(ceil_path or ""),
                    now_ts,
                    str(job_id or ""),
                    str(mode or "dictate"),
                    float(duration_sec or 0.0),
                    str(audio_path or ""),
                    1 if pinned else 0,
                    json.dumps(segments or [], ensure_ascii=False),
                    json.dumps(warnings or [], ensure_ascii=False),
                    str(model_used or ""),
                    str(device_used or ""),
                ),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (rid,)).fetchone()
        return _row_to_dict(row) or {}

    def mark_normalized(
        self,
        record_id: str,
        *,
        normalized_text: str,
        source: str = "",
        file_path: str = "",
        ceil_path: str = "",
        status: str = "normalized",
        normalized_segments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        now_ts = time.time()
        with self._lock:
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                return None
            fp = str(file_path or row["file_path"] or "")
            cp = str(ceil_path or row["ceil_path"] or "")
            nseg_json = (
                json.dumps(normalized_segments, ensure_ascii=False)
                if normalized_segments is not None
                else row["normalized_segments_json"]
            )
            self._db.execute(
                "UPDATE voice_records SET normalized_text=?, normalized_segments_json=?, source=?, "
                "file_path=?, ceil_path=?, status=?, updated_at=? WHERE id=?",
                (str(normalized_text or ""), nseg_json, str(source or ""), fp, cp, str(status or "normalized"), now_ts, record_id),
            )
            self._db.commit()
            updated = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(updated)

    def update_speaker_map(
        self,
        record_id: str,
        speaker_map: dict[str, str],
        *,
        rendered_text: str | None = None,
    ) -> dict[str, Any] | None:
        clean_map: dict[str, str] = {}
        for k, v in (speaker_map or {}).items():
            sk = str(k or "").strip()
            sv = str(v or "").strip()
            if sk and sv and sv != sk:
                clean_map[sk] = sv[:80]
        now_ts = time.time()
        with self._lock:
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                return None
            if rendered_text is not None:
                self._db.execute(
                    "UPDATE voice_records SET speaker_map_json=?, normalized_text=?, updated_at=? WHERE id=?",
                    (json.dumps(clean_map, ensure_ascii=False), str(rendered_text), now_ts, record_id),
                )
            else:
                self._db.execute(
                    "UPDATE voice_records SET speaker_map_json=?, updated_at=? WHERE id=?",
                    (json.dumps(clean_map, ensure_ascii=False), now_ts, record_id),
                )
            self._db.commit()
            updated = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(updated)

    def update_diarization(
        self,
        record_id: str,
        *,
        segments: list[dict[str, Any]],
        raw_text: str,
        warnings: list[str] | None = None,
        rendered_text: str | None = None,
        duration_sec: float | None = None,
        model_used: str = "",
        device_used: str = "",
    ) -> dict[str, Any] | None:
        now_ts = time.time()
        with self._lock:
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                return None
            seg_json = json.dumps(segments or [], ensure_ascii=False)
            warn_json = json.dumps(warnings or [], ensure_ascii=False)
            norm_text = str(rendered_text if rendered_text is not None else row["normalized_text"] or "")
            dur = float(duration_sec if duration_sec is not None else row["duration_sec"] or 0.0)
            mu = str(model_used or row["model_used"] or "")
            du = str(device_used or row["device_used"] or "")
            self._db.execute(
                "UPDATE voice_records SET segments_json=?, raw_text=?, warnings_json=?, "
                "normalized_text=?, duration_sec=?, model_used=?, device_used=?, updated_at=? WHERE id=?",
                (seg_json, str(raw_text or ""), warn_json, norm_text, dur, mu, du, now_ts, record_id),
            )
            self._db.commit()
            updated = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(updated)

    def set_pinned(self, record_id: str, pinned: bool) -> dict[str, Any] | None:
        now_ts = time.time()
        val = 1 if pinned else 0
        with self._lock:
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                return None
            self._db.execute(
                "UPDATE voice_records SET pinned=?, updated_at=? WHERE id=?",
                (val, now_ts, record_id),
            )
            if row["job_id"]:
                self._db.execute(
                    "UPDATE meeting_jobs SET pinned=?, updated_at=? WHERE id=?",
                    (val, now_ts, row["job_id"]),
                )
            self._db.commit()
            updated = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(updated)

    def clear_audio_path(self, record_id: str) -> None:
        now_ts = time.time()
        with self._lock:
            self._db.execute(
                "UPDATE voice_records SET audio_path='', updated_at=? WHERE id=?",
                (now_ts, record_id),
            )
            self._db.execute(
                "UPDATE meeting_jobs SET audio_path='', updated_at=? WHERE record_id=?",
                (now_ts, record_id),
            )
            self._db.commit()

    def expired_unpinned_audio_records(self, older_than_ts: float) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM voice_records WHERE pinned=0 AND audio_path!='' AND created_at < ?",
                (float(older_than_ts),),
            ).fetchall()
        return [d for r in rows if (d := _row_to_dict(r)) is not None]

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(row)

    def list_for_brain(
        self,
        brain: str = "",
        brain_path: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 30), 200))
        ident = (brain_path or "").strip() or (brain or "").strip()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM voice_records ORDER BY created_at DESC LIMIT ?",
                (lim * 4 if ident else lim,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = _row_to_dict(r)
            if not item:
                continue
            if ident:
                target = item["brain_path"] or item["brain"]
                if not (
                    brain_ident.same_brain(target, ident)
                    or (brain and item["brain"] == brain and not brain_path)
                ):
                    continue
            out.append(item)
            if len(out) >= lim:
                break
        return out

    # ── Meeting Jobs CRUD ─────────────────────────────────────────────

    def create_job(
        self,
        *,
        brain: str,
        brain_path: str,
        title: str = "",
        upload_mode: str = "raw_upload",
        file_ext: str = ".webm",
        model: str = "large-v3-turbo",
        device: str = "auto",
        language: str = "vi",
        diarize: bool = True,
        num_speakers: int = 0,
        min_speakers: int = 0,
        max_speakers: int = 0,
        total_chunks: int = 0,
        audio_path: str = "",
        record_id: str = "",
        status: str = "uploading",
        pinned: bool = False,
        created_at: float | None = None,
        created_iso: str = "",
    ) -> dict[str, Any]:
        jid = uuid.uuid4().hex
        now_ts = float(created_at if created_at is not None else time.time())
        with self._lock:
            self._db.execute(
                "INSERT INTO meeting_jobs "
                "(id, created_at, created_iso, updated_at, brain, brain_path, title, status, progress, "
                "stage_detail, upload_mode, file_ext, model, device, language, diarize, "
                "num_speakers, min_speakers, max_speakers, total_chunks, received_chunks, audio_path, record_id, pinned) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    jid,
                    now_ts,
                    str(created_iso or ""),
                    now_ts,
                    str(brain or ""),
                    str(brain_path or ""),
                    str(title or ""),
                    str(status or "uploading"),
                    str(upload_mode or "raw_upload"),
                    str(file_ext or ".webm"),
                    str(model or "large-v3-turbo"),
                    str(device or "auto"),
                    str(language or "vi"),
                    1 if diarize else 0,
                    int(num_speakers or 0),
                    int(min_speakers or 0),
                    int(max_speakers or 0),
                    int(total_chunks or 0),
                    str(audio_path or ""),
                    str(record_id or ""),
                    1 if pinned else 0,
                ),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM meeting_jobs WHERE id=?", (jid,)).fetchone()
        return _job_to_dict(row) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM meeting_jobs WHERE id=?", (job_id,)).fetchone()
        return _job_to_dict(row)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status", "progress", "stage_detail", "upload_mode", "file_ext",
            "total_chunks", "received_chunks", "audio_path", "duration_sec",
            "pinned", "record_id", "error", "warnings_json",
        }
        sets: list[str] = ["updated_at=?"]
        vals: list[Any] = [time.time()]
        if "warnings" in fields and "warnings_json" not in fields:
            fields["warnings_json"] = json.dumps(fields.pop("warnings") or [], ensure_ascii=False)
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            if k == "pinned":
                vals.append(1 if v else 0)
            else:
                vals.append(v)
        vals.append(job_id)
        with self._lock:
            self._db.execute(f"UPDATE meeting_jobs SET {', '.join(sets)} WHERE id=?", vals)
            self._db.commit()
            row = self._db.execute("SELECT * FROM meeting_jobs WHERE id=?", (job_id,)).fetchone()
        return _job_to_dict(row)

    def fail_stuck_jobs(self, reason: str = "Server restarted before job completed") -> int:
        now_ts = time.time()
        with self._lock:
            cur = self._db.execute(
                "UPDATE meeting_jobs SET status='failed', error=?, updated_at=? "
                "WHERE status IN ('queued', 'converting', 'transcribing', 'diarizing', 'merging', 'processing')",
                (str(reason), now_ts),
            )
            self._db.commit()
            return cur.rowcount or 0

    def list_jobs_for_brain(
        self,
        brain: str = "",
        brain_path: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 20), 100))
        ident = (brain_path or "").strip() or (brain or "").strip()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM meeting_jobs ORDER BY created_at DESC LIMIT ?",
                (lim * 4 if ident else lim,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = _job_to_dict(r)
            if not item:
                continue
            if ident:
                target = item["brain_path"] or item["brain"]
                if not (
                    brain_ident.same_brain(target, ident)
                    or (brain and item["brain"] == brain and not brain_path)
                ):
                    continue
            out.append(item)
            if len(out) >= lim:
                break
        return out


_STORE_LOCK = threading.Lock()
_STORE: VoiceRecordStore | None = None
_STORE_PATH: Path | None = None


def get_store() -> VoiceRecordStore:
    global _STORE, _STORE_PATH
    wanted = _db_path().resolve()
    with _STORE_LOCK:
        if _STORE is None or _STORE_PATH != wanted:
            if _STORE is not None:
                _STORE.close()
            _STORE = VoiceRecordStore(wanted)
            _STORE_PATH = wanted
        return _STORE
