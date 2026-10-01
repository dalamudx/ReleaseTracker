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


async def verify_pod_digests(adapter, namespace, workload, images, submitted_at):
    """Confirm actual running manifest digests for immutable image references.

    Containerd's config digest is not necessarily the registry manifest digest.
    Only an ImageID containing @sha256:<manifest> can prove equality; every
    other format is unknown, never assumed to match.
    """
    pinned = {
        name: ref.rsplit("@sha256:", 1)[1].lower()
        for name, ref in images.items()
        if isinstance(ref, str) and re.search(r"@sha256:[0-9a-fA-F]{64}$", ref)
    }
    if not pinned:
        return "confirmed"
    spec = workload.get("spec") or {}
    selector_spec = spec.get("selector") or {}
    selector = selector_spec.get("matchLabels") or selector_spec.get("match_labels") or {}
    count = spec.get("replicas", 1)
    if (
        not selector
        or not isinstance(selector, dict)
        or not submitted_at
        or not isinstance(count, int)
        or count < 1
    ):
        return "unknown"
    try:

        def read():
            adapter._authorize_namespace(namespace)
            return (
                adapter._get_core_api()
                .list_namespaced_pod(
                    namespace,
                    label_selector=",".join(f"{k}={v}" for k, v in sorted(selector.items())),
                    limit=100,
                    _request_timeout=3,
                )
                .items
            )

        pods = await asyncio.wait_for(asyncio.to_thread(read), timeout=4)
        confirmed = 0
        for pod in pods[:100]:
            created = _timestamp(
                getattr(getattr(pod, "metadata", None), "creation_timestamp", None)
            )
            if created is None or created < submitted_at - 30:
                continue
            containers = getattr(getattr(pod, "spec", None), "containers", None) or []
            if any(images.get(item.name) != item.image for item in containers):
                continue
            statuses = {
                item.name: item
                for item in getattr(getattr(pod, "status", None), "container_statuses", None) or []
            }
            if not all(
                name in statuses and getattr(statuses[name], "ready", False) for name in pinned
            ):
                continue
            matches = []
            for name, digest in pinned.items():
                image_id = getattr(statuses[name], "image_id", "") or ""
                match = re.search(r"@sha256:([0-9a-fA-F]{64})$", image_id)
                if not match:
                    return "unknown"
                matches.append(match.group(1).lower() == digest)
            if not all(matches):
                return "superseded"
            confirmed += 1
        return "confirmed" if confirmed >= count else "unknown"
    except asyncio.CancelledError:
        raise
    except Exception:
        return "unknown"


async def pod_evidence(adapter, namespace, workload, images, submitted_at):
    spec = workload.get("spec") or {}
    selector_spec = spec.get("selector") or {}
    selector = selector_spec.get("matchLabels") or selector_spec.get("match_labels") or {}
    if not selector or not isinstance(selector, dict) or not submitted_at:
        return None
    # Label keys/values come from the workload, not arbitrary user input.
    label_selector = ",".join(f"{k}={v}" for k, v in sorted(selector.items()))
    try:

        def read():
            adapter._authorize_namespace(namespace)
            return (
                adapter._get_core_api()
                .list_namespaced_pod(
                    namespace,
                    label_selector=label_selector,
                    limit=100,
                    _request_timeout=3,
                )
                .items
            )

        pods = await asyncio.wait_for(asyncio.to_thread(read), timeout=4)
        return _diagnose(pods, images, submitted_at)
    except asyncio.CancelledError:
        raise
    except Exception:
        # RBAC, transport failure and missing Pod timestamps are unknown, not
        # proof of a retryable error or of a terminal broken image.
        return None
