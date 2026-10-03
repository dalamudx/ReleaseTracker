"""Read-only recovery configuration projection and secret-safe, changed-only review.

Compare original values BEFORE masking. The HMAC binds review to the executor,
connection, historical snapshot, live identity and live recoverable settings.
Runtime status/counters never appear as configuration changes.
"""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import hmac
import json
import re

import yaml

from .snapshot_service import SnapshotRedactor, REDACTED_MARKER
from ..executors import kubernetes_artifact_evidence, kubernetes_recovery

MAX_LINES = 1000
MAX_VALUE_CHARS = 4096
_MISSING = object()


def _json(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _normalize(value, key=""):
    if isinstance(value, dict):
        return {str(k): _normalize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if key.lower() in {"environment", "env"} and all(
            isinstance(v, str) and "=" in v for v in value
        ):
            names = [v.split("=", 1)[0] for v in value]
            if len(set(names)) == len(names):
                return {v.split("=", 1)[0]: v.split("=", 1)[1] for v in value}
        if value and all(isinstance(v, dict) and isinstance(v.get("name"), str) for v in value):
            if len({v["name"] for v in value}) == len(value):
                return {
                    v["name"]: _normalize({k: x for k, x in v.items() if k != "name"})
                    for v in value
                }
        return [_normalize(v) for v in value]
    return value


def _container(snapshot, *, desired):
    config = deepcopy(snapshot.get("create_config"))
    if not isinstance(config, dict):
        # Legacy image-only adapters have no reconstructable configuration.
        return {"image": snapshot.get("image")}
    evidence = snapshot.get("recovery_evidence") or {}
    if evidence.get("image_id"):
        config["image"] = evidence["image_id"]
    networks = snapshot.get("network_config") or {}
    endpoints = {}
    identifier = snapshot.get("container_id") or ""
    for name, endpoint in (networks.get("endpoints") or {}).items():
        # Only options actually consumed by native network restore.
        options = {}
        aliases = [
            a for a in endpoint.get("Aliases") or [] if a not in (identifier, identifier[:12])
        ]
        if aliases:
            options["aliases"] = sorted(aliases)
        if endpoint.get("Links"):
            options["links"] = sorted(endpoint["Links"])
        for field, target in (("IPv4Address", "ipv4_address"), ("IPv6Address", "ipv6_address")):
            address = (endpoint.get("IPAMConfig") or {}).get(field)
            if address:
                options[target] = address
        endpoints[name] = options
    return _normalize(
        {
            "create_config": config,
            "networks": {"network_mode": networks.get("network_mode"), "endpoints": endpoints},
        }
    )


def project(snapshot, *, desired=False, live=None, adapter=None):
    """Only configuration consumed by recovery; never arbitrary inspect JSON."""
    mode = snapshot.get("mode")
    if mode == "helm_release":
        # Helm snapshots currently record chart/revision, NOT all values/manifests.
        return {
            k: snapshot.get(k) for k in ("chart_name", "chart_version", "revision")
        }, "helm_revision"
    if mode == "kubernetes_workload":
        prepared = kubernetes_artifact_evidence.prepare(snapshot) if desired else snapshot
        if snapshot.get("recovery_scope") == "workload_spec":
            spec = prepared["workload"].get("api_spec")
            if desired:
                if live is None:
                    raise ValueError("current Kubernetes workload is required for recovery diff")
                spec = kubernetes_recovery.recovery_spec(prepared, live["workload"])
            return _normalize({"spec": kubernetes_recovery.normalize_spec(spec)}), "workload_spec"
        return {"images": prepared["containers"]}, "container_images"
    if "stack_file" in snapshot:
        parsed = yaml.safe_load(snapshot["stack_file"])
        if not isinstance(parsed, dict):
            raise ValueError("Compose configuration cannot be compared safely")
        return (
            _normalize({"compose": parsed, "env": snapshot.get("env", [])}),
            "stack_configuration",
        )
    if mode == "docker_compose":
        items = snapshot.get("snapshots")
        if not isinstance(items, list) or not items:
            raise ValueError("grouped recovery configuration is unavailable")
        containers = {}
        for item in items:
            name = item.get("container_name")
            if not isinstance(name, str) or name in containers:
                raise ValueError("grouped recovery container identity is ambiguous")
            containers[name] = _container(item, desired=desired)
        result = {"containers": containers}
        if snapshot.get("runtime_type") == "podman" and any(item.get("pod_id") for item in items):
            if adapter is None:
                raise ValueError("Podman pod configuration projection requires the native adapter")
            result["pod"] = _normalize(adapter._pod_create_payload_from_snapshots(items))
        return result, "container_configuration"
    return _container(snapshot, desired=desired), "container_configuration"


def _identity(snapshot):
    if snapshot is None:
        return None
    workload = snapshot.get("workload") or {}
    return {
        **{
            k: snapshot.get(k)
            for k in (
                "runtime_type",
                "mode",
                "container_id",
                "container_name",
                "pod_id",
                "endpoint_id",
                "stack_id",
                "stack_name",
                "engine_identity",
            )
        },
        "uid": (workload.get("metadata") or {}).get("uid"),
        "members": [_identity(s) for s in snapshot.get("snapshots", [])],
    }


def _hidden(path):
    # Config values can embed credentials even when their keys are innocuous.
    # Environment/commands/labels are conservatively masked in full.
    sensitive = {
        "env",
        "environment",
        "env_vars",
        "command",
        "args",
        "entrypoint",
        "labels",
        "annotations",
        "healthcheck",
        "test",
        "data",
        "stringdata",
        "values",
        "secrets",
        "configs",
    }
    return any(
        str(p).lower() in sensitive or SnapshotRedactor._is_sensitive_key(str(p)) for p in path
    )


def _contains_sensitive(value):
    if isinstance(value, dict):
        return any(_hidden((key,)) or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return isinstance(value, str) and bool(
        re.search(
            r"(?:[a-z]+://[^/\s]*:[^/\s]*@|-----BEGIN .*PRIVATE KEY|Bearer\s+)",
            value,
            re.IGNORECASE,
        )
    )


def changed_lines(before, after):
    result = []
    count = 0
    oversized = False

    def emit(sign, path, value):
        nonlocal count, oversized
        count += 1
        if len(result) < MAX_LINES:
            redacted_value, _ = SnapshotRedactor().redact(value)
            masked = (
                _hidden(path) or _contains_sensitive(value) or _json(redacted_value) != _json(value)
            )
            displayed = REDACTED_MARKER if masked else _json(value)
            if len(displayed) > MAX_VALUE_CHARS:
                displayed = displayed[:MAX_VALUE_CHARS] + "…"
                oversized = True
            result.append(
                {
                    "operation": sign,
                    "path": "/"
                    + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path),
                    "value": displayed,
                    "redacted": masked,
                }
            )

    def visit(left, right, path=()):
        if left is not _MISSING and right is not _MISSING and _json(left) == _json(right):
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                visit(left.get(key, _MISSING), right.get(key, _MISSING), (*path, key))
        elif left is _MISSING and isinstance(right, dict) and right:
            for key in sorted(right):
                visit(_MISSING, right[key], (*path, key))
        elif right is _MISSING and isinstance(left, dict) and left:
            for key in sorted(left):
                visit(left[key], _MISSING, (*path, key))
        elif isinstance(left, list) and isinstance(right, list) and not _hidden(path):
            # Omit unchanged list entries as well as unchanged mapping keys.
            matcher = SequenceMatcher(
                None, [_json(v) for v in left], [_json(v) for v in right], autojunk=False
            )
            for operation, i, j, k, m in matcher.get_opcodes():
                if operation == "equal":
                    continue
                for offset in range(max(j - i, m - k)):
                    visit(
                        left[i + offset] if i + offset < j else _MISSING,
                        right[k + offset] if k + offset < m else _MISSING,
                        (*path, k + offset),
                    )
        else:
            if left is not _MISSING:
                emit("-", path, left)
            if right is not _MISSING:
                emit("+", path, right)

    visit(before, after)
    return result, count > MAX_LINES or oversized


async def capture_current(adapter, target_ref):
    try:
        image = (
            await adapter.get_current_image(target_ref)
            if target_ref.get("mode") != "helm_release"
            and adapter.supports_single_image_operations(target_ref)
            else ""
        )
        return await adapter.capture_snapshot(target_ref, image)
    except Exception as exc:
        if adapter.is_target_missing_error(exc):
            return None
        raise


async def build_review(storage, executor, adapter, snapshot):
    current = await capture_current(adapter, executor.target_ref)
    desired, scope = project(snapshot.snapshot_data, desired=True, live=current, adapter=adapter)
    observed = project(current, adapter=adapter)[0] if current is not None else {}
    if (
        snapshot.snapshot_data.get("mode") == "docker_compose"
        and snapshot.snapshot_data.get("runtime_type") == "docker"
    ):
        # Docker restores captured containers; other, newly added services stay untouched.
        for name, config in observed.get("containers", {}).items():
            desired["containers"].setdefault(name, config)
    lines, truncated = changed_lines(observed, desired)
    connection = await storage.get_runtime_connection(executor.runtime_connection_id)
    binding = {
        "executor": executor.model_dump(mode="json"),
        "connection": connection.model_dump(mode="json") if connection else None,
        "snapshot_id": snapshot.id,
        "snapshot": snapshot.snapshot_data,
        "live_identity": _identity(current),
        "current": observed,
        "desired": desired,
    }
    # No plain hashes of potentially low-entropy secrets leave the server.
    secret = storage.system_key_manager.jwt_secret.encode()
    fingerprint = hmac.new(
        secret, ("recovery-review-v1:" + _json(binding)).encode(), hashlib.sha256
    ).hexdigest()
    return {
        "scope": scope,
        "lines": lines,
        "truncated": truncated,
        "current_missing": current is None,
        "review_fingerprint": fingerprint if not truncated else None,
    }


async def require_review(storage, executor, adapter, snapshot, expected):
    review = await build_review(storage, executor, adapter, snapshot)
    if not review["review_fingerprint"] or not hmac.compare_digest(
        expected, review["review_fingerprint"]
    ):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail="Recovery configuration changed. Refresh the configuration diff and confirm again.",
        )
    return review
