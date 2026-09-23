from __future__ import annotations

from .models import TrackerStatus
from .scheduler_status import _status_type, _tracker_channel_count


class ReleaseSchedulerRebuilds:
    """Rebuild aggregate tracker views from persisted source truth."""

    async def rebuild_tracker_views_from_storage(self, name: str) -> TrackerStatus:
        config = await self.storage.get_tracker_config(name)
        aggregate_tracker = await self.storage.get_aggregate_tracker(name)
        if not config and aggregate_tracker is None:
            raise ValueError(f"Tracker {name} not found")

        current_status = await self.storage.get_tracker_status(name)
        preserved_last_check = current_status.last_check if current_status else None
        latest_version = current_status.last_version if current_status else None

        tracker_type = _status_type(config, aggregate_tracker)
        aggregate_enabled = aggregate_tracker.enabled if aggregate_tracker is not None else True
        tracker_enabled = config.enabled if config is not None else True
        if not tracker_enabled or not aggregate_enabled:
            status = TrackerStatus(
                name=name,
                type=tracker_type,
                enabled=False,
                last_check=preserved_last_check,
                last_version=latest_version,
                error="Tracker is disabled",
            )
            await self.storage.update_tracker_status(status)
            return status

        if aggregate_tracker is None:
            raise ValueError(f"Aggregate tracker missing during local rebuild: {name}")

        result = await self._process_aggregate_tracker_local_rebuild(
            name, aggregate_tracker, config
        )
        if result["latest_version"]:
            latest_version = result["latest_version"]

        # Rebuilding local views performs no fetch. Neither invent a failure
        # for empty history nor clear a genuine previous fetch failure.
        previous_error = current_status.error if current_status else None
        if previous_error in {"No version information found", "Tracker is disabled"}:
            previous_error = None

        status = TrackerStatus(
            name=name,
            type=tracker_type,
            enabled=True,
            last_check=preserved_last_check,
            last_version=latest_version,
            error=result.get("error") or previous_error,
            channel_count=_tracker_channel_count(config),
        )
        await self.storage.update_tracker_status(status)
        return status
