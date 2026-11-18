import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from releasetracker.services.runtime_health_watch import RuntimeHealthWatch


def policy_storage(values: dict[str, str | None]) -> MagicMock:
    storage = MagicMock()
    storage.get_setting = AsyncMock(side_effect=values.get)
    return storage


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "1", "299", "86401", "bad", "-1"])
async def test_invalid_watch_interval_keeps_the_watch_disabled(value):
    host = MagicMock()
    watch = RuntimeHealthWatch(
        policy_storage({"system.runtime_health_interval_seconds": value}),
        MagicMock(),
        MagicMock(),
        host,
    )
    await watch.initialize()
    host.add_interval_job.assert_not_called()
    assert watch.interval == 0


@pytest.mark.asyncio
async def test_disabled_watch_registers_no_background_work():
    host = MagicMock()
    watch = RuntimeHealthWatch(policy_storage({}), MagicMock(), MagicMock(), host)
    await watch.initialize()
    await watch.tick()
    await watch.shutdown()
    host.add_interval_job.assert_not_called()
    assert watch.worker is None


@pytest.mark.asyncio
async def test_enabled_watch_registration_and_shutdown():
    host = MagicMock()
    watch = RuntimeHealthWatch(
        policy_storage({"system.runtime_health_interval_seconds": "300"}),
        MagicMock(),
        MagicMock(),
        host,
    )
    await watch.initialize()
    host.add_interval_job.assert_called_once()
    assert watch.interval == 300
    watch._work = AsyncMock()
    await watch.tick()
    await watch.shutdown()
    host.remove_job.assert_called_once_with("runtime_health", "tick")


@pytest.mark.asyncio
async def test_saved_interval_starts_and_stops_the_watch():
    host = MagicMock()
    watch = RuntimeHealthWatch(
        policy_storage({"system.runtime_health_interval_seconds": "600"}),
        MagicMock(),
        MagicMock(),
        host,
    )
    await watch.initialize()
    assert watch.interval == 600
    host.add_interval_job.assert_called_once()

    watch._work = AsyncMock()
    await watch.tick()
    worker = watch.worker

    await watch.apply_policy(policy_storage({}))  # reset returns to the disabled default
    assert watch.interval == 0
    host.remove_job.assert_called_with("runtime_health", "tick")
    await asyncio.gather(worker, return_exceptions=True)
    assert worker.cancelled()

    await watch.apply_policy(policy_storage({"system.runtime_health_interval_seconds": "3600"}))
    assert watch.interval == 3600
    assert host.add_interval_job.call_count == 2
