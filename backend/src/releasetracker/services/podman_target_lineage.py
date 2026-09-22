"""Persisted Podman target lineage; labels and names are context, never identity proof."""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from .deployment_plan import MARKER_KEYS

SCHEMA = 1
ACTIVE_PODMAN_LINEAGE: ContextVar[dict[str, Any] | None] = ContextVar(
    "active_podman_lineage", default=None
)


def target_id_for_executor(executor_id: int) -> str:
    if type(executor_id) is not int or executor_id < 1:
        raise ValueError("Podman lineage requires a persisted executor ID")
    return f"executor:{executor_id}"


def member_from_target(
    target_ref: dict[str, Any], markers: Any, *, container_id: str | None = None
) -> dict[str, Any]:
    mode = target_ref.get("mode", "container")
    if mode != "container":
        raise ValueError("Podman grouped lineage requires a dedicated grouped transition")
    values = tuple(markers) if isinstance(markers, (list, tuple)) else ()
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("Podman target does not have exactly one complete managed marker set")
    identifier = container_id or target_ref.get("container_id")
    return {
        "container_id": identifier,
        "name": str(target_ref.get("container_name") or "").lstrip("/"),
        "project": None,
        "service": None,
        "replica": None,
        "markers": values[0],
    }


def _id(value: Any) -> str:
    if not isinstance(value, str) or len(value.strip()) < 8 or any(ch.isspace() for ch in value):
        raise ValueError("Podman target member lacks a stable full container ID")
    return value.strip()


def normalize_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(members, list) or not members:
        raise ValueError("Podman target has no lineage members")
    result = []
    for value in members:
        if not isinstance(value, dict):
            raise ValueError("Podman lineage member must be an object")
        labels = value.get("markers")
        marker_state = value.get("marker_state", "managed")
        if marker_state not in {"managed", "approved_unmanaged"}:
            raise ValueError("Podman target member has an invalid marker state")
        if not isinstance(labels, dict) or any(
            not isinstance(labels.get(key), str) or not labels[key] for key in MARKER_KEYS
        ):
            raise ValueError("Podman target member lacks complete approved markers")
        result.append(
            {
                "container_id": _id(value.get("container_id")),
                "name": str(value.get("name") or "").lstrip("/"),
                "project": value.get("project") if isinstance(value.get("project"), str) else None,
                "service": value.get("service") if isinstance(value.get("service"), str) else None,
                "replica": value.get("replica") if isinstance(value.get("replica"), str) else None,
                "markers": {key: labels[key] for key in MARKER_KEYS},
                "marker_state": marker_state,
            }
        )
    result.sort(
        key=lambda item: (
            item["project"] or "",
            item["service"] or "",
            item["replica"] or "",
            item["name"],
            item["container_id"],
        )
    )
    if len({item["container_id"] for item in result}) != len(result):
        raise ValueError("Podman target lineage contains duplicate container IDs")
    slots = [(item["project"], item["service"], item["replica"], item["name"]) for item in result]
    if len(set(slots)) != len(slots):
        raise ValueError("Podman target lineage contains ambiguous member slots")
    return result


def target_fingerprint(mode: str, target_ref: dict[str, Any]) -> str:
    if mode not in {"container", "docker_compose"}:
        raise ValueError("unsupported Podman lineage mode")
    stable: dict[str, Any] = {"mode": mode}
    if mode == "container":
        name = target_ref.get("container_name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Podman lineage target requires container_name")
        stable["container_name"] = name.strip().lstrip("/")
    else:
        project = target_ref.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("Podman lineage target requires project")
        stable["project"] = project.strip()
        stable["services"] = sorted(
            {str(x).strip() for x in target_ref.get("services", []) if str(x).strip()}
        )
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot_binding(lineage: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "target_id": lineage["target_id"],
        "generation": lineage["generation"],
        "target_fingerprint": lineage["target_fingerprint"],
        "members": deepcopy(lineage["members"]),
    }


def validate_snapshot_binding(
    snapshot: dict[str, Any], lineage: dict[str, Any], *, allow_historical: bool = True
) -> dict[str, Any]:
    binding = snapshot.get("podman_target_lineage")
    if not isinstance(binding, dict) or binding.get("schema") != SCHEMA:
        raise ValueError("snapshot lacks verified Podman target lineage")
    if (
        binding.get("target_id") != lineage["target_id"]
        or binding.get("target_fingerprint") != lineage["target_fingerprint"]
    ):
        raise ValueError("snapshot belongs to a different Podman managed target")
    generation = binding.get("generation")
    if (
        type(generation) is not int
        or generation < 1
        or generation > lineage["generation"]
        or (not allow_historical and generation != lineage["generation"])
    ):
        raise ValueError("snapshot Podman target lineage generation is not trusted")
    return {"generation": generation, "members": normalize_members(binding.get("members"))}
