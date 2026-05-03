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
