"""Read-only Team usage views over the existing usage event/index pipeline.

Team turns and agent calls are execution references, never additional bills. A CLI total
remains partial until explicitly linked request detail reconciles its token counts.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3

import usage_index
import usage_store
from usage_execution import _aggregate


def _execution_references(team_id, task_id=None):
    """Execution stores supply missing-telemetry evidence only; no tokens or costs are read."""
    refs, sources, errors = {}, [], 0
    root = usage_store._PATH.parent
    for name in ("team.sqlite3", "resonance.sqlite3"):
        path = root / name
        if not path.is_file():
            continue
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            if name == "team.sqlite3":
                where, args = "team_id=? AND engine_at IS NOT NULL", [team_id]
                if task_id is not None:
                    where += " AND task_id=?"
                    args.append(task_id)
                for turn, role in conn.execute("SELECT turn_id,role FROM team_turns WHERE " + where, args):
                    refs[turn] = {"role": role, "shared": False}
            elif task_id is None:
                for cid, purpose, reason, mirror, attempt in conn.execute(
                        "SELECT id,purpose,reason_ref,mirror_ref,attempt FROM agent_calls "
                        "WHERE context_ref=? AND engine_at IS NOT NULL", ("team:" + team_id,)):
                    key = mirror.split(":", 1)[1] if str(mirror).startswith("team_turn:") else reason
                    # A mirror is the same execution as team_turns. Attempts with a reason
                    # reused after an error remain distinct references until linked explicitly.
                    if not mirror and key in refs:
                        key = "call:" + str(cid)
                    refs.setdefault(key, {"role": purpose, "shared": not bool(mirror), "attempt": attempt})
            sources.append(name)
        except (OSError, sqlite3.Error):
            errors += 1
        finally:
            if conn is not None:
                conn.close()
    return refs, sources, errors


def _reconcile_executions(result, events, team_id, task_id=None):
    refs, sources, errors = _execution_references(team_id, task_id)
    observed = {e.get("execution", {}).get("turn_id") for e in events}
    missing = {key: value for key, value in refs.items() if key not in observed}
    coverage = result["coverage"]
    coverage.update(execution_sources=sources, execution_reference_errors=errors,
                    unobserved_by_role=dict(_counts(v["role"] or "unknown" for v in missing.values())),
                    unscoped_background="cross_context_review_not_allocated")
    coverage["unobserved_attempts"] += len(missing)
    result["attempts"] += len(missing)
    if missing or errors:
        coverage.update(status="partial", tokens_complete=False, money_complete=False, requests_complete=False)
        result["requests"] = None
    if task_id is None and "shared" in result:
        shared_missing = sum(bool(v["shared"]) for v in missing.values())
        result["shared"]["attempts"] += shared_missing
        result["shared"]["coverage"]["unobserved_attempts"] += shared_missing
        if shared_missing:
            result["shared"]["coverage"].update(status="partial", tokens_complete=False, money_complete=False,
                                                 requests_complete=False)
            result["shared"]["requests"] = None


def _counts(values):
    result = defaultdict(int)
    for value in values:
        result[value] += 1
    return result


def _view(team_id, task_id=None):
    try:
        events, details = usage_index.execution_events(team_id, task_id)
        result = _aggregate(events, details, usage_store.telemetry_errors(team_id, task_id))
        roles = defaultdict(list)
        for e in events:
            roles[e.get("execution", {}).get("role") or "unresolved"].append(e)
        result["roles"] = {role: _aggregate(rows, details) for role, rows in sorted(roles.items())}
        if task_id is None:
            result["shared"] = _aggregate([e for e in events if not e.get("execution", {}).get("task_id")], details)
        _reconcile_executions(result, events, team_id, task_id)
        result.update(team_id=team_id, task_id=task_id)
        return result
    except Exception:
        result = _aggregate([], telemetry_errors=1)
        result.update(team_id=team_id, task_id=task_id, roles={})
        if task_id is None:
            result["shared"] = _aggregate([])
        return result


def task_usage(team_id, task_id) -> dict:
    return _view(team_id, task_id)


def team_usage(team_id) -> dict:
    return _view(team_id)
