"""RecoveryHookCoordinator unit tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import pytest

from releasetracker.executors.base import BaseRuntimeAdapter, RuntimeUpdateResult
from releasetracker.executors.health_check.recovery_hook import (
    MAX_RECOVERY_ERROR_LENGTH,
    RecoveryHookCoordinator,
)
from releasetracker.models import ExecutorSnapshot


class _FakeStorage:
    def __init__(self, snapshot: ExecutorSnapshot | None):
        self._snapshot = snapshot

    async def get_executor_snapshot(self, executor_id: int):
        return self._snapshot


@dataclass
class _ValidatingAdapter(BaseRuntimeAdapter):
    """Adapter with overridable validate/recover hooks for recovery tests."""

    validate_raises: Exception | None = None
    recover_raises: Exception | None = None
    recover_result: RuntimeUpdateResult | None = None
    recover_delay: float = 0

    def __init__(self, *, validate_raises=None, recover_raises=None, recover_result=None, recover_delay=0):
        # Skip super().__init__ because runtime_connection isn't used.
        self.validate_raises = validate_raises
        self.recover_raises = recover_raises
        self.recover_result = recover_result or RuntimeUpdateResult(
            updated=True, old_image=None, new_image="img:prev"
        )
        self.recover_delay = recover_delay

    async def discover_targets(self):
        return []

    async def validate_target_ref(self, target_ref):
        return None

    async def get_current_image(self, target_ref):
        return ""

    async def capture_snapshot(self, target_ref, current_image):
        return {}

    async def validate_snapshot(self, target_ref, snapshot):
        if self.validate_raises is not None:
            raise self.validate_raises

    async def update_image(self, target_ref, new_image):
        raise NotImplementedError

    async def recover_from_snapshot(self, target_ref, snapshot):
        if self.recover_delay > 0:
            await asyncio.sleep(self.recover_delay)
        if self.recover_raises is not None:
            raise self.recover_raises
        return self.recover_result


def _snapshot() -> ExecutorSnapshot:
    return ExecutorSnapshot(
        id=1,
        executor_id=42,
        snapshot_data={"image": "img:prev"},
        trigger="pre_update",
        image_at_capture="img:prev",
        created_at=datetime(2026, 5, 8, 10, 0, 0),
        updated_at=datetime(2026, 5, 8, 10, 0, 0),
    )


@pytest.mark.asyncio
async def test_returns_no_snapshot_when_history_empty():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=None))
    adapter = _ValidatingAdapter()

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert outcome == "no_snapshot"


@pytest.mark.asyncio
async def test_returns_invalid_snapshot_when_validate_raises():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(validate_raises=ValueError("corrupt"))

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert outcome == "invalid_snapshot"


@pytest.mark.asyncio
async def test_returns_not_supported_when_recover_is_not_implemented():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(recover_raises=NotImplementedError("no recovery"))

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert outcome == "not_supported"


@pytest.mark.asyncio
async def test_returns_not_supported_when_validate_is_not_implemented():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(validate_raises=NotImplementedError("no validate"))

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert result.outcome == "not_supported"
    assert result.error is None


@pytest.mark.asyncio
async def test_recover_detailed_truncates_recovery_error():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    long_error = "x" * (MAX_RECOVERY_ERROR_LENGTH + 50)
    adapter = _ValidatingAdapter(recover_raises=RuntimeError(long_error))

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert result.outcome == "failed"
    assert result.error == "x" * MAX_RECOVERY_ERROR_LENGTH


@pytest.mark.asyncio
async def test_returns_timeout_when_recovery_exceeds_budget():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(recover_delay=3)

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=1,
    )
    assert outcome == "timeout"


@pytest.mark.asyncio
async def test_returns_failed_when_recover_raises_generic_exception():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(recover_raises=RuntimeError("boom"))

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert outcome == "failed"


@pytest.mark.asyncio
async def test_returns_succeeded_when_recover_reports_updated():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(
        recover_result=RuntimeUpdateResult(updated=True, old_image=None, new_image="img:prev")
    )

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert outcome == "succeeded"


@pytest.mark.asyncio
async def test_recover_detailed_returns_new_container_id():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(
        recover_result=RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image="img:prev",
            new_container_id="fresh-container-id",
        )
    )

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert result.outcome == "succeeded"
    assert result.new_container_id == "fresh-container-id"


@pytest.mark.asyncio
async def test_returns_failed_when_recover_reports_not_updated():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(
        recover_result=RuntimeUpdateResult(updated=False, old_image=None, new_image=None)
    )

    outcome = await coord.recover(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert outcome == "failed"


@pytest.mark.asyncio
async def test_recover_detailed_captures_exception_message():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(recover_raises=RuntimeError("container name already in use"))

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert result.outcome == "failed"
    assert result.error == "container name already in use"


@pytest.mark.asyncio
async def test_recover_detailed_captures_invalid_snapshot_detail():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(validate_raises=ValueError("corrupt payload"))

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert result.outcome == "invalid_snapshot"
    assert result.error == "corrupt payload"


@pytest.mark.asyncio
async def test_recover_detailed_records_timeout_budget():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(recover_delay=3)

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=1,
    )
    assert result.outcome == "timeout"
    assert result.error is not None
    assert "1s" in result.error


@pytest.mark.asyncio
async def test_recover_detailed_records_not_updated_reason():
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=_snapshot()))
    adapter = _ValidatingAdapter(
        recover_result=RuntimeUpdateResult(
            updated=False,
            old_image=None,
            new_image=None,
            message="adapter aborted rollback",
        )
    )

    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
    )
    assert result.outcome == "failed"
    assert result.error == "adapter aborted rollback"


@pytest.mark.asyncio
async def test_recover_uses_explicit_snapshot_over_storage_latest():
    """When an explicit snapshot is passed, storage.get_executor_snapshot is ignored."""
    # Storage would hand back a *different* snapshot (the pre_rollback row
    # the rollback service just inserted). The coordinator must ignore it
    # and pass the explicit target snapshot to the adapter.
    latest = ExecutorSnapshot(
        id=99,
        executor_id=42,
        snapshot_data={"image": "pre-rollback:latest"},
        trigger="pre_rollback",
        image_at_capture="pre-rollback:latest",
        created_at=datetime(2026, 5, 9, 12, 0, 0),
        updated_at=datetime(2026, 5, 9, 12, 0, 0),
    )
    target = _snapshot()  # id=1, image=img:prev
    coord = RecoveryHookCoordinator(_FakeStorage(snapshot=latest))

    captured: dict = {}

    class _CaptureAdapter(_ValidatingAdapter):
        async def validate_snapshot(self, target_ref, snapshot):
            captured["validated"] = snapshot
            return None

        async def recover_from_snapshot(self, target_ref, snapshot):
            captured["recovered"] = snapshot
            return RuntimeUpdateResult(
                updated=True, old_image=None, new_image="img:prev"
            )

    adapter = _CaptureAdapter()
    result = await coord.recover_detailed(
        executor_id=42,
        adapter=adapter,
        target_ref={"mode": "container"},
        budget_seconds=10,
        snapshot=target,
    )
    assert result.outcome == "succeeded"
    assert captured["validated"] == target.snapshot_data
    assert captured["recovered"] == target.snapshot_data
