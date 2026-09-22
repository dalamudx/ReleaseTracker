"""Admission evidence is synthetic; these tests never contact a real runtime."""

import json
import time
from dataclasses import replace

import pytest

from releasetracker.services.deployment_plan import TargetEvidence, managed_markers
from releasetracker.storage.sqlite_deployment_admission import (
    AdmissionConflict,
    DeploymentAdmissionStore,
)

pytestmark = pytest.mark.asyncio


def evidence(**overrides):
    return TargetEvidence(
        **(
            {
                "runtime_identity": "synthetic-daemon-a",
                "target_identity": "service-a",
                "configuration": {
                    "image": "registry.example.test/team/service-a:1.0.0",
                    "env": ["PASSWORD=fictional-only"],
                },
                "markers": ({},),
                "recovery": "container_config",
            }
            | overrides
        )
    )


async def task(storage, executor_id=1):
    return await storage.tasks.enqueue(
        kind="deploy",
        resource_key="deployment-mutations",
        dedupe_key=f"deploy:{executor_id}",
        target_label="Example executor",
        trigger_mode="manual",
        max_retries=0,
        payload={
            "executor_id": executor_id,
            "config_identity": "synthetic-config",
            "targets": [{"target": ["1.1.0", "sha256:" + "a" * 64]}],
        },
    )


async def staged(storage, **overrides):
    store = DeploymentAdmissionStore(storage)
    queued = await task(storage)
    claimed = await storage.tasks.claim("deploy")
    observed = evidence(**overrides)
    plan = await store.stage(claimed, observed)
    return store, queued, observed, plan


async def test_parked_task_survives_restart_without_attempt_or_global_lock(storage):
    store, queued, _, plan = await staged(storage)
    parked = await storage.tasks.get(queued["id"])
    assert parked["state"] == "awaiting_approval"
    assert parked["attempts"] == 0
    assert parked["owner"] is None
    assert plan["state"] == "pending"
    assert await storage.tasks.claim("deploy") is None
    assert await storage.tasks.recover(startup=True) == 0
    assert await storage.tasks.clear_finished() == 0
    assert len(await storage.tasks.list(state="awaiting_approval")) == 1
    assert await storage.tasks.list(state="queued") == []
    duplicate = await task(storage)
    assert duplicate["id"] == queued["id"]
    assert duplicate["state"] == "awaiting_approval"
    other = await task(storage, 2)
    assert (await storage.tasks.claim("deploy"))["id"] == other["id"]
    assert (await store.latest(queued["id"]))["id"] == plan["id"]


async def test_approval_is_idempotent_and_only_queues(storage):
    store, queued, observed, plan = await staged(storage)
    for _ in range(2):
        result = await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
        assert result["approved_by"] == "admin"
    ready = await storage.tasks.get(queued["id"])
    assert ready["state"] == "queued" and ready["attempts"] == 0
    claimed = await storage.tasks.claim("deploy")
    admitted = await store.stage(claimed, observed)
    assert admitted["state"] == "approved"
    await storage.tasks.start_attempt(claimed)
    assert (await store.verify_before_write(claimed, observed))["id"] == plan["id"]


@pytest.mark.parametrize("change", ["configuration", "identity", "marker", "artifact"])
async def test_changed_plan_never_uses_prior_approval(storage, change):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    if change == "configuration":
        observed = replace(observed, configuration={"image": "changed"})
    elif change == "identity":
        observed = replace(observed, runtime_identity="different-daemon")
    elif change == "marker":
        observed = replace(
            observed, markers=({"releasetracker.io/managed-by": "another-installation"},)
        )
    else:
        claimed["payload"]["targets"] = [{"target": ["1.2.0", None]}]
    with pytest.raises(AdmissionConflict):
        await store.verify_before_write(claimed, observed)


async def test_expired_approval_and_stale_fingerprint_rejected(storage):
    store, queued, _, plan = await staged(storage)
    with pytest.raises(AdmissionConflict):
        await store.approve(queued["id"], plan["id"], "incorrect", "admin")
    with pytest.raises(AdmissionConflict):
        await store.approve(
            queued["id"], plan["id"], plan["fingerprint"], "admin", now=plan["expires_at"]
        )
    assert (await storage.tasks.get(queued["id"]))["state"] == "awaiting_approval"


@pytest.mark.parametrize(
    "markers",
    [
        ({"releasetracker.io/managed-by": "foreign"},),
        ({"releasetracker.io/target-id": "foreign-target"},),
        ({"releasetracker.io/schema": "99"},),
        ({}, {"releasetracker.io/managed-by": "foreign"}),
    ],
)
async def test_conflicting_markers_block_even_if_some_services_unmarked(storage, markers):
    store, queued, _, plan = await staged(storage, markers=markers)
    assert plan["state"] == "blocked"
    with pytest.raises(AdmissionConflict):
        await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")


async def test_cancel_invalidates_approval_and_intent_is_secret_free(storage):
    store, queued, _, plan = await staged(storage)
    db = await storage._get_connection()
    rows = await (await db.execute("SELECT * FROM deployment_admission_events")).fetchall()
    assert len(rows) == 1
    assert rows[0]["event"] == "executor_approval_required"
    assert "fictional-only" not in str(dict(rows[0]))
    assert "PASSWORD" not in json.dumps(plan)
    assert await storage.tasks.cancel(queued["id"])
    assert (await store.latest(queued["id"]))["state"] == "cancelled"
    with pytest.raises(AdmissionConflict):
        await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")


async def test_write_check_requires_live_lease_and_nonmutating_checkpoint(storage):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    with pytest.raises(AdmissionConflict):
        await store.verify_before_write(claimed, observed, now=time.time() + 3600)
    await storage.tasks.checkpoint(claimed, {"mutation_started": True})
    with pytest.raises(AdmissionConflict):
        await store.verify_before_write(claimed, observed)


async def test_different_executor_cannot_claim_same_target(storage):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    assert await storage.tasks.cancel(queued["id"])
    second = await task(storage, 2)
    claimed = await storage.tasks.claim("deploy")
    conflict = await store.stage(claimed, observed)
    assert conflict["reason"] == "target_already_owned"
    with pytest.raises(AdmissionConflict):
        await store.approve(second["id"], conflict["id"], conflict["fingerprint"], "admin")


async def test_copied_marker_is_not_authority_without_local_claim(storage):
    store = DeploymentAdmissionStore(storage)
    markers = managed_markers(await store.installation_id(), "copied-target", 3)
    _, _, _, plan = await staged(storage, markers=(markers,))
    assert plan["state"] == "blocked"


async def test_approval_api_requires_login(client):
    assert client.get("/api/tasks/1/deployment-plan").status_code == 401
    assert (
        client.post(
            "/api/tasks/1/approve",
            json={
                "plan_id": 1,
                "fingerprint": "unknown",
                "plan_reviewed": True,
            },
        ).status_code
        == 401
    )


async def test_approval_api_reviews_exact_plan_and_only_queues(storage, authed_client):
    _, queued, _, plan = await staged(storage)
    url = f"/api/tasks/{queued['id']}"
    response = authed_client.get(url + "/deployment-plan")
    assert response.status_code == 200
    assert response.json()["fingerprint"] == plan["fingerprint"]
    assert "fictional-only" not in response.text
    body = {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "plan_reviewed": False}
    assert authed_client.post(url + "/approve", json=body).status_code == 422
    body["plan_reviewed"] = True
    assert authed_client.post(url + "/approve", json=body).status_code == 202
    assert authed_client.post(url + "/approve", json=body).status_code == 202
    current = await storage.tasks.get(queued["id"])
    assert current["state"] == "queued" and current["attempts"] == 0
    assert (await DeploymentAdmissionStore(storage).latest(queued["id"]))["approved_by"] == "admin"
    body["fingerprint"] = "changed"
    assert authed_client.post(url + "/approve", json=body).status_code == 409


async def test_configuration_reversion_does_not_resurrect_old_approval(storage):
    store, queued, original, first = await staged(storage)
    await store.approve(queued["id"], first["id"], first["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    changed = replace(original, configuration={"image": "different"})
    second = await store.stage(claimed, changed)
    assert second["state"] == "pending"
    await store.approve(queued["id"], second["id"], second["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    third = await store.stage(claimed, original)
    assert third["id"] > second["id"] > first["id"]
    assert third["state"] == "pending"
    assert third["fingerprint"] == first["fingerprint"]
    with pytest.raises(AdmissionConflict):
        await store.approve(queued["id"], first["id"], first["fingerprint"], "admin")
    assert (await store.approve(queued["id"], third["id"], third["fingerprint"], "admin"))[
        "state"
    ] == "approved"


async def test_queue_handoff_never_starts_attempt_or_remote_execution(storage):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from releasetracker.services.task_queue import TaskQueue, TaskResult

    store = DeploymentAdmissionStore(storage)
    queued = await task(storage)

    async def prepare(claimed):
        await store.stage(claimed, evidence())
        return TaskResult("awaiting_approval")

    execute = AsyncMock()
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", SimpleNamespace(prepare=prepare, execute=execute))
    await queue._run(await storage.tasks.claim("deploy"))
    execute.assert_not_awaited()
    current = await storage.tasks.detail(queued["id"])
    assert current["state"] == "awaiting_approval"
    assert current["attempt_history"] == []


@pytest.mark.parametrize("approved", [False, True])
async def test_migration_downgrade_cancels_parked_work(storage, approved):
    from pathlib import Path
    from db_helpers import dbmate_migrations_dir

    store, queued, _, plan = await staged(storage)
    if approved:
        await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    migration = Path(dbmate_migrations_dir()) / "20260922000001_managed_deployment.sql"
    down = migration.read_text().split("-- migrate:down", 1)[1]
    async with storage.tasks.transaction() as db:
        await db.executescript(down)
    assert (await storage.tasks.get(queued["id"]))["state"] == "cancelled"


async def test_expired_approval_keeps_audit_and_requires_new_plan(storage):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    now = plan["expires_at"] + 1
    claimed = await storage.tasks.claim("deploy", now=now)
    replacement = await store.stage(claimed, observed, now=now)
    assert replacement["state"] == "pending" and replacement["id"] != plan["id"]
    db = await storage._get_connection()
    old = await (
        await db.execute("SELECT * FROM deployment_plans WHERE id=?", (plan["id"],))
    ).fetchone()
    assert old["state"] == "superseded" and old["approved_by"] == "admin"
    assert old["approved_at"] is not None
    assert (await storage.tasks.get(queued["id"]))["state"] == "awaiting_approval"


async def test_downgrade_refuses_active_admitted_deployment(storage):
    from pathlib import Path
    from db_helpers import dbmate_migrations_dir
    import sqlite3

    store, queued, _, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    await storage.tasks.claim("deploy")
    migration = Path(dbmate_migrations_dir()) / "20260922000001_managed_deployment.sql"
    down = migration.read_text().split("-- migrate:down", 1)[1]
    with pytest.raises(sqlite3.IntegrityError):
        async with storage.tasks.transaction() as db:
            await db.executescript(down)
    assert (await store.latest(queued["id"]))["state"] == "approved"
    assert (await storage.tasks.get(queued["id"]))["state"] == "running"


async def test_second_executor_concurrent_approval_cannot_steal_target(storage):
    store, first_task, observed, first = await staged(storage)
    second_task = await task(storage, 2)
    second = await store.stage(await storage.tasks.claim("deploy"), observed)
    assert second["state"] == "pending"
    await store.approve(first_task["id"], first["id"], first["fingerprint"], "admin")
    with pytest.raises(AdmissionConflict, match="target_already_owned"):
        await store.approve(second_task["id"], second["id"], second["fingerprint"], "admin")


async def test_stage_rolls_back_park_if_notification_intent_fails(storage):
    store = DeploymentAdmissionStore(storage)
    queued = await task(storage)
    claimed = await storage.tasks.claim("deploy")
    db = await storage._get_connection()
    await db.execute(
        "CREATE TRIGGER reject_admission BEFORE INSERT ON deployment_admission_events BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    await db.commit()
    with pytest.raises(Exception, match="injected"):
        await store.stage(claimed, evidence())
    assert await store.latest(queued["id"]) is None
    assert (await storage.tasks.get(queued["id"]))["state"] == "running"
