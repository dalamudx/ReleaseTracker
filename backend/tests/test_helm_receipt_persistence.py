"""Runtime artifact receipts must survive the same model/storage path as production."""

import pytest

from releasetracker.config import (
    ExecutorConfig,
    RuntimeConnectionConfig,
    normalize_executor_target_ref,
)
from test_helm_fact_contract import adapter_with


@pytest.mark.parametrize("revision", [0, -1, True, "34", 1.5])
def test_helm_revision_rejects_nonpositive_or_noninteger_receipts(revision):
    with pytest.raises(ValueError):
        normalize_executor_target_ref(
            {
                "mode": "helm_release",
                "namespace": "apps",
                "release_name": "demo",
                "chart_revision": revision,
            },
            runtime_type="kubernetes",
        )


async def test_saved_helm_receipt_survives_storage_roundtrip_and_verifies_digest(storage):
    connection = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="isolated-helm",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        )
    )
    ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "demo",
        "chart_name": "demo",
        "chart_version": "1.0.0",
    }
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="isolated-chart",
            tracker_name="isolated",
            runtime_type="kubernetes",
            runtime_connection_id=connection,
            target_ref=ref,
        )
    )
    receipt = ref | {
        "chart_version": "1.1.0",
        "chart_digest": "sha256:" + "a" * 64,
        "chart_revision": 34,
    }
    await storage.update_executor_target_ref(executor_id, receipt)
    reloaded = await storage.get_executor_config(executor_id)
    assert reloaded.target_ref["chart_revision"] == 34
    adapter = adapter_with({"chart": "demo", "chart_version": "1.1.0", "revision": 34})
    assert await adapter.get_helm_release_digest(reloaded.target_ref) == "sha256:" + "a" * 64
    # A later external Helm revision invalidates the saved archive proof.
    changed = adapter_with({"chart": "demo", "chart_version": "1.1.0", "revision": 35})
    assert await changed.get_helm_release_digest(reloaded.target_ref) is None
