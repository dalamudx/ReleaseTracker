from dataclasses import replace

import pytest

from releasetracker.services.version_policy import version_policy_reason
from releasetracker.services.deployment_plan import TargetEvidence, managed_markers
from releasetracker.storage.sqlite_deployment_admission import DeploymentAdmissionStore


def evidence(current="1.2.3", recovery="container_config"):
    config = (
        {"current_image": f"app:{current}"}
        if recovery == "container_config"
        else {"current_chart_version": current}
    )
    return TargetEvidence("runtime", "target", config, ({},), recovery)


def task(policy="patch", after="1.2.4", manual=False):
    return {
        "payload": {
            "auto_update_policy": policy,
            "manual": manual,
            "targets": [{"target": [after, None], "chart_version": after}],
        }
    }


@pytest.mark.parametrize(
    "policy,before,after,allowed",
    [
        ("patch", "1.2.3", "1.2.4", True),
        ("patch", "1.2.3", "1.3.0", False),
        ("minor", "1.2.3", "1.3.0", True),
        ("minor", "1.2.3", "2.0.0", False),
        ("minor", "v1.2.3", "v1.3.0", True),
        ("patch", "1.2.3", "1.2.3", True),
        ("patch", "1.2.3", "1.2.2", False),
        ("minor", "latest", "1.3.0", False),
        ("minor", "1.2.3", "1.3.0-beta.1", False),
        ("patch", "1.2.3", "latest", False),
        ("all", "latest", "latest", True),
    ],
)
@pytest.mark.parametrize("recovery", ["container_config", "helm_revision"])
def test_limits(policy, before, after, allowed, recovery):
    assert (
        version_policy_reason(task(policy, after), evidence(before, recovery)) is None
    ) == allowed


def test_manual_and_unknown_digest():
    assert version_policy_reason(task(after="2.0.0", manual=True), evidence()) is None
    assert version_policy_reason(
        task(), replace(evidence(), configuration={"current_image": "app@sha256:abc"})
    )


def test_group_policy_checks_only_bound_services():
    queued = task()
    queued["payload"]["targets"][0].update(source_id=1, channel="stable")
    queued["payload"]["policy_bindings"] = [
        {"service": "app", "tracker_source_id": 1, "channel_name": "stable"}
    ]
    observed = replace(
        evidence(),
        recovery="workload_images",
        configuration={"current_services": {"app": "app:1.2.3", "sidecar": "other:latest"}},
    )
    assert version_policy_reason(queued, observed) is None
    assert version_policy_reason(queued, replace(observed, configuration={"current_services": {}}))


@pytest.mark.asyncio
async def test_version_policy_survives_create_update_and_default(storage):
    from releasetracker.config import ExecutorConfig
    from helpers.executor_runtime import create_runtime_connection, save_docker_tracker_config

    await save_docker_tracker_config(storage, name="policy-store", image="app")
    tracker = await storage.get_aggregate_tracker("policy-store")
    runtime = await create_runtime_connection(storage)
    config = ExecutorConfig(
        name="policy-store",
        runtime_type="docker",
        runtime_connection_id=runtime,
        tracker_name="policy-store",
        tracker_source_id=tracker.sources[0].id,
        channel_name="stable",
        target_ref={"mode": "container", "container_id": "app"},
        auto_update_policy="patch",
    )
    executor_id = await storage.create_executor_config(config)
    saved = await storage.get_executor_config(executor_id)
    assert saved.auto_update_policy == "patch"
    saved.auto_update_policy = "minor"
    await storage.update_executor_config(executor_id, saved)
    assert (await storage.get_executor_config(executor_id)).auto_update_policy == "minor"
    assert (
        ExecutorConfig.model_validate(
            config.model_dump(exclude={"auto_update_policy"})
        ).auto_update_policy
        == "all"
    )


@pytest.mark.asyncio
async def test_managed_baseline_still_requires_major_approval(storage):
    store = DeploymentAdmissionStore(storage)
    payload = task("minor", "2.0.0")["payload"] | {"executor_id": 1, "config_identity": "config"}
    queued = await storage.tasks.enqueue(
        kind="deploy",
        resource_key="deployment-mutations",
        dedupe_key="policy:1",
        target_label="app",
        trigger_mode="automatic",
        payload=payload,
    )
    claimed = await storage.tasks.claim("deploy")
    observed = evidence()
    plan = await store.stage(claimed, observed)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    installation = await store.installation_id()
    observed = replace(
        observed, markers=(managed_markers(installation, plan["target_id"], claimed["id"]),)
    )
    async with storage.tasks.transaction() as db:
        await db.execute(
            "UPDATE managed_targets SET baseline=? WHERE executor_id=1", (observed.evidence_hash,)
        )
    # Changed evidence invalidates the first unmanaged approval. A managed target
    # must not silently auto-authorize a major version.
    plan = await store.stage(claimed, observed)
    assert plan["state"] == "pending"
    assert plan["reason"] == "version_policy_requires_approval"
    assert (await storage.tasks.get(queued["id"]))["attempts"] == 0
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    assert (await store.stage(claimed, observed))["state"] == "approved"
    await storage.tasks.start_attempt(claimed)
    assert (await store.verify_before_write(claimed, observed))["approved_by"] == "admin"
