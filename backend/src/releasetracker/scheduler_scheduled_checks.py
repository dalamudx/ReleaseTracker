from __future__ import annotations

import logging
from datetime import datetime

from .models import TrackerStatus
from .scheduler_status import _status_type

logger = logging.getLogger(__name__)


class ReleaseSchedulerScheduledChecks:
    """Run scheduled aggregate tracker checks and persist status outcomes."""

    async def _check_tracker(self, tracker_name: str):
        """Check one tracker"""
        aggregate_tracker = await self.storage.get_aggregate_tracker(tracker_name)

        # Fetch the latest configuration from the database to keep status synchronized
        tracker_config = await self.storage.get_tracker_config(tracker_name)
        if not tracker_config and aggregate_tracker is None:
            logger.warning(f"Tracker config missing during check: {tracker_name}")
            return None

        tracker_type = _status_type(tracker_config, aggregate_tracker)

        # Check only when the configuration is enabled
        aggregate_enabled = aggregate_tracker.enabled if aggregate_tracker is not None else True
        tracker_enabled = tracker_config.enabled if tracker_config is not None else True
        if not tracker_enabled or not aggregate_enabled:
            # If disabled, update status without running a check
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=False,
                last_check=datetime.now(),
                last_version=None,  # or keep the previous value
                error="Tracker is disabled",
            )
            await self.storage.update_tracker_status(status)
            return status

        try:
            aggregate_tracker = self._require_aggregate_tracker_for_live_check(
                tracker_name, aggregate_tracker
            )
            result = await self._process_aggregate_tracker_check(
                tracker_name,
                aggregate_tracker,
                tracker_config,
                trigger_mode="scheduled",
            )

            releases = result["releases"]
            latest_version = result["latest_version"]
            error = result.get("error")

            if releases or latest_version:
                status = TrackerStatus(
                    name=tracker_name,
                    type=tracker_type,
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=latest_version,
                    error=error,
                )
            else:
                status = TrackerStatus(
                    name=tracker_name,
                    type=tracker_type,
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=None,
                    error=error or "No version information found",
                )

            await self.storage.update_tracker_status(status)
            return status

        except Exception as e:
            error_msg = str(e) or getattr(e, "__class__", Exception).__name__
            logger.error(f"Check failed for {tracker_name}: {error_msg}")

            # Update error status
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=True,
                last_check=datetime.now(),
                last_version=None,
                error=error_msg,
            )
            await self.storage.update_tracker_status(status)
            return status
