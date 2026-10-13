"""Docker daemon identity, with Podman Docker-compatibility socket binding."""

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import re

from .base import RuntimeMutationError

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{7,255}")
PODMAN_COMPAT_PREFIX = "podman-compat-v1:"


def _text(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError()
    return value


def _podman_compat_id(adapter, client, info) -> str:
    version = client.version()
    components = version.get("Components") if isinstance(version, dict) else None
    if not isinstance(components, list) or not any(
        isinstance(item, dict) and item.get("Name") == "Podman Engine" for item in components
    ):
        raise ValueError()

    config = getattr(adapter.runtime_connection, "config", None)
    if not isinstance(config, dict):
        raise ValueError()
    socket = _text(config.get("socket"))
    tls_verify = config.get("tls_verify", False)
    if type(tls_verify) is not bool:
        raise ValueError()
    api_version = config.get("api_version")
    if api_version is not None:
        api_version = _text(api_version)
    credential_id = getattr(adapter.runtime_connection, "credential_id", None)
    if credential_id is not None and (type(credential_id) is not int or credential_id < 1):
        raise ValueError()

    root = _text(info.get("DockerRootDir"))
    if not root.startswith("/") or root == "/" or any(p in (".", "..") for p in root.split("/")):
        raise ValueError()
    if type(info.get("Rootless")) is not bool:
        raise ValueError()

    identity = {
        "connection": {
            "socket": socket,
            "tls_verify": tls_verify,
            "api_version": api_version or "",
            "credential_id": credential_id or 0,
        },
        "host": _text(info.get("Name")),
        "storage_root": str(PurePosixPath(root)),
        "storage_driver": _text(info.get("Driver")),
        "rootless": info["Rootless"],
        "os": _text(info.get("OSType")),
        "architecture": _text(info.get("Architecture")),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PODMAN_COMPAT_PREFIX + digest


def read(adapter):
    try:
        if adapter.runtime_connection.type != "docker":
            raise ValueError()
        client = adapter._get_client()
        info = client.info()
        if not isinstance(info, dict):
            raise ValueError()
        if "BuildahVersion" in info or "Rootless" in info:
            identifier = _podman_compat_id(adapter, client, info)
        else:
            identifier = info.get("ID")
            if (
                not isinstance(identifier, str)
                or identifier.startswith(PODMAN_COMPAT_PREFIX)
                or not ID.fullmatch(identifier)
            ):
                raise ValueError()
        return {"schema": 1, "kind": "docker", "daemon_id": identifier}
    except Exception:
        raise ValueError("Docker daemon identity could not be verified") from None


def expected(snapshot):
    identity = snapshot.get("engine_identity")
    if (
        not isinstance(identity, dict)
        or type(identity.get("schema")) is not int
        or identity != {"schema": 1, "kind": "docker", "daemon_id": identity.get("daemon_id")}
        or not isinstance(identity.get("daemon_id"), str)
        or not ID.fullmatch(identity["daemon_id"])
    ):
        raise ValueError("snapshot lacks verified Docker daemon identity")
    return identity


def verify(adapter, snapshot, *, destructive=False):
    try:
        saved = expected(snapshot)
        if read(adapter) != saved:
            raise ValueError("Docker daemon identity changed since snapshot capture")
    except ValueError as exc:
        if destructive:
            raise RuntimeMutationError(str(exc), destructive_started=True) from None
        raise


def bind(adapter, snapshot, identity):
    verify(adapter, {"engine_identity": identity})
    snapshot["engine_identity"] = deepcopy(identity)
    for item in snapshot.get("snapshots", []):
        item["engine_identity"] = deepcopy(identity)
    return snapshot


def verify_group(adapter, snapshot):
    items = snapshot["snapshots"]
    identity = expected(items[0])
    if any(expected(item) != identity for item in items):
        raise ValueError("Docker group snapshot contains different daemon identities")
    if "engine_identity" in snapshot and expected(snapshot) != identity:
        raise ValueError("Docker group snapshot daemon identity disagrees with its members")
    verify(adapter, items[0])
