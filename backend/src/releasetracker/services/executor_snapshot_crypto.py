"""Encryption helpers for generic executor snapshots."""

from __future__ import annotations

import json
from typing import Any

KIND = "executor_encrypted_v1"


def is_encrypted(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("kind") == KIND
        and isinstance(payload.get("secret"), str)
    )


def encrypt(storage, payload: dict[str, Any]) -> dict[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"kind": KIND, "secret": storage.fernet.encrypt(raw.encode("utf-8")).decode("utf-8")}


def decrypt(storage, payload: Any) -> dict[str, Any]:
    if not is_encrypted(payload):
        if not isinstance(payload, dict):
            raise ValueError("snapshot payload must be an object")
        return payload
    raw = storage.fernet.decrypt(payload["secret"].encode("utf-8"))
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("snapshot plaintext must be an object")
    return value
