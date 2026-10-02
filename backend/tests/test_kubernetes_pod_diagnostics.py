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


def adapter_fixture():
    adapter = MagicMock()
    adapter._get_core_api.return_value.list_namespaced_pod.return_value.metadata = NS(
        _continue=None
    )
    return adapter


def pod(created, image="example:v2", reason="ImagePullBackOff", message="timeout secret-123"):
    status = NS(state=NS(waiting=NS(reason=reason, message=message), terminated=None))
    return NS(
        metadata=NS(
            creation_timestamp=created,
            deletion_timestamp=None,
            owner_references=[NS(kind="StatefulSet", uid="controller", controller=True)],
        ),
        spec=NS(containers=[NS(name="app", image=image)]),
        status=NS(container_statuses=[status]),
    )


@pytest.mark.asyncio
async def test_pod_evidence_filters_old_versions_and_fails_closed():
    now = datetime.now(timezone.utc)
    adapter = adapter_fixture()
    api = adapter._get_core_api.return_value
    api.list_namespaced_pod.return_value.items = [
        pod(now - timedelta(hours=2), reason="InvalidImageName"),
        pod(now + timedelta(seconds=1), image="app:old", reason="InvalidImageName"),
        pod(
            now + timedelta(seconds=1),
            message="GET https://private.secret: token=bad request timeout",
        ),
    ]
    workload = {
        "kind": "StatefulSet",
        "metadata": {"uid": "controller"},
        "spec": {"selector": {"match_labels": {"app": "demo"}}},
    }
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
    adapter = adapter_fixture()
    api = adapter._get_core_api.return_value
    first = pod(now, image=expected)
    first.status.container_statuses[0].name = "app"
    first.status.container_statuses[0].ready = True
    first.status.container_statuses[0].image_id = "docker-pullable://example/app@sha256:" + "a" * 64
    second = pod(now, image=expected)
    second.status.container_statuses[0].name = "app"
    second.status.container_statuses[0].ready = True
    second.status.container_statuses[0].image_id = first.status.container_statuses[0].image_id
    workload = {
        "kind": "StatefulSet",
        "metadata": {"uid": "controller"},
        "spec": {"replicas": 2, "selector": {"match_labels": {"app": "test"}}},
    }
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
        == "unknown"
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
async def test_pod_stability_changes_on_restart_or_replacement():
    from releasetracker.services.kubernetes_pod_diagnostics import pod_stability_fingerprint

    now = datetime.now(timezone.utc)
    adapter = adapter_fixture()
    api = adapter._get_core_api.return_value
    sample = pod(now)
    sample.metadata.uid = "first-pod"
    sample.metadata.deletion_timestamp = None
    status = sample.status.container_statuses[0]
    status.name = "app"
    status.ready = True
    status.restart_count = 0
    api.list_namespaced_pod.return_value.items = [sample]
    target = {
        "kind": "StatefulSet",
        "metadata": {"uid": "controller"},
        "spec": {"replicas": 1, "selector": {"match_labels": {"app": "test"}}},
    }
    first = await pod_stability_fingerprint(
        adapter, "test", target, {"app": "example:v2"}, now.timestamp()
    )
    assert first
    assert first == await pod_stability_fingerprint(
        adapter, "test", target, {"app": "example:v2"}, now.timestamp()
    )
    status.restart_count = 1
    second = await pod_stability_fingerprint(
        adapter, "test", target, {"app": "example:v2"}, now.timestamp()
    )
    assert second and second != first
    sample.metadata.uid = "replacement-pod"
    assert second != await pod_stability_fingerprint(
        adapter, "test", target, {"app": "example:v2"}, now.timestamp()
    )
    status.ready = False
    assert (
        await pod_stability_fingerprint(
            adapter, "test", target, {"app": "example:v2"}, now.timestamp()
        )
        is None
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
async def test_deployment_evidence_requires_owned_replicaset_chain():
    now = datetime.now(timezone.utc)
    adapter = adapter_fixture()
    sample = pod(now, reason="CrashLoopBackOff")
    sample.metadata.owner_references = [NS(kind="ReplicaSet", uid="rs", controller=True)]
    adapter._get_core_api.return_value.list_namespaced_pod.return_value.items = [sample]
    rs = NS(
        metadata=NS(
            uid="rs",
            owner_references=[NS(kind="Deployment", uid="wrong-deployment", controller=True)],
        )
    )
    adapter._get_apps_api.return_value.list_namespaced_replica_set.return_value = NS(
        items=[rs], metadata=NS(_continue=None)
    )
    workload = {
        "kind": "Deployment",
        "metadata": {"uid": "controller"},
        "spec": {"selector": {"match_labels": {"app": "test"}}},
    }
    assert (
        await pod_evidence(adapter, "test", workload, {"app": "example:v2"}, now.timestamp())
        is None
    )
    rs.metadata.owner_references[0].uid = "controller"
    assert (
        await pod_evidence(adapter, "test", workload, {"app": "example:v2"}, now.timestamp())
        == "container_start_failed"
    )
    sample.metadata.owner_references = []
    assert (
        await pod_evidence(adapter, "test", workload, {"app": "example:v2"}, now.timestamp())
        is None
    )
    sample.metadata.owner_references = [NS(kind="ReplicaSet", uid="rs", controller=True)]
    sample.metadata.deletion_timestamp = now
    assert (
        await pod_evidence(adapter, "test", workload, {"app": "example:v2"}, now.timestamp())
        is None
    )


@pytest.mark.asyncio
async def test_daemonset_digest_requires_all_scheduled_pods_and_complete_listing():
    now = datetime.now(timezone.utc)
    image = "example/app@sha256:" + "a" * 64
    adapter = adapter_fixture()
    first = pod(now, image=image)
    first.metadata.owner_references = [NS(kind="DaemonSet", uid="controller", controller=True)]
    status = first.status.container_statuses[0]
    status.name, status.ready = "app", True
    status.image_id = "docker-pullable://example/app@sha256:" + "a" * 64
    workload = {
        "kind": "DaemonSet",
        "metadata": {"uid": "controller"},
        "status": {"desiredNumberScheduled": 2},
        "spec": {"selector": {"match_labels": {"app": "test"}}},
    }
    response = adapter._get_core_api.return_value.list_namespaced_pod.return_value
    response.items = [first]
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": image}, now.timestamp())
        == "unknown"
    )
    import copy

    second = copy.deepcopy(first)
    response.items = [first, second]
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": image}, now.timestamp())
        == "confirmed"
    )
    response.metadata._continue = "more-results"
    assert (
        await verify_pod_digests(adapter, "test", workload, {"app": image}, now.timestamp())
        == "unknown"
    )


@pytest.mark.asyncio
async def test_distinct_manifest_needs_proven_index_membership():
    from unittest.mock import AsyncMock

    now = datetime.now(timezone.utc)
    image = "example/app@sha256:" + "a" * 64
    adapter = adapter_fixture()
    sample = pod(now, image=image)
    status = sample.status.container_statuses[0]
    status.name, status.ready = "app", True
    status.image_id = "docker-pullable://example/app@sha256:" + "b" * 64
    adapter._get_core_api.return_value.list_namespaced_pod.return_value.items = [sample]
    workload = {
        "kind": "StatefulSet",
        "metadata": {"uid": "controller"},
        "spec": {"replicas": 1, "selector": {"match_labels": {"app": "test"}}},
    }
    resolve = AsyncMock(return_value="confirmed")
    assert (
        await verify_pod_digests(
            adapter, "test", workload, {"app": image}, now.timestamp(), resolve
        )
        == "confirmed"
    )
    resolve.assert_awaited_once_with(image, "sha256:" + "b" * 64)
    resolve.return_value = "unknown"
    assert (
        await verify_pod_digests(
            adapter, "test", workload, {"app": image}, now.timestamp(), resolve
        )
        == "unknown"
    )
    resolve.return_value = "superseded"
    assert (
        await verify_pod_digests(
            adapter, "test", workload, {"app": image}, now.timestamp(), resolve
        )
        == "superseded"
    )


@pytest.mark.asyncio
async def test_unverified_pod_selector_is_not_listed():
    adapter = MagicMock()
    assert await pod_evidence(adapter, "namespace", {"spec": {}}, {"app": "example:v2"}, 42) is None
    adapter._get_core_api.assert_not_called()
