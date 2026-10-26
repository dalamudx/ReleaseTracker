"""API commit boundaries and atomic Portainer grouped validation."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
from releasetracker.executors.portainer import PortainerRuntimeAdapter
from releasetracker import executor_scheduler_grouped_runtime_portainer as grouped_portainer
from releasetracker.services import deployment_diff, task_effects


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet", "portainer"])
@pytest.mark.parametrize("failure", ["preflight", "guard", "write"])
async def test_api_commit_records_boundary_after_preflight(monkeypatch, kind, failure):
    events = []
    owner_loop = asyncio.get_running_loop()
    owner_thread = threading.get_ident()

    async def guard():
        assert asyncio.get_running_loop() is owner_loop
        assert threading.get_ident() == owner_thread
        events.append("guard")
        if failure == "guard":
            raise ValueError("approval changed")
        events.append("marked")

    async def verify(*args):
        events.append("verify")
        if failure == "preflight":
            raise ValueError("configuration changed")
        return {"workload": {"metadata": {"uid": "isolated", "resourceVersion": "7"}}}

    def patch(name, namespace, body):
        assert events[-1] == "marked"
        assert body["metadata"] == {"uid": "isolated", "resourceVersion": "7"}
        events.append("write")
        raise OSError("commit outcome unknown")

    monkeypatch.setattr(deployment_diff, "verify_update_state", verify)
    if kind == "portainer":
        runtime = RuntimeConnectionConfig(
            name="isolated-stack",
            type="portainer",
            credential_id=1,
            config={"base_url": "https://unused.invalid", "endpoint_id": 1},
            secrets={"api_key": "isolated-fictional"},
        )
        adapter = PortainerRuntimeAdapter(runtime, client=SimpleNamespace())
        target = {
            "mode": "portainer_stack",
            "endpoint_id": 1,
            "stack_id": 2,
            "stack_name": "isolated",
            "stack_type": "standalone",
        }
        monkeypatch.setattr(adapter, "fetch_stack_detail", AsyncMock(return_value={"Type": 2}))
        monkeypatch.setattr(adapter, "_resolve_unsupported_stack_kind_reason", lambda _: None)
        monkeypatch.setattr(
            adapter,
            "fetch_stack_file",
            AsyncMock(return_value="services:\n  web:\n    image: nginx:1\n"),
        )

        async def commit(**kwargs):
            assert events[-1] == "marked"
            events.append("write")
            raise OSError("commit outcome unknown")

        monkeypatch.setattr(adapter, "_update_stack", commit)

        def operation():
            return adapter.update_stack_services(target, {"web": "nginx:2"})

    else:
        runtime = RuntimeConnectionConfig(
            name="isolated-workload",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        )
        api = SimpleNamespace(
            patch_namespaced_deployment=patch,
            patch_namespaced_stateful_set=patch,
            patch_namespaced_daemon_set=patch,
        )
        adapter = KubernetesRuntimeAdapter(runtime, apps_api=api)
        target = {
            "mode": "kubernetes_workload",
            "namespace": "apps",
            "kind": kind,
            "name": "isolated",
        }
        monkeypatch.setattr(
            adapter, "fetch_workload_service_images", AsyncMock(return_value={"web": "nginx:1"})
        )

        def operation():
            return adapter.update_workload_services(target, {"web": "nginx:2"})

    marker_token = task_effects.MUTATION_GUARD.set(guard)
    owner_token = task_effects._MUTATION_OWNER.set(None)
    try:
        await task_effects.prepare_deployment_mutation()
        with pytest.raises(OSError if failure == "write" else ValueError):
            await operation()
        assert events[0] == "verify"
        assert ("marked" in events) is (failure == "write")
        assert ("write" in events) is (failure == "write")
    finally:
        task_effects._MUTATION_OWNER.reset(owner_token)
        task_effects.MUTATION_GUARD.reset(marker_token)
        if kind == "portainer":
            await adapter.close()


@pytest.mark.parametrize("bad_binding_first", [False, True])
async def test_portainer_any_failed_binding_aborts_entire_pending_set(
    monkeypatch, bad_binding_first
):
    runtime = RuntimeConnectionConfig(
        name="isolated-stack",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://unused.invalid", "endpoint_id": 1},
        secrets={"api_key": "isolated-fictional"},
    )
    adapter = PortainerRuntimeAdapter(runtime, client=SimpleNamespace())
    adapter.validate_target_ref = AsyncMock()
    adapter.fetch_stack_service_images = AsyncMock(
        return_value={"web": "nginx:1", "bad": "nginx:1"}
    )
    adapter.update_stack_services = AsyncMock()
    scheduler = grouped_portainer.ExecutorSchedulerPortainerRuntime()
    scheduler.storage = SimpleNamespace(
        get_runtime_connection=AsyncMock(return_value=runtime),
        get_tracker_config=AsyncMock(return_value=SimpleNamespace(name="tracker")),
    )
    monkeypatch.setattr(
        grouped_portainer,
        "materialize_runtime_connection_credentials",
        AsyncMock(return_value=runtime),
    )
    scheduler._get_adapter = lambda *a: adapter
    bindings = [
        SimpleNamespace(service="web", tracker_source_id=1, channel_name="stable"),
        SimpleNamespace(service="bad", tracker_source_id=2, channel_name="stable"),
    ]
    scheduler._build_executor_binding_contexts = lambda _: (
        list(reversed(bindings)) if bad_binding_first else bindings
    )

    async def resolve(source_id):
        return (
            ("tracker", SimpleNamespace(id=1, source_type="container")) if source_id == 1 else None
        )

    scheduler._resolve_tracker_binding_by_source_id = resolve
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("2", None))
    scheduler._build_target_image = lambda **kwargs: "nginx:2"
    scheduler._supports_persisted_full_config_snapshots = Mock(return_value=False)
    scheduler._capture_pre_update_snapshot = AsyncMock()
    scheduler._summarize_portainer_stack_run = lambda results: ("failed", "validation failed")
    scheduler._build_grouped_run_diagnostics = lambda mode, results, **kwargs: {"results": results}
    scheduler._manual_rollback_diagnostics = lambda **kwargs: {}
    scheduler._summarize_portainer_stack_image_versions = lambda results: (None, None)
    scheduler._finalize_run = AsyncMock()
    executor = SimpleNamespace(
        id=1, enabled=True, runtime_connection_id=1, target_ref={"mode": "portainer_stack"}
    )
    await scheduler._execute_portainer_stack_executor(executor, manual=True, _run_id=1)
    adapter.update_stack_services.assert_not_awaited()
    scheduler._capture_pre_update_snapshot.assert_not_awaited()
    scheduler._supports_persisted_full_config_snapshots.assert_not_called()
    results = scheduler._finalize_run.call_args.kwargs["diagnostics"]["results"]
    assert {item.service: item.status for item in results} == {"web": "failed", "bad": "failed"}
    assert "binding failed" in next(item.message for item in results if item.service == "web")
