from __future__ import annotations

import logging
from datetime import datetime

from .models import TrackerStatus
from .scheduler_status import _status_type

logger = logging.getLogger(__name__)


class ReleaseSchedulerScheduledChecks:
    """Run scheduled aggregate tracker checks and persist status outcomes."""

    async def _check_tracker(self, tracker_name: str):
        """Schedule one tracker; execution belongs to the durable worker."""
        if self.fetch_tasks is not None:
            aggregate = await self.storage.get_aggregate_tracker(tracker_name)
            if aggregate is None or not aggregate.enabled:
                return None
            return await self.fetch_tasks.enqueue(tracker_name, trigger_mode="scheduled")
        aggregate_tracker = await self.storage.get_aggregate_tracker(tracker_name)
        tracker_config = await self.storage.get_tracker_config(tracker_name)
        if not tracker_config and aggregate_tracker is None:
            logger.warning("Tracker config missing during check: %s", tracker_name)
            return None

        tracker_type = _status_type(tracker_config, aggregate_tracker)
        aggregate_enabled = aggregate_tracker.enabled if aggregate_tracker is not None else True
        tracker_enabled = tracker_config.enabled if tracker_config is not None else True
        if not tracker_enabled or not aggregate_enabled:
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=False,
                last_check=datetime.now(),
                last_version=None,
                error="Tracker is disabled",
            )
            await self.storage.update_tracker_status(status)
            return status

        if tracker_name in self._manual_checks_in_progress:
            return await self.storage.get_tracker_status(tracker_name)
        self._manual_checks_in_progress.add(tracker_name)
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
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=True,
                last_check=datetime.now(),
                last_version=latest_version if releases or latest_version else None,
                # A successful empty fetch is not a transport/source failure.
                error=error,
            )
            await self.storage.update_tracker_status(status)
            return status
        except Exception as exc:
            error_msg = str(exc) or exc.__class__.__name__
            logger.error("Check failed for %s: %s", tracker_name, error_msg)
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
        finally:
            self._manual_checks_in_progress.discard(tracker_name)
