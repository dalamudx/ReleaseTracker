from __future__ import annotations

from typing import Any

from ..config import normalize_executor_target_ref
from .base import BaseRuntimeAdapter, RuntimeTarget, RuntimeUpdateResult


class _ContainerRuntimeAdapter(BaseRuntimeAdapter):
    def __init__(self, runtime_connection, client=None):
        super().__init__(runtime_connection)
        self._client = client

    async def discover_targets(self) -> list[RuntimeTarget]:
        client = self._get_client()
        containers = client.containers.list(all=True)
        targets: list[RuntimeTarget] = []
        compose_containers_by_project: dict[str, list[Any]] = {}
        for container in containers:
            if not self._should_expose_container(container):
                continue
            compose_project = self._compose_project_for_container(container)
            compose_service = self._compose_service_for_container(container)
            if compose_project and compose_service:
                compose_containers_by_project.setdefault(compose_project, []).append(container)
                continue
            if self._is_managed_container(container):
                continue
            name = getattr(container, "name", None) or getattr(container, "id", "")
            targets.append(self._runtime_target(container, name))
        targets.extend(self._compose_runtime_targets(compose_containers_by_project))
        return targets

    def _should_expose_container(self, container) -> bool:
        return True

    async def validate_target_ref(self, target_ref: dict[str, Any]) -> None:
        normalize_executor_target_ref(
            target_ref,
            runtime_type=self.runtime_connection.type,
        )

    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        container = self._get_container(target_ref)
        image = self._extract_image(container)
        if not image:
            raise ValueError("Unable to resolve container image")
        return image

    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        container = self._get_container(target_ref)
        return {
            "runtime_type": self.runtime_connection.type,
            "container_id": getattr(container, "id", None),
            "container_name": getattr(container, "name", None),
            "image": current_image,
        }

    async def validate_snapshot(self, target_ref: dict[str, Any], snapshot: dict[str, Any]) -> None:
        self._ensure_target_ref_valid(target_ref)
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError("snapshot must be a non-empty dict")
        if not isinstance(snapshot.get("image"), str) or not snapshot["image"].strip():
            raise ValueError("snapshot.image must be a non-empty string")
        has_id = isinstance(snapshot.get("container_id"), str) and snapshot["container_id"].strip()
        has_name = (
            isinstance(snapshot.get("container_name"), str) and snapshot["container_name"].strip()
        )
        if not (has_id or has_name):
            raise ValueError("snapshot must include container_id or container_name")

    async def update_image(self, target_ref: dict[str, Any], new_image: str) -> RuntimeUpdateResult:
        if not isinstance(new_image, str) or not new_image.strip():
            raise ValueError("new_image must be a non-empty string")
        container = self._get_container(target_ref)
        old_image = self._extract_image(container)
        if old_image == new_image:
            return RuntimeUpdateResult(updated=False, old_image=old_image, new_image=new_image)
        client = self._get_client()
        if hasattr(client, "update_container_image"):
            client.update_container_image(container.id, new_image)
            return RuntimeUpdateResult(updated=True, old_image=old_image, new_image=new_image)
        if hasattr(container, "update_image"):
            container.update_image(new_image)
            return RuntimeUpdateResult(updated=True, old_image=old_image, new_image=new_image)
        raise ValueError("Runtime client does not support image-only updates")

    def _get_client(self):
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _get_container(self, target_ref: dict[str, Any]):
        self._ensure_target_ref_valid(target_ref)
        container_id = target_ref.get("container_id")
        container_name = target_ref.get("container_name")
        client = self._get_client()
        identifier = container_id or container_name
        return client.containers.get(identifier)

    def _ensure_target_ref_valid(self, target_ref: dict[str, Any]) -> None:
        container_id = target_ref.get("container_id")
        container_name = target_ref.get("container_name")
        if not container_id and not container_name:
            raise ValueError("target_ref must include container_id or container_name")

    def _extract_image(self, container) -> str | None:
        configured_ref = self._extract_configured_image_ref(container)
        if configured_ref:
            return configured_ref

        inspect_image_name = self._extract_inspect_image_name(container)
        if inspect_image_name:
            return inspect_image_name

        runtime_tag = self._extract_runtime_image_tag(container)
        if runtime_tag:
            return runtime_tag

        runtime_image_id = self._extract_runtime_image_id(container)
        if runtime_image_id:
            return runtime_image_id

        return None

    def _extract_configured_image_ref(self, container) -> str | None:
        attrs = getattr(container, "attrs", {}) or {}
        config = attrs.get("Config", {}) if isinstance(attrs, dict) else {}
        configured_image = config.get("Image") if isinstance(config, dict) else None
        if isinstance(configured_image, str) and configured_image.strip():
            return configured_image
        return None

    def _extract_runtime_image_tag(self, container) -> str | None:
        image = getattr(container, "image", None)
        if image is None:
            return None
        tags = getattr(image, "tags", None)
        if not isinstance(tags, list):
            return None
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                return tag
        return None

    def _extract_inspect_image_name(self, container) -> str | None:
        attrs = getattr(container, "attrs", {}) or {}
        inspect_image_name = attrs.get("ImageName") if isinstance(attrs, dict) else None
        if isinstance(inspect_image_name, str) and inspect_image_name.strip():
            return inspect_image_name
        return None

    def _extract_runtime_image_id(self, container) -> str | None:
        image = getattr(container, "image", None)
        if image is None:
            return None
        image_id = getattr(image, "id", None)
        if isinstance(image_id, str) and image_id.strip():
            return image_id
        return None

    def _runtime_target(self, container, name: str) -> RuntimeTarget:
        target_ref = {
            "mode": "container",
            "container_id": getattr(container, "id", None),
            "container_name": getattr(container, "name", None),
        }
        return RuntimeTarget(
            runtime_type=self.runtime_connection.type,
            name=name,
            target_ref=target_ref,
            image=self._extract_image(container),
        )

    def _compose_runtime_targets(
        self,
        containers_by_project: dict[str, list[Any]],
    ) -> list[RuntimeTarget]:
        targets: list[RuntimeTarget] = []
        for project, containers in sorted(containers_by_project.items()):
            if not containers:
                continue
            target_ref: dict[str, Any] = {
                "mode": "docker_compose",
                "project": project,
            }
            working_dir = self._compose_working_dir_for_containers(containers)
            if working_dir:
                target_ref["working_dir"] = working_dir
            config_files = self._compose_config_files_for_containers(containers)
            if config_files:
                target_ref["config_files"] = config_files

            services = self._compose_services_for_containers(containers)
            target_ref["services"] = services
            target_ref["service_count"] = len(services)

            targets.append(
                RuntimeTarget(
                    runtime_type=self.runtime_connection.type,
                    name=project,
                    target_ref=target_ref,
                    image=self._compose_target_image(services),
                )
            )
        return targets

    def _compose_labels_for_container(self, container) -> dict[str, str]:
        labels_by_key: dict[str, str] = {}
        attrs = getattr(container, "attrs", {}) or {}
        if isinstance(attrs, dict):
            config = attrs.get("Config")
            if isinstance(config, dict):
                config_labels = config.get("Labels") or config.get("labels")
                if isinstance(config_labels, dict):
                    labels_by_key.update(
                        {
                            key: value
                            for key, value in config_labels.items()
                            if isinstance(key, str) and isinstance(value, str)
                        }
                    )
            attrs_labels = attrs.get("Labels") or attrs.get("labels")
            if isinstance(attrs_labels, dict):
                labels_by_key.update(
                    {
                        key: value
                        for key, value in attrs_labels.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                )

        container_labels = getattr(container, "labels", None)
        if isinstance(container_labels, dict):
            labels_by_key.update(
                {
                    key: value
                    for key, value in container_labels.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
            )
        return labels_by_key

    def _compose_project_for_container(self, container) -> str | None:
        labels = self._compose_labels_for_container(container)
        project = labels.get("com.docker.compose.project") or labels.get(
            "io.podman.compose.project"
        )
        return project.strip() if isinstance(project, str) and project.strip() else None

    def _compose_service_for_container(self, container) -> str | None:
        labels = self._compose_labels_for_container(container)
        service = labels.get("com.docker.compose.service")
        return service.strip() if isinstance(service, str) and service.strip() else None

    def _compose_working_dir_for_containers(self, containers: list[Any]) -> str | None:
        for container in containers:
            labels = self._compose_labels_for_container(container)
            working_dir = labels.get("com.docker.compose.project.working_dir")
            if isinstance(working_dir, str) and working_dir.strip():
                return working_dir.strip()
        return None

    def _compose_config_files_for_containers(self, containers: list[Any]) -> list[str]:
        for container in containers:
            labels = self._compose_labels_for_container(container)
            config_files = labels.get("com.docker.compose.project.config_files")
            if not isinstance(config_files, str) or not config_files.strip():
                continue
            normalized = [item.strip() for item in config_files.split(",") if item.strip()]
            if normalized:
                return normalized
        return []

    def _compose_services_for_containers(self, containers: list[Any]) -> list[dict[str, Any]]:
        containers_by_service: dict[str, list[Any]] = {}
        for container in containers:
            service = self._compose_service_for_container(container)
            if service:
                containers_by_service.setdefault(service, []).append(container)

        services: list[dict[str, Any]] = []
        for service, service_containers in sorted(containers_by_service.items()):
            images = sorted(
                {
                    image
                    for container in service_containers
                    if (image := self._extract_image(container))
                }
            )
            service_payload: dict[str, Any] = {
                "service": service,
                "replica_count": len(service_containers),
            }
            if len(images) == 1:
                service_payload["image"] = images[0]
            elif images:
                service_payload["image"] = None
            services.append(service_payload)
        return services

    def _compose_target_image(self, services: list[dict[str, Any]]) -> str | None:
        images = sorted(
            {
                image
                for service in services
                if isinstance((image := service.get("image")), str) and image.strip()
            }
        )
        return images[0] if len(images) == 1 else None

    def _is_managed_container(self, container) -> bool:
        return False

    def _create_client(self):
        raise NotImplementedError
