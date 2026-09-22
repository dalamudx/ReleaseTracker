"""Podman lineage scheduler integration; no real runtime is contacted."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.executors.podman import PodmanRuntimeAdapter
from releasetracker.services.deployment_plan import MANAGED_MARKERS, MARKER_KEYS
from releasetracker.services.podman_target_lineage import ACTIVE_PODMAN_LINEAGE
from test_executor_adapters import FakeContainer, FakeImage, FakePodmanClient
from releasetracker.storage.sqlite_podman_lineage import LineageConflict

pytestmark = pytest.mark.asyncio

MARKERS = dict(zip(MARKER_KEYS, ("fictional-installation", "fictional-target", "1")))


class Adapter:
    def __init__(self):
        self.container_id = "0123456789abcdef"
        self.name = "service-a"
        self.marker_state = "approved_unmanaged"

    async def get_target_lineage_members(self, target_ref, planned_markers):
        del target_ref
        return [
            {
                "container_id": self.container_id,
                "name": self.name,
                "project": None,
                "service": None,
                "replica": None,
                "markers": dict(planned_markers),
                "marker_state": self.marker_state,
            }
        ]

    async def capture_snapshot(self, target_ref, current_image):
        return {
            "runtime_type": "podman",
            "container_id": self.container_id,
            "container_name": self.name,
            "image": current_image,
            "target": dict(target_ref),
        }

    async def validate_snapshot(self, target_ref, snapshot):
        assert snapshot["target"] == target_ref


async def test_approved_takeover_binds_snapshot_then_advances_to_managed_replacement(storage):
    scheduler = ExecutorScheduler(storage)
    saved = []

    async def save(snapshot):
        saved.append(snapshot)
        return 1

    storage.create_executor_snapshot = save
    scheduler._prune_snapshot_history = AsyncMock()
    adapter = Adapter()
    config = SimpleNamespace(
        id=7,
        runtime_type="podman",
        target_ref={"mode": "container", "container_id": adapter.container_id},
    )
    marker_token = MANAGED_MARKERS.set(MARKERS)
    try:
        pending = await scheduler._verify_podman_lineage_before_write(config, adapter)
        assert pending["pending"] is True
        assert pending["members"][0]["marker_state"] == "approved_unmanaged"
        lineage_token = ACTIVE_PODMAN_LINEAGE.set(pending)
        try:
            await scheduler._capture_pre_update_snapshot(
                config,
                adapter,
                run_id=99,
                current_image="registry.example.test/team/service-a:1.0.0",
            )
        finally:
            ACTIVE_PODMAN_LINEAGE.reset(lineage_token)
        assert saved[0].snapshot_data["podman_target_lineage"]["generation"] == 1
        initial = await storage.podman_lineage.establish(
            executor_id=7,
            target_id=pending["target_id"],
            mode="container",
            target_fingerprint=pending["target_fingerprint"],
            members=pending["members"],
        )
        adapter.container_id = "fedcba9876543210"
        adapter.marker_state = "managed"
        config.target_ref = {"mode": "container", "container_id": adapter.container_id}
        await scheduler._advance_podman_lineage(config, adapter, initial, run_id=99)
    finally:
        MANAGED_MARKERS.reset(marker_token)
    current = await storage.podman_lineage.get(7)
    assert current["generation"] == 2
    assert current["members"][0]["container_id"] == "fedcba9876543210"
    assert current["members"][0]["marker_state"] == "managed"


async def test_recorded_lineage_rejects_missing_approval_and_external_replacement(storage):
    scheduler = ExecutorScheduler(storage)
    adapter = Adapter()
    config = SimpleNamespace(
        id=7,
        runtime_type="podman",
        target_ref={"mode": "container", "container_id": adapter.container_id},
    )
    token = MANAGED_MARKERS.set(MARKERS)
    try:
        pending = await scheduler._verify_podman_lineage_before_write(config, adapter)
        await storage.podman_lineage.establish(
            executor_id=7,
            target_id=pending["target_id"],
            mode="container",
            target_fingerprint=pending["target_fingerprint"],
            members=pending["members"],
        )
    finally:
        MANAGED_MARKERS.reset(token)
    with pytest.raises(LineageConflict, match="approved deployment task"):
        await scheduler._verify_podman_lineage_before_write(config, adapter)
    adapter.container_id = "1111111111111111"
    token = MANAGED_MARKERS.set(MARKERS)
    try:
        with pytest.raises(LineageConflict, match="no longer matches"):
            await scheduler._verify_podman_lineage_before_write(config, adapter)
    finally:
        MANAGED_MARKERS.reset(token)


async def test_real_adapter_rechecks_full_id_inside_write_boundary():
    labels = dict(MARKERS)
    original = FakeContainer(
        "0123456789abcdef",
        "service-a",
        FakeImage(tags=["registry.example.test/team/service-a:1.0.0"]),
        attrs={"Config": {"Labels": labels}, "Labels": labels},
    )
    client = FakePodmanClient([original])
    adapter = PodmanRuntimeAdapter(
        RuntimeConnectionConfig(
            name="fictional-podman",
            type="podman",
            config={"socket": "unix:///fictional/podman.sock"},
        ),
        client=client,
    )
    members = await adapter.get_target_lineage_members(
        {"mode": "container", "container_name": "service-a"}, MARKERS
    )
    lineage = {"members": members}
    token = ACTIVE_PODMAN_LINEAGE.set(lineage)
    try:
        await adapter._verify_active_target_lineage(
            {"mode": "container", "container_name": "service-a"}
        )
        replacement = FakeContainer(
            "fedcba9876543210",
            "service-a",
            FakeImage(tags=["registry.example.test/team/service-a:1.0.0"]),
            attrs={"Config": {"Labels": labels}, "Labels": labels},
        )
        client.containers._containers[:] = [replacement]
        with pytest.raises(ValueError, match="changed immediately before mutation"):
            await adapter._verify_active_target_lineage(
                {"mode": "container", "container_name": "service-a"}
            )
    finally:
        ACTIVE_PODMAN_LINEAGE.reset(token)


async def test_real_adapter_rejects_partial_or_foreign_managed_markers():
    partial = {MARKER_KEYS[0]: MARKERS[MARKER_KEYS[0]]}
    container = FakeContainer(
        "0123456789abcdef",
        "service-a",
        FakeImage(tags=["registry.example.test/team/service-a:1.0.0"]),
        attrs={"Config": {"Labels": partial}, "Labels": partial},
    )
    adapter = PodmanRuntimeAdapter(
        RuntimeConnectionConfig(
            name="fictional-podman",
            type="podman",
            config={"socket": "unix:///fictional/podman.sock"},
        ),
        client=FakePodmanClient([container]),
    )
    with pytest.raises(ValueError, match="incomplete"):
        await adapter.get_target_lineage_members(
            {"mode": "container", "container_name": "service-a"}, MARKERS
        )
    foreign = dict(MARKERS) | {MARKER_KEYS[1]: "other-target"}
    container.attrs["Config"]["Labels"] = foreign
    container.attrs["Labels"] = foreign
    with pytest.raises(ValueError, match="do not match"):
        await adapter.get_target_lineage_members(
            {"mode": "container", "container_name": "service-a"}, MARKERS
        )


async def test_real_adapter_collects_every_compose_replica(storage):
    labels = dict(MARKERS)

    def container(identifier, service, number):
        compose = {
            **labels,
            "com.docker.compose.project": "sample-project",
            "com.docker.compose.service": service,
            "com.docker.compose.container-number": str(number),
        }
        return FakeContainer(
            identifier,
            f"sample-project-{service}-{number}",
            FakeImage(tags=["registry.example.test/team/service-a:1.0.0"]),
            attrs={"Config": {"Labels": compose}, "Labels": compose},
        )

    adapter = PodmanRuntimeAdapter(
        RuntimeConnectionConfig(
            name="fictional-podman",
            type="podman",
            config={"socket": "unix:///fictional/podman.sock"},
        ),
        client=FakePodmanClient(
            [container("a-1", "web", 1), container("a-2", "web", 2), container("b-1", "api", 1)]
        ),
    )
    members = await adapter.get_target_lineage_members(
        {"mode": "docker_compose", "project": "sample-project"}, MARKERS
    )
    assert [(item["service"], item["replica"]) for item in members] == [
        ("api", "1"),
        ("web", "1"),
        ("web", "2"),
    ]
    assert all(item["marker_state"] == "managed" for item in members)


async def test_replacement_without_managed_labels_does_not_advance(storage):
    scheduler = ExecutorScheduler(storage)
    adapter = Adapter()
    config = SimpleNamespace(id=7, runtime_type="podman", target_ref={"mode": "container"})
    token = MANAGED_MARKERS.set(MARKERS)
    try:
        pending = await scheduler._verify_podman_lineage_before_write(config, adapter)
        initial = await storage.podman_lineage.establish(
            executor_id=7,
            target_id=pending["target_id"],
            mode="container",
            target_fingerprint=pending["target_fingerprint"],
            members=pending["members"],
        )
        adapter.container_id = "fedcba9876543210"
        with pytest.raises(Exception, match="lacks managed markers"):
            await scheduler._advance_podman_lineage(config, adapter, initial, run_id=99)
    finally:
        MANAGED_MARKERS.reset(token)
    assert (await storage.podman_lineage.get(7))["generation"] == 1
