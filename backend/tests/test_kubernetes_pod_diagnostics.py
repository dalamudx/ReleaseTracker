"""Pod evidence is bounded, sanitized, and limited to this submitted rollout."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from releasetracker.services.kubernetes_pod_diagnostics import (
    waiting_diagnosis,
    pod_evidence,
    verify_pod_digests,
)


@pytest.mark.parametrize(
    "reason,message,expected",
    [
        (
            "ImagePullBackOff",
            "net/http: request canceled while waiting for connection (Client.Timeout exceeded while awaiting headers)",
            "image_pull_retrying",
        ),
        ("ErrImagePull", "unexpected EOF", "image_pull_retrying"),
        ("ErrImagePull", "response 503 Service Unavailable", "image_pull_retrying"),
        ("ErrImagePull", "unauthorized: timeout at https://registry.example", "invalid_image"),
        ("ImagePullBackOff", "manifest unknown", "invalid_image"),
        ("ErrImagePull", "unknown network error", None),
        ("InvalidImageName", "secret:1234", "invalid_image"),
        ("CrashLoopBackOff", "more-private-logs", "container_start_failed"),
    ],
)
def test_classification_does_not_leak_messages(reason, message, expected):
    assert waiting_diagnosis(reason, message) == expected


def pod(created, image="example:v2", reason="ImagePullBackOff", message="timeout secret-123"):
    status = NS(state=NS(waiting=NS(reason=reason, message=message), terminated=None))
    return NS(
        metadata=NS(creation_timestamp=created),
        spec=NS(containers=[NS(name="app", image=image)]),
        status=NS(container_statuses=[status]),
    )


@pytest.mark.asyncio
async def test_pod_evidence_filters_old_versions_and_fails_closed():
    now = datetime.now(timezone.utc)
    adapter = MagicMock()
    api = adapter._get_core_api.return_value
    api.list_namespaced_pod.return_value.items = [
        pod(now - timedelta(hours=2), reason="InvalidImageName"),
        pod(now + timedelta(seconds=1), image="app:old", reason="InvalidImageName"),
        pod(
            now + timedelta(seconds=1),
            message="GET https://private.secret: token=bad request timeout",
        ),
    ]
    workload = {"spec": {"selector": {"match_labels": {"app": "demo"}}}}
    result = await pod_evidence(
        adapter, "namespace", workload, {"app": "example:v2"}, now.timestamp()
    )
    assert result == "image_pull_retrying"
    assert "secret" not in str(result)
    api.list_namespaced_pod.assert_called_once()
    assert api.list_namespaced_pod.call_args.kwargs["label_selector"] == "app=demo"
    api.list_namespaced_pod.side_effect = PermissionError("credentials secret")
    assert (
        await pod_evidence(adapter, "namespace", workload, {"app": "example:v2"}, now.timestamp())
        is None
    )
    adapter._get_core_api.assert_called()


@pytest.mark.asyncio
async def test_pinned_digest_checks_actual_ready_pods_and_fails_closed():
    now = datetime.now(timezone.utc)
    expected = "example/app@sha256:" + "a" * 64
    adapter = MagicMock()
    api = adapter._get_core_api.return_value
    first = pod(now, image=expected)
    first.status.container_statuses[0].name = "app"
    first.status.container_statuses[0].ready = True
    first.status.container_statuses[0].image_id = "docker-pullable://example/app@sha256:" + "a" * 64
    second = pod(now, image=expected)
    second.status.container_statuses[0].name = "app"
    second.status.container_statuses[0].ready = True
    second.status.container_statuses[0].image_id = first.status.container_statuses[0].image_id
    workload = {"spec": {"replicas": 2, "selector": {"match_labels": {"app": "test"}}}}
    api.list_namespaced_pod.return_value.items = [first]
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": expected}, now.timestamp())
        == "unknown"
    )
    api.list_namespaced_pod.return_value.items = [first, second]
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": expected}, now.timestamp())
        == "confirmed"
    )
    second.status.container_statuses[0].image_id = (
        "docker-pullable://example/app@sha256:" + "b" * 64
    )
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": expected}, now.timestamp())
        == "superseded"
    )
    second.status.container_statuses[0].image_id = "containerd://sha256:" + "a" * 64
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": expected}, now.timestamp())
        == "unknown"
    )
    api.list_namespaced_pod.side_effect = PermissionError("secret")
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": expected}, now.timestamp())
        == "unknown"
    )


@pytest.mark.asyncio
async def test_tagged_images_need_no_additional_digest_rbac():
    adapter = MagicMock()
    assert (
        await verify_pod_digests(adapter, "test", {}, {"app": "example/app:v1"}, None)
        == "confirmed"
    )
    adapter._get_core_api.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_pod_selector_is_not_listed():
    adapter = MagicMock()
    assert await pod_evidence(adapter, "namespace", {"spec": {}}, {"app": "example:v2"}, 42) is None
    adapter._get_core_api.assert_not_called()
