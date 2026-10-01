"""Select immutable deployment inputs without depending on scheduler internals.

Automatic tasks use their desired-state snapshot. Manual and legacy tasks select
image and chart metadata from one resolved release, never two separate queries.
"""

from __future__ import annotations

from ..executor_trigger import _binding_contexts
from ..executor_scheduler_target_resolution import (
    _resolve_tracker_binding_by_source_id_from_storage,
    _resolve_tracker_latest_target_details_from_storage,
)


async def resolve_deployment_targets(storage, executor, *, manual, desired_revision=None):
    if not manual and desired_revision is not None:
        desired = await storage.get_executor_desired_state(executor.id)
        if desired is None or desired.desired_state_revision != desired_revision:
            raise ValueError("Desired deployment revision changed")
        bindings = desired.desired_target.get("binding_targets") or []
        # Older desired rows did not snapshot chart metadata. Resolve those once.
        if bindings and all("chart_version" in item for item in bindings):
            return [
                {
                    "tracker_name": item["tracker_name"],
                    "source_id": item["tracker_source_id"],
                    "channel": item["channel_name"],
                    "target": [item["deploy_alias"], item.get("digest")],
                    "chart_version": item.get("chart_version"),
                    "chart_digest": item.get("chart_digest"),
                }
                for item in bindings
            ]

    targets = []
    for binding in _binding_contexts(executor):
        resolved = await _resolve_tracker_binding_by_source_id_from_storage(
            storage, binding.tracker_source_id
        )
        if resolved is None:
            raise ValueError("Executor source is unavailable")
        name, source = resolved
        target = await _resolve_tracker_latest_target_details_from_storage(
            storage,
            name,
            binding.channel_name,
            tracker_source_id=source.id,
            tracker_source_type=source.source_type,
        )
        targets.append(
            {
                "tracker_name": name,
                "source_id": source.id,
                "channel": binding.channel_name,
                "target": [target.deploy_alias, target.digest] if target else None,
                "chart_version": (
                    target.chart_version if target and source.source_type == "helm" else None
                ),
                "chart_digest": (
                    target.chart_digest if target and source.source_type == "helm" else None
                ),
            }
        )
    if not targets:
        target = await _resolve_tracker_latest_target_details_from_storage(
            storage, executor.tracker_name, executor.channel_name
        )
        targets.append(
            {
                "tracker_name": executor.tracker_name,
                "source_id": None,
                "channel": executor.channel_name,
                "target": [target.deploy_alias, target.digest] if target else None,
                "chart_version": None,
                "chart_digest": None,
            }
        )
    return targets
