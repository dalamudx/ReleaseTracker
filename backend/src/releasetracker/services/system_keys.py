from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet

if TYPE_CHECKING:
    from ..storage.sqlite import SQLiteStorage

JWT_SECRET_MIN_LENGTH = 32


class SystemKeyManager:
    def __init__(self, secrets_path: Path):
        self.secrets_path = secrets_path
        self._jwt_secret = ""
        self._encryption_key = ""
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def jwt_secret(self) -> str:
        if not self._jwt_secret:
            raise RuntimeError("JWT secret is not initialized")
        return self._jwt_secret

    @property
    def encryption_key(self) -> str:
        if not self._encryption_key:
            raise RuntimeError("Encryption key is not initialized")
        return self._encryption_key

    async def initialize(self) -> None:
        async with self._lock:
            payload = self._load_payload()
            changed = False

            jwt_secret = self._normalize_secret(payload.get("jwt_secret"))
            if jwt_secret is None:
                jwt_secret = self.generate_jwt_secret()
                payload["jwt_secret"] = jwt_secret
                changed = True

            encryption_key = self._normalize_secret(payload.get("encryption_key"))
            if encryption_key is None:
                encryption_key = self.generate_encryption_key()
                payload["encryption_key"] = encryption_key
                changed = True

            self.validate_encryption_key(encryption_key)
            self._jwt_secret = jwt_secret
            self._encryption_key = encryption_key

            if changed or not self.secrets_path.exists():
                payload["updated_at"] = self._now()
                self._write_payload(payload)

    async def set_jwt_secret(self, value: str) -> None:
        normalized = self.validate_jwt_secret(value)
        async with self._lock:
            self._set_jwt_secret_locked(normalized)

    async def set_encryption_key(self, value: str) -> None:
        normalized = self.validate_encryption_key(value)
        async with self._lock:
            self._set_encryption_key_locked(normalized)

    def _set_jwt_secret_locked(self, value: str) -> None:
        payload = self._current_payload()
        payload["jwt_secret"] = value
        payload["updated_at"] = self._now()
        self._write_payload(payload)
        self._jwt_secret = value

    def _set_encryption_key_locked(self, value: str) -> None:
        payload = self._current_payload()
        payload["encryption_key"] = value
        payload["updated_at"] = self._now()
        self._write_payload(payload)
        self._encryption_key = value

    def _load_payload(self) -> dict[str, Any]:
        if not self.secrets_path.exists():
            return {}
        try:
            payload = json.loads(self.secrets_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid system secrets file: {self.secrets_path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid system secrets file: {self.secrets_path}")
        return payload

    def _current_payload(self) -> dict[str, Any]:
        payload = self._load_payload()
        payload["jwt_secret"] = self.jwt_secret
        payload["encryption_key"] = self.encryption_key
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.secrets_path.with_name(f".{self.secrets_path.name}.tmp")
        content = json.dumps(payload, indent=2, sort_keys=True)
        tmp_path.write_text(f"{content}\n", encoding="utf-8")
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp_path, self.secrets_path)
        try:
            self.secrets_path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def fingerprint(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def generate_jwt_secret() -> str:
        return secrets.token_urlsafe(48)

    @staticmethod
    def generate_encryption_key() -> str:
        return Fernet.generate_key().decode("utf-8")

    @staticmethod
    def validate_jwt_secret(value: str) -> str:
        normalized = SystemKeyManager._normalize_secret(value)
        if normalized is None:
            raise ValueError("JWT secret must not be empty")
        if len(normalized) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(f"JWT secret must be at least {JWT_SECRET_MIN_LENGTH} characters")
        return normalized

    @staticmethod
    def validate_encryption_key(value: str) -> str:
        normalized = SystemKeyManager._normalize_secret(value)
        if normalized is None:
            raise ValueError("Encryption key must not be empty")
        Fernet(normalized.encode("utf-8"))
        return normalized

    @staticmethod
    def _normalize_secret(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


async def rotate_jwt_secret(
    storage: "SQLiteStorage",
    key_manager: SystemKeyManager,
    *,
    value: str | None = None,
    generate: bool = False,
) -> dict[str, Any]:
    new_secret = key_manager.generate_jwt_secret() if generate else key_manager.validate_jwt_secret(value or "")
    async with key_manager.lock:
        key_manager.validate_jwt_secret(new_secret)
        key_manager._set_jwt_secret_locked(new_secret)
        invalidated_sessions = await storage.delete_all_sessions()
        return {
            "fingerprint": key_manager.fingerprint(new_secret),
            "invalidated_sessions": invalidated_sessions,
            "requires_reauth": True,
        }


async def rotate_encryption_key(
    storage: "SQLiteStorage",
    key_manager: SystemKeyManager,
    *,
    value: str | None = None,
    generate: bool = False,
) -> dict[str, Any]:
    new_key = key_manager.generate_encryption_key() if generate else key_manager.validate_encryption_key(value or "")
    async with key_manager.lock:
        key_manager.validate_encryption_key(new_key)
        stats = await storage.rotate_encrypted_data(new_key)
        key_manager._set_encryption_key_locked(new_key)
        storage.set_encryption_key(new_key)
        stats["fingerprint"] = key_manager.fingerprint(new_key)
        return stats
