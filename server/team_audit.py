# -*- coding: utf-8 -*-
"""Team Job Audit Logger (Nhat ky kiem toan viec lam nhom).

Ghi nhan chi tiet toan bo qua trinh lam viec cua team khi che do "Ghi nhat ky viec lam" duoc bat:
- Thoi diem (timestamp)
- Cong su (agent_key, ten/slug, model, provider)
- Hanh dong (action: plan, work, help, synthesize, reply...)
- Cac lenh goi cong cu (tool calls: tool name, cmd, args, duration, ok, output)
- Yeu cau gui den LLM (system prompt, user prompt, context)
- Thong tin tra ve (text ket qua, tokens_in, tokens_out, cost_usd)

Du lieu duoc ghi song song vao:
1. File JSONL: server/state/team_audit.jsonl va team_audit_{team_id}.jsonl (phuc vu xuat file/audit doc lap)
2. Bang SQLite: team_audit_log trong server/state/team.sqlite3 (phuc vu truy van va hien thi UI)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import STATE_DIR

log = logging.getLogger("team_audit")

AUDIT_LOG_FILE = STATE_DIR / "team_audit.jsonl"
TEAM_DB_PATH = STATE_DIR / "team.sqlite3"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS team_audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id TEXT NOT NULL,
        task_id TEXT,
        step_id TEXT,
        turn_id TEXT,
        agent_key TEXT NOT NULL,
        action TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        tool_calls_json TEXT NOT NULL DEFAULT '[]',
        prompt_snippet TEXT NOT NULL DEFAULT '',
        response_snippet TEXT NOT NULL DEFAULT '',
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS team_audit_team ON team_audit_log(team_id, created_at);")
    conn.execute("CREATE INDEX IF NOT EXISTS team_audit_task ON team_audit_log(task_id);")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(TEAM_DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def is_audit_enabled(team_id: str) -> bool:
    """Kiem tra xem team co dang bat che do Ghi nhat ky viec lam hay khong."""
    if not team_id or not TEAM_DB_PATH.exists():
        return False
    try:
        with closing(_conn()) as conn:
            # 1. Kiem tra bang team_quality_policy
            row = conn.execute("SELECT body FROM team_quality_policy WHERE team_id=?", (team_id,)).fetchone()
            if row and row["body"]:
                policy = json.loads(row["body"])
                if policy.get("audit_enabled") is not None:
                    return bool(policy.get("audit_enabled"))

            # 2. Fallback: kiem tra trong tags_json cua bang teams
            trow = conn.execute("SELECT tags_json FROM teams WHERE team_id=?", (team_id,)).fetchone()
            if trow and trow["tags_json"]:
                tags = json.loads(trow["tags_json"] or "[]")
                if "audit_enabled" in tags:
                    return True
    except Exception as ex:
        log.debug("is_audit_enabled error for %s: %s", team_id, ex)
    return False


def set_audit_enabled(team_id: str, enabled: bool) -> bool:
    """Cap nhat trang thai bat/tat Ghi nhat ky viec lam cho team vao database."""
    if not team_id:
        return False
    try:
        with closing(_conn()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body FROM team_quality_policy WHERE team_id=?", (team_id,)).fetchone()
            now = time.time()
            if row and row["body"]:
                policy = json.loads(row["body"])
                policy["audit_enabled"] = bool(enabled)
                payload = json.dumps(policy, ensure_ascii=False)
                conn.execute(
                    "UPDATE team_quality_policy SET body=?, updated_at=? WHERE team_id=?",
                    (payload, now, team_id)
                )
            else:
                policy = {
                    "enabled": False,
                    "cost_behavior": "observe_only",
                    "limit_migration": "retain",
                    "reference_usd": None,
                    "limit_migration_selected": False,
                    "audit_enabled": bool(enabled)
                }
                payload = json.dumps(policy, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO team_quality_policy(team_id, body, updated_at) VALUES(?, ?, ?)",
                    (team_id, payload, now)
                )
            conn.commit()
            return True
    except Exception as ex:
        log.warning("set_audit_enabled error for %s: %s", team_id, ex)
        return False


def record_audit(
    team_id: str,
    agent_key: str,
    action: str,
    *,
    task_id: str | None = None,
    step_id: str | None = None,
    turn_id: str | None = None,
    model: str = "",
    provider: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    system_prompt: str = "",
    user_prompt: str = "",
    response_text: str = "",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    extra_details: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Ghi mot ban ghi audit day du vao ca JSONL lan SQLite."""
    now_ts = time.time()
    iso_time = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
    tools_list = list(tool_calls or [])

    entry = {
        "timestamp": iso_time,
        "ts": now_ts,
        "team_id": team_id,
        "task_id": task_id or "",
        "step_id": step_id or "",
        "turn_id": turn_id or "",
        "agent_key": agent_key,
        "action": action,
        "model": model,
        "provider": provider,
        "tool_calls": tools_list,
        "llm_request": {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        },
        "llm_response": {
            "text": response_text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
        },
        "extra": extra_details or {}
    }

    # 1. Ghi vao file JSONL chung va file JSONL cua rieng team
    try:
        json_line = json.dumps(entry, ensure_ascii=False) + "\n"
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json_line)

        team_file = STATE_DIR / f"team_audit_{team_id}.jsonl"
        with team_file.open("a", encoding="utf-8") as f:
            f.write(json_line)
    except Exception as ex:
        log.warning("record_audit file write error: %s", ex)

    # 2. Ghi vao bang SQLite
    try:
        with closing(_conn()) as conn:
            conn.execute(
                """
                INSERT INTO team_audit_log(
                    team_id, task_id, step_id, turn_id, agent_key, action,
                    model, provider, tool_calls_json, prompt_snippet,
                    response_snippet, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    task_id,
                    step_id,
                    turn_id,
                    agent_key,
                    action,
                    model,
                    provider,
                    json.dumps(tools_list, ensure_ascii=False),
                    (user_prompt or "")[:1000],
                    (response_text or "")[:1000],
                    json.dumps(entry, ensure_ascii=False),
                    now_ts
                )
            )
            conn.commit()
    except Exception as ex:
        log.warning("record_audit sqlite write error: %s", ex)

    return entry


def get_audit_entries(team_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Lay danh sach cac ban ghi audit cua team tu SQLite hoac file JSONL."""
    if not team_id:
        return []
    out = []
    try:
        if TEAM_DB_PATH.exists():
            with closing(_conn()) as conn:
                rows = conn.execute(
                    "SELECT details_json FROM team_audit_log WHERE team_id=? ORDER BY id DESC LIMIT ?",
                    (team_id, max(1, min(int(limit), 500)))
                ).fetchall()
                for r in rows:
                    try:
                        out.append(json.loads(r["details_json"]))
                    except Exception:
                        pass
        if not out:
            # Fallback doc tu file team_audit_{team_id}.jsonl
            team_file = STATE_DIR / f"team_audit_{team_id}.jsonl"
            if team_file.exists():
                lines = team_file.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines[-limit:]):
                    if line.strip():
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            pass
    except Exception as ex:
        log.debug("get_audit_entries error for %s: %s", team_id, ex)
    return out


def get_team_tool_records(team_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Trich xuat danh sach cac lenh cong cu da chay cho team tu mcp_audit.jsonl va team_audit_log."""
    if not team_id:
        return []
    records = []
    tid_short = team_id.replace("tm_", "")[-8:]

    # 1. Doc tu mcp_audit.jsonl
    mcp_audit_path = STATE_DIR / "mcp_audit.jsonl"
    if mcp_audit_path.exists():
        try:
            with mcp_audit_path.open("r", encoding="utf-8") as f:
                for line in f:
                    if tid_short in line:
                        try:
                            item = json.loads(line)
                            records.append({
                                "ts": item.get("ts"),
                                "tool": item.get("tool") or "tool",
                                "cmd": item.get("cmd") or "",
                                "ok": bool(item.get("ok", True)),
                                "head": item.get("head") or "",
                                "source": "mcp"
                            })
                        except Exception:
                            pass
        except Exception as ex:
            log.debug("get_team_tool_records mcp_audit error: %s", ex)

    # 2. Doc tu team_audit_log
    try:
        if TEAM_DB_PATH.exists():
            with closing(_conn()) as conn:
                rows = conn.execute(
                    "SELECT tool_calls_json, created_at, agent_key FROM team_audit_log WHERE team_id=? AND tool_calls_json != '[]'",
                    (team_id,)
                ).fetchall()
                for r in rows:
                    try:
                        calls = json.loads(r["tool_calls_json"])
                        for c in calls:
                            records.append({
                                "ts": c.get("ts") or r["created_at"],
                                "tool": c.get("tool") or "tool",
                                "cmd": c.get("cmd") or c.get("arguments") or "",
                                "ok": bool(c.get("ok", True)),
                                "head": c.get("head") or c.get("output") or "",
                                "agent_key": r["agent_key"],
                                "source": "turn"
                            })
                    except Exception:
                        pass
    except Exception as ex:
        log.debug("get_team_tool_records audit_log error: %s", ex)

    return records[-limit:]
