"""Durable, encrypted raw-byte revisions, independent of short-lived evidence cleanup."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Callable

MAX_ASSET_BYTES = 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENVELOPE = "revision-v1:"


class AssetUnavailable(RuntimeError):
    """A required immutable blob is missing, corrupt or cannot be decrypted."""


def is_link(path: Path) -> bool:
    """Also reject Windows junctions and other reparse points."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def check_ancestry(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        if component.exists() or component.is_symlink():
            if is_link(component):
                raise AssetUnavailable("linked_asset_path")


class RevisionBlobs:
    """Content-addressed bytes with a brain-specific directory and verified decryption."""

    def __init__(self, root: Path, encrypt: Callable[[str], str] | None = None,
                 decrypt: Callable[[str], str] | None = None):
        self.root = Path(root).absolute()
        self.encrypt = encrypt
        self.decrypt = decrypt

    def _path(self, brain: str, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise AssetUnavailable("invalid_asset_hash")
        brain_key = hashlib.sha256(brain.encode("utf-8")).hexdigest()
        path = self.root / brain_key / (digest + ".enc")
        check_ancestry(path)
        return path

    def write(self, brain: str, content: bytes) -> str:
        if not isinstance(content, bytes) or len(content) > MAX_ASSET_BYTES:
            raise ValueError("invalid_asset_size")
        digest = hashlib.sha256(content).hexdigest()
        path = self._path(brain, digest)
        if path.exists():
            if self.read(brain, digest) != content:
                raise AssetUnavailable("asset_hash_collision")
            return digest
        if self.encrypt is None:
            from secrets_store import encrypt_required
            encrypt = encrypt_required
        else:
            encrypt = self.encrypt
        ciphertext = encrypt(_ENVELOPE + base64.b64encode(content).decode("ascii"))
        if not isinstance(ciphertext, str) or not ciphertext.startswith("enc:"):
            raise AssetUnavailable("asset_encryption_unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        check_ancestry(path.parent)
        descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, path)
            except PermissionError:
                # Windows can deny replacement while another publisher/reader
                # holds the winner open. Reuse only verified identical content;
                # no existing winner means this remains a real write failure.
                if not path.exists():
                    raise
                if self.read(brain, digest) != content:
                    raise AssetUnavailable("asset_hash_collision")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.read(brain, digest)
        return digest

    def read(self, brain: str, digest: str) -> bytes:
        path = self._path(brain, digest)
        try:
            if not path.is_file() or path.stat().st_size > MAX_ASSET_BYTES * 3:
                raise AssetUnavailable("asset_missing_or_oversized")
            ciphertext = path.read_text(encoding="ascii")
            if not ciphertext.startswith("enc:"):
                raise AssetUnavailable("asset_not_encrypted")
            if self.decrypt is None:
                from secrets_store import decrypt
            else:
                decrypt = self.decrypt
            plaintext = decrypt(ciphertext)
            if not isinstance(plaintext, str) or not plaintext.startswith(_ENVELOPE):
                raise AssetUnavailable("asset_decryption_failed")
            raw = base64.b64decode(plaintext[len(_ENVELOPE):], validate=True)
            if len(raw) > MAX_ASSET_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                raise AssetUnavailable("asset_integrity_failed")
            return raw
        except (OSError, ValueError, UnicodeError) as error:
            raise AssetUnavailable("asset_unavailable") from error
