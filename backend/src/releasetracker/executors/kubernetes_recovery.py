"""Read-only rollout verification for explicit Kubernetes image recovery.

Proves controller rollout, not application data recovery or mutable-tag identity.
The caller retains the recovery lock; no writes are retried.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
import re
import time

from ..services.deployment_readiness_probes import _workload_status
from .base import RuntimeMutationError

VERIFY_TIMEOUT = 60.0
VERIFY_INTERVAL = 1.0
PROBE_TIMEOUT = 10.0


def api_spec(model):
    """Serialize real SDK models using API names, never snake_case to_dict keys."""
    if not isinstance(getattr(model, "attribute_map", None), dict):
        return None

    def encode(value):
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: encode(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [encode(v) for v in value]
        if isinstance(getattr(value, "attribute_map", None), dict):
            return {
                wire: encode(getattr(value, field))
                for field, wire in value.attribute_map.items()
                if getattr(value, field) is not None
            }
        if value is None or isinstance(value, str | int | float | bool):
            return value
        raise ValueError("Unsupported Kubernetes API spec value")

    return normalize_spec(encode(model))


def normalize_spec(raw):
    spec = deepcopy(raw)
    if not isinstance(spec, dict):
        raise ValueError("Kubernetes API spec must be an object")
    # These are server bookkeeping, not user Pod template configuration.
    metadata = spec.get("template", {}).get("metadata", {})
    for key in (
        "uid",
        "resourceVersion",
        "generation",
        "creationTimestamp",
        "deletionTimestamp",
        "deletionGracePeriodSeconds",
        "managedFields",
        "selfLink",
    ):
        metadata.pop(key, None)
    return spec


def recovery_spec(snapshot, current):
    scope = snapshot.get("recovery_scope")
    if scope in (None, "container_images"):
        return None
    if scope != "workload_spec":
        raise ValueError("Unsupported Kubernetes snapshot recovery scope")
    saved = snapshot["workload"].get("api_spec")
    live = current.get("api_spec")
    if not isinstance(saved, dict) or not isinstance(live, dict):
        raise ValueError("Kubernetes full-spec snapshot lacks API configuration")
    spec = deepcopy(saved)
    for key in ("selector",) + (
        ("serviceName", "podManagementPolicy", "volumeClaimTemplates")
        if current["kind"] == "StatefulSet"
        else ()
    ):
        if spec.get(key) != live.get(key):
            raise ValueError(f"Kubernetes recovery immutable configuration differs: {key}")
    pod = spec.get("template", {}).get("spec", {})
    images = {}
    for field in ("containers", "initContainers"):
        entries = pod.get(field, [])
        if not isinstance(entries, list) or (field == "containers" and not entries):
            raise ValueError("Kubernetes snapshot container configuration is incomplete")
        group = {}
        for entry in entries:
            name = entry.get("name") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name or name in images:
                raise ValueError("Kubernetes snapshot has invalid or duplicate container names")
            images[name] = group[name] = entry.get("image")
        if field == "containers" and group != snapshot["containers"]:
            raise ValueError("Kubernetes snapshot image indexes disagree")
    validate_images(images)
    validate_strategy({"kind": current["kind"], "metadata": current["metadata"], "spec": spec})
    return spec


def validate_images(images):
    if not images or any(
        not isinstance(image, str) or not re.fullmatch(r"[^@\s]+@sha256:[a-f0-9]{64}", image)
        for image in images.values()
    ):
        raise ValueError(
            "Kubernetes recovery requires digest-pinned snapshot images; mutable tags cannot prove artifact identity"
        )


def validate_strategy(workload):
    spec = workload.get("spec") or {}
    metadata = workload.get("metadata") or {}
    if type(metadata.get("generation")) is not int or metadata["generation"] < 1:
        raise ValueError("Kubernetes recovery requires observed workload generation")
    if spec.get("paused"):
        raise ValueError("Kubernetes recovery cannot verify a paused rollout")
    kind = workload.get("kind")
    if kind in {"Deployment", "StatefulSet"}:
        replicas = spec.get("replicas", 1)
        if type(replicas) is not int or replicas < 1:
            raise ValueError("Kubernetes recovery requires positive desired replicas")
    if kind in {"StatefulSet", "DaemonSet"}:
        strategy = spec.get("update_strategy", spec.get("updateStrategy")) or {}
        rolling = strategy.get("rolling_update", strategy.get("rollingUpdate")) or {}
        if strategy.get("type", "RollingUpdate") != "RollingUpdate" or rolling.get("partition", 0):
            raise ValueError("Kubernetes recovery cannot verify OnDelete or partitioned rollouts")


async def verify_rollout(adapter, target_ref, submitted, expected, previous, expected_spec=None):
    """Later GETs cannot replace the baseline supplied by the PATCH response."""
    try:
        target = adapter._workload_from_obj(target_ref["kind"], submitted)
        metadata = target.get("metadata") or {}
        old = previous.get("metadata") or {}
        generation = metadata.get("generation")
        if (
            metadata.get("uid") != old.get("uid")
            or type(generation) is not int
            or generation < old["generation"]
        ):
            raise ValueError("PATCH response lacks matching workload UID/generation")
        validate_strategy(target)
        # Verify complete configuration through raw GET below. The typed PATCH
        # response can omit fields unknown to this SDK; UID/generation remain authoritative.
        deadline = time.monotonic() + VERIFY_TIMEOUT
        stable = False
        while True:
            remaining = max(0.001, deadline - time.monotonic())
            current = adapter._get_workload(
                target_ref["kind"],
                target_ref["name"],
                target_ref["namespace"],
                request_timeout=min(PROBE_TIMEOUT, remaining),
            )
            validate_strategy(current)
            if expected_spec is not None and current.get("api_spec") != expected_spec:
                raise ValueError("Restored workload configuration changed during rollout")
            status, message = _workload_status(current, target, expected)
            if status not in {"healthy", "pending"}:
                raise RuntimeError(f"Kubernetes recovery rollout {status}: {message}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Kubernetes recovery rollout verification timed out")
            if status == "healthy" and stable:
                return
            stable = status == "healthy"
            await asyncio.sleep(min(VERIFY_INTERVAL, max(0, deadline - time.monotonic())))
    except Exception as exc:
        raise RuntimeMutationError(
            f"Kubernetes recovery not verified: {exc}", destructive_started=True
        ) from exc
