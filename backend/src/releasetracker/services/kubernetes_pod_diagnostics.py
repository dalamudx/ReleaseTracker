"""Read-only, bounded Kubernetes Pod diagnostics for the submitted rollout.

Never persist Pod messages: registry errors can contain credentials or URLs.
Unknown evidence must not extend a deployment deadline or imply success.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import re

TRANSIENT_PULL = re.compile(
    r"timed?\s*out|timeout|deadline exceeded|connection (?:reset|refused)|"
    r"temporary failure|tls handshake timeout|unexpected eof|(?:status|code|response).*5\d\d|"
    r"server gave http response",
    re.IGNORECASE,
)
TERMINAL_PULL = re.compile(
    r"unauthorized|authentication required|forbidden|denied|manifest unknown|"
    r"not found|invalid reference|no basic auth credentials|insufficient_scope",
    re.IGNORECASE,
)
TERMINAL_WAITING = {
    "InvalidImageName": "invalid_image",
    "ErrImageNeverPull": "invalid_image",
    "CreateContainerConfigError": "container_start_failed",
    "CreateContainerError": "container_start_failed",
    "RunContainerError": "container_start_failed",
    "CrashLoopBackOff": "container_start_failed",
}


def waiting_diagnosis(reason: str | None, message: str | None) -> str | None:
    if reason in TERMINAL_WAITING:
        return TERMINAL_WAITING[reason]
    if reason in {"ErrImagePull", "ImagePullBackOff"}:
        if TERMINAL_PULL.search(message or ""):
            return "invalid_image"
        if TRANSIENT_PULL.search(message or ""):
            return "image_pull_retrying"
    return None


def _timestamp(value):
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None


def _diagnose(pods, desired_images, submitted_at):
    # Only Pods created for this submission and using the submitted images can
    # influence the result. Old ReplicaSet Pods often share the same selector.
    observed = []
    for pod in pods[:100]:
        created = _timestamp(getattr(getattr(pod, "metadata", None), "creation_timestamp", None))
        if created is None or created < submitted_at - 30:
            continue
        spec = getattr(pod, "spec", None)
        containers = getattr(spec, "containers", None) or []
        if not containers or any(
            desired_images.get(container.name) != container.image for container in containers
        ):
            continue
        for item in getattr(getattr(pod, "status", None), "container_statuses", None) or []:
            waiting = getattr(getattr(item, "state", None), "waiting", None)
            if waiting is not None:
                code = waiting_diagnosis(
                    getattr(waiting, "reason", None), getattr(waiting, "message", None)
                )
                if code:
                    observed.append(code)
            terminated = getattr(getattr(item, "state", None), "terminated", None)
            if terminated is not None and getattr(terminated, "reason", None) == "OOMKilled":
                observed.append("oom_killed")
    return next((code for code in observed if code != "image_pull_retrying"), None) or (
        "image_pull_retrying" if "image_pull_retrying" in observed else None
    )


def desired_pod_count(workload):
    if workload.get("kind") == "DaemonSet":
        status = workload.get("status") or {}
        return status.get("desiredNumberScheduled", status.get("desired_number_scheduled"))
    return (workload.get("spec") or {}).get("replicas", 1)


def _owned_by(obj, kind, uid):
    owners = getattr(getattr(obj, "metadata", None), "owner_references", None) or []
    return any(
        owner.kind == kind and owner.uid == uid and owner.controller is True for owner in owners
    )


async def owned_pods(adapter, namespace, workload, images, submitted_at):
    """Read complete bounded evidence with an actual controller UID chain."""
    spec = workload.get("spec") or {}
    selector_spec = spec.get("selector") or {}
    selector = selector_spec.get("matchLabels") or selector_spec.get("match_labels") or {}
    uid = (workload.get("metadata") or {}).get("uid")
    kind = workload.get("kind")
    if (
        not selector
        or not isinstance(selector, dict)
        or not uid
        or kind not in {"Deployment", "StatefulSet", "DaemonSet"}
        or not submitted_at
    ):
        return None

    def read():
        adapter._authorize_namespace(namespace)
        options = {
            "label_selector": ",".join(f"{k}={v}" for k, v in sorted(selector.items())),
            "limit": 101,
            "_request_timeout": 3,
        }
        response = adapter._get_core_api().list_namespaced_pod(namespace, **options)
        if len(response.items) > 100 or getattr(
            getattr(response, "metadata", None), "_continue", None
        ):
            return None
        if kind == "Deployment":
            replicas = adapter._get_apps_api().list_namespaced_replica_set(namespace, **options)
            if len(replicas.items) > 100 or getattr(
                getattr(replicas, "metadata", None), "_continue", None
            ):
                return None
            uids = {
                item.metadata.uid for item in replicas.items if _owned_by(item, "Deployment", uid)
            }

            def belongs(pod):
                return any(_owned_by(pod, "ReplicaSet", rs) for rs in uids)

        else:

            def belongs(pod):
                return _owned_by(pod, kind, uid)

        result = []
        for pod in response.items:
            metadata = getattr(pod, "metadata", None)
            created = _timestamp(getattr(metadata, "creation_timestamp", None))
            if (
                not belongs(pod)
                or getattr(metadata, "deletion_timestamp", None)
                or created is None
                or created < submitted_at - 30
            ):
                continue
            containers = getattr(getattr(pod, "spec", None), "containers", None) or []
            if (
                not containers
                or any(images.get(item.name) != item.image for item in containers)
                or not set(images).issubset({item.name for item in containers})
            ):
                continue
            result.append(pod)
        return result

    from .deployment_readiness_probes import _bounded_thread

    return await _bounded_thread(adapter, "owned_pods", read)


async def pod_stability_fingerprint(adapter, namespace, workload, images, submitted_at):
    """Only unchanged, ready Pod UID/restart tuples establish stable readiness."""
    import hashlib
    import json

    count = desired_pod_count(workload)
    if not isinstance(count, int) or not 0 < count <= 100:
        return None
    try:
        pods = await owned_pods(adapter, namespace, workload, images, submitted_at)
        if pods is None or len(pods) != count:
            return None
        evidence = []
        for pod in pods:
            statuses = {
                item.name: item
                for item in getattr(getattr(pod, "status", None), "container_statuses", None) or []
            }
            uid = getattr(pod.metadata, "uid", None)
            if not uid or not all(name in statuses and statuses[name].ready for name in images):
                return None
            restarts = {name: getattr(statuses[name], "restart_count", None) for name in images}
            if any(not isinstance(value, int) or value < 0 for value in restarts.values()):
                return None
            evidence.append((uid, sorted(restarts.items())))
        return hashlib.sha256(json.dumps(sorted(evidence)).encode()).hexdigest()
    except asyncio.CancelledError:
        raise
    except Exception:
        return None


async def verify_pod_digests(
    adapter, namespace, workload, images, submitted_at, resolve_digest=None
):
    """Manifest ImageID must match the pin or its cryptographically verified child.

    A mismatch alone is UNKNOWN: an OCI index and its platform manifest differ.
    Config IDs are not manifest evidence. Proven graph exclusion is superseded.
    """
    pinned = {
        name: ref.rsplit("@", 1)[1].lower()
        for name, ref in images.items()
        if isinstance(ref, str) and re.search(r"@sha256:[0-9a-fA-F]{64}$", ref)
    }
    if not pinned:
        return "confirmed"
    count = desired_pod_count(workload)
    if not isinstance(count, int) or not 0 < count <= 100:
        return "unknown"
    try:
        pods = await owned_pods(adapter, namespace, workload, images, submitted_at)
        if pods is None or len(pods) != count:
            return "unknown"
        cache = {}
        for pod in pods:
            statuses = {
                item.name: item
                for item in getattr(getattr(pod, "status", None), "container_statuses", None) or []
            }
            if not all(
                name in statuses and getattr(statuses[name], "ready", False) for name in pinned
            ):
                return "unknown"
            for name, digest in pinned.items():
                match = re.search(
                    r"@(sha256:[0-9a-fA-F]{64})$", getattr(statuses[name], "image_id", "") or ""
                )
                if not match:
                    return "unknown"
                actual = match.group(1).lower()
                if actual != digest:
                    key = (images[name], actual)
                    if key not in cache:
                        cache[key] = await resolve_digest(*key) if resolve_digest else "unknown"
                    if cache[key] != "confirmed":
                        return cache[key]
        return "confirmed"
    except asyncio.CancelledError:
        raise
    except Exception:
        return "unknown"


async def pod_evidence(adapter, namespace, workload, images, submitted_at):
    try:
        pods = await owned_pods(adapter, namespace, workload, images, submitted_at)
        return _diagnose(pods, images, submitted_at) if pods is not None else None
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
