"""Read-only byte-exact source snapshots for an explicitly supplied host context plan.

The caller owns the surface schema, authorized roots and completeness decision. This
primitive does not assemble a production prompt, heal memory, inspect settings, or infer
that every protected source for a live surface has been included.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from evidence_store import redact
from improvement_blobs import MAX_ASSET_BYTES, AssetUnavailable, check_ancestry
from improvement_cases import _frozen, output_path
from improvement_store import _hash, _json

MAX_CONTEXT_SOURCES = 100
MAX_PLAN_METADATA_BYTES = 16384


@dataclass(frozen=True)
class ContextSource:
    """A host-created protected source descriptor; never construct from client JSON."""

    ref: str
    root: Path
    relative: str
    required: bool = True


def _ref(value):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value.encode("utf-8")) > 400 or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid_context_ref")
    return value


def _root(value):
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise ValueError("invalid_context_root")
    try:
        _check_path(value)
        canonical = value.resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("insufficient_source: context_root_unavailable")
        return canonical
    except (AssetUnavailable, OSError, RuntimeError) as error:
        raise ValueError("insufficient_source: context_root_unavailable") from error


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _check_path(path):
    check_ancestry(path)
    # exists() follows links and is false for dangling Windows junctions. Inspect
    # every lexical component itself before allowing an absent optional file.
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AssetUnavailable("linked_context_path")


def _read(source):
    root = _root(source.root)
    if root != source.root:
        raise ValueError("insufficient_source: context_root_changed")
    path = root / source.relative
    base = {"ref": source.ref, "root": str(root), "relative": source.relative, "required": source.required}
    observed_file = False
    try:
        _check_path(path)
        before = path.stat()
        observed_file = True
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("insufficient_source: context_source_not_file")
        if before.st_size > MAX_ASSET_BYTES:
            raise ValueError("insufficient_source: context_source_too_large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("insufficient_source: context_source_not_file")
            raw = handle.read(MAX_ASSET_BYTES + 1)
            finished = os.fstat(handle.fileno())
        _check_path(path)
        after = path.stat()
        # Windows path and descriptor ctime have different semantics. Compare each
        # source with itself, alongside inode/device/size/mtime across both APIs.
        if (len(raw) > MAX_ASSET_BYTES or _stamp(before) != _stamp(opened)
                or _stamp(opened) != _stamp(finished) or _stamp(finished) != _stamp(after)
                or before.st_ctime_ns != after.st_ctime_ns or opened.st_ctime_ns != finished.st_ctime_ns):
            raise ValueError("insufficient_source: context_source_changed")
        text = raw.decode("utf-8", errors="strict")
        if redact(text) != text:
            raise ValueError("insufficient_source: credential_bearing_context")
        identity = _hash({"dev": before.st_dev, "ino": before.st_ino, "ctime_ns": before.st_ctime_ns})
        return {**base, "present": True, "identity": identity, "hash": hashlib.sha256(raw).hexdigest()}, text
    except FileNotFoundError as error:
        if observed_file:
            raise ValueError("insufficient_source: context_source_changed") from error
        if source.required:
            raise ValueError("insufficient_source: required_context_source_missing") from error
        return {**base, "present": False, "identity": None, "hash": None}, None
    except (AssetUnavailable, OSError, UnicodeError, RuntimeError) as error:
        raise ValueError("insufficient_source: context_source_unavailable") from error


def capture_context(sources, *, surface: dict, complete_plan: bool, excluded_refs=()) -> dict:
    """Capture a host plan, preserving all source bytes and explicit missing optionals.

    ``surface`` is an opaque nonempty canonical-JSON host descriptor. The composing
    service validates its closed production schema. Completeness comes only from
    ``complete_plan``; source presence alone never upgrades unknown coverage.
    """
    if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= MAX_CONTEXT_SOURCES or type(complete_plan) is not bool:
        raise ValueError("invalid_context_plan")
    if not isinstance(surface, dict) or not surface:
        raise ValueError("invalid_context_surface")
    surface = _frozen(surface)
    if len(_json(surface).encode("utf-8")) > MAX_PLAN_METADATA_BYTES:
        raise ValueError("invalid_context_surface")
    if not isinstance(excluded_refs, (list, tuple, set, frozenset)) or len(excluded_refs) > MAX_CONTEXT_SOURCES:
        raise ValueError("invalid_context_exclusions")
    excluded = [_ref(value) for value in excluded_refs]
    folded_excluded = {value.casefold() for value in excluded}
    if len(folded_excluded) != len(excluded):
        raise ValueError("duplicate_context_exclusion")
    plan, refs, paths = [], set(), set()
    for source in sources:
        if type(source) is not ContextSource or type(source.required) is not bool:
            raise ValueError("invalid_context_source")
        ref, relative, root = _ref(source.ref), output_path(source.relative), _root(source.root)
        if ref.casefold() in folded_excluded or relative.casefold() in folded_excluded:
            raise ValueError("excluded_context_source")
        path_key = str(root / relative).casefold()
        if ref.casefold() in refs or path_key in paths:
            raise ValueError("duplicate_context_source")
        refs.add(ref.casefold())
        paths.add(path_key)
        plan.append(ContextSource(ref, root, relative, source.required))
    serialized_plan = [{"ref": item.ref, "root": str(item.root), "relative": item.relative, "required": item.required} for item in plan]
    plan_record = {"version": 1, "surface": surface, "complete_plan": complete_plan, "sources": serialized_plan,
                   "excluded_refs": sorted(excluded, key=str.casefold)}
    if redact(plan_record) != plan_record:
        raise ValueError("insufficient_source: credential_bearing_context_plan")
    metadata, rendered, identities = [], [], set()
    rendered_size = 0
    for source in plan:
        record, text = _read(source)
        if record["present"]:
            if record["identity"] in identities:
                raise ValueError("duplicate_context_source_identity")
            identities.add(record["identity"])
        metadata.append(record)
        header = "[context-source " + _json({"ref": source.ref}) + "]\n"
        section = header + (text if text is not None else "[optional source absent]") + "\n[/context-source]\n"
        rendered_size += len(section.encode("utf-8"))
        if rendered_size > MAX_ASSET_BYTES:
            raise ValueError("insufficient_source: rendered_context_too_large")
        rendered.append(section)
    # Observe all members again after collection. A change to an earlier file or
    # appearance of an optional source cannot silently produce a mixed snapshot.
    for source, original in zip(plan, metadata):
        current, _ = _read(source)
        if current != original:
            raise ValueError("insufficient_source: context_source_changed")
    content = "".join(rendered)
    return {"version": 1, "surface": surface, "plan_hash": _hash(plan_record), "sources": metadata,
            "baseline_context": {"content": content, "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                 "coverage": "complete" if complete_plan else "unknown"}}
