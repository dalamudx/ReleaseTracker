from __future__ import annotations

import logging
from datetime import datetime

from .models import TrackerStatus
from .scheduler_status import _status_type, _tracker_channel_count

logger = logging.getLogger(__name__)

MANUAL_CHECK_COOLDOWN_SECONDS = 30
MANUAL_CHECK_ALREADY_RUNNING_MESSAGE = "Check already in progress; skipping duplicate request"
MANUAL_CHECK_COOLDOWN_MESSAGE = "Recently checked; skipping duplicate request"


class ReleaseSchedulerManualChecks:
    """Run manual aggregate tracker checks with duplicate and cooldown protection."""

    async def check_tracker_now_v2(self, name: str) -> TrackerStatus:
        """Submit to the durable queue when running inside the application."""
        if self.fetch_tasks is not None:
            return await self.fetch_tasks.enqueue(name, trigger_mode="manual")
        config = await self.storage.get_tracker_config(name)
        aggregate_tracker = await self.storage.get_aggregate_tracker(name)
        if not config and aggregate_tracker is None:
            raise ValueError(f"Tracker {name} not found")

        tracker_type = _status_type(config, aggregate_tracker)
        current_status = await self.storage.get_tracker_status(name)
        enabled = (
            config.enabled
            if config is not None
            else bool(aggregate_tracker and aggregate_tracker.enabled)
        )

        if name in self._manual_checks_in_progress:
            return TrackerStatus(
                name=name,
                type=tracker_type,
                enabled=enabled,
                last_check=current_status.last_check if current_status else None,
                last_version=current_status.last_version if current_status else None,
                error=MANUAL_CHECK_ALREADY_RUNNING_MESSAGE,
                channel_count=_tracker_channel_count(config),
                manual_check_outcome="skipped",
                manual_check_reason="already_running",
            )

        if (
            current_status
            and current_status.last_check
            and (datetime.now() - current_status.last_check).total_seconds()
            < MANUAL_CHECK_COOLDOWN_SECONDS
        ):
            return TrackerStatus(
                name=name,
                type=tracker_type,
                enabled=enabled,
                last_check=current_status.last_check,
                last_version=current_status.last_version,
                error=MANUAL_CHECK_COOLDOWN_MESSAGE,
                channel_count=_tracker_channel_count(config),
                manual_check_outcome="skipped",
                manual_check_reason="cooldown",
            )

        try:
            latest_version = current_status.last_version if current_status else None
            self._manual_checks_in_progress.add(name)
            aggregate_tracker = self._require_aggregate_tracker_for_live_check(
                name, aggregate_tracker
            )
            result = await self._process_aggregate_tracker_check(
                name,
                aggregate_tracker,
                config,
                log_prefix="Manual ",
                trigger_mode="manual",
            )

            releases = result["releases"]
            if result["latest_version"]:
                latest_version = result["latest_version"]
            error = result.get("error")

            status = TrackerStatus(
                name=name,
                type=_status_type(config, aggregate_tracker),
                enabled=(
                    config.enabled
                    if config is not None
                    else bool(aggregate_tracker and aggregate_tracker.enabled)
                ),
                last_check=datetime.now(),
                last_version=latest_version,
                error=(
                    error
                    if releases or latest_version
                    else (error or "No version information found")
                ),
                channel_count=_tracker_channel_count(config),
                manual_check_outcome="completed",
            )
            await self.storage.update_tracker_status(status)

            return status

        except Exception as e:
            error_msg = str(e) or getattr(e, "__class__", Exception).__name__
            logger.error(f"Manual check failed for {name}: {error_msg}")

            # Update error status in the database
            status = TrackerStatus(
                name=name,
                type=_status_type(config, aggregate_tracker),
                enabled=(
                    config.enabled
                    if config is not None
                    else bool(aggregate_tracker and aggregate_tracker.enabled)
                ),
                last_check=datetime.now(),
                last_version=None,
                error=error_msg,
                channel_count=_tracker_channel_count(config),
                manual_check_outcome="failed",
            )
            await self.storage.update_tracker_status(status)

            # Return status with an error message instead of raising
            return status
        finally:
            self._manual_checks_in_progress.discard(name)
