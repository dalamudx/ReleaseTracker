from __future__ import annotations

from typing import cast

from .config import TrackerConfig
from .models import AggregateTracker, TrackerSourceType


def _tracker_channel_count(config: TrackerConfig | None) -> int:
    return len(config.channels) if config and config.channels else 0


def _status_type(
    tracker_config: TrackerConfig | None, aggregate_tracker: AggregateTracker | None
) -> TrackerSourceType:
    if tracker_config is not None:
        return tracker_config.type

    if aggregate_tracker is not None:
        enabled_sources = [source for source in aggregate_tracker.sources if source.enabled]
        if enabled_sources:
            return cast(TrackerSourceType, enabled_sources[0].source_type)
        if aggregate_tracker.sources:
            return cast(TrackerSourceType, aggregate_tracker.sources[0].source_type)

    return cast(TrackerSourceType, "github")
