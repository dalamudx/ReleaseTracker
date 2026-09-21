"""Build durable executor work from the bindings that actually select each target."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from .executor_scheduler_grouped_runtime_support import _ExecutorBindingRunContext
from .executor_scheduler_target_resolution import (
    _resolve_tracker_latest_target_details_from_storage,
    _target_identity_key,
)

if TYPE_CHECKING:
    from .config import ExecutorConfig
    from .storage.sqlite import SQLiteStorage


async def enqueue_executor_binding_targets(
    storage: "SQLiteStorage",
    executor_config: "ExecutorConfig",
    *,
    tracker_name: str | None = None,
) -> bool:
    """Queue work when any bound source/channel target changes.

    Grouped executors use a stable vector, so a release in one channel cannot
    be hidden by a newer release in another channel of the same tracker.
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
        if tracker_name is not None and bound_tracker_name != tracker_name:
            continue
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
                "identity_key": _target_identity_key(version, digest),
            }
        )

    if not targets:
        return False

    targets.sort(
        key=lambda target: (
            str(target["tracker_name"]),
            str(target["service"] or ""),
            int(target["tracker_source_id"] or 0),
            str(target["channel_name"]),
        )
    )
    revision_targets = [
        {
            "service": target["service"],
            "tracker_name": target["tracker_name"],
            "tracker_source_id": target["tracker_source_id"],
            "channel_name": target["channel_name"],
            "identity_key": target["identity_key"],
        }
        for target in targets
    ]
    serialized_identity = json.dumps(revision_targets, sort_keys=True, separators=(",", ":"))
    revision_identity = (
        "bindings:" + hashlib.sha256(serialized_identity.encode("utf-8")).hexdigest()
    )
    primary_target = targets[0]
    queued_tracker_name = tracker_name or str(primary_target["tracker_name"])
    existing_state = await storage.get_executor_desired_state(executor_config.id)
    if (
        existing_state is not None
        and existing_state.desired_state_revision == f"{queued_tracker_name}:{revision_identity}"
    ):
        return False

    previous_target = existing_state.desired_target if existing_state is not None else {}
    return await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_config.id,
        tracker_name=queued_tracker_name,
        previous_version=previous_target.get("current_version"),
        current_version=str(primary_target["display_version"]),
        previous_identity_key=previous_target.get("current_identity_key"),
        current_identity_key=revision_identity,
        binding_targets=targets,
    )


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
