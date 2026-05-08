"""Recovery Hook Coordinator (Req 10.*).

When a Health Check Phase concludes unhealthy and the executor's
``failure_policy`` is ``mark_failed_and_recover``, the coordinator:
1. Loads the most recent ``ExecutorSnapshot`` from the multi-row history.
2. Validates it via the runtime adapter.
3. Invokes ``recover_from_snapshot`` under a wall-clock budget.
4. Maps exceptions to a ``recovery_outcome`` string the scheduler
   persists in ``diagnostics.recovery_outcome``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from ...models import ExecutorSnapshot

if TYPE_CHECKING:
    from ...storage.sqlite import SQLiteStorage
    from ..base import BaseRuntimeAdapter


logger = logging.getLogger(__name__)

RecoveryOutcome = Literal[
    "succeeded",
    "failed",
    "not_supported",
    "no_snapshot",
    "invalid_snapshot",
    "timeout",
]


class RecoveryHookCoordinator:
    def __init__(self, storage: "SQLiteStorage") -> None:
        self._storage = storage

    async def recover(
        self,
        *,
        executor_id: int,
        adapter: "BaseRuntimeAdapter",
        target_ref: dict[str, Any],
        budget_seconds: int,
    ) -> RecoveryOutcome:
        snapshot: ExecutorSnapshot | None = await self._storage.get_executor_snapshot(
            executor_id
        )
        if snapshot is None:
            logger.info(
                "recovery_hook_no_snapshot executor_id=%s", executor_id
            )
            return "no_snapshot"

        snapshot_data = snapshot.snapshot_data

        try:
            await adapter.validate_snapshot(target_ref, snapshot_data)
        except NotImplementedError:
            logger.info(
                "recovery_hook_not_supported executor_id=%s validate=NotImplementedError",
                executor_id,
            )
            return "not_supported"
        except Exception as exc:
            logger.warning(
                "recovery_hook_invalid_snapshot executor_id=%s cause=%s",
                executor_id,
                exc,
            )
            return "invalid_snapshot"

        try:
            result = await asyncio.wait_for(
                adapter.recover_from_snapshot(target_ref, snapshot_data),
                timeout=max(1, int(budget_seconds)),
            )
        except NotImplementedError:
            logger.info(
                "recovery_hook_not_supported executor_id=%s recover=NotImplementedError",
                executor_id,
            )
            return "not_supported"
        except asyncio.TimeoutError:
            logger.warning(
                "recovery_hook_timeout executor_id=%s budget=%ss",
                executor_id,
                budget_seconds,
            )
            return "timeout"
        except Exception as exc:
            logger.warning(
                "recovery_hook_failed executor_id=%s cause=%s",
                executor_id,
                exc,
            )
            return "failed"

        updated = bool(getattr(result, "updated", False))
        return "succeeded" if updated else "failed"


__all__ = ["RecoveryHookCoordinator", "RecoveryOutcome"]
