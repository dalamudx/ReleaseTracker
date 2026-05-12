from __future__ import annotations

import importlib
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from .base import RuntimeMutationError, RuntimeUpdateResult
from .compose_runtime_update import GroupedRuntimeRecreateSpec, build_grouped_runtime_recreate_spec
from .container_runtime import _ContainerRuntimeAdapter


class DockerRuntimeAdapter(_ContainerRuntimeAdapter):
    async def fetch_compose_service_images(self, target_ref: dict[str, Any]) -> dict[str, str]:
        if target_ref.get("mode") != "docker_compose":
            raise ValueError("target_ref.mode must be docker_compose")
        project = target_ref.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("target_ref.project must be a non-empty string")

        result: dict[str, str] = {}
        for service, containers in self._find_compose_service_containers(project).items():
            images = sorted(
                {image for container in containers if (image := self._extract_image(container))}
            )
            if len(images) > 1:
                raise ValueError(
                    f"Docker Compose service has inconsistent replica images: {service}"
                )
            if images:
                result[service] = images[0]
        return result

    async def update_compose_services(
        self,
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
                message="no docker compose services require update",
            )

        service_containers = self._find_compose_service_containers(project)
        update_plan: dict[str, str] = {}
        current_images: dict[str, str] = {}
        for service, target_image in sorted(service_target_images.items()):
            normalized_target_image = self._normalize_target_image(target_image)
            if normalized_target_image is None:
                raise ValueError("compose target images must be non-empty strings")
            containers = service_containers.get(service) or []
            if not containers:
                raise ValueError(f"Docker Compose service container missing: {service}")
            current_image = self._preflight_compose_service_containers(service, containers)
            current_images[service] = current_image
            if current_image != normalized_target_image:
                update_plan[service] = normalized_target_image

        if not update_plan:
            return RuntimeUpdateResult(
                updated=False,
                old_image=None,
                new_image=None,
                message="docker compose services already at target images",
            )

        specs = self._build_grouped_runtime_recreate_specs(service_containers, update_plan)
        self._validate_grouped_runtime_recreate_specs(specs)
        ordered_specs = self._order_grouped_runtime_recreate_specs(specs)
        client = self._get_client()
        images = getattr(client, "images", None)
        if images is not None and hasattr(images, "pull"):
            for target_image in sorted({spec.target_image for spec in ordered_specs}):
                images.pull(target_image)

        created_containers = []
        try:
            for spec in reversed(ordered_specs):
                self._resolve_compose_container(spec).stop()
            for spec in reversed(ordered_specs):
                self._resolve_compose_container(spec).remove()

            for spec in ordered_specs:
                new_container = self._create_docker_container(client, spec.create_config)
                created_containers.append(new_container)
                self._restore_container_networks(client, new_container, spec)
                new_container.start()
        except Exception as exc:
            for container in created_containers:
                self._remove_container_if_present(container)
            raise RuntimeMutationError(
                "docker compose grouped update failed after destructive steps began; "
                "manual rollback from snapshot is required: "
                f"{exc}",
                destructive_started=True,
            ) from exc

        return RuntimeUpdateResult(
            updated=True,
            old_image="; ".join(
                f"{service}={image}"
                for service, image in sorted(current_images.items())
                if service in update_plan
            ),
            new_image="; ".join(
                f"{service}={image}" for service, image in sorted(update_plan.items())
            ),
            message="docker compose grouped update applied",
        )

    def _create_client(self):
        try:
            docker = importlib.import_module("docker")
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python dependency 'docker' required by DockerRuntimeAdapter"
            ) from exc

        config = self.runtime_connection.config
        base_url = config.get("socket")
        tls_verify = config.get("tls_verify", False)
        api_version = config.get("api_version")

        tls_config = None
        if tls_verify:
            client_cert = self.runtime_connection.secrets.get("client_cert")
            client_key = self.runtime_connection.secrets.get("client_key")
            ca_cert = self.runtime_connection.secrets.get("ca_cert")
            cert_pair = (
                (client_cert, client_key)
                if isinstance(client_cert, str) and isinstance(client_key, str)
                else None
            )
            tls_config = docker.tls.TLSConfig(
                client_cert=cert_pair,
                ca_cert=ca_cert if isinstance(ca_cert, str) else None,
                verify=True,
            )

        return docker.DockerClient(
            base_url=base_url,
            version=api_version,
            tls=tls_config,
        )

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

        recreate_spec = self._build_recreate_spec_from_inspect(container, new_image)
        if recreate_spec is None:
            raise ValueError(
                f"Docker cannot recreate container '{container.id}' without a restorable create configuration"
            )

        images = getattr(client, "images", None)
        if images is not None and hasattr(images, "pull"):
            images.pull(new_image)

        new_container = None
        try:
            container.stop()
            container.remove()
            new_container = self._create_docker_container(client, recreate_spec.create_config)
            self._restore_container_networks(client, new_container, recreate_spec)
            new_container.start()
        except Exception as exc:
            if new_container is not None:
                self._remove_container_if_present(new_container)
            raise RuntimeMutationError(
                "docker update failed after destructive steps began; "
                "manual rollback from snapshot is required: "
                f"{exc}",
                destructive_started=True,
            ) from exc

        return RuntimeUpdateResult(
            updated=True,
            old_image=old_image,
            new_image=new_image,
            new_container_id=getattr(new_container, "id", None),
        )

    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        if target_ref.get("mode") == "docker_compose":
            service_images = await self.fetch_compose_service_images(target_ref)
            if not service_images:
                raise ValueError("Unable to resolve Docker Compose service images")
            return self._compose_snapshot_image_summary(service_images)
        return await super().get_current_image(target_ref)

    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        if target_ref.get("mode") == "docker_compose":
            return await self._capture_compose_snapshot(target_ref, current_image)

        container = self._get_container(target_ref)
        spec = self._build_recreate_spec_from_inspect(container, current_image)
        if spec is None:
            return {
                "runtime_type": self.runtime_connection.type,
                "container_id": getattr(container, "id", None),
                "container_name": getattr(container, "name", None),
                "image": current_image,
            }
        return dict(spec.snapshot_payload)

    async def validate_snapshot(self, target_ref: dict[str, Any], snapshot: dict[str, Any]) -> None:
        if target_ref.get("mode") == "docker_compose":
            self._validate_compose_snapshot(target_ref, snapshot)
            return

        await super().validate_snapshot(target_ref, snapshot)
        create_config = snapshot.get("create_config")
        if not create_config and hasattr(self._get_client(), "update_container_image"):
            return
        if not isinstance(create_config, dict) or not create_config:
            raise ValueError("snapshot.create_config must be a non-empty dict")
        if create_config.get("image") != snapshot.get("image"):
            raise ValueError("snapshot.create_config.image must match snapshot.image")

    async def recover_from_snapshot(
        self, target_ref: dict[str, Any], snapshot: dict[str, Any]
    ) -> RuntimeUpdateResult:
        if target_ref.get("mode") == "docker_compose":
            return await self._recover_compose_from_snapshot(target_ref, snapshot)

        await self.validate_snapshot(target_ref, snapshot)

        create_config = snapshot.get("create_config")
        if not isinstance(create_config, dict):
            raise ValueError("snapshot.create_config must be a dict")

        client = self._get_client()
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        images = getattr(client, "images", None)
        if recovered_image and images is not None and hasattr(images, "pull"):
            images.pull(recovered_image)

        existing_container = self._cleanup_replacement_conflict(client, snapshot, create_config)
        if existing_container is not None:
            state = getattr(existing_container, "attrs", {}) or {}
            is_running = bool((state.get("State") or {}).get("Running"))
            if not is_running:
                existing_container.start()
            return RuntimeUpdateResult(
                updated=True,
                old_image=None,
                new_image=recovered_image,
                message="runtime recovered from snapshot",
                new_container_id=getattr(existing_container, "id", None),
            )

        recovered_container = None
        try:
            recovered_container = self._create_docker_container(client, create_config)
            self._restore_container_networks_from_snapshot(client, recovered_container, snapshot)
            recovered_container.start()
        except Exception:
            if recovered_container is not None:
                self._remove_container_if_present(recovered_container)
            raise

        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=recovered_image,
            message="runtime recovered from snapshot",
            new_container_id=getattr(recovered_container, "id", None),
        )

    async def _capture_compose_snapshot(
        self,
        target_ref: dict[str, Any],
        current_image: str,
    ) -> dict[str, Any]:
        project = target_ref.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("target_ref.project must be a non-empty string")
        service_containers = self._find_compose_service_containers(project)
        service_images = await self.fetch_compose_service_images(target_ref)
        update_plan = {
            service: image
            for service, image in service_images.items()
            if isinstance(image, str) and image.strip()
        }
        specs = self._build_grouped_runtime_recreate_specs(service_containers, update_plan)
        self._validate_grouped_runtime_recreate_specs(specs)
        ordered_specs = self._order_grouped_runtime_recreate_specs(specs)
        snapshots = []
        for spec in ordered_specs:
            snapshot = dict(spec.snapshot_payload)
            snapshot["compose_project"] = spec.compose_project
            snapshot["compose_service"] = spec.compose_service
            snapshots.append(snapshot)
        image_summary = current_image or self._compose_snapshot_image_summary(service_images)
        return {
            "runtime_type": self.runtime_connection.type,
            "mode": "docker_compose",
            "project": project.strip(),
            "image": image_summary,
            "services": list(target_ref.get("services") or []),
            "snapshots": snapshots,
        }

    def _validate_compose_snapshot(
        self,
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
        snapshot_project = snapshot.get("project")
        if snapshot_project != project.strip():
            raise ValueError("snapshot.project must match target_ref.project")
        if not isinstance(snapshot.get("image"), str) or not snapshot["image"].strip():
            raise ValueError("snapshot.image must be a non-empty string")
        snapshots = snapshot.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            raise ValueError("snapshot.snapshots must be a non-empty list")
        for item in snapshots:
            self._validate_compose_container_snapshot(item)

    def _validate_compose_container_snapshot(self, snapshot: Any) -> None:
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

    async def _recover_compose_from_snapshot(
        self,
        target_ref: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> RuntimeUpdateResult:
        self._validate_compose_snapshot(target_ref, snapshot)
        recovered_ids: list[str] = []
        snapshots = snapshot.get("snapshots")
        if not isinstance(snapshots, list):
            raise ValueError("snapshot.snapshots must be a list")
        for item in snapshots:
            result = await self.recover_from_snapshot(
                self._target_ref_for_compose_snapshot_entry(item),
                item,
            )
            if result.new_container_id:
                recovered_ids.append(result.new_container_id)
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=recovered_image,
            message="docker compose recovered from snapshot",
            new_container_id=",".join(recovered_ids) or None,
        )

    @staticmethod
    def _target_ref_for_compose_snapshot_entry(snapshot: dict[str, Any]) -> dict[str, str]:
        target_ref: dict[str, str] = {}
        container_id = snapshot.get("container_id")
        container_name = snapshot.get("container_name")
        if isinstance(container_id, str) and container_id.strip():
            target_ref["container_id"] = container_id.strip()
        if isinstance(container_name, str) and container_name.strip():
            target_ref["container_name"] = container_name.strip()
        if not target_ref:
            raise ValueError("compose snapshot entry missing container identity")
        return target_ref

    @staticmethod
    def _compose_snapshot_image_summary(service_images: dict[str, str]) -> str:
        return "; ".join(
            f"{service}={image}"
            for service, image in sorted(service_images.items())
            if isinstance(image, str) and image.strip()
        )

    def _create_docker_container(self, client, create_config: dict[str, Any]):
        create_kwargs = self._sanitize_docker_create_kwargs(create_config)
        exposed_ports = create_kwargs.pop("_releasetracker_exposed_ports", None)
        if not exposed_ports:
            return client.containers.create(**create_kwargs)

        api_client = getattr(client, "api", None)
        if api_client is None or not hasattr(api_client, "create_container"):
            return client.containers.create(**create_kwargs)

        try:
            docker_containers = importlib.import_module("docker.models.containers")
            convert_create_args = docker_containers._create_container_args
        except (AttributeError, ImportError):
            return client.containers.create(**create_kwargs)

        api_version = getattr(api_client, "_version", None)
        raw_create_kwargs = convert_create_args({**create_kwargs, "version": api_version})
        raw_ports = list(raw_create_kwargs.get("ports") or [])
        existing_raw_ports = {self._docker_raw_port_key(port) for port in raw_ports}
        for exposed_port in exposed_ports:
            raw_port = self._parse_docker_port_spec(exposed_port)
            if raw_port is not None and raw_port not in existing_raw_ports:
                raw_ports.append(raw_port)
                existing_raw_ports.add(raw_port)
        if raw_ports:
            raw_create_kwargs["ports"] = raw_ports

        response = api_client.create_container(**raw_create_kwargs)
        container_id = response.get("Id") if isinstance(response, dict) else None
        return client.containers.get(container_id)

    @staticmethod
    def _sanitize_docker_create_kwargs(create_config: dict[str, Any]) -> dict[str, Any]:
        create_kwargs = dict(create_config)
        create_kwargs.pop("stop_timeout", None)
        return create_kwargs

    @staticmethod
    def _parse_docker_port_spec(port_spec: str) -> tuple[str, str] | None:
        port, separator, protocol = port_spec.partition("/")
        if not separator:
            protocol = "tcp"
        if not port.strip() or not protocol.strip():
            return None
        return (port.strip(), protocol.strip())

    @staticmethod
    def _docker_raw_port_key(port: Any) -> tuple[str, str] | None:
        if isinstance(port, tuple) and len(port) == 2:
            port_number, protocol = port
            return (str(port_number), str(protocol))
        return None

    def _cleanup_replacement_conflict(
        self,
        client,
        snapshot: dict[str, Any],
        create_config: dict[str, Any],
    ):
        container_name = create_config.get("name")
        if not isinstance(container_name, str) or not container_name.strip():
            return None

        try:
            existing_container = client.containers.get(container_name)
        except Exception:
            return None

        # Decide whether the live container is already at the snapshot's
        # state. We compare live image tags / digest to the snapshot's
        # recorded image; stored ``container_id`` is not a reliable
        # signal because every recreate assigns a new id.
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        if recovered_image and self._container_matches_image(existing_container, recovered_image):
            return existing_container

        self._remove_container_if_present(existing_container)
        return None

    @staticmethod
    def _container_matches_image(container, target_image: str) -> bool:
        """Return True when the live container is already serving ``target_image``.

        Checks the SDK-level image helpers first, then falls back to
        ``attrs.Config.Image`` / ``attrs.Image`` (digest) so we tolerate
        both tag-pinned and digest-pinned runtimes.
        """
        candidates: list[str] = []

        image = getattr(container, "image", None)
        if image is not None:
            tags = getattr(image, "tags", None)
            if isinstance(tags, list):
                candidates.extend(tag for tag in tags if isinstance(tag, str))
            image_id = getattr(image, "id", None)
            if isinstance(image_id, str):
                candidates.append(image_id)

        attrs = getattr(container, "attrs", {}) or {}
        if isinstance(attrs, dict):
            config = attrs.get("Config")
            if isinstance(config, dict):
                config_image = config.get("Image")
                if isinstance(config_image, str):
                    candidates.append(config_image)
            attrs_image = attrs.get("Image")
            if isinstance(attrs_image, str):
                candidates.append(attrs_image)

        for candidate in candidates:
            if candidate == target_image:
                return True

        return False

    def _remove_container_if_present(self, container) -> None:
        try:
            container.remove(force=True)
            return
        except TypeError:
            pass
        except Exception:
            pass

        if self._container_looks_running(container):
            try:
                container.stop()
            except Exception:
                pass
        container.remove()

    @staticmethod
    def _container_looks_running(container) -> bool:
        attrs = getattr(container, "attrs", {}) or {}
        state = attrs.get("State") if isinstance(attrs, dict) else None
        if not isinstance(state, dict):
            return False
        running = state.get("Running")
        if isinstance(running, bool):
            return running
        status = state.get("Status")
        if isinstance(status, str):
            return status.strip().lower() == "running"
        return False

    def _extract_create_kwargs(self, container, image: str) -> dict[str, Any]:
        spec = self._build_recreate_spec_from_inspect(container, image)
        return dict(spec.create_config) if spec is not None else {}

    def _build_recreate_spec_from_inspect(
        self, container, image: str
    ) -> GroupedRuntimeRecreateSpec | None:
        attrs = getattr(container, "attrs", {}) or {}
        config = attrs.get("Config")
        host_config = attrs.get("HostConfig")
        if not isinstance(config, dict) or not isinstance(host_config, dict):
            return None

        return build_grouped_runtime_recreate_spec(
            container,
            runtime_type=self.runtime_connection.type,
            target_image=image,
            current_image=self._extract_image(container),
        )

    def _find_compose_service_containers(self, project: str) -> dict[str, list[Any]]:
        containers_by_service: dict[str, list[Any]] = {}
        for container in self._get_client().containers.list(all=True):
            attrs = getattr(container, "attrs", {}) or {}
            config = attrs.get("Config") if isinstance(attrs, dict) else {}
            labels = config.get("Labels") if isinstance(config, dict) else {}
            if not isinstance(labels, dict):
                continue
            if labels.get("com.docker.compose.project") != project:
                continue
            service = labels.get("com.docker.compose.service")
            if not isinstance(service, str) or not service.strip():
                continue
            containers_by_service.setdefault(service.strip(), []).append(container)

        for service, containers in containers_by_service.items():
            containers_by_service[service] = sorted(
                containers, key=self._compose_container_sort_key
            )

        return containers_by_service

    def _build_grouped_runtime_recreate_specs(
        self,
        service_containers: dict[str, list[Any]],
        update_plan: dict[str, str],
    ) -> list[Any]:
        specs: list[Any] = []
        for service, target_image in sorted(update_plan.items()):
            for container in sorted(
                service_containers.get(service, []), key=self._compose_container_sort_key
            ):
                attrs = getattr(container, "attrs", {}) or {}
                config = attrs.get("Config") if isinstance(attrs, dict) else {}
                labels = config.get("Labels") if isinstance(config, dict) else {}
                compose_project = (
                    labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
                )
                specs.append(
                    build_grouped_runtime_recreate_spec(
                        container,
                        runtime_type=self.runtime_connection.type,
                        target_image=target_image,
                        current_image=self._extract_image(container),
                        compose_project=(
                            compose_project if isinstance(compose_project, str) else None
                        ),
                        compose_service=service,
                    )
                )
        return specs

    def _normalize_target_image(self, target_image: Any) -> str | None:
        if not isinstance(target_image, str):
            return None
        normalized = target_image.strip()
        if not normalized or any(ch.isspace() for ch in normalized):
            return None
        return normalized

    def _preflight_compose_service_containers(self, service: str, containers: list[Any]) -> str:
        replica_images = sorted(
            {image for container in containers if (image := self._extract_image(container))}
        )
        if len(replica_images) > 1:
            raise ValueError(f"Docker Compose service has inconsistent replica images: {service}")
        if not replica_images:
            raise ValueError(f"Docker Compose service image missing: {service}")
        for container in containers:
            if self._container_publishes_runtime_endpoint(container):
                raise ValueError(
                    f"Docker Compose service publishes active runtime endpoint and cannot be recreated through this connection: {service}"
                )
        return replica_images[0]

    def _compose_container_sort_key(self, container) -> tuple[str, int, str, str]:
        attrs = getattr(container, "attrs", {}) or {}
        config = attrs.get("Config") if isinstance(attrs, dict) else {}
        labels = config.get("Labels") if isinstance(config, dict) else {}
        service = ""
        replica_number = 0
        if isinstance(labels, dict):
            raw_service = labels.get("com.docker.compose.service")
            if isinstance(raw_service, str):
                service = raw_service
            raw_number = labels.get("com.docker.compose.container-number")
            if isinstance(raw_number, str):
                try:
                    replica_number = int(raw_number)
                except ValueError:
                    replica_number = 0
        return (
            service,
            replica_number,
            getattr(container, "name", "") or "",
            getattr(container, "id", "") or "",
        )

    def _resolve_compose_container(self, spec: GroupedRuntimeRecreateSpec):
        identifier = spec.container_id or spec.container_name
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("grouped recreate spec missing container identity")
        return self._get_client().containers.get(identifier)

    def _validate_grouped_runtime_recreate_specs(
        self, specs: list[GroupedRuntimeRecreateSpec]
    ) -> None:
        if not specs:
            raise ValueError("grouped recreate plan is empty")
        for spec in specs:
            if not spec.create_config:
                raise ValueError(
                    f"Docker cannot recreate compose container '{spec.container_name or spec.container_id}' without a restorable create configuration"
                )
            if self._normalize_target_image(spec.target_image) is None:
                raise ValueError("compose target images must be non-empty strings")

    def _order_grouped_runtime_recreate_specs(
        self, specs: list[GroupedRuntimeRecreateSpec]
    ) -> list[GroupedRuntimeRecreateSpec]:
        if len(specs) <= 1:
            return list(specs)

        stable_specs = sorted(
            specs,
            key=lambda spec: (
                spec.compose_service or "",
                spec.container_name or "",
                spec.container_id or "",
            ),
        )
        stable_order = {
            (spec.container_id or spec.container_name or f"spec-{index}"): index
            for index, spec in enumerate(stable_specs)
        }

        service_to_keys: dict[str, list[str]] = defaultdict(list)
        name_to_key: dict[str, str] = {}
        key_to_spec: dict[str, GroupedRuntimeRecreateSpec] = {}
        dependencies_by_key: dict[str, set[str]] = {}

        for index, spec in enumerate(stable_specs):
            key = spec.container_id or spec.container_name or f"spec-{index}"
            key_to_spec[key] = spec
            dependencies_by_key.setdefault(key, set())
            if spec.container_name:
                name_to_key[spec.container_name] = key
            if spec.compose_service:
                service_to_keys[spec.compose_service].append(key)

        for key, spec in key_to_spec.items():
            resolved_dependencies: set[str] = set()
            for dependency in spec.dependencies:
                # Dependencies are used only to order currently targeted specs.
                # Refs that point outside the grouped update set are ignored.
                if dependency in name_to_key:
                    resolved_dependencies.add(name_to_key[dependency])
                for dependency_key in service_to_keys.get(dependency, []):
                    resolved_dependencies.add(dependency_key)
            resolved_dependencies.discard(key)
            dependencies_by_key[key] = resolved_dependencies

        ordered: list[GroupedRuntimeRecreateSpec] = []
        ready = [key for key, deps in dependencies_by_key.items() if not deps]
        ready.sort(key=lambda key: stable_order[key])

        while ready:
            key = ready.pop(0)
            ordered.append(key_to_spec[key])
            for candidate_key, deps in dependencies_by_key.items():
                if key not in deps:
                    continue
                deps.remove(key)
                if (
                    not deps
                    and key_to_spec[candidate_key] not in ordered
                    and candidate_key not in ready
                ):
                    ready.append(candidate_key)
                    ready.sort(key=lambda item: stable_order[item])

        if len(ordered) != len(stable_specs):
            return stable_specs
        return ordered

    def _container_publishes_runtime_endpoint(self, container) -> bool:
        endpoint = self._active_runtime_endpoint()
        if endpoint is None:
            return False
        runtime_host, runtime_port = endpoint
        attrs = getattr(container, "attrs", {}) or {}
        host_config = attrs.get("HostConfig") if isinstance(attrs, dict) else {}
        port_bindings = host_config.get("PortBindings") if isinstance(host_config, dict) else {}
        if not isinstance(port_bindings, dict):
            return False

        for bindings in port_bindings.values():
            if not isinstance(bindings, list):
                continue
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                host_port = binding.get("HostPort")
                if str(host_port or "").strip() != str(runtime_port):
                    continue
                published_host = str(binding.get("HostIp") or "").strip().lower()
                if not published_host or published_host in {"0.0.0.0", "::"}:
                    return True
                if published_host == runtime_host:
                    return True
                if runtime_host in {"localhost", "127.0.0.1", "::1"} and published_host in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                }:
                    return True
        return False

    def _active_runtime_endpoint(self) -> tuple[str, str] | None:
        raw_endpoint = self.runtime_connection.config.get(
            "host"
        ) or self.runtime_connection.config.get("socket")
        if not isinstance(raw_endpoint, str) or not raw_endpoint.strip():
            return None

        parsed = urlparse(raw_endpoint)
        if parsed.scheme in {"unix", "npipe"}:
            return None

        if parsed.scheme in {"http", "https", "tcp"}:
            hostname = (parsed.hostname or "").strip().lower()
            port = parsed.port
            if hostname and port is not None:
                return hostname, str(port)
        return None

    def _restore_container_networks(
        self,
        client,
        new_container,
        spec: GroupedRuntimeRecreateSpec,
    ) -> None:
        self._restore_container_networks_for_payload(
            client,
            new_container,
            container_id=spec.container_id,
            network_config=spec.network_config,
        )

    def _restore_container_networks_from_snapshot(
        self,
        client,
        new_container,
        snapshot: dict[str, Any],
    ) -> None:
        network_config = snapshot.get("network_config")
        if not isinstance(network_config, dict) or not network_config:
            return
        self._restore_container_networks_for_payload(
            client,
            new_container,
            container_id=(
                snapshot.get("container_id")
                if isinstance(snapshot.get("container_id"), str)
                else None
            ),
            network_config=network_config,
        )

    def _restore_container_networks_for_payload(
        self,
        client,
        new_container,
        *,
        container_id: str | None,
        network_config: dict[str, Any],
    ) -> None:
        network_mode = network_config.get("network_mode")
        if isinstance(network_mode, str) and (
            network_mode == "host"
            or network_mode == "none"
            or network_mode.startswith("container:")
        ):
            return

        endpoints = network_config.get("endpoints")
        networks = getattr(client, "networks", None)
        if (
            not isinstance(endpoints, dict)
            or not endpoints
            or networks is None
            or not hasattr(networks, "get")
        ):
            return

        for network_name in sorted(endpoints):
            endpoint = endpoints.get(network_name)
            if not isinstance(endpoint, dict):
                continue
            network = networks.get(network_name)
            if hasattr(network, "disconnect"):
                try:
                    network.disconnect(new_container, force=True)
                except Exception:
                    pass
            network.connect(new_container, **self._network_connect_kwargs(container_id, endpoint))

    def _network_connect_kwargs(
        self,
        container_id: str | None,
        endpoint: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}

        aliases = endpoint.get("Aliases")
        if isinstance(aliases, list):
            container_id_aliases = {
                container_id or "",
                (container_id or "")[:12],
            }
            normalized_aliases = [
                alias
                for alias in aliases
                if isinstance(alias, str) and alias.strip() and alias not in container_id_aliases
            ]
            if normalized_aliases:
                kwargs["aliases"] = normalized_aliases

        links = endpoint.get("Links")
        if isinstance(links, list):
            normalized_links = [link for link in links if isinstance(link, str) and link.strip()]
            if normalized_links:
                kwargs["links"] = normalized_links

        ipam_config = endpoint.get("IPAMConfig")
        if isinstance(ipam_config, dict):
            ipv4_address = ipam_config.get("IPv4Address")
            if isinstance(ipv4_address, str) and ipv4_address.strip():
                kwargs["ipv4_address"] = ipv4_address
            ipv6_address = ipam_config.get("IPv6Address")
            if isinstance(ipv6_address, str) and ipv6_address.strip():
                kwargs["ipv6_address"] = ipv6_address

        return kwargs
