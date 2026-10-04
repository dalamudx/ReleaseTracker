import pytest

from helpers.executor_runtime import create_runtime_connection, save_docker_tracker_config
from releasetracker.config import ExecutorConfig
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.scheduler_host import SchedulerHost


async def _get_primary_source_id(storage, tracker_name: str) -> int:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    assert aggregate_tracker.sources
    primary_source = aggregate_tracker.sources[0]
    assert primary_source.id is not None
    return primary_source.id


@pytest.mark.asyncio
async def test_release_and_executor_schedulers_share_one_scheduler_host(storage):
    tracker_name = "shared-host-app"
    tracker_interval_minutes = 7
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        image="ghcr.io/acme/shared-host-app",
        interval=tracker_interval_minutes,
    )

    runtime_connection_id = await create_runtime_connection(storage)
    tracker_source_id = await _get_primary_source_id(storage, tracker_name)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="shared-host-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_connection_id,
            tracker_name=tracker_name,
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "shared-host-container"},
        )
    )

    scheduler_host = SchedulerHost()
    release_scheduler = ReleaseScheduler(storage, scheduler_host=scheduler_host)
    executor_scheduler = ExecutorScheduler(storage, scheduler_host=scheduler_host)

    await release_scheduler.initialize()
    await executor_scheduler.initialize()

    assert release_scheduler.scheduler_host.scheduler is scheduler_host.scheduler
    assert executor_scheduler.scheduler_host.scheduler is scheduler_host.scheduler

    tracker_job = scheduler_host.get_job("tracker", tracker_name)
    assert tracker_job is not None
    assert tracker_job.id == scheduler_host.namespaced_job_id("tracker", tracker_name)
    assert tracker_job.trigger.interval.total_seconds() == tracker_interval_minutes * 60

    executor_job = scheduler_host.get_job("executor", executor_id)
    assert executor_job is None

    desired_state_reconcile_job = scheduler_host.get_job("executor", "desired_state_reconcile")
    assert desired_state_reconcile_job is None

    await executor_scheduler.start()

    desired_state_reconcile_job = scheduler_host.get_job("executor", "desired_state_reconcile")
    assert desired_state_reconcile_job is not None
    assert desired_state_reconcile_job.id == scheduler_host.namespaced_job_id(
        "executor", "desired_state_reconcile"
    )
    assert desired_state_reconcile_job.trigger.interval.total_seconds() == 30

    await release_scheduler.remove_tracker(tracker_name)
    await executor_scheduler.remove_executor(executor_id)

    assert scheduler_host.get_job("tracker", tracker_name) is None
    assert scheduler_host.get_job("executor", executor_id) is None

    await executor_scheduler.shutdown()
    await scheduler_host.shutdown()


def test_worker_poll_setting_only_changes_short_worker_intervals(monkeypatch):
    monkeypatch.setenv("RELEASETRACKER_WORKER_POLL_SECONDS", "5")
    host = SchedulerHost()

    async def tick():
        pass

    for namespace in (
        "tasks",
        "readiness",
        "executor_notifications",
        "deployment_admission_notifications",
        "repository_webhooks",
    ):
        host.add_interval_job(namespace, "tick", tick, seconds=2)
        assert host.get_job(namespace, "tick").trigger.interval.total_seconds() == 5
    host.add_interval_job("repository_webhooks", "cleanup", tick, seconds=86400)
    assert host.get_job("repository_webhooks", "cleanup").trigger.interval.total_seconds() == 86400
    host.add_interval_job("tracker", "check", tick, seconds=2)
    assert host.get_job("tracker", "check").trigger.interval.total_seconds() == 2


@pytest.mark.parametrize("value", ["0", "61", "nan", "2.5", ""])
def test_worker_poll_setting_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("RELEASETRACKER_WORKER_POLL_SECONDS", value)
    with pytest.raises(ValueError, match="RELEASETRACKER_WORKER_POLL_SECONDS"):
        SchedulerHost()


@pytest.mark.asyncio
async def test_pause_drains_submitted_jobs_without_cancelling_them():
    import asyncio

    host = SchedulerHost()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def job():
        entered.set()
        await finish.wait()

    worker = asyncio.create_task(host._tracked(job)())
    await entered.wait()
    host.pause()
    with pytest.raises(TimeoutError):
        await host.drain(timeout=0.01)
    assert not worker.done()
    skipped = False

    async def should_skip():
        nonlocal skipped
        skipped = True

    await host._tracked(should_skip)()
    assert not skipped
    finish.set()
    await host.drain()
    await worker
    assert not host._active
    host.resume()
    await host._tracked(should_skip)()
    assert skipped


def test_worker_poll_setting_preserves_default(monkeypatch):
    monkeypatch.delenv("RELEASETRACKER_WORKER_POLL_SECONDS", raising=False)
    assert SchedulerHost().worker_poll_seconds == 2
