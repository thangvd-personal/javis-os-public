"""Corporate Local Guard and 15-Minute Ephemeral Vault Lease Manager (`G0-011`, `G0-018`, `P0-02`)."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Any, Mapping

from javisos.server.iam_contract.port import IamPdpAdapterPort


class VaultLockedError(PermissionError):
    """Raised when local corporate cache/vault is locked due to revocation, lease expiry, or network failure."""


def is_corporate_mode_active() -> bool:
    return os.getenv("JAVIS_WORKSPACE_TIER", "").strip().upper() == "CORPORATE"


def _xor_stream_cipher(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    block_idx = 0
    offset = 0
    while offset < len(data):
        digest = hashlib.sha256(key + nonce + block_idx.to_bytes(4, "big")).digest()
        chunk = data[offset : offset + 32]
        for i, b in enumerate(chunk):
            out[offset + i] = b ^ digest[i]
        offset += 32
        block_idx += 1
    return bytes(out)


class CorporateLocalGuard:
    """Workstation local defense-in-depth guard (`G0-011`).

    Not a substitute for the centralized Server-Side MCP Gateway PEP, but enforces:
    1. Disabling `_brain_dang_mo()` / `_brain_duy_nhat()` implicit brain fallback in Corporate mode.
    2. Blocking any direct enterprise tool invocation that did not route through MCP Gateway PEP.
    """

    def __init__(self, *, corporate_mode: bool | None = None) -> None:
        self._corporate_mode = corporate_mode

    @property
    def corporate_mode(self) -> bool:
        if self._corporate_mode is not None:
            return self._corporate_mode
        return is_corporate_mode_active()

    def resolve_corporate_vault(
        self,
        *,
        explicit_vault: str | None,
        verified_workspace_id: str | None,
    ) -> str:
        if not self.corporate_mode:
            return explicit_vault or ""
        if not explicit_vault or not str(explicit_vault).strip():
            raise PermissionError("CORPORATE_VAULT_CONTEXT_REQUIRED")
        if not verified_workspace_id or str(explicit_vault).strip() != str(verified_workspace_id).strip():
            raise PermissionError("CORPORATE_VAULT_MISMATCH")
        return str(explicit_vault).strip()

    def assert_routed_via_gateway(self, *, is_corporate: bool, via_gateway: bool) -> None:
        if (is_corporate or self.corporate_mode) and not via_gateway:
            raise PermissionError("DIRECT_ENTERPRISE_BYPASS_FORBIDDEN")


class LocalVaultLeaseManager:
    """Manages ephemeral in-RAM decryption keys and 15m lease renewal for Corporate cache (`G0-018`).

    - Ephemeral keys live strictly in RAM (`_ephemeral_keys`) and are never written to disk.
    - Max lease TTL is capped at 900s (15 minutes); max retention TTL is capped at 3600s (1 hour).
    - On lease expiry, `read_cached_artifact` synchronously polls `adapter.check_revocation`.
    - If `revoked=True` OR the adapter is unreachable (network/5xx failure), all keys for the
      affected entry/principal are immediately wiped from RAM and `VaultLockedError` is raised.
    """

    MAX_LEASE_SECONDS = 900
    MAX_RETENTION_SECONDS = 3600

    def __init__(
        self,
        *,
        adapter: IamPdpAdapterPort,
        default_lease_seconds: int = 900,
    ) -> None:
        self._adapter = adapter
        self._default_lease_seconds = min(int(default_lease_seconds), self.MAX_LEASE_SECONDS)
        self._ephemeral_keys: dict[str, bytes] = {}
        self._entries: dict[str, dict[str, Any]] = {}

    def store_cached_artifact(
        self,
        *,
        cache_key: str,
        plaintext: bytes,
        execution_context: Mapping[str, Any],
        resource_id: str,
        retention_seconds: int = 3600,
    ) -> None:
        now = time.time()
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(16)
        ciphertext = _xor_stream_cipher(plaintext, key, nonce)
        bounded_retention = min(int(retention_seconds), self.MAX_RETENTION_SECONDS)

        self._ephemeral_keys[cache_key] = key
        self._entries[cache_key] = {
            "ciphertext": ciphertext,
            "nonce": nonce,
            "execution_context": dict(execution_context),
            "resource_id": resource_id,
            "created_at": now,
            "lease_expires_at": now + self._default_lease_seconds,
            "retention_expires_at": now + bounded_retention,
            "locked": False,
        }

    def has_ephemeral_key(self, cache_key: str) -> bool:
        return cache_key in self._ephemeral_keys

    def force_expire_lease(self, cache_key: str) -> None:
        if cache_key in self._entries:
            self._entries[cache_key]["lease_expires_at"] = time.time() - 1.0

    def wipe_entry_key(self, cache_key: str) -> None:
        self._ephemeral_keys.pop(cache_key, None)
        if cache_key in self._entries:
            self._entries[cache_key]["locked"] = True

    def wipe_principal_keys(self, principal_id: str) -> int:
        wiped = 0
        for ck, entry in list(self._entries.items()):
            if entry["execution_context"].get("principal_id") == principal_id:
                self.wipe_entry_key(ck)
                wiped += 1
        return wiped

    def poll_and_enforce_revocation(self, cache_key: str) -> dict[str, Any]:
        entry = self._entries.get(cache_key)
        if not entry:
            raise VaultLockedError("VAULT_LOCKED_ENTRY_NOT_FOUND")
        try:
            rev = self._adapter.check_revocation(
                {
                    "execution_context": entry["execution_context"],
                    "resource_ids": [entry["resource_id"]],
                }
            )
        except Exception as exc:
            self.wipe_entry_key(cache_key)
            raise VaultLockedError(f"VAULT_LOCKED_NETWORK_OR_PDP_FAILURE: {exc}") from exc

        if rev.get("revoked"):
            self.wipe_entry_key(cache_key)
            return dict(rev)

        entry["lease_expires_at"] = time.time() + self._default_lease_seconds
        return dict(rev)

    def read_cached_artifact(self, cache_key: str) -> bytes:
        entry = self._entries.get(cache_key)
        if not entry or entry.get("locked"):
            self.wipe_entry_key(cache_key)
            raise VaultLockedError("VAULT_LOCKED_REVOKED_OR_MISSING")

        now = time.time()
        if now >= entry["retention_expires_at"]:
            self.wipe_entry_key(cache_key)
            self._entries.pop(cache_key, None)
            raise VaultLockedError("VAULT_LOCKED_RETENTION_EXPIRED")

        if now >= entry["lease_expires_at"]:
            rev = self.poll_and_enforce_revocation(cache_key)
            if rev.get("revoked"):
                raise VaultLockedError("VAULT_LOCKED_REVOKED_ON_LEASE_RENEWAL")

        key = self._ephemeral_keys.get(cache_key)
        if not key:
            raise VaultLockedError("VAULT_LOCKED_KEY_WIPED")

        return _xor_stream_cipher(entry["ciphertext"], key, entry["nonce"])
