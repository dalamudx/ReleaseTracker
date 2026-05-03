from __future__ import annotations

import importlib
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from .base import RuntimeMutationError, RuntimeUpdateResult
from .compose_runtime_update import GroupedRuntimeRecreateSpec, build_grouped_runtime_recreate_spec
from .container_runtime import _ContainerRuntimeAdapter


class PodmanRuntimeAdapter(_ContainerRuntimeAdapter):
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
                    f"Podman Compose service has inconsistent replica images: {service}"
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
                message="no podman compose services require update",
            )

        service_containers = self._find_compose_service_containers(project)
        update_plan: dict[str, str] = {}
        target_pod_id: str | None = None
        for service, target_image in sorted(service_target_images.items()):
            if not isinstance(target_image, str) or not target_image.strip():
                raise ValueError("compose target images must be non-empty strings")
            containers = service_containers.get(service) or []
            if not containers:
                raise ValueError(f"Podman Compose service container missing: {service}")

            current_images = sorted(
                {image for container in containers if (image := self._extract_image(container))}
            )
            if len(current_images) > 1:
                raise ValueError(
                    f"Podman Compose service has inconsistent replica images: {service}"
                )

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

            current_image = self._extract_image(containers[0]) or ""
            if current_image != target_image:
                update_plan[service] = target_image

        specs = self._build_grouped_runtime_recreate_specs(service_containers, update_plan)

        if not update_plan:
            return RuntimeUpdateResult(
                updated=False,
                old_image=None,
                new_image=None,
                message="podman compose services already at target images",
            )

        client = self._get_client()
        for image in sorted({spec.target_image for spec in specs}):
            client.images.pull(image)

        snapshots_by_spec_key = {
            self._grouped_runtime_recreate_spec_key(spec): dict(spec.snapshot_payload)
            for spec in specs
        }
        new_container_ids: list[str] = []
        try:
            for spec in specs:
                create_config = self._podman_grouped_create_config_for_spec(spec)

                container = client.containers.get(spec.container_id)
                self._stop_grouped_container_with_sdk_decode_tolerance(client, container)
                self._remove_grouped_container_with_sdk_decode_tolerance(client, container)

                replacement = client.containers.create(**create_config)
                self._restore_container_networks(client, replacement, spec)
                replacement.start()
                replacement_id = getattr(replacement, "id", None)
                if isinstance(replacement_id, str) and replacement_id.strip():
                    new_container_ids.append(replacement_id)
        except Exception as exc:
            recovery_error = await self._recover_grouped_compose_runtime_update(
                specs, snapshots_by_spec_key
            )
            if recovery_error is not None:
                raise RuntimeMutationError(
                    "podman grouped compose update failed after destructive steps began; "
                    f"best-effort recovery also failed: {recovery_error}; original error: {exc}",
                    destructive_started=True,
                ) from exc
            raise RuntimeMutationError(
                "podman grouped compose update failed after destructive steps began and recovery "
                f"succeeded best-effort: {exc}",
                destructive_started=True,
            ) from exc

        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=None,
            new_container_id=",".join(new_container_ids) or None,
            message=(
                "podman compose grouped pod-aware update completed "
                f"for {len(specs)} container(s) in pod '{target_pod_id}'"
            ),
        )

    def _should_expose_container(self, container) -> bool:
        attrs = getattr(container, "attrs", {}) or {}
        return not bool(attrs.get("Pod") or "")

    def _create_client(self):
        try:
            podman = importlib.import_module("podman")
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python dependency 'podman' required by PodmanRuntimeAdapter"
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
            tls_config = podman.tls.TLSConfig(
                client_cert=cert_pair,
                ca_cert=ca_cert if isinstance(ca_cert, str) else None,
                verify=True,
            )

        return podman.PodmanClient(
            base_url=base_url,
            version=api_version,
            tls=tls_config,
        )

    async def update_image(self, target_ref: dict[str, Any], new_image: str) -> RuntimeUpdateResult:
        if not isinstance(new_image, str) or not new_image.strip():
            raise ValueError("new_image must be a non-empty string")

        container = self._get_container(target_ref)
        old_image = self._extract_image(container)

        # Podman sets attrs["Pod"] to a non-empty string when the container
        # belongs to a pod. Recreating such a container independently would
        # silently break the pod topology, so we reject it before any
        # destructive step.
        attrs = getattr(container, "attrs", {}) or {}
        pod_id = attrs.get("Pod") or ""
        if pod_id:
            raise ValueError(
                f"Podman container '{container.id}' is a member of pod '{pod_id}' "
                "and cannot be recreated independently. "
                "Pod-member updates are not supported in phase 1."
            )

        if old_image == new_image:
            return RuntimeUpdateResult(updated=False, old_image=old_image, new_image=new_image)

        client = self._get_client()
        create_kwargs = self._extract_create_kwargs(container, new_image)

        client.images.pull(new_image)

        try:
            container.stop()
            container.remove()

            new_container = client.containers.create(**create_kwargs)
            new_container.start()
        except Exception as exc:
            raise RuntimeMutationError(
                f"podman update failed after destructive steps began: {exc}",
                destructive_started=True,
            ) from exc

        return RuntimeUpdateResult(
            updated=True,
            old_image=old_image,
            new_image=new_image,
            new_container_id=new_container.id,
        )

    async def discover_targets(self):
        client = self._get_client()
        containers = client.containers.list(all=True)
        targets = []
        compose_containers_by_project: dict[str, list[Any]] = {}
        for container in containers:
            full_container = self._get_full_container_with_fallback(container, client)
            compose_project = self._compose_project_for_container(full_container)
            compose_service = self._compose_service_for_container(full_container)
            if compose_project and compose_service:
                compose_containers_by_project.setdefault(compose_project, []).append(full_container)
                continue
            if self._is_pod_member(container):
                continue
            name = getattr(full_container, "name", None) or getattr(full_container, "id", "")
            targets.append(self._build_runtime_target(full_container, name))
        targets.extend(self._compose_runtime_targets(compose_containers_by_project))
        return targets

    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        container = self._get_container(target_ref)
        attrs = getattr(container, "attrs", {}) or {}
        return {
            "runtime_type": self.runtime_connection.type,
            "container_id": getattr(container, "id", None),
            "container_name": getattr(container, "name", None),
            "image": current_image,
            "create_config": self._extract_create_kwargs(container, current_image),
            "pod_id": attrs.get("Pod") or None,
        }

    async def validate_snapshot(self, target_ref: dict[str, Any], snapshot: dict[str, Any]) -> None:
        await super().validate_snapshot(target_ref, snapshot)
        create_config = snapshot.get("create_config")
        if not isinstance(create_config, dict) or not create_config:
            raise ValueError("snapshot.create_config must be a non-empty dict")
        if create_config.get("image") != snapshot.get("image"):
            raise ValueError("snapshot.create_config.image must match snapshot.image")
        pod_id = snapshot.get("pod_id")
        if isinstance(pod_id, str) and pod_id.strip():
            raise ValueError(
                f"Podman container '{snapshot.get('container_id')}' is a member of pod '{pod_id}' and cannot be recreated independently"
            )

    async def recover_from_snapshot(
        self, target_ref: dict[str, Any], snapshot: dict[str, Any]
    ) -> RuntimeUpdateResult:
        await self.validate_snapshot(target_ref, snapshot)

        create_config = snapshot.get("create_config")
        if not isinstance(create_config, dict):
            raise ValueError("snapshot.create_config must be a dict")

        client = self._get_client()
        recovered_container = client.containers.create(**create_config)
        recovered_container.start()

        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=recovered_image,
            message="runtime recovered from snapshot",
            new_container_id=recovered_container.id,
        )

    def _extract_create_kwargs(self, container, new_image: str) -> dict[str, Any]:
        attrs = getattr(container, "attrs", {}) or {}
        host_config = attrs.get("HostConfig") or {}
        config = attrs.get("Config") or {}

        if not isinstance(config, dict) or not isinstance(host_config, dict):
            return {}

        spec = build_grouped_runtime_recreate_spec(
            container,
            runtime_type=self.runtime_connection.type,
            target_image=new_image,
            current_image=self._extract_image(container),
        )
        create_config = dict(spec.create_config)
        return self._apply_podman_host_config_preservation(create_config, host_config)

    async def _recover_grouped_compose_runtime_update(
        self,
        specs: list[GroupedRuntimeRecreateSpec],
        snapshots_by_spec_key: dict[str, dict[str, Any]],
    ) -> str | None:
        recovery_failures: list[str] = []
        for spec in specs:
            snapshot = snapshots_by_spec_key.get(self._grouped_runtime_recreate_spec_key(spec))
            if not isinstance(snapshot, dict) or not snapshot:
                recovery_failures.append(
                    f"missing snapshot for {spec.container_name or spec.container_id or 'unknown container'}"
                )
                continue
            try:
                await self._recover_grouped_container_from_snapshot(snapshot)
            except Exception as recovery_exc:
                recovery_failures.append(
                    f"{spec.container_name or spec.container_id or 'unknown container'}: {recovery_exc}"
                )

        if recovery_failures:
            return "; ".join(recovery_failures)
        return None

    async def _recover_grouped_container_from_snapshot(
        self, snapshot: dict[str, Any]
    ) -> RuntimeUpdateResult:
        create_config = snapshot.get("create_config")
        if not isinstance(create_config, dict) or not create_config:
            raise ValueError("snapshot.create_config must be a non-empty dict")
        if create_config.get("image") != snapshot.get("image"):
            raise ValueError("snapshot.create_config.image must match snapshot.image")

        client = self._get_client()
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        if recovered_image:
            client.images.pull(recovered_image)

        restorable_create_config = self._podman_grouped_create_config_for_snapshot(snapshot)
        existing_container = self._cleanup_grouped_replacement_conflict(
            client,
            snapshot,
            restorable_create_config,
        )
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
            recovered_container = client.containers.create(**restorable_create_config)
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

    def _cleanup_grouped_replacement_conflict(
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

        snapshot_container_id = snapshot.get("container_id")
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        existing_container_id = getattr(existing_container, "id", None)
        existing_image = self._extract_image(existing_container)
        if existing_container_id == snapshot_container_id and existing_image == recovered_image:
            return existing_container

        self._remove_container_if_present(existing_container)
        return None

    def _remove_container_if_present(self, container) -> None:
        try:
            container.remove()
        except Exception:
            pass

    def _stop_grouped_container_with_sdk_decode_tolerance(self, client, container) -> None:
        try:
            container.stop()
            return
        except Exception as exc:
            if not self._is_sdk_json_decode_error(exc):
                raise
            decode_error = exc

        if self._grouped_stop_postcondition_satisfied(client, container):
            return
        raise decode_error

    def _remove_grouped_container_with_sdk_decode_tolerance(self, client, container) -> None:
        try:
            container.remove()
            return
        except Exception as exc:
            if not self._is_sdk_json_decode_error(exc):
                raise
            decode_error = exc

        if self._grouped_remove_postcondition_satisfied(client, container):
            return
        raise decode_error

    def _grouped_stop_postcondition_satisfied(self, client, container) -> bool:
        container_id = getattr(container, "id", None)
        container_name = getattr(container, "name", None)
        for identifier in (container_id, container_name):
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            try:
                refreshed = client.containers.get(identifier.strip())
            except Exception:
                continue
            state = getattr(refreshed, "attrs", {}) or {}
            state_payload = state.get("State") if isinstance(state, Mapping) else None
            if isinstance(state_payload, Mapping):
                running = state_payload.get("Running")
                if isinstance(running, bool):
                    return not running
                status = state_payload.get("Status")
                if isinstance(status, str) and status.strip().lower() in {
                    "stopped",
                    "exited",
                    "configured",
                    "created",
                    "dead",
                }:
                    return True
            return False
        return True

    def _grouped_remove_postcondition_satisfied(self, client, container) -> bool:
        container_id = getattr(container, "id", None)
        container_name = getattr(container, "name", None)
        for identifier in (container_id, container_name):
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            try:
                client.containers.get(identifier.strip())
                return False
            except Exception:
                continue
        return True

    def _is_sdk_json_decode_error(self, exc: Exception) -> bool:
        for err in self._iter_exception_chain(exc):
            if err.__class__.__name__ == "JSONDecodeError":
                return True
        return False

    def _iter_exception_chain(self, exc: Exception) -> Iterator[BaseException]:
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None:
            marker = id(current)
            if marker in seen:
                break
            seen.add(marker)
            yield current
            if current.__cause__ is not None:
                current = current.__cause__
                continue
            current = current.__context__

    def _grouped_runtime_recreate_spec_key(self, spec: GroupedRuntimeRecreateSpec) -> str:
        identifier = spec.container_id or spec.container_name
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("grouped recreate spec missing container identity")
        return identifier

    def _podman_grouped_create_config_for_spec(
        self, spec: GroupedRuntimeRecreateSpec
    ) -> dict[str, Any]:
        create_config = dict(spec.create_config)
        create_config = self._apply_podman_host_config_preservation(create_config, spec.host_config)
        return self._apply_pod_membership_to_create_config(
            create_config,
            pod_name=spec.pod_name,
            pod_id=spec.pod_id,
        )

    def _podman_grouped_create_config_for_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        create_config = dict(snapshot.get("create_config") or {})
        host_config = (
            snapshot.get("host_config") if isinstance(snapshot.get("host_config"), dict) else {}
        )
        create_config = self._apply_podman_host_config_preservation(create_config, host_config)
        return self._apply_pod_membership_to_create_config(
            create_config,
            pod_name=(
                snapshot.get("pod_name") if isinstance(snapshot.get("pod_name"), str) else None
            ),
            pod_id=snapshot.get("pod_id") if isinstance(snapshot.get("pod_id"), str) else None,
        )

    def _apply_podman_host_config_preservation(
        self,
        create_config: dict[str, Any],
        host_config: Any,
    ) -> dict[str, Any]:
        if not isinstance(host_config, Mapping):
            return create_config

        binds = host_config.get("Binds")
        if isinstance(binds, list):
            normalized_volumes: dict[str, Any] = {}
            normalized_mounts: list[dict[str, Any]] = []
            updated_volumes = False
            updated_mounts = False
            for bind in binds:
                if not isinstance(bind, str):
                    continue
                host_path, container_path, raw_mode = self._parse_bind_mount(bind)
                if not host_path or not container_path:
                    continue
                option_tokens = [
                    token.strip() for token in (raw_mode or "").split(",") if token.strip()
                ]
                if host_path.startswith("/"):
                    mount_payload: dict[str, Any] = {
                        "type": "bind",
                        "source": host_path,
                        "target": container_path,
                    }
                    if "ro" in option_tokens:
                        mount_payload["read_only"] = True
                    for token in option_tokens:
                        if token in {"shared", "slave", "private", "rshared", "rslave", "rprivate"}:
                            mount_payload["propagation"] = token
                        elif token in {"Z", "z"}:
                            mount_payload["relabel"] = token
                    normalized_mounts.append(mount_payload)
                    updated_mounts = True
                    continue

                volume_mode = "ro" if "ro" in option_tokens else "rw"
                normalized_volumes[host_path] = {"bind": container_path, "mode": volume_mode}
                updated_volumes = True
            if updated_volumes:
                if normalized_volumes:
                    create_config["volumes"] = normalized_volumes
                else:
                    create_config.pop("volumes", None)
            elif "volumes" in create_config:
                create_config.pop("volumes", None)
            if updated_mounts:
                create_config["mounts"] = normalized_mounts
            elif "mounts" in create_config:
                create_config.pop("mounts", None)

        init_value = host_config.get("Init")
        if isinstance(init_value, bool):
            create_config["init"] = init_value

        log_config = host_config.get("LogConfig")
        if isinstance(log_config, Mapping):
            normalized_log_config: dict[str, Any] = {}
            log_type = log_config.get("Type")
            if isinstance(log_type, str) and log_type.strip():
                normalized_log_config["Type"] = log_type.strip()
            raw_config = log_config.get("Config")
            if isinstance(raw_config, Mapping):
                normalized_log_config["Config"] = dict(raw_config)
            elif raw_config is None:
                normalized_log_config["Config"] = {}
            if normalized_log_config:
                create_config["log_config"] = normalized_log_config

        ulimits = create_config.get("ulimits")
        if isinstance(ulimits, list):
            normalized_ulimits: list[dict[str, Any]] = []
            for item in ulimits:
                if not isinstance(item, Mapping):
                    continue
                normalized = dict(item)
                name = normalized.get("Name")
                if isinstance(name, str) and name.strip():
                    normalized["Name"] = self._normalize_podman_ulimit_name(name)
                normalized_ulimits.append(normalized)
            create_config["ulimits"] = normalized_ulimits

        return create_config

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

        if "podman" not in endpoints:
            try:
                default_network = networks.get("podman")
                default_network.disconnect(new_container, force=True)
            except Exception:
                pass

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

        ipam_config = endpoint.get("IPAMConfig")
        if isinstance(ipam_config, dict):
            ipv4_address = ipam_config.get("IPv4Address")
            if isinstance(ipv4_address, str) and ipv4_address.strip():
                kwargs["ipv4_address"] = ipv4_address
            ipv6_address = ipam_config.get("IPv6Address")
            if isinstance(ipv6_address, str) and ipv6_address.strip():
                kwargs["ipv6_address"] = ipv6_address

        return kwargs

    def _normalize_podman_ulimit_name(self, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("RLIMIT_"):
            normalized = normalized[len("RLIMIT_") :]
        return normalized.lower()

    def _parse_bind_mount(self, bind: str) -> tuple[str | None, str | None, str | None]:
        parts = bind.split(":", 2)
        if len(parts) < 2:
            return None, None, None
        host_path = parts[0]
        container_path = parts[1]
        mode = parts[2] if len(parts) >= 3 else None
        if not isinstance(host_path, str) or not host_path.strip():
            return None, None, None
        if not isinstance(container_path, str) or not container_path.strip():
            return None, None, None
        if not isinstance(mode, str) or not mode.strip():
            return host_path, container_path, None
        return host_path, container_path, mode

    def _apply_pod_membership_to_create_config(
        self,
        create_config: dict[str, Any],
        *,
        pod_name: str | None,
        pod_id: str | None,
    ) -> dict[str, Any]:
        if isinstance(pod_name, str) and pod_name.strip():
            create_config["pod"] = pod_name.strip()
        elif isinstance(pod_id, str) and pod_id.strip():
            create_config["pod"] = pod_id.strip()
        return create_config

    def _find_compose_service_containers(self, project: str) -> dict[str, list[Any]]:
        containers_by_service: dict[str, list[Any]] = {}
        client = self._get_client()
        for container in client.containers.list(all=True):
            full_container = self._get_full_container_with_fallback(container, client)
            labels = self._get_compose_labels_with_fallback(full_container, client)
            if not labels:
                continue
            label_project = labels.get("com.docker.compose.project") or labels.get(
                "io.podman.compose.project"
            )
            if label_project != project:
                continue
            service = labels.get("com.docker.compose.service")
            if not isinstance(service, str) or not service.strip():
                continue
            containers_by_service.setdefault(service.strip(), []).append(full_container)
        return containers_by_service

    def _get_full_container_with_fallback(self, container, client):
        identifier_candidates = [getattr(container, "id", None), getattr(container, "name", None)]
        for identifier in identifier_candidates:
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            try:
                return client.containers.get(identifier.strip())
            except Exception:
                continue
        return container

    def _build_grouped_runtime_recreate_specs(
        self,
        service_containers: dict[str, list[Any]],
        update_plan: dict[str, str],
    ) -> list[Any]:
        specs: list[Any] = []
        client = self._get_client()
        for service, target_image in sorted(update_plan.items()):
            for container in service_containers.get(service, []):
                attrs = getattr(container, "attrs", {}) or {}
                labels = self._get_compose_labels_with_fallback(container, client)
                compose_project = None
                if labels:
                    compose_project = labels.get("com.docker.compose.project") or labels.get(
                        "io.podman.compose.project"
                    )
                compose_label_overrides = self._extract_compose_label_overrides(labels)
                pod_relation_payload = self._extract_pod_relation_payload(attrs)
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
                        pod_id=pod_relation_payload.get("pod_id"),
                        pod_name=pod_relation_payload.get("pod_name"),
                        pod_relation_payload=pod_relation_payload,
                        create_config_labels_override=compose_label_overrides,
                    )
                )
        return specs

    def _extract_compose_label_overrides(self, labels: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(labels, Mapping):
            return {}

        required_keys = {
            "com.docker.compose.project",
            "com.docker.compose.service",
            "com.docker.compose.container-number",
            "io.podman.compose.project",
            "io.podman.compose.version",
        }
        compose_label_prefixes = ("com.docker.compose.", "io.podman.compose.")

        collected: dict[str, str] = {}
        for key, value in labels.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            if key in required_keys or key.startswith(compose_label_prefixes):
                collected[key] = value

        return collected

    def _extract_pod_relation_payload(self, attrs: Any) -> dict[str, Any]:
        if not isinstance(attrs, Mapping):
            return {}

        payload: dict[str, Any] = {}

        pod_id = attrs.get("Pod")
        if isinstance(pod_id, str) and pod_id.strip():
            payload["pod_id"] = pod_id.strip()

        pod_name = attrs.get("PodName")
        if isinstance(pod_name, str) and pod_name.strip():
            payload["pod_name"] = pod_name.strip()

        for source_key, target_key in (
            ("PodInfraId", "pod_infra_id"),
            ("PodInfraName", "pod_infra_name"),
        ):
            value = attrs.get(source_key)
            if isinstance(value, str) and value.strip():
                payload[target_key] = value.strip()

        return payload

    def _get_compose_labels(self, container) -> dict[str, str]:
        candidates: list[Mapping[str, Any]] = []
        attrs = getattr(container, "attrs", {}) or {}
        if isinstance(attrs, Mapping):
            attrs_labels = attrs.get("labels") or attrs.get("Labels")
            if isinstance(attrs_labels, Mapping):
                candidates.append(attrs_labels)
            config = attrs.get("Config")
            if isinstance(config, Mapping):
                config_labels = config.get("Labels") or config.get("labels")
                if isinstance(config_labels, Mapping):
                    candidates.append(config_labels)

        container_labels = getattr(container, "labels", None)
        if isinstance(container_labels, Mapping):
            candidates.append(container_labels)

        merged: dict[str, str] = {}
        for labels in candidates:
            for key, value in labels.items():
                if isinstance(key, str) and isinstance(value, str):
                    merged[key] = value
        return merged

    def _get_compose_labels_with_fallback(self, container, client) -> dict[str, str]:
        labels = self._get_compose_labels(container)
        if self._has_compose_identity(labels):
            return labels

        identifier_candidates = [getattr(container, "id", None), getattr(container, "name", None)]
        for identifier in identifier_candidates:
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            try:
                full_container = client.containers.get(identifier.strip())
            except Exception:
                continue
            full_labels = self._get_compose_labels(full_container)
            if self._has_compose_identity(full_labels):
                return full_labels

        return labels

    def _compose_labels_for_container(self, container) -> dict[str, str]:
        return self._get_compose_labels_with_fallback(container, self._get_client())

    def _has_compose_identity(self, labels: Mapping[str, Any]) -> bool:
        project = labels.get("com.docker.compose.project") or labels.get(
            "io.podman.compose.project"
        )
        service = labels.get("com.docker.compose.service")
        return (
            isinstance(project, str)
            and bool(project.strip())
            and isinstance(service, str)
            and bool(service.strip())
        )

    def _is_pod_member(self, container) -> bool:
        attrs = getattr(container, "attrs", {}) or {}
        pod_id = attrs.get("Pod")
        return isinstance(pod_id, str) and bool(pod_id.strip())

    def _build_runtime_target(self, container, name: str):
        return self._runtime_target(container, name)
