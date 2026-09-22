"""Only verified recovery can release persistent uncertain deployment tasks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.services.recovery_tasks import RecoveryTasks
from releasetracker.services.rollback_service import RollbackService


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_recovery_requires_verified_result_before_releasing_blocked_task(
    storage, monkeypatch, verified
):
    from releasetracker.services import runtime_credentials

    executor = SimpleNamespace(id=1, runtime_connection_id=1)
    monkeypatch.setattr(storage, "get_executor_config", AsyncMock(return_value=executor))
    monkeypatch.setattr(storage, "get_runtime_connection", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        runtime_credentials,
        "materialize_runtime_connection_credentials",
        AsyncMock(return_value=object()),
    )
    rollback = AsyncMock(
        return_value=SimpleNamespace(
            run=SimpleNamespace(id=7, status="success" if verified else "failed"),
            recovery_outcome="succeeded" if verified else "timeout",
        )
    )
    monkeypatch.setattr(RollbackService, "rollback", rollback)
    scheduler = SimpleNamespace(
        _try_acquire_executor_run=AsyncMock(return_value=True),
        _release_executor_run=AsyncMock(),
        _get_adapter=MagicMock(return_value=object()),
        snapshot_service=object(),
    )
    blocked = await storage.tasks.enqueue(
        kind="deploy",
        resource_key="deployment-mutations",
        dedupe_key="native-recovery-test",
        target_label="example-stack",
        payload={"executor_id": executor.id},
        trigger_mode="manual",
    )
    claimed = await storage.tasks.claim("deploy")
    await storage.tasks.finish(claimed, "needs_attention")
    recovery = await storage.tasks.enqueue(
        kind="recover",
        resource_key="deployment-mutations",
        dedupe_key="native-recovery-check",
        target_label="example-stack",
        trigger_mode="manual",
        payload={
            "executor_id": executor.id,
            "action": "rollback",
            "snapshot_id": 3,
            "actor": "admin",
        },
    )
    result = await RecoveryTasks(storage, scheduler).execute(recovery)
    persisted = await storage.tasks.get(blocked["id"])
    if verified:
        assert persisted["state"] == "failed"
        assert persisted["error_code"] == "operator_reconciled"
    else:
        assert persisted["state"] == "needs_attention"
        assert result.state == "needs_attention"
    rollback.assert_awaited_once()
    scheduler._release_executor_run.assert_awaited_once_with(executor.id)
