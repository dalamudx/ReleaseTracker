"""Docker daemon identity; Podman's random compatibility ID is not supported."""

from copy import deepcopy
import re

from .base import RuntimeMutationError

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{7,255}")


def read(adapter):
    try:
        if adapter.runtime_connection.type != "docker":
            raise ValueError()
        info = adapter._get_client().info()
        identifier = info.get("ID") if isinstance(info, dict) else None
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
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
