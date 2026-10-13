"""Build durable executor work from the bindings that actually select each target."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING

from .executor_scheduler_grouped_runtime_support import _ExecutorBindingRunContext
from .executor_scheduler_target_resolution import (
    _resolve_tracker_latest_target_details_from_storage,
    _target_identity_key,
)

if TYPE_CHECKING:
    from .config import ExecutorConfig
    from .storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)


async def enqueue_executor_binding_targets(
    storage: "SQLiteStorage",
    executor_config: "ExecutorConfig",
    *,
    tracker_name: str | None = None,
) -> bool:
    """Queue work when any bound source/channel target changes.

    Grouped executors use a stable vector, so a release in one channel cannot
    be hidden by a newer release in another channel of the same tracker.

    ``tracker_name`` only selects which executors are affected by a tracker
    check. The desired revision always covers every binding: deriving it from
    the checked tracker's subset made executors bound to several trackers
    flip revisions on every check and queue duplicate deployments.
    """
    if executor_config.id is None:
        return False
    if not executor_config.enabled or executor_config.update_mode == "manual":
        return False

    contexts = _binding_contexts(executor_config)
    if not contexts:
        # Legacy single-target executors may predate explicit source bindings.
        # They still select a particular channel from their aggregate tracker.
        if (
            tracker_name is not None and executor_config.tracker_name != tracker_name
        ) or executor_config.channel_name is None:
            return False
        contexts = [
            _ExecutorBindingRunContext(
                service=None,
                tracker_source_id=None,
                channel_name=executor_config.channel_name,
            )
        ]

    targets: list[dict[str, str | int | None]] = []
    relevant = tracker_name is None
    for context in contexts:
        if context.tracker_source_id is None:
            bound_tracker_name = executor_config.tracker_name
            source_type = None
        else:
            binding = await storage.get_executor_binding(context.tracker_source_id)
            if binding is None:
                continue
            aggregate_tracker, source = binding
            if not source.enabled:
                continue
            bound_tracker_name = aggregate_tracker.name
            source_type = source.source_type
        if bound_tracker_name == tracker_name:
            relevant = True
        target = await _resolve_tracker_latest_target_details_from_storage(
            storage,
            bound_tracker_name,
            context.channel_name,
            tracker_source_id=context.tracker_source_id,
            tracker_source_type=source_type,
        )
        if target is None:
            continue
        version = target.deploy_alias
        digest = target.digest
        targets.append(
            {
                "service": context.service,
                "tracker_name": bound_tracker_name,
                "tracker_source_id": context.tracker_source_id,
                "channel_name": context.channel_name,
                "version": version,
                "display_version": target.display_version,
                "deploy_alias": target.deploy_alias,
                "aliases": list(target.aliases),
                "digest": digest,
                "chart_version": (target.chart_version if source_type == "helm" else None),
                "chart_digest": (target.chart_digest if source_type == "helm" else None),
                "identity_key": _target_identity_key(version, digest),
                "selection_revision": target.selection_revision,
            }
        )

    if not relevant or not targets:
        return False

    targets.sort(
        key=lambda target: (
            str(target["tracker_name"]),
            str(target["service"] or ""),
            int(target["tracker_source_id"] or 0),
            str(target["channel_name"]),
        )
    )
    revision_targets = [_revision_coordinates(target) for target in targets]
    serialized_identity = json.dumps(revision_targets, sort_keys=True, separators=(",", ":"))
    revision_identity = (
        "bindings:" + hashlib.sha256(serialized_identity.encode("utf-8")).hexdigest()
    )
    primary_target = targets[0]
    queued_tracker_name = str(primary_target["tracker_name"])
    revision = f"{queued_tracker_name}:{revision_identity}"
    existing_state = await storage.get_executor_desired_state(executor_config.id)
    previous_target = existing_state.desired_target if existing_state is not None else {}
    if existing_state is not None and (
        existing_state.desired_state_revision == revision
        or (
            previous_target.get("tracker_name") == queued_tracker_name
            and _same_coordinates(previous_target.get("binding_targets"), revision_targets)
        )
    ):
        # Selection rules or the revision format changed without moving any
        # deployment coordinate: refresh the snapshot in place, never queue work.
        from .versioning import SELECTION_RULES_VERSION

        refreshed = previous_target | {
            "selection_revision": SELECTION_RULES_VERSION,
            "current_identity_key": revision_identity,
            "binding_targets": targets,
        }
        if existing_state.desired_state_revision != revision or refreshed != previous_target:
            await storage.rebase_executor_desired_state(
                executor_config.id,
                expected_revision=existing_state.desired_state_revision,
                desired_state_revision=revision,
                desired_target=refreshed,
            )
        return False

    return await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_config.id,
        tracker_name=queued_tracker_name,
        previous_version=previous_target.get("current_version"),
        current_version=str(primary_target["display_version"]),
        previous_identity_key=previous_target.get("current_identity_key"),
        current_identity_key=revision_identity,
        binding_targets=targets,
    )


async def reconcile_desired_states(storage: "SQLiteStorage") -> tuple[int, int]:
    """Startup counterpart of selection-rule reconciliation for executor work."""
    from .versioning import SELECTION_RULES_VERSION

    queued = await republish_executor_desired_states(storage)
    retired = await storage.tasks.supersede_obsolete_deploys(SELECTION_RULES_VERSION)
    return queued, retired


# Only what an executor writes defines a desired revision. Selection rule
# versions and snapshot hashes are bookkeeping: bumping them must not queue
# a deployment whose target is unchanged.
_REVISION_FIELDS = (
    "service",
    "tracker_name",
    "tracker_source_id",
    "channel_name",
    "deploy_alias",
    "identity_key",
    "chart_version",
    "chart_digest",
)


def _revision_coordinates(target: dict) -> dict:
    return {field: target.get(field) for field in _REVISION_FIELDS}


def _same_coordinates(stored, coordinates: list[dict]) -> bool:
    if not isinstance(stored, list) or not stored:
        return False

    def key(item):
        return json.dumps(item, sort_keys=True)

    return sorted(key(_revision_coordinates(item)) for item in stored) == sorted(
        key(item) for item in coordinates
    )


async def republish_executor_desired_states(storage: "SQLiteStorage") -> int:
    """Re-derive every executor's desired state from the current selections.

    Runs at startup after selection rules are reconciled, so work queued under
    older rules is retargeted instead of waiting for the next tracker check.
    Unchanged deployment coordinates are refreshed in place.
    """
    queued = 0
    for executor_config in await storage.get_all_executor_configs():
        try:
            if await enqueue_executor_binding_targets(storage, executor_config):
                queued += 1
        except Exception as exc:
            logger.warning(
                "Could not republish desired state for executor %s: %s",
                executor_config.id,
                type(exc).__name__,
            )
    return queued


def _binding_contexts(executor_config: "ExecutorConfig") -> list[_ExecutorBindingRunContext]:
    target_mode = executor_config.target_ref.get("mode")
    if target_mode in {"portainer_stack", "docker_compose", "kubernetes_workload", "ssh_compose"}:
        return [
            _ExecutorBindingRunContext(
                service=binding.service,
                tracker_source_id=binding.tracker_source_id,
                channel_name=binding.channel_name,
            )
            for binding in sorted(
                executor_config.service_bindings, key=lambda binding: binding.service
            )
        ]
    if executor_config.tracker_source_id is None or executor_config.channel_name is None:
        return []
    return [
        _ExecutorBindingRunContext(
            service=None,
            tracker_source_id=executor_config.tracker_source_id,
            channel_name=executor_config.channel_name,
        )
    ]
