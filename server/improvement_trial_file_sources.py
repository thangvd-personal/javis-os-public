"""Owner-selected, current UTF-8 files inside the authenticated principal's brain.

The observer returns metadata only. Frozen inventories are re-read, never refreshed.
Permission versions are hashes of observed authority projections, not monotonic ACL
counters. Mutable files cannot prove retention of an old version, so snapshot_version
is unsupported. This component neither executes tools nor writes filesystem state.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
from pathlib import Path

import improvement_cases as IC
import improvement_store as IM
from evidence_store import redact
from improvement_blobs import MAX_ASSET_BYTES
from improvement_trial_selection import _identity as _read_identity, _read_stable
from improvement_trial_sources import validate_inventory
from resonance_store import Principal

_METADATA = {"timezone", "data_period", "cutoff", "schema", "freshness_policy"}
_DENIED_PARTS = {"state", "auth", "secrets", "credentials", "logs", "cache", "node_modules", "__pycache__", "venv"}
_DENIED_NAMES = {"settings", "credentials", "secrets", "token", "tokens", "auth", "id_rsa", "id_ed25519", "id_ecdsa"}
_DENIED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".kdbx"}


def _relative(ref):
    IC._text(ref)
    if not ref.startswith("brain-file:") or redact(ref) != ref:
        raise PermissionError("brain_file_source_denied")
    relative = IC.output_path(ref[len("brain-file:"):])
    parts = relative.split("/")
    for part in parts:
        folded = part.casefold()
        if (folded.startswith(".") or folded in _DENIED_PARTS or folded in _DENIED_NAMES
                or folded.split(".", 1)[0] in _DENIED_NAMES or Path(folded).suffix in _DENIED_SUFFIXES):
            raise PermissionError("brain_file_source_denied")
    return relative


def _metadata(value):
    IC._fields(value, _METADATA)
    value = IC._frozen(value)
    if value["freshness_policy"] != "require_current_version":
        raise ValueError("unsupported_brain_file_freshness_policy")
    # Reuse the inventory's exact snapshot metadata validation, without claiming authority.
    validate_inventory([{**value, "source_ref": "metadata-validation", "source_version": "metadata-validation",
        "identity": "a" * 64, "source_hash": "b" * 64, "fixture_hash": "c" * 64,
        "redaction": "none", "sufficient_source": True, "permission_id": "metadata-validation", "permission_version": 1}])
    return value


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _incarnation(info):
    # Windows birthtime survives content/mode edits. Platforms without birthtime
    # conservatively invalidate on ctime changes rather than guessing an incarnation.
    return {"device": info.st_dev, "inode": info.st_ino, "kind": stat.S_IFMT(info.st_mode),
            "birth": getattr(info, "st_birthtime_ns", info.st_ctime_ns)}


def _acl_hash(path):
    """Read actual native ACL metadata; failure never becomes a permissive default."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        function = ctypes.WinDLL("advapi32", use_last_error=True).GetFileSecurityW
        function.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        function.restype = wintypes.BOOL
        needed = wintypes.DWORD()
        # OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
        function(str(path), 7, None, 0, ctypes.byref(needed))
        if ctypes.get_last_error() != 122 or not 1 <= needed.value <= MAX_ASSET_BYTES:
            raise OSError("brain_file_acl_unavailable")
        buffer = ctypes.create_string_buffer(needed.value)
        if not function(str(path), 7, buffer, len(buffer), ctypes.byref(needed)) or needed.value > len(buffer):
            raise OSError("brain_file_acl_unavailable")
        return hashlib.sha256(buffer.raw[:needed.value]).hexdigest()
    if sys.platform == "linux" and hasattr(os, "getxattr"):
        projection = {}
        for name in ("system.posix_acl_access", "system.posix_acl_default", "security.selinux"):
            try:
                value = os.getxattr(path, name, follow_symlinks=False)
                projection[name] = hashlib.sha256(value).hexdigest()
            except OSError as error:
                if error.errno not in {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise
                projection[name] = "absent"
        return IM._hash(projection)
    raise OSError("brain_file_acl_platform_unverified")


def _root(principal):
    if not isinstance(principal, Principal):
        raise PermissionError("owner_required")
    raw = IM._brain(principal)
    root = Path(raw)
    if (not root.is_absolute() or os.path.normcase(os.path.normpath(raw)) != os.path.normcase(raw)
            or root != Path(os.path.abspath(raw))):
        raise PermissionError("canonical_brain_root_required")
    try:
        for ancestor in reversed((root, *root.parents)):
            info = ancestor.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                raise PermissionError("brain_file_root_unavailable")
    except OSError:
        raise PermissionError("brain_file_root_unavailable") from None
    return root


def _authority(root, relative):
    try:
        paths = [root]
        for part in relative.split("/"):
            paths.append(paths[-1] / part)
        projection = []
        for index, path in enumerate(paths):
            info = path.lstat()
            correct_kind = stat.S_ISREG(info.st_mode) if index == len(paths) - 1 else stat.S_ISDIR(info.st_mode)
            if (_linked(info) or not correct_kind
                    or (index == len(paths) - 1 and info.st_nlink != 1)):
                raise PermissionError("brain_file_source_unavailable")
            projection.append({"incarnation": _incarnation(info), "mode": stat.S_IMODE(info.st_mode),
                "uid": getattr(info, "st_uid", None), "gid": getattr(info, "st_gid", None),
                "attributes": getattr(info, "st_file_attributes", 0), "acl_hash": _acl_hash(path)})
        return projection
    except OSError:
        raise PermissionError("brain_file_authority_unavailable") from None


class BrainFileFixtures:
    def __init__(self, initial_sources=None):
        if initial_sources is None:
            initial_sources = {}
        if not isinstance(initial_sources, dict) or len(initial_sources) > 1000:
            raise ValueError("invalid_brain_file_sources")
        self.initial_sources = {}
        for ref, value in initial_sources.items():
            _relative(ref)
            self.initial_sources[ref] = _metadata(value)

    def _capture(self, principal, ref, metadata):
        relative = _relative(ref)
        metadata = _metadata(metadata)
        root = _root(principal)
        before = _authority(root, relative)
        path = root.joinpath(*relative.split("/"))
        raw, read_identity = _read_stable(path, MAX_ASSET_BYTES)
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("brain_file_utf8_required") from None
        if "\x00" in text or redact(text) != text:
            raise ValueError("brain_file_sensitive_or_binary_input")
        # Recheck root lineage after the read, including a moved/deleted ancestor.
        if (_root(principal) != root or _authority(root, relative) != before
                or _read_identity(path.lstat()) != read_identity):
            raise IM.ConflictError("brain_file_authority_changed")
        root_identity = IM._hash({"path": os.path.normcase(str(root)), "incarnation": before[0]["incarnation"]})
        identity = IM._hash({"root": root_identity, "relative": relative,
                             "lineage": [item["incarnation"] for item in before]})
        permission_id = IM._hash({"actor": principal.by, "root": root_identity, "ref": ref})
        authority = {"actor": principal.by, "root": root_identity, "ref": ref, "projection": before, "metadata": metadata}
        permission_version = int(IM._hash(authority), 16) + 1
        digest = hashlib.sha256(raw).hexdigest()
        entry = {**metadata, "source_ref": ref, "source_version": digest, "source_hash": digest,
                 "fixture_hash": digest, "identity": identity, "redaction": "none", "sufficient_source": True,
                 "permission_id": permission_id, "permission_version": permission_version}
        return validate_inventory([entry])[0], raw

    def __call__(self, principal, source_refs, *, frozen_inventory=None):
        _root(principal)
        if (not isinstance(source_refs, list) or not 1 <= len(source_refs) <= 1000
                or any(not isinstance(ref, str) for ref in source_refs) or len(set(source_refs)) != len(source_refs)):
            raise ValueError("invalid_brain_file_scope")
        expected = None
        if frozen_inventory is not None:
            expected = validate_inventory(frozen_inventory)
            mapping = {entry["source_ref"]: entry for entry in expected}
            if len(mapping) != len(expected) or set(mapping) != set(source_refs):
                raise PermissionError("brain_file_scope_changed")
            metadata = {ref: {key: entry[key] for key in _METADATA} for ref, entry in mapping.items()}
        else:
            metadata = self.initial_sources
            if not set(source_refs) <= set(metadata):
                raise PermissionError("brain_file_selection_required")
        result = validate_inventory([self._capture(principal, ref, metadata[ref])[0] for ref in source_refs])
        if expected is not None and result != expected:
            raise IM.ConflictError("brain_file_frozen_source_changed")
        # A later member can change an earlier member after its individual read
        # bracket closed. Re-observe the entire set, including authority and root
        # incarnation, and require the first inventory without refreshing any grant.
        verified = validate_inventory([self._capture(principal, ref, metadata[ref])[0] for ref in source_refs])
        if verified != result:
            raise IM.ConflictError("brain_file_inventory_changed_during_capture")
        return result

    def capture(self, principal, source_ref, *, expected_entry):
        expected = validate_inventory([expected_entry])[0]
        if expected["source_ref"] != source_ref:
            raise PermissionError("brain_file_scope_changed")
        metadata = {key: expected[key] for key in _METADATA}
        entry, raw = self._capture(principal, source_ref, metadata)
        if entry != expected:
            raise IM.ConflictError("brain_file_frozen_source_changed")
        manifest = {key: entry[key] for key in ("source_ref", "source_version", "timezone", "data_period",
                                                "cutoff", "schema", "redaction", "sufficient_source")}
        return {"raw": raw, "fixture": raw, "manifest": {"version": 1, "freshness": "current", **manifest}}
