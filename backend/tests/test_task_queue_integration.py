import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from releasetracker.config import Channel, ExecutorConfig
from releasetracker.models import AggregateTracker, Release, ReleaseChannel, TrackerSource
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.services.fetch_tasks import FetchTasks
from releasetracker.services.deploy_tasks import DeployTasks
from releasetracker.services.task_queue import TaskQueue
from releasetracker.storage.sqlite_deployment_admission import DeploymentAdmissionStore
from releasetracker.trackers.base import BaseTracker
from releasetracker.webhook_scheduler import RepositoryWebhookScheduler
from test_repository_webhook_queue import setup_hook
from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)

pytestmark = pytest.mark.asyncio


class Source(BaseTracker):
    def __init__(self, name, kind):
        super().__init__(name, channels=[Channel(name="stable", type="release")])
        self.kind = kind
        self.failure = False
        self.fallback_calls = 0

    async def fetch_all(self, limit=20, fallback_tags=False):
        if self.failure:
            raise httpx.ReadTimeout("secret=https://user:password@registry.example")
        return [
            Release(
                tracker_name=self.name,
                version="1.0.0",
                tag_name="1.0.0",
                name="Release 1.0.0",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                url="https://example.org/releases/1.0.0",
                type=self.kind,
            )
        ]

    async def fetch_latest(self, fallback_tags=False):
        self.fallback_calls += 1
        return (await self.fetch_all())[0]


async def sources(storage, monkeypatch):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="queue-check",
            sources=[
                TrackerSource(
                    source_key=kind,
                    source_type=kind,
                    source_config=(
                        {"repo": "acme/test"}
                        if kind == "github"
                        else {"image": "acme/test", "registry": "registry.example"}
                    ),
                    release_channels=[
                        ReleaseChannel(release_channel_key="stable", name="stable", type="release")
                    ],
                )
                for kind in ("github", "container")
            ],
        )
    )
    scheduler = ReleaseScheduler(storage)
    adapters = {kind: Source(tracker.name, kind) for kind in ("github", "container")}

    async def factory(config):
        return adapters[config.type]

    monkeypatch.setattr(scheduler, "_create_tracker", factory)
    handler = FetchTasks(storage, scheduler)
    scheduler.fetch_tasks = handler
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("fetch", handler)
    return tracker, scheduler, adapters, handler, queue


async def run_one(storage, queue, kind="fetch"):
    task = await storage.tasks.claim(kind)
    assert task is not None
    await queue._run(task)
    return await storage.tasks.get(task["id"])


async def approve_if_pending(storage, task_id):
    task = await storage.tasks.get(task_id)
    if task["approval_pending"]:
        plan = await DeploymentAdmissionStore(storage).latest(task_id)
        await DeploymentAdmissionStore(storage).approve(
            task_id, plan["id"], plan["fingerprint"], "test-admin"
        )


async def test_real_fetch_partial_retries_without_fallback_or_projection(storage, monkeypatch):
    tracker, scheduler, adapters, handler, queue = await sources(storage, monkeypatch)
    adapters["container"].failure = True
    receipt = await scheduler.check_tracker_now_v2(tracker.name)
    assert receipt["status"] == "queued"
    first = await run_one(storage, queue)
    assert first["state"] == "retry_wait", first
    assert first["attempts"] == 1
    assert first["error_code"] == "upstream_timeout"
    assert "password" not in str(first)
    assert adapters["container"].fallback_calls == 0
    assert await storage.get_tracker_current_releases(tracker.id) == []
    assert len(first["result"]["source_fetch_run_ids"]) == 2
    adapters["container"].failure = False
    monkeypatch.setattr(storage.webhooks, "last_source_run_at", AsyncMock(return_value=None))
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE tasks SET due_at=0 WHERE id=?", (first["id"],))
    second = await run_one(storage, queue)
    assert second["state"] == "succeeded", second
    assert second["attempts"] == 2
    assert await storage.get_tracker_current_releases(tracker.id)


async def test_source_config_change_supersedes_before_io(storage, monkeypatch):
    tracker, scheduler, adapters, handler, queue = await sources(storage, monkeypatch)
    await handler.enqueue(tracker.name)
    changed = tracker.model_copy(update={"enabled": False})
    await storage.update_aggregate_tracker(changed)
    task = await run_one(storage, queue)
    assert task["state"] == "superseded"
    assert task["attempts"] == 0


async def test_complete_empty_fetch_is_no_change_not_retry(storage, monkeypatch):
    tracker, scheduler, adapters, handler, queue = await sources(storage, monkeypatch)
    for adapter in adapters.values():
        adapter.fetch_all = AsyncMock(return_value=[])
    await scheduler._check_tracker(tracker.name)
    task = await run_one(storage, queue)
    assert task["state"] == "no_change", task
    assert task["attempts"] == 1


async def test_webhook_bridge_uses_queue_and_projects_receipt_status(storage, monkeypatch):
    source, hook, event = await setup_hook(storage, "queued-hook")
    await storage.webhooks.receive(hook, "id:queue", "hash", event, "", now=time.time() - 10)
    scheduler = ReleaseScheduler(storage)
    fake = Source("queued-hook", "github")
    monkeypatch.setattr(scheduler, "_create_tracker", AsyncMock(return_value=fake))
    handler = FetchTasks(storage, scheduler)
    worker = RepositoryWebhookScheduler(storage, scheduler, MagicMock())
    worker.fetch_tasks = handler
    await worker.tick()
    tasks = await storage.tasks.list()
    assert len(tasks) == 1
    assert fake.fallback_calls == 0
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("fetch", handler)
    done = await run_one(storage, queue)
    assert done["state"] == "succeeded", done
    await worker.tick()
    delivery = (await storage.webhooks.deliveries(hook["id"]))[0]
    assert delivery["requests"][0]["state"] == "completed"
    assert delivery["requests"][0]["attempts"] == 1
    assert delivery["requests"][0]["source_fetch_run_id"]


async def test_queued_deploy_pins_target_and_retains_run_reference(storage, monkeypatch):
    await save_docker_tracker_config(storage, name="queue-deploy", image="acme/app")
    await seed_docker_release(storage, tracker_name="queue-deploy", version="1.0.0")
    tracker = await storage.get_aggregate_tracker("queue-deploy")
    connection_id = await create_runtime_connection(storage)
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="queue-deploy",
            tracker_name="queue-deploy",
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            runtime_connection_id=connection_id,
            runtime_type="docker",
            target_ref={"mode": "container", "container_id": "app"},
        )
    )
    scheduler = ExecutorScheduler(storage)
    handler = DeployTasks(storage, scheduler)
    from releasetracker.services.deployment_plan import TargetEvidence

    monkeypatch.setattr(
        handler,
        "_collect_admission_evidence",
        AsyncMock(
            return_value=TargetEvidence(
                "test-runtime", "test-target", {"kind": "container"}, (), "container_config"
            )
        ),
    )
    receipt = await handler.enqueue(executor_id, manual=True)
    assert receipt["status"] == "queued"
    task = await storage.tasks.get(receipt["task_id"])
    assert task["payload"]["targets"][0]["target"][0] == "1.0.0"
    assert await handler.enqueue(executor_id, manual=True) == receipt
    await seed_docker_release(storage, tracker_name="queue-deploy", version="2.0.0")
    observed = []

    async def run(executor, **kwargs):
        observed.append(
            await scheduler._resolve_tracker_latest_target(
                tracker.name,
                "stable",
                tracker_source_id=tracker.sources[0].id,
                tracker_source_type="container",
            )
        )
        await storage.set_executor_run_status(kwargs["run_id"], "success")
        return SimpleNamespace(status="success")

    monkeypatch.setattr(scheduler, "_run_executor_with_overlap_guard", run)
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", handler)
    done = await run_one(storage, queue, "deploy")
    await approve_if_pending(storage, receipt["task_id"])
    if done["state"] != "succeeded":
        done = await run_one(storage, queue, "deploy")
    assert done["state"] == "succeeded", done
    assert observed == [("1.0.0", None)]
    assert done["result"]["run_id"]


async def test_worker_does_not_block_other_fetches(storage):
    gate = asyncio.Event()

    class Handler:
        async def prepare(self, task):
            return None

        async def execute(self, task):
            await gate.wait()
            from releasetracker.services.task_queue import TaskResult

            return TaskResult()

    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("fetch", Handler())
    for index in range(4):
        await storage.tasks.enqueue(
            kind="fetch",
            resource_key=f"tracker:{index}",
            dedupe_key=str(index),
            target_label=str(index),
            payload={},
            trigger_mode="manual",
        )
    await asyncio.wait_for(queue.tick(), 1)
    assert len(queue.workers) == 3
    assert len(await storage.tasks.list(state="queued")) == 1
    gate.set()
    await queue.shutdown()


@pytest.mark.parametrize("invalid", [False, True])
async def test_native_deployment_queue_executes_and_distinguishes_preflight_failure(
    storage, invalid
):
    from test_executor_scheduler import FakeAdapter

    await save_docker_tracker_config(storage, name="native-queue", image="nginx")
    await seed_docker_release(storage, tracker_name="native-queue", version="2.0.0")
    tracker = await storage.get_aggregate_tracker("native-queue")
    connection_id = await create_runtime_connection(storage)
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="native-queue",
            tracker_name=tracker.name,
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            runtime_connection_id=connection_id,
            runtime_type="docker",
            target_ref={"mode": "container", "container_id": "app", "invalid": invalid},
        )
    )
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[executor_id] = FakeAdapter(
        await storage.get_runtime_connection(connection_id),
        current_image="nginx:1.0.0",
        storage=storage,
        executor_id=executor_id,
    )
    if invalid:
        scheduler._adapters[executor_id].validate_target_ref = AsyncMock(
            side_effect=ValueError("invalid target")
        )
    handler = DeployTasks(storage, scheduler)
    scheduler.deploy_tasks = handler
    receipt = await scheduler.run_executor_now_async(executor_id)
    assert receipt["task_id"]
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", handler)
    done = await run_one(storage, queue, "deploy")
    if invalid:
        assert done["state"] == "queued", done
        assert done["error_code"] == "admission_evidence_unavailable"
        assert done["attempts"] == 0
        return
    await approve_if_pending(storage, receipt["task_id"])
    if done["state"] != "succeeded":
        done = await run_one(storage, queue, "deploy")
    assert done["state"] == "succeeded", done
    assert done["result"]["mutation_started"] is True
    assert (await storage.get_executor_run(done["result"]["run_id"])).status == "success"


async def test_task_api_redacts_private_payload_and_enforces_cancel_guard(storage, authed_client):
    task = await storage.tasks.enqueue(
        kind="fetch",
        resource_key="sensitive-resource",
        dedupe_key="sensitive-key",
        payload={"tracker_id": 1, "tracker_name": "app", "private": "secret-token"},
        target_label="app",
        trigger_mode="manual",
    )
    response = authed_client.get(f"/api/tasks/{task['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["target"] == {"tracker_id": 1, "tracker_name": "app"}
    assert "secret-token" not in response.text
    assert not {"owner", "lease_until", "payload", "resource_key", "dedupe_key"}.intersection(data)
    running = await storage.tasks.claim("fetch")
    assert authed_client.post(f"/api/tasks/{task['id']}/cancel").status_code == 409
    await storage.tasks.finish(running, "failed")
    assert authed_client.get("/api/tasks?state=failed").json()[0]["id"] == task["id"]


async def test_manual_check_api_returns_202_and_no_remote_io(storage, authed_client, monkeypatch):
    from releasetracker.main import app
    from releasetracker.dependencies import get_scheduler

    tracker, scheduler, adapters, handler, queue = await sources(storage, monkeypatch)
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    for source in adapters.values():
        source.fetch_all = AsyncMock(return_value=[])
    response = authed_client.post(f"/api/trackers/{tracker.name}/check")
    assert response.status_code == 202, response.text
    assert response.json()["task_id"]
    for source in adapters.values():
        source.fetch_all.assert_not_awaited()


async def test_manual_resolution_requires_attestation_and_cannot_bypass_snapshot(
    storage, authed_client
):
    from releasetracker.models import ExecutorSnapshot

    await save_docker_tracker_config(storage, name="resolve-test", image="nginx")
    tracker = await storage.get_aggregate_tracker("resolve-test")
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="resolve-test",
            tracker_name=tracker.name,
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            runtime_connection_id=await create_runtime_connection(storage),
            runtime_type="docker",
            target_ref={"mode": "container", "container_id": "app"},
        )
    )
    task = await storage.tasks.enqueue(
        kind="deploy",
        resource_key="deployment-mutations",
        dedupe_key="resolve",
        target_label="resolve-test",
        payload={"executor_id": executor_id},
        trigger_mode="manual",
    )
    running = await storage.tasks.claim("deploy")
    await storage.tasks.finish(running, "needs_attention")
    url = f"/api/tasks/{task['id']}/resolve"
    assert (
        authed_client.post(url, json={"remote_stopped": False, "state_verified": True}).status_code
        == 422
    )
    await storage.save_executor_snapshot(
        ExecutorSnapshot(executor_id=executor_id, snapshot_data={}, locked=True)
    )
    confirmation = {"remote_stopped": True, "state_verified": True}
    assert authed_client.post(url, json=confirmation).status_code == 409
    assert (await storage.tasks.get(task["id"]))["state"] == "needs_attention"
    db = await storage._get_connection()
    await db.execute("UPDATE executor_snapshots SET locked=0 WHERE executor_id=?", (executor_id,))
    await db.commit()
    response = authed_client.post(url, json=confirmation)
    assert response.status_code == 200, response.text
    done = await storage.tasks.get(task["id"])
    assert done["state"] == "failed"  # Human acknowledgment is never deployment success.
    assert done["result"]["manual_resolution"]["actor"]


async def test_migrated_exhausted_attempt_budget_finishes_without_remote_io(storage):
    task = await storage.tasks.enqueue(
        kind="fetch",
        resource_key="legacy",
        dedupe_key="legacy",
        target_label="legacy",
        payload={},
        trigger_mode="webhook",
        initial_attempts=4,
        max_retries=3,
    )
    assert task["attempts"] == 4
    handler = SimpleNamespace(
        prepare=AsyncMock(return_value=None), execute=AsyncMock(), finished=AsyncMock()
    )
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("fetch", handler)
    done = await run_one(storage, queue)
    assert done["state"] == "failed"
    assert done["error_code"] == "retry_budget_exhausted"
    handler.execute.assert_not_awaited()
