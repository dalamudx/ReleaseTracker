"""Immutable, local-only container recovery and read-only native verification."""

from __future__ import annotations

import asyncio
import re
import time
from copy import deepcopy
from typing import Any

from . import docker_identity

IMAGE_ID = re.compile(r"^(?:sha256:)?([a-f0-9]{64})$")
VERIFY_TIMEOUT = 60.0
VERIFY_INTERVAL = 1.0


def normalize_image_id(value: Any) -> str | None:
    match = IMAGE_ID.fullmatch(value) if isinstance(value, str) else None
    return f"sha256:{match[1]}" if match else None


def container_image_id(container) -> str | None:
    # Inspect is authoritative; never resolve Config.Image or a mutable alias.
    attrs = getattr(container, "attrs", {}) or {}
    value = attrs.get("Image") or attrs.get("ImageID")
    if value is not None:
        return normalize_image_id(value)
    return normalize_image_id(getattr(getattr(container, "image", None), "id", None))


def capture_evidence(container) -> dict[str, Any]:
    config = (getattr(container, "attrs", {}) or {}).get("Config")
    return {
        "schema": 1,
        "image_id": container_image_id(container),
        "healthcheck": deepcopy(config.get("Healthcheck")) if isinstance(config, dict) else None,
        "config_observed": isinstance(config, dict),
    }


def validate_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence = snapshot.get("recovery_evidence")
    if (
        not isinstance(evidence, dict)
        or type(evidence.get("schema")) is not int
        or evidence["schema"] != 1
    ):
        raise ValueError("snapshot lacks immutable container recovery evidence")
    if (
        not normalize_image_id(evidence.get("image_id"))
        or evidence.get("config_observed") is not True
    ):
        raise ValueError("snapshot lacks immutable image ID or inspected configuration")
    healthcheck = evidence.get("healthcheck")
    if healthcheck is not None and not isinstance(healthcheck, dict):
        raise ValueError("snapshot healthcheck evidence is invalid")
    if healthcheck:
        test = healthcheck.get("Test")
        if (
            not isinstance(test, list)
            or not test
            or not all(isinstance(v, str) for v in test)
            or test[0] not in ("NONE", "CMD", "CMD-SHELL")
        ):
            raise ValueError("snapshot healthcheck command is invalid")
    config = snapshot.get("create_config")
    if not isinstance(config, dict) or config.get("healthcheck") != healthcheck:
        raise ValueError("snapshot create configuration disagrees with healthcheck evidence")
    return evidence


def prepare_image(adapter, snapshot: dict[str, Any]) -> str:
    evidence = validate_evidence(snapshot)
    if adapter.runtime_connection.type == "docker":
        docker_identity.verify(adapter, snapshot)
    image_id = normalize_image_id(evidence["image_id"])
    # Missing artifacts must never trigger a tag pull or retagging operation.
    image = adapter._get_client().images.get(image_id)
    if normalize_image_id(getattr(image, "id", None)) != image_id:
        raise ValueError("local recovery image does not match snapshot image ID")
    return image_id


def _sample(adapter, container_id: str, snapshot: dict[str, Any], evidence: dict[str, Any]):
    expected_name = str(
        snapshot.get("container_name") or snapshot["create_config"].get("name") or ""
    ).lstrip("/")
    if adapter.runtime_connection.type == "docker":
        docker_identity.verify(adapter, snapshot, destructive=True)
    current = adapter._get_client().containers.get(container_id)
    if adapter.runtime_connection.type == "docker":
        docker_identity.verify(adapter, snapshot, destructive=True)
    attrs = getattr(current, "attrs", {}) or {}
    if (
        getattr(current, "id", None) != container_id
        or str(getattr(current, "name", "")).lstrip("/") != expected_name
    ):
        raise RuntimeError("recovery container identity changed during verification")
    if container_image_id(current) != normalize_image_id(evidence["image_id"]):
        raise RuntimeError("recovered container image ID does not match snapshot")
    config = attrs.get("Config")
    if not isinstance(config, dict) or config.get("Healthcheck") != evidence.get("healthcheck"):
        raise RuntimeError("recovered container healthcheck configuration does not match snapshot")
    state = attrs.get("State") or {}
    test = (evidence.get("healthcheck") or {}).get("Test")
    has_healthcheck = bool(test and test != ["NONE"])
    health = state.get("Health") or state.get("Healthcheck") or {}
    running = state.get("Running") is True and not any(
        state.get(k) for k in ("Paused", "Restarting", "Dead")
    )
    ready = running and (health.get("Status") == "healthy" if has_healthcheck else True)
    return ready, (container_id, attrs.get("RestartCount"), state.get("StartedAt"))


async def verify_group(adapter, container_ids: list[str], snapshots: list[dict[str, Any]]) -> None:
    if (
        not container_ids
        or len(container_ids) != len(snapshots)
        or len(set(container_ids)) != len(container_ids)
        or any(not isinstance(v, str) or not v for v in container_ids)
    ):
        raise RuntimeError("recovery did not return complete unique container identities")
    evidence = [validate_evidence(snapshot) for snapshot in snapshots]
    deadline = time.monotonic() + VERIFY_TIMEOUT
    previous = None
    while True:
        samples = [
            _sample(adapter, identifier, snapshot, item)
            for identifier, snapshot, item in zip(container_ids, snapshots, evidence, strict=True)
        ]
        ready = all(item[0] for item in samples)
        sample = tuple(item[1] for item in samples)
        if ready and previous == sample:
            return
        previous = sample if ready else None
        if time.monotonic() >= deadline:
            raise RuntimeError("recovery native readiness verification timed out")
        await asyncio.sleep(VERIFY_INTERVAL)


async def verify_container(adapter, container, snapshot: dict[str, Any]) -> None:
    await verify_group(adapter, [getattr(container, "id", None)], [snapshot])
