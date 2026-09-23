"""Admission uses the same saved runtime credential as target discovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.models import Credential
from releasetracker.services.deploy_tasks import DeployTasks


@pytest.mark.asyncio
async def test_kubernetes_admission_loads_saved_credential_before_read_only_evidence(storage):
    kubeconfig = "apiVersion: v1\nclusters: []\ncontexts: []\n"
    credential_id = await storage.create_credential(
        Credential(
            name="example-cluster",
            type="kubernetes_runtime",
            secrets={"kubeconfig": kubeconfig},
        )
    )
    connection_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="example-runtime",
            type="kubernetes",
            credential_id=credential_id,
            config={"namespaces": ["example"]},
        )
    )
    saved = await storage.get_runtime_connection(connection_id)
    assert saved.secrets == {}
    assert saved.credential_id == credential_id

    adapter = SimpleNamespace(
        validate_target_ref=AsyncMock(),
        get_managed_markers=AsyncMock(return_value=({},)),
        fetch_workload_service_images=AsyncMock(
            return_value={"api": "registry.example.test/team/service-a:1.2.0"}
        ),
    )
    scheduler = MagicMock()

    def get_adapter(_executor_id, connection):
        assert connection.secrets["kubeconfig"] == kubeconfig
        return adapter

    scheduler._get_adapter.side_effect = get_adapter
    handler = DeployTasks(storage, scheduler)
    target_ref = {
        "mode": "kubernetes_workload",
        "kind": "Deployment",
        "namespace": "example",
        "name": "service-a",
    }
    executor = SimpleNamespace(id=7, runtime_connection_id=connection_id, target_ref=target_ref)
    evidence = await handler._collect_admission_evidence(executor)
    assert evidence.configuration["current_services"] == {
        "api": "registry.example.test/team/service-a:1.2.0"
    }
    assert kubeconfig not in repr(evidence)
    adapter.validate_target_ref.assert_awaited_once_with(target_ref)
    adapter.get_managed_markers.assert_awaited_once_with(target_ref)
