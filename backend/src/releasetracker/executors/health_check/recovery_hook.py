"""Recovery Hook Coordinator.

When a post-update health check concludes unhealthy and the executor's
``failure_policy`` is ``mark_failed_and_recover``, the coordinator:
1. Loads the most recent ``ExecutorSnapshot`` from the multi-row history.
2. Validates it via the runtime adapter.
3. Invokes ``recover_from_snapshot`` under a wall-clock budget.
4. Maps exceptions to a ``recovery_outcome`` string and preserves the
   underlying error text so callers can persist it into
   ``diagnostics.recovery_error``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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


# Upper bound on the persisted error text. The same limit applies to
# ``recovery_error`` and to the rollback run's ``message`` so rows stay
# bounded regardless of adapter verbosity.
MAX_RECOVERY_ERROR_LENGTH = 1000


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome + optional adapter detail captured from recovery."""

    outcome: RecoveryOutcome
    error: str | None = None
    new_container_id: str | None = None


def _truncate_error(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= MAX_RECOVERY_ERROR_LENGTH:
        return value
    return value[:MAX_RECOVERY_ERROR_LENGTH]


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
        snapshot: ExecutorSnapshot | None = None,
    ) -> RecoveryOutcome:
        """Run the recovery and return only the outcome string.

        Kept for backwards compatibility; new callers should use
        :meth:`recover_detailed` to also receive the underlying error
        text for diagnostics.
        """
        result = await self.recover_detailed(
            executor_id=executor_id,
            adapter=adapter,
            target_ref=target_ref,
            budget_seconds=budget_seconds,
            snapshot=snapshot,
        )
        return result.outcome

    async def recover_detailed(
        self,
        *,
        executor_id: int,
        adapter: "BaseRuntimeAdapter",
        target_ref: dict[str, Any],
        budget_seconds: int,
        snapshot: ExecutorSnapshot | None = None,
    ) -> RecoveryResult:
        # Manual rollbacks supply the explicit target snapshot so the
        # coordinator does not pick up a pre_rollback row the caller
        # just inserted. Automatic recovery from a failed post-update
        # health check passes ``None`` and relies on "most recent" semantics.
        if snapshot is None:
            snapshot = await self._storage.get_executor_snapshot(executor_id)
        if snapshot is None:
            logger.info(
                "recovery_hook_no_snapshot executor_id=%s", executor_id
            )
            return RecoveryResult(outcome="no_snapshot")

        snapshot_data = snapshot.snapshot_data

        try:
            await adapter.validate_snapshot(target_ref, snapshot_data)
        except NotImplementedError:
            logger.info(
                "recovery_hook_not_supported executor_id=%s validate=NotImplementedError",
                executor_id,
            )
            return RecoveryResult(outcome="not_supported")
        except Exception as exc:
            logger.warning(
                "recovery_hook_invalid_snapshot executor_id=%s cause=%s",
                executor_id,
                exc,
            )
            return RecoveryResult(
                outcome="invalid_snapshot",
                error=_truncate_error(str(exc)),
            )

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
            return RecoveryResult(outcome="not_supported")
        except asyncio.TimeoutError:
            logger.warning(
                "recovery_hook_timeout executor_id=%s budget=%ss",
                executor_id,
                budget_seconds,
            )
            return RecoveryResult(
                outcome="timeout",
                error=_truncate_error(
                    f"recovery exceeded {budget_seconds}s budget"
                ),
            )
        except Exception as exc:
            logger.warning(
                "recovery_hook_failed executor_id=%s cause=%s",
                executor_id,
                exc,
            )
            return RecoveryResult(
                outcome="failed",
                error=_truncate_error(str(exc)),
            )

        updated = bool(getattr(result, "updated", False))
        new_container_id = getattr(result, "new_container_id", None)
        if not isinstance(new_container_id, str) or not new_container_id:
            new_container_id = None
        if updated:
            return RecoveryResult(
                outcome="succeeded",
                new_container_id=new_container_id,
            )

        message = getattr(result, "message", None)
        error = _truncate_error(message) if isinstance(message, str) and message else (
            "recovery completed without applying changes"
        )
        return RecoveryResult(
            outcome="failed",
            error=error,
            new_container_id=new_container_id,
        )


__all__ = [
    "MAX_RECOVERY_ERROR_LENGTH",
    "RecoveryHookCoordinator",
    "RecoveryOutcome",
    "RecoveryResult",
]
