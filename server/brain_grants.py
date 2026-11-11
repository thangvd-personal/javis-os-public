"""brain_grants.py - which account may open which other account's brain (multi-user, step 3).

One row = (brain path, owner, grantee, role, status). Kept in ONE file at the state ROOT, not in an
account's own folder, because a grant crosses accounts: the owner writes it, the grantee reads it.

- role: `editor` (reads and writes like the owner) or `viewer` (reads only; writes get 403 and the
  chat on that brain runs at `suggest`).
- status: `pending` until the grantee accepts. A pending grant opens NOTHING: the brain only appears
  in the grantee's list and passes the brain gate once accepted.
- Paths are stored resolved, so `brain`, `path:<folder>` and a symlinked spelling meet on one key.

Not to be confused with share_store.py, which holds PUBLIC links to single files.
"""
import json
import os
import threading
import time
from pathlib import Path

import config as cfgmod

ROLES = ("editor", "viewer")
_LOCK = threading.RLock()


def _store() -> Path:
    return cfgmod._state_root() / "brain_grants.json"


def _key(path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _read() -> list:
    try:
        rows = json.loads(_store().read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _write(rows: list) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def grant(brain, owner: str, grantee: str, role: str) -> dict:
    """Invite (or change the role of) `grantee` on `brain`. A new invite is `pending`; changing the
    role of an accepted grant keeps it accepted, so downgrading editor -> viewer bites at once."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not grantee or grantee == owner:
        raise ValueError("cannot share a brain with its owner")
    k = _key(brain)
    with _LOCK:
        rows = _read()
        for r in rows:
            if r.get("brain") == k and r.get("grantee") == grantee:
                r["role"], r["owner"], r["updated"] = role, owner, time.time()
                _write(rows)
                return dict(r)
        row = {"brain": k, "owner": owner, "grantee": grantee, "role": role,
               "status": "pending", "created": time.time(), "updated": time.time()}
        rows.append(row)
        _write(rows)
        return dict(row)


def accept(brain, grantee: str) -> dict | None:
    k = _key(brain)
    with _LOCK:
        rows = _read()
        for r in rows:
            if r.get("brain") == k and r.get("grantee") == grantee:
                r["status"], r["updated"] = "accepted", time.time()
                _write(rows)
                return dict(r)
    return None


def revoke(brain, grantee: str) -> bool:
    """Remove a grant (the owner revoking, or the grantee declining/leaving)."""
    k = _key(brain)
    with _LOCK:
        rows = _read()
        keep = [r for r in rows if not (r.get("brain") == k and r.get("grantee") == grantee)]
        if len(keep) == len(rows):
            return False
        _write(keep)
        return True


def role_of(brain, grantee: str) -> str:
    """`editor`/`viewer` for an ACCEPTED grant covering `brain` (or a folder inside it), else ""."""
    if not grantee:
        return ""
    k = Path(_key(brain))
    for r in _read():
        if r.get("grantee") != grantee or r.get("status") != "accepted":
            continue
        root = Path(r.get("brain") or "")
        if k == root or root in k.parents:
            return r.get("role") if r.get("role") in ROLES else ""
    return ""


def for_grantee(grantee: str, status: str = "") -> list:
    return [dict(r) for r in _read() if r.get("grantee") == grantee and (not status or r.get("status") == status)]


def for_brain(brain) -> list:
    k = _key(brain)
    return [dict(r) for r in _read() if r.get("brain") == k]
