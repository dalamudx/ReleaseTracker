import pytest
from unittest.mock import AsyncMock, MagicMock
from releasetracker.services.runtime_health_watch import watch_interval, RuntimeHealthWatch


@pytest.mark.parametrize("value", ["1", "299", "86401", "bad", "-1"])
def test_invalid_watch_interval_rejected(monkeypatch, value):
    monkeypatch.setenv("RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError):
        watch_interval()


@pytest.mark.asyncio
async def test_disabled_watch_registers_no_background_work(monkeypatch):
    monkeypatch.delenv("RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS", raising=False)
    host = MagicMock()
    watch = RuntimeHealthWatch(MagicMock(), MagicMock(), MagicMock(), host)
    await watch.initialize()
    await watch.tick()
    await watch.shutdown()
    host.add_interval_job.assert_not_called()
    host.remove_job.assert_not_called()
    assert watch.worker is None


@pytest.mark.asyncio
async def test_enabled_watch_registration_and_shutdown(monkeypatch):
    monkeypatch.setenv("RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS", "300")
    host = MagicMock()
    watch = RuntimeHealthWatch(MagicMock(), MagicMock(), MagicMock(), host)
    await watch.initialize()
    host.add_interval_job.assert_called_once()
    watch._work = AsyncMock()
    await watch.tick()
    await watch.shutdown()
    host.remove_job.assert_called_once_with("runtime_health", "tick")
