from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

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

    async def probe_runtime_native_health(
        self,
        target_ref: dict[str, Any],
        *,
        baseline: dict[str, Any],
        services: list[str] | None = None,
    ):
        """Container runtime-native readiness.

        Healthy iff the container state is ``running`` AND either the image
        reports ``healthy`` via Docker/Podman HEALTHCHECK or (when no
        HEALTHCHECK is defined) the restart count has not increased above
        the baseline recorded at the end of the image update.
        """
        # Imported here to avoid a circular dependency via
        # ``health_check.types`` → ``config`` → ``executors`` → ``container_runtime``.
        if target_ref.get("mode") == "docker_compose":
            return await self._probe_compose_runtime_native_health(
                target_ref,
                baseline=baseline,
                services=services,
            )

        try:
            container = self._get_container(target_ref)
        except Exception as exc:  # pragma: no cover - surfaced as runtime_api_error
            from .health_check.types import ProbeAttemptResult  # noqa: WPS433

            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"failed to fetch container: {exc}",
            )

        attrs = getattr(container, "attrs", {}) or {}
        return self._probe_container_attrs_runtime_native(attrs, baseline=baseline)

    async def _probe_compose_runtime_native_health(
        self,
        target_ref: dict[str, Any],
        *,
        baseline: dict[str, Any],
        services: list[str] | None = None,
    ):
        from .health_check.types import ProbeAttemptResult  # noqa: WPS433

        project = target_ref.get("project")
        if not isinstance(project, str) or not project.strip():
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="target_ref.project must be a non-empty string",
            )
        selected_services = {service.lower() for service in services} if services else None
        containers_by_service = self._find_compose_service_containers(project.strip())
        per_service: dict[str, ProbeAttemptResult] = {}
        detail: dict[str, Any] = {"services": {}}
        matched = False
        aggregate_last_error: str | None = None

        for service, containers in sorted(containers_by_service.items()):
            if selected_services is not None and service.lower() not in selected_services:
                continue
            matched = True
            if not containers:
                result = ProbeAttemptResult(
                    healthy=False,
                    error_category="runtime_api_error",
                    last_error=f"compose service has no containers: {service}",
                )
            else:
                service_results = [
                    self._probe_container_attrs_runtime_native(
                        getattr(container, "attrs", {}) or {},
                        baseline=baseline,
                    )
                    for container in containers
                ]
                unhealthy = next((item for item in service_results if not item.healthy), None)
                result = unhealthy or ProbeAttemptResult(
                    healthy=True,
                    detail={"replicas": len(containers)},
                )
            per_service[service] = result
            detail["services"][service] = result.detail
            if not result.healthy and aggregate_last_error is None:
                aggregate_last_error = f"{service}: {result.last_error or 'unhealthy'}"

        if not matched:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                detail=detail,
                last_error="no compose services matched runtime-native health check",
            )

        overall_healthy = all(result.healthy for result in per_service.values())
        return ProbeAttemptResult(
            healthy=overall_healthy,
            error_category="ok" if overall_healthy else "runtime_api_error",
            detail=detail,
            last_error=None if overall_healthy else aggregate_last_error,
            per_service=per_service,
        )

    def _probe_container_attrs_runtime_native(
        self,
        attrs: dict[str, Any],
        *,
        baseline: dict[str, Any],
    ):
        from .health_check.types import ProbeAttemptResult  # noqa: WPS433

        state = attrs.get("State", {}) if isinstance(attrs, dict) else {}
        status = state.get("Status") if isinstance(state, dict) else None
        is_running = bool((state or {}).get("Running")) or status == "running"
        health = None
        if isinstance(state, dict):
            health_obj = state.get("Health")
            if isinstance(health_obj, dict):
                health = health_obj.get("Status")
        restart_count = attrs.get("RestartCount") if isinstance(attrs, dict) else None
        detail: dict[str, Any] = {
            "status": status,
            "health": health,
            "restart_count": restart_count,
        }
        if not is_running:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                detail=detail,
                last_error=f"container state is {status!r}; expected 'running'",
            )
        if health is not None:
            if health == "healthy":
                return ProbeAttemptResult(healthy=True, detail=detail)
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                detail=detail,
                last_error=f"container health is {health!r}; expected 'healthy'",
            )
        baseline_restart_count = baseline.get("restart_count")
        if (
            isinstance(restart_count, int)
            and isinstance(baseline_restart_count, int)
            and restart_count > baseline_restart_count
        ):
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                detail=detail,
                last_error=(
                    f"container restart_count increased from {baseline_restart_count} "
                    f"to {restart_count}"
                ),
            )
        return ProbeAttemptResult(healthy=True, detail=detail)

    async def resolve_probe_hosts(
        self,
        target_ref: dict[str, Any],
        *,
        services: list[str] | None = None,
        default_port: int | None = None,
    ):
        """Resolve the container's primary bridge IP for legacy HTTP / TCP probes."""
        from .health_check.host_resolver import ProbeHost  # noqa: WPS433

        del services  # Not used for single-container targets.

        container = self._get_container(target_ref)
        attrs = getattr(container, "attrs", {}) or {}
        primary_ip = self._extract_primary_container_ip(attrs)
        if primary_ip is None:
            host_port = self._extract_published_host_port(attrs, default_port)
            if host_port is not None:
                return [
                    ProbeHost(
                        service=None,
                        host=self._runtime_connection_probe_host(),
                        port=host_port,
                    )
                ]
            raise ValueError("container has no primary IP and no published host port")

        return [ProbeHost(service=None, host=primary_ip, port=default_port)]

    async def resolve_auto_probe_hosts(
        self,
        target_ref: dict[str, Any],
        *,
        services: list[str] | None = None,
        default_port: int | None = None,
    ):
        """Resolve published host ports for Docker / Podman auto fallback probes."""
        from .health_check.host_resolver import ProbeHost  # noqa: WPS433

        target_mode = target_ref.get("mode")
        runtime_host = self._runtime_connection_probe_host()
        if target_mode == "docker_compose":
            project = target_ref.get("project")
            if not isinstance(project, str) or not project.strip():
                raise ValueError("target_ref.project must be a non-empty string")
            selected_services = [service.lower() for service in services] if services else None
            containers_by_service = self._find_compose_service_containers(project.strip())
            probe_hosts: list[ProbeHost] = []
            for service, containers in sorted(containers_by_service.items()):
                if selected_services is not None and service.lower() not in selected_services:
                    continue
                if not containers:
                    continue
                if len(containers) != 1:
                    raise ValueError(
                        f"auto host-port probe for service '{service}' requires exactly one replica"
                    )
                attrs = getattr(containers[0], "attrs", {}) or {}
                host_port = self._select_published_host_port(attrs, default_port)
                probe_hosts.append(ProbeHost(service=service, host=runtime_host, port=host_port))
            if not probe_hosts:
                raise ValueError("no compose services with published host ports were found")
            return probe_hosts

        del services
        container = self._get_container(target_ref)
        attrs = getattr(container, "attrs", {}) or {}
        host_port = self._select_published_host_port(attrs, default_port)
        return [ProbeHost(service=None, host=runtime_host, port=host_port)]

    async def has_runtime_native_healthcheck(
        self,
        target_ref: dict[str, Any],
        *,
        services: list[str] | None = None,
    ) -> bool:
        target_mode = target_ref.get("mode")
        if target_mode == "docker_compose":
            project = target_ref.get("project")
            if not isinstance(project, str) or not project.strip():
                return False
            selected_services = {service.lower() for service in services} if services else None
            containers_by_service = self._find_compose_service_containers(project.strip())
            matched = False
            for service, containers in containers_by_service.items():
                if selected_services is not None and service.lower() not in selected_services:
                    continue
                matched = True
                if not containers or not any(self._container_has_healthcheck(container) for container in containers):
                    return False
            return matched

        del services
        try:
            container = self._get_container(target_ref)
        except Exception:
            return False
        return self._container_has_healthcheck(container)

    @staticmethod
    def _extract_primary_container_ip(attrs: dict[str, Any]) -> str | None:
        network_settings = (
            attrs.get("NetworkSettings") if isinstance(attrs, dict) else None
        )
        networks: dict[str, Any] = {}
        if isinstance(network_settings, dict):
            nested = network_settings.get("Networks")
            if isinstance(nested, dict):
                networks = nested

        for network in networks.values():
            if not isinstance(network, dict):
                continue
            ip = network.get("IPAddress")
            if isinstance(ip, str) and ip.strip():
                return ip.strip()
        if isinstance(network_settings, dict):
            fallback_ip = network_settings.get("IPAddress")
            if isinstance(fallback_ip, str) and fallback_ip.strip():
                return fallback_ip.strip()
        return None

    @staticmethod
    def _container_has_healthcheck(container) -> bool:
        attrs = getattr(container, "attrs", {}) or {}
        if not isinstance(attrs, dict):
            return False
        state = attrs.get("State")
        if isinstance(state, dict) and isinstance(state.get("Health"), dict):
            return True
        config = attrs.get("Config")
        if isinstance(config, dict) and isinstance(config.get("Healthcheck"), dict):
            return True
        return False

    def _runtime_connection_probe_host(self) -> str:
        socket = self.runtime_connection.config.get("socket")
        if not isinstance(socket, str) or not socket.strip():
            return "127.0.0.1"
        parsed = urlparse(socket.strip())
        if parsed.scheme == "tcp" and parsed.hostname:
            return parsed.hostname
        return "127.0.0.1"

    def _select_published_host_port(self, attrs: dict[str, Any], container_port: int | None) -> int:
        host_port = self._extract_published_host_port(attrs, container_port)
        if host_port is not None:
            return host_port
        if container_port is not None:
            raise ValueError(f"container has no published host port for container port {container_port}")

        all_host_ports = self._extract_all_published_host_ports(attrs)
        if len(all_host_ports) == 1:
            return all_host_ports[0]
        if len(all_host_ports) > 1:
            raise ValueError(
                "multiple published host ports found; configure a container port for auto probing"
            )
        raise ValueError("container has no published host ports")

    @staticmethod
    def _extract_all_published_host_ports(attrs: dict[str, Any]) -> list[int]:
        if not isinstance(attrs, dict):
            return []
        network_settings = attrs.get("NetworkSettings")
        if not isinstance(network_settings, dict):
            return []
        ports = network_settings.get("Ports")
        if not isinstance(ports, dict):
            return []
        host_ports: list[int] = []
        for value in ports.values():
            if not isinstance(value, list):
                continue
            for mapping in value:
                if not isinstance(mapping, dict):
                    continue
                host_port = mapping.get("HostPort")
                if isinstance(host_port, str) and host_port.isdigit():
                    host_ports.append(int(host_port))
                elif isinstance(host_port, int):
                    host_ports.append(host_port)
        return sorted(set(host_ports))

    @staticmethod
    def _extract_published_host_port(
        attrs: dict[str, Any], container_port: int | None
    ) -> int | None:
        if not isinstance(attrs, dict) or container_port is None:
            return None
        network_settings = attrs.get("NetworkSettings")
        if not isinstance(network_settings, dict):
            return None
        ports = network_settings.get("Ports")
        if not isinstance(ports, dict):
            return None
        for key, value in ports.items():
            # Key looks like "8080/tcp"; value is a list of {"HostPort": ...}.
            if not isinstance(key, str):
                continue
            port_part = key.split("/", 1)[0]
            if not port_part.isdigit() or int(port_part) != container_port:
                continue
            if not isinstance(value, list) or not value:
                continue
            for mapping in value:
                if not isinstance(mapping, dict):
                    continue
                host_port = mapping.get("HostPort")
                if isinstance(host_port, str) and host_port.isdigit():
                    return int(host_port)
                if isinstance(host_port, int):
                    return host_port
        return None

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
