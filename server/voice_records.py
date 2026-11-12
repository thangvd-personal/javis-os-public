"""Luu ban tho ghi am (audit SQLite trong STATE_DIR) va ten tep chuan hoa trong brain.

Thiet ke:
  - Du lieu audit (thoi gian, brain, raw_text, lien ket file chuan hoa) nam trong SQLite
    o `<STATE_DIR>/voice_records.sqlite3` (khong nam trong brain) de tach biet ban tho
    audit voi file Markdown nguoi dung chinh sua.
  - Ban tho duoc INSERT vao SQLite TRUOC khi goi model chuan hoa, nen ke ca khi mang/model
    loi hoac nguoi dung sua lai file .md ve sau, ban tho goc van con nguyen de doi chieu.
  - Tep da chuan hoa nam trong `<brain>/VoiceRecord/YYYY.MM.DD-HH.MM.SS.md`.

Ghi chu: KHONG dung ky tu em dash (U+2014) o bat ky dau trong file nay.
"""
from __future__ import annotations

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
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    created_iso     TEXT NOT NULL,
    brain           TEXT NOT NULL,
    brain_path      TEXT NOT NULL DEFAULT '',
    raw_text        TEXT NOT NULL,
    normalized_text TEXT NOT NULL DEFAULT '',
    file_path       TEXT NOT NULL DEFAULT '',
    ceil_path       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'raw_saved',
    source          TEXT NOT NULL DEFAULT '',
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vr_brain_created ON voice_records(brain_path, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_vr_created ON voice_records(created_at DESC);
"""


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


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
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
    ) -> dict[str, Any]:
        rid = uuid.uuid4().hex
        now_ts = float(created_at if created_at is not None else time.time())
        with self._lock:
            self._db.execute(
                "INSERT INTO voice_records "
                "(id, created_at, created_iso, brain, brain_path, raw_text, normalized_text, "
                "file_path, ceil_path, status, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 'raw_saved', '', ?)",
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
    ) -> dict[str, Any] | None:
        now_ts = time.time()
        with self._lock:
            row = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                return None
            fp = str(file_path or row["file_path"] or "")
            cp = str(ceil_path or row["ceil_path"] or "")
            self._db.execute(
                "UPDATE voice_records SET normalized_text=?, source=?, file_path=?, ceil_path=?, "
                "status=?, updated_at=? WHERE id=?",
                (str(normalized_text or ""), str(source or ""), fp, cp, str(status or "normalized"), now_ts, record_id),
            )
            self._db.commit()
            updated = self._db.execute("SELECT * FROM voice_records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(updated)

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
