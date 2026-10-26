"""Write receipts must follow preflight and precede the first runtime mutation."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors import docker, podman, podman_compose
from releasetracker.executors.base import RuntimeMutationError
from releasetracker.services import task_effects


def runtime(kind):
    return RuntimeConnectionConfig(
        name="isolated-boundary-test",
        type=kind,
        config={"socket": "unix:///unused-test.sock"},
        secrets={},
    )


@pytest.mark.parametrize("kind", ["docker", "podman", "docker_compose", "podman_compose"])
@pytest.mark.parametrize("failure", ["pull", "preflight", "first_read", "guard", "write"])
async def test_sdk_boundary_after_preflight_before_stop(monkeypatch, kind, failure):
    events = []
    owner_loop = asyncio.get_running_loop()
    owner_thread = threading.get_ident()

    async def preparation():
        events.append("prepare")

    async def guard():
        assert asyncio.get_running_loop() is owner_loop
        assert threading.get_ident() == owner_thread
        events.append("guard")
        if failure == "guard":
            raise ValueError("guard refused")
        events.append("marked")

    def pull(*args):
        events.append("pull")
        if failure == "pull":
            raise ValueError("pull failed")

    def defaults(*args):
        events.append("preflight")
        if failure == "preflight":
            raise ValueError("preflight failed")

    def stop(*args):
        assert events[-1] == "marked"
        events.append("stop")
        raise OSError("write outcome unknown")

    container = SimpleNamespace(
        id="old", attrs={"Pod": "pod-1" if "compose" in kind else ""}, stop=stop
    )

    def first_read(*args):
        if failure == "first_read":
            raise ValueError("initial container read failed")
        return container

    client = SimpleNamespace(
        images=SimpleNamespace(pull=pull), containers=SimpleNamespace(get=first_read)
    )
    cls = podman.PodmanRuntimeAdapter if kind.startswith("podman") else docker.DockerRuntimeAdapter
    adapter = cls(runtime("podman" if kind.startswith("podman") else "docker"), client=client)
    monkeypatch.setattr(adapter, "_get_container", first_read)
    monkeypatch.setattr(adapter, "_extract_image", lambda _: "nginx:1")
    spec = SimpleNamespace(
        create_config={"image": "nginx:2"},
        target_image="nginx:2",
        host_config={},
        container_id="old",
    )
    if kind.startswith("docker"):
        monkeypatch.setattr(adapter, "_build_recreate_spec_from_inspect", lambda *a: spec)
    monkeypatch.setattr(adapter, "_find_compose_service_containers", lambda _: {"web": [container]})
    monkeypatch.setattr(adapter, "_build_grouped_runtime_recreate_specs", lambda *a: [spec])
    if kind.startswith("podman"):
        monkeypatch.setattr(podman, "build_grouped_runtime_recreate_spec", lambda *a, **k: spec)
        monkeypatch.setattr(adapter, "_podman_container_create_config", lambda conf: conf)
        monkeypatch.setattr(adapter, "_apply_podman_host_config_preservation", lambda conf, _: conf)
        monkeypatch.setattr(adapter, "_podman_create_arguments", lambda *a, **k: {})
        monkeypatch.setattr(
            adapter, "_validate_podman_grouped_recreate_specs", lambda *a, **k: None
        )
        monkeypatch.setattr(adapter, "_podman_grouped_create_config_for_spec", lambda *a, **k: {})
        monkeypatch.setattr(
            adapter, "_stop_grouped_container_with_sdk_decode_tolerance", lambda c, v: stop()
        )
    else:
        monkeypatch.setattr(adapter, "_docker_create_arguments", lambda *a: {})
        monkeypatch.setattr(adapter, "_validate_grouped_runtime_recreate_specs", lambda *a: None)
        monkeypatch.setattr(adapter, "_order_grouped_runtime_recreate_specs", lambda specs: specs)
        monkeypatch.setattr(adapter, "_preflight_compose_service_containers", lambda *a: "nginx:1")
        monkeypatch.setattr(adapter, "_resolve_compose_container", first_read)
    module = podman_compose if kind == "podman_compose" else podman if kind == "podman" else docker
    monkeypatch.setattr(module, "verify_existing_volumes", lambda *a: None)
    monkeypatch.setattr(module, "verify_image_defaults", defaults)
    prep_token = task_effects.PREPARATION_GUARD.set(preparation)
    marker_token = task_effects.MUTATION_GUARD.set(guard)
    owner_token = task_effects._MUTATION_OWNER.set(None)
    try:
        await task_effects.prepare_deployment_mutation()
        if "compose" in kind:
            operation = adapter.update_compose_services(
                {"mode": "docker_compose", "project": "isolated"}, {"web": "nginx:2"}
            )
        else:
            operation = adapter.update_image({"container_name": "isolated"}, "nginx:2")
        expected = RuntimeMutationError if failure == "write" else ValueError
        with pytest.raises(expected):
            await operation
        assert events[0] == "prepare"
        assert ("marked" in events) is (failure == "write")
        assert ("stop" in events) is (failure == "write")
        if failure in {"guard", "write"}:
            assert events.index("pull") < events.index("preflight") < events.index("guard")
    finally:
        task_effects._MUTATION_OWNER.reset(owner_token)
        task_effects.MUTATION_GUARD.reset(marker_token)
        task_effects.PREPARATION_GUARD.reset(prep_token)
        await adapter.close()


async def test_cancelled_owner_cannot_start_write_after_preflight():
    pulling = threading.Event()
    release_pull = threading.Event()
    writes = []

    async def guard():
        writes.append("marked")

    async def worker_operation():
        def run():
            async def operation():
                pulling.set()
                assert release_pull.wait(5)
                await task_effects.mark_deployment_mutation()
                writes.append("write")

            return asyncio.run(operation())

        from releasetracker.executors.adapter_lifetime import wait_for_runtime_worker

        await task_effects.prepare_deployment_mutation()
        await wait_for_runtime_worker(asyncio.create_task(asyncio.to_thread(run)))

    marker_token = task_effects.MUTATION_GUARD.set(guard)
    task = asyncio.create_task(worker_operation())
    try:
        assert await asyncio.to_thread(pulling.wait, 5)
        task.cancel()
        release_pull.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writes == []
    finally:
        release_pull.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        task_effects.MUTATION_GUARD.reset(marker_token)
