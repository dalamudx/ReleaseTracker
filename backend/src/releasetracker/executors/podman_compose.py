"""Podman Compose service update orchestration."""

from __future__ import annotations

from . import container_recovery

import logging
from typing import Any, TYPE_CHECKING

from .base import RuntimeMutationError, RuntimeUpdateResult

if TYPE_CHECKING:
    from .podman import PodmanRuntimeAdapter


logger = logging.getLogger(__name__)


async def update_compose_services(
    adapter: "PodmanRuntimeAdapter",
    target_ref: dict[str, Any],
    service_target_images: dict[str, str],
) -> RuntimeUpdateResult:
    if target_ref.get("mode") != "docker_compose":
        raise ValueError("target_ref.mode must be docker_compose")
    project = target_ref.get("project")
    if not isinstance(project, str) or not project.strip():
        raise ValueError("target_ref.project must be a non-empty string")
    if not service_target_images:
        return RuntimeUpdateResult(
            updated=False,
            old_image=None,
            new_image=None,
            message="no podman compose services require update",
        )

    service_containers = adapter._find_compose_service_containers(project)
    update_plan: dict[str, str] = {}
    current_images_by_service: dict[str, str] = {}
    target_pod_id: str | None = None
    for service, target_image in sorted(service_target_images.items()):
        if not isinstance(target_image, str) or not target_image.strip():
            raise ValueError("compose target images must be non-empty strings")
        target_image = target_image.strip()
        containers = service_containers.get(service) or []
        if not containers:
            raise ValueError(f"Podman Compose service container missing: {service}")

        current_images = sorted(
            {image for container in containers if (image := adapter._extract_image(container))}
        )
        if len(current_images) > 1:
            raise ValueError(f"Podman Compose service has inconsistent replica images: {service}")
        if not current_images:
            raise ValueError(f"Podman Compose service image missing: {service}")

        for container in containers:
            attrs = getattr(container, "attrs", {}) or {}
            pod_id = attrs.get("Pod") or ""
            if not isinstance(pod_id, str) or not pod_id.strip():
                raise ValueError(
                    "Podman compose grouped pod-aware update requires pod-backed services. "
                    f"Service '{service}' includes non-pod container '{getattr(container, 'id', '')}'."
                )
            pod_id = pod_id.strip()
            if target_pod_id is None:
                target_pod_id = pod_id
            elif target_pod_id != pod_id:
                raise ValueError(
                    "Podman compose grouped pod-aware update only supports targets in one pod. "
                    f"Found pod '{target_pod_id}' and '{pod_id}'."
                )

        current_image = current_images[0]
        current_images_by_service[service] = current_image
        if current_image != target_image:
            update_plan[service] = target_image

    specs = adapter._build_grouped_runtime_recreate_specs(service_containers, update_plan)

    if not update_plan:
        return RuntimeUpdateResult(
            updated=False,
            old_image=None,
            new_image=None,
            message="podman compose services already at target images",
        )

    client = adapter._get_client()
    adapter._validate_podman_grouped_recreate_specs(specs, target_pod_id=target_pod_id)
    for image in sorted(set(update_plan.values())):
        client.images.pull(image)

    new_container_ids: list[str] = []
    backup_names_by_spec_key: dict[str, str] = {}
    removal_order = list(reversed(specs))
    try:
        for spec in removal_order:
            container = client.containers.get(spec.container_id)
            adapter._stop_grouped_container_with_sdk_decode_tolerance(client, container)

        for spec in removal_order:
            container = client.containers.get(spec.container_id)
            backup_name = adapter._podman_replacement_backup_name(spec)
            backup_names_by_spec_key[adapter._grouped_runtime_recreate_spec_key(spec)] = backup_name
            if spec.pod_id or spec.pod_name:
                original_name = spec.container_name or getattr(container, "name", None)
                if not isinstance(original_name, str) or not original_name.strip():
                    raise ValueError("podman grouped recreate spec missing container name")
                adapter._rename_grouped_container_for_replacement(container, backup_name)
                adapter._remove_grouped_container_with_sdk_decode_tolerance(client, container)
            else:
                adapter._remove_grouped_container_with_sdk_decode_tolerance(client, container)

        recreated_pods: dict[str, Any] = {}
        specs_by_pod_key = adapter._group_pod_backed_specs_by_pod_key(specs)
        for spec in specs:
            create_config = adapter._podman_grouped_create_config_for_spec(spec, client=client)
            pod_key = adapter._grouped_spec_pod_key(spec)
            if pod_key is not None:
                recreated_pod_ref = recreated_pods.get(pod_key)
                if recreated_pod_ref is None:
                    pod_name_hint = create_config.get("pod")
                    recreated_pod_ref, _ = adapter._recreate_pod_for_grouped_specs(
                        client,
                        specs_by_pod_key[pod_key],
                        pod_name_hint=pod_name_hint if isinstance(pod_name_hint, str) else None,
                    )
                    if recreated_pod_ref is not None:
                        recreated_pods[pod_key] = recreated_pod_ref
                if recreated_pod_ref is not None:
                    create_config["pod"] = recreated_pod_ref

            replacement = adapter._create_podman_grouped_container(client, create_config)
            adapter._restore_container_networks(client, replacement, spec, phase="forward")
            replacement.start()
            backup_name = backup_names_by_spec_key.get(
                adapter._grouped_runtime_recreate_spec_key(spec)
            )
            if backup_name:
                adapter._remove_replaced_grouped_container_backup(client, backup_name)
            replacement_id = getattr(replacement, "id", None)
            if isinstance(replacement_id, str) and replacement_id.strip():
                new_container_ids.append(replacement_id)
    except Exception as exc:
        logger.warning(
            "Podman grouped compose update failed after destructive steps began: "
            "exception_class=%s exception_message=%s affected_container_count=%s",
            exc.__class__.__name__,
            adapter._safe_exception_message(exc),
            len(specs),
        )
        raise RuntimeMutationError(
            "podman grouped compose update failed after destructive steps began; "
            "manual rollback from snapshot is required: "
            f"{exc}",
            destructive_started=True,
        ) from exc

    return RuntimeUpdateResult(
        updated=True,
        old_image="; ".join(
            f"{service}={image}"
            for service, image in sorted(current_images_by_service.items())
            if service in update_plan
        ),
        new_image="; ".join(f"{service}={image}" for service, image in sorted(update_plan.items())),
        new_container_id=",".join(new_container_ids) or None,
        message=(
            "podman compose grouped pod-aware update completed "
            f"for {len(specs)} container(s) in pod '{target_pod_id}'"
        ),
    )


async def _capture_compose_snapshot(
    adapter: "PodmanRuntimeAdapter",
    target_ref: dict[str, Any],
    current_image: str,
) -> dict[str, Any]:
    project = target_ref.get("project")
    if not isinstance(project, str) or not project.strip():
        raise ValueError("target_ref.project must be a non-empty string")
    service_containers = adapter._find_compose_service_containers(project)
    service_images = await adapter.fetch_compose_service_images(target_ref)
    update_plan = {
        service: image
        for service, image in service_images.items()
        if isinstance(image, str) and image.strip()
    }
    specs = adapter._build_grouped_runtime_recreate_specs(service_containers, update_plan)
    adapter._validate_podman_grouped_recreate_specs(specs, target_pod_id=None)
    snapshots = []
    for spec in specs:
        snapshot = dict(spec.snapshot_payload)
        snapshot["compose_project"] = spec.compose_project
        snapshot["compose_service"] = spec.compose_service
        snapshot["create_config"] = adapter._apply_podman_host_config_preservation(
            dict(snapshot.get("create_config") or {}),
            spec.host_config,
        )
        snapshots.append(snapshot)
    image_summary = current_image or adapter._compose_snapshot_image_summary(service_images)
    return {
        "runtime_type": adapter.runtime_connection.type,
        "mode": "docker_compose",
        "project": project.strip(),
        "image": image_summary,
        "services": list(target_ref.get("services") or []),
        "snapshots": snapshots,
    }


def _validate_compose_snapshot(
    adapter: "PodmanRuntimeAdapter",
    target_ref: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("snapshot must be a non-empty dict")
    if snapshot.get("mode") != "docker_compose":
        raise ValueError("snapshot.mode must be docker_compose")
    project = target_ref.get("project")
    if not isinstance(project, str) or not project.strip():
        raise ValueError("target_ref.project must be a non-empty string")
    if snapshot.get("runtime_type") != adapter.runtime_connection.type:
        raise ValueError("snapshot runtime_type does not match recovery runtime")
    snapshot_project = snapshot.get("project")
    if snapshot_project != project.strip():
        raise ValueError("snapshot.project must match target_ref.project")
    if not isinstance(snapshot.get("image"), str) or not snapshot["image"].strip():
        raise ValueError("snapshot.image must be a non-empty string")
    snapshots = snapshot.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValueError("snapshot.snapshots must be a non-empty list")
    for item in snapshots:
        adapter._validate_compose_container_snapshot(item)


def _validate_compose_container_snapshot(adapter: "PodmanRuntimeAdapter", snapshot: Any) -> None:
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("compose snapshot entry must be a non-empty dict")
    if not isinstance(snapshot.get("image"), str) or not snapshot["image"].strip():
        raise ValueError("compose snapshot entry image must be a non-empty string")
    has_id = isinstance(snapshot.get("container_id"), str) and snapshot["container_id"].strip()
    has_name = (
        isinstance(snapshot.get("container_name"), str) and snapshot["container_name"].strip()
    )
    if not (has_id or has_name):
        raise ValueError("compose snapshot entry must include container_id or container_name")
    create_config = snapshot.get("create_config")
    if not isinstance(create_config, dict) or not create_config:
        raise ValueError("compose snapshot entry create_config must be a non-empty dict")
    if create_config.get("image") != snapshot.get("image"):
        raise ValueError("compose snapshot entry create_config.image must match image")
    adapter._validate_snapshot_target_identity(
        {"container_name": snapshot.get("container_name")}, snapshot
    )
    container_recovery.validate_evidence(snapshot)


async def _recover_compose_from_snapshot(
    adapter: "PodmanRuntimeAdapter",
    target_ref: dict[str, Any],
    snapshot: dict[str, Any],
) -> RuntimeUpdateResult:
    adapter._validate_compose_snapshot(target_ref, snapshot)
    recovered_ids: list[str] = []
    snapshots = snapshot.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("snapshot.snapshots must be a list")
    client = adapter._get_client()
    for item in snapshots:
        container_recovery.prepare_image(adapter, item)
        adapter._inspect_recovery_conflict(item["create_config"])
    current_pod_refs = adapter._recover_snapshot_pods(client, snapshots)
    for item in snapshots:
        result = await adapter._recover_grouped_container_from_snapshot(
            item,
            client=client,
            pod_ref_override=adapter._snapshot_pod_ref_override(item, current_pod_refs),
        )
        if result.new_container_id:
            recovered_ids.append(result.new_container_id)
    await container_recovery.verify_group(adapter, recovered_ids, snapshots)
    recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
    return RuntimeUpdateResult(
        updated=True,
        old_image=None,
        new_image=recovered_image,
        message="podman compose recovered from snapshot",
        new_container_id=",".join(recovered_ids) or None,
    )


def _compose_snapshot_image_summary(service_images: dict[str, str]) -> str:
    return "; ".join(
        f"{service}={image}"
        for service, image in sorted(service_images.items())
        if isinstance(image, str) and image.strip()
    )
