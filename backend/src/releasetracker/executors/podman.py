from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import signal
import urllib.parse
from collections.abc import Iterator, Mapping
from typing import Any

from .base import RuntimeMutationError, RuntimeUpdateResult
from .compose_runtime_update import GroupedRuntimeRecreateSpec, build_grouped_runtime_recreate_spec
from .container_runtime import _ContainerRuntimeAdapter

logger = logging.getLogger(__name__)

PODMAN_LIBPOD_CONTAINER_CREATE_ENDPOINT = "containers/create"
PODMAN_MOUNT_DESTINATION_KEYS = ("destination", "target", "dest", "Destination", "Target", "Dest")
PODMAN_NAMED_VOLUME_DESTINATION_KEYS = (
    "Dest",
    "dest",
    "destination",
    "Destination",
    "Target",
    "target",
)


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
        renamed_backups: list[tuple[str, str]] = []
        try:
            for spec in specs:
                container = client.containers.get(spec.container_id)
                create_config = self._podman_grouped_create_config_for_spec(spec, client=client)
                self._stop_grouped_container_with_sdk_decode_tolerance(client, container)
                backup_name = self._podman_replacement_backup_name(spec)
                pod_join_strategy: str | None = None
                if spec.pod_id or spec.pod_name:
                    self._rename_grouped_container_for_replacement(container, backup_name)
                    self._remove_grouped_container_with_sdk_decode_tolerance(client, container)
                    pod_name_hint = create_config.get("pod")
                    recreated_pod_ref, pod_join_strategy = (
                        self._recreate_pod_for_grouped_replacement(
                            client,
                            spec,
                            pod_name_hint=(
                                pod_name_hint if isinstance(pod_name_hint, str) else None
                            ),
                        )
                    )
                    if recreated_pod_ref is not None:
                        create_config["pod"] = recreated_pod_ref
                else:
                    self._remove_grouped_container_with_sdk_decode_tolerance(client, container)

                create_compatible_requested, create_compatible_accepted = (
                    self._podman_low_level_create_compatibility_summary(client)
                )
                self._log_podman_grouped_create_boundary(
                    "forward",
                    spec,
                    create_config,
                    boundary="containers.create",
                    create_strategy="podman_py_low_level_rendered_create",
                    create_endpoint=PODMAN_LIBPOD_CONTAINER_CREATE_ENDPOINT,
                    create_compatible_requested=create_compatible_requested,
                    create_compatible_accepted=create_compatible_accepted,
                    pod_join_strategy=pod_join_strategy,
                )
                replacement = self._create_podman_grouped_container(client, create_config)
                if not self._is_pod_backed_grouped_spec(spec):
                    self._restore_container_networks(client, replacement, spec, phase="forward")
                replacement.start()
                if spec.pod_id or spec.pod_name:
                    self._remove_replaced_grouped_container_backup(client, backup_name)
                replacement_id = getattr(replacement, "id", None)
                if isinstance(replacement_id, str) and replacement_id.strip():
                    new_container_ids.append(replacement_id)
        except Exception as exc:
            logger.warning(
                "Podman grouped compose update failed after SDK boundary: "
                "exception_class=%s exception_message=%s specs=%s",
                exc.__class__.__name__,
                self._safe_exception_message(exc),
                [
                    {
                        "container_name": self._safe_identifier(spec.container_name),
                        "container_id": self._safe_identifier(spec.container_id),
                        "pod_name": self._safe_identifier(spec.pod_name),
                        "pod_id": self._safe_identifier(spec.pod_id),
                        "network_endpoint_names": self._safe_network_names(
                            self._endpoint_names_from_network_config(spec.network_config)
                        ),
                        "dropped_empty_network_count": self._dropped_empty_endpoint_count(
                            spec.network_config
                        ),
                    }
                    for spec in specs
                ],
            )
            backup_recovery_error = self._recover_renamed_grouped_backups(client, renamed_backups)
            if backup_recovery_error is None and renamed_backups:
                raise RuntimeMutationError(
                    "podman grouped compose update failed after destructive steps began and recovery "
                    f"succeeded best-effort: {exc}",
                    destructive_started=True,
                ) from exc
            recovery_error = await self._recover_grouped_compose_runtime_update(
                specs, snapshots_by_spec_key
            )
            if recovery_error is not None:
                combined_recovery_error = recovery_error
                if backup_recovery_error is not None:
                    combined_recovery_error = f"{backup_recovery_error}; {recovery_error}"
                raise RuntimeMutationError(
                    "podman grouped compose update failed after destructive steps began; "
                    f"best-effort recovery also failed: {combined_recovery_error}; original error: {exc}",
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
        recreate_spec = build_grouped_runtime_recreate_spec(
            container,
            runtime_type=self.runtime_connection.type,
            target_image=new_image,
            current_image=old_image,
        )
        create_kwargs = self._podman_container_create_config(
            self._apply_podman_host_config_preservation(
                dict(recreate_spec.create_config),
                recreate_spec.host_config,
            )
        )

        client.images.pull(new_image)

        try:
            container.stop()
            container.remove()

            new_container = self._create_podman_container(client, create_kwargs)
            self._restore_container_networks(client, new_container, recreate_spec)
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
        spec = build_grouped_runtime_recreate_spec(
            container,
            runtime_type=self.runtime_connection.type,
            target_image=current_image,
            current_image=self._extract_image(container),
            pod_id=attrs.get("Pod") or None,
            pod_name=attrs.get("PodName") or None,
            pod_relation_payload=self._extract_pod_relation_payload(attrs),
        )
        snapshot = dict(spec.snapshot_payload)
        snapshot["create_config"] = self._apply_podman_host_config_preservation(
            dict(snapshot.get("create_config") or {}),
            spec.host_config,
        )
        return snapshot

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
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        images = getattr(client, "images", None)
        if recovered_image and images is not None and hasattr(images, "pull"):
            try:
                images.pull(recovered_image)
            except Exception:
                pass

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
        create_config = self._podman_container_create_config(create_config)

        try:
            recovered_container = self._create_podman_container(client, create_config)
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

        snapshot_container_id = snapshot.get("container_id")
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        existing_container_id = getattr(existing_container, "id", None)
        existing_image = self._extract_image(existing_container)
        if existing_container_id == snapshot_container_id and existing_image == recovered_image:
            return existing_container

        self._remove_container_if_present(existing_container)
        return None

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

        restorable_create_config = self._podman_grouped_create_config_for_snapshot(
            snapshot,
            client=client,
        )
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

        restorable_create_config = self._podman_container_create_config(restorable_create_config)

        recovered_container = None
        try:
            create_compatible_requested, create_compatible_accepted = (
                self._podman_low_level_create_compatibility_summary(client)
            )
            self._log_podman_grouped_recovery_create_boundary(
                snapshot,
                restorable_create_config,
                boundary="containers.create",
                create_strategy="podman_py_low_level_rendered_create",
                create_endpoint=PODMAN_LIBPOD_CONTAINER_CREATE_ENDPOINT,
                create_compatible_requested=create_compatible_requested,
                create_compatible_accepted=create_compatible_accepted,
            )
            recovered_container = self._create_podman_grouped_container(
                client,
                restorable_create_config,
            )
            if not self._snapshot_has_pod_membership(snapshot):
                self._restore_container_networks_from_snapshot(
                    client,
                    recovered_container,
                    snapshot,
                    phase="recovery",
                )
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

    def _podman_replacement_backup_name(self, spec: GroupedRuntimeRecreateSpec) -> str:
        base_name = spec.container_name or spec.container_id or "container"
        suffix_source = spec.container_id or base_name
        return f"{base_name}-rt-backup-{suffix_source[:12]}"

    def _recreate_pod_for_grouped_replacement(
        self,
        client,
        spec: GroupedRuntimeRecreateSpec,
        *,
        pod_name_hint: str | None = None,
    ) -> tuple[Any | None, str | None]:
        pod_ref = spec.pod_name or spec.pod_id
        if not isinstance(pod_ref, str) or not pod_ref.strip():
            return None, None
        pod_name = (
            pod_name_hint if isinstance(pod_name_hint, str) and pod_name_hint.strip() else None
        )
        if pod_name is None:
            pod_name = self._resolve_existing_pod_name(client, pod_ref.strip()) or pod_ref.strip()
        try:
            client.pods.remove(pod_ref.strip(), force=True)
        except Exception:
            pass
        pod_payload = self._pod_create_payload_from_spec(spec)
        pod_payload["name"] = pod_name
        pod_name = pod_payload.pop("name")
        self._log_podman_grouped_pod_create_boundary(
            "forward",
            spec,
            pod_name,
            pod_payload,
            boundary="pods.create",
        )
        created_pod = client.pods.create(pod_name, **pod_payload)
        if created_pod is not None:
            return created_pod, "recreated_pod_object"
        resolved_pod = self._resolve_pod_object_after_create(client, pod_name)
        if resolved_pod is not None:
            return resolved_pod, "recreated_pod_object"
        return pod_name, "recreated_pod_name"

    def _resolve_pod_object_after_create(self, client, pod_name: str):
        pods = getattr(client, "pods", None)
        if pods is None or not hasattr(pods, "get"):
            return None
        try:
            return pods.get(pod_name)
        except Exception:
            return None

    def _resolve_existing_pod_name(self, client, pod_ref: str) -> str | None:
        pod = self._resolve_pod_object_after_create(client, pod_ref)
        pod_name = getattr(pod, "name", None)
        if isinstance(pod_name, str) and pod_name.strip():
            return pod_name.strip()
        return None

    def _pod_create_payload_from_spec(self, spec: GroupedRuntimeRecreateSpec) -> dict[str, Any]:
        pod_name = spec.pod_name or spec.pod_id
        payload: dict[str, Any] = {
            "name": pod_name,
            "infra": False,
        }
        hostname = spec.create_config.get("hostname")
        if isinstance(hostname, str) and hostname.strip():
            payload["hostname"] = hostname.strip()
        shm_size = spec.create_config.get("shm_size")
        if isinstance(shm_size, int) and shm_size > 0:
            payload["shm_size"] = shm_size
        portmappings = self._pod_portmappings_from_create_config(spec.create_config)
        if portmappings:
            payload["portmappings"] = portmappings
        extra_hosts = self._pod_extra_hosts_from_create_config(spec.create_config)
        if extra_hosts:
            payload["hostadd"] = extra_hosts
        dns = spec.create_config.get("dns")
        if isinstance(dns, list) and dns:
            payload["dns"] = list(dns)
        dns_search = spec.create_config.get("dns_search")
        if isinstance(dns_search, list) and dns_search:
            payload["dns_search"] = list(dns_search)
        networks = self._pod_networks_from_config(spec.network_config, spec.container_id)
        if networks:
            payload["share"] = "net"
            payload["networks"] = networks
        return payload

    def _pod_portmappings_from_create_config(
        self,
        create_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ports = create_config.get("ports")
        if not isinstance(ports, dict):
            return []
        portmappings: list[dict[str, Any]] = []
        for container_port, host_binding in sorted(ports.items()):
            if host_binding is None:
                continue
            port, protocol = self._split_port_protocol(container_port)
            if port is None:
                continue
            bindings = host_binding if isinstance(host_binding, list) else [host_binding]
            for binding in bindings:
                mapping: dict[str, Any] = {"container_port": port, "protocol": protocol}
                if isinstance(binding, int):
                    mapping["host_port"] = binding
                elif isinstance(binding, str) and binding.isdigit():
                    mapping["host_port"] = int(binding)
                elif isinstance(binding, tuple) and len(binding) >= 2:
                    host_ip, host_port = binding[0], binding[1]
                    if isinstance(host_ip, str) and host_ip:
                        mapping["host_ip"] = host_ip
                    if isinstance(host_port, int):
                        mapping["host_port"] = host_port
                    elif isinstance(host_port, str) and host_port.isdigit():
                        mapping["host_port"] = int(host_port)
                elif isinstance(binding, dict):
                    host_ip = binding.get("ip") or binding.get("host_ip")
                    host_port = binding.get("port") or binding.get("host_port")
                    if isinstance(host_ip, str) and host_ip:
                        mapping["host_ip"] = host_ip
                    if isinstance(host_port, int):
                        mapping["host_port"] = host_port
                    elif isinstance(host_port, str) and host_port.isdigit():
                        mapping["host_port"] = int(host_port)
                if "host_port" in mapping:
                    portmappings.append(mapping)
        return portmappings

    def _split_port_protocol(self, value: Any) -> tuple[int | None, str]:
        if isinstance(value, int):
            return value, "tcp"
        if not isinstance(value, str) or not value.strip():
            return None, "tcp"
        port_text, separator, protocol = value.partition("/")
        if not port_text.isdigit():
            return None, protocol or "tcp"
        return int(port_text), protocol if separator and protocol else "tcp"

    def _pod_extra_hosts_from_create_config(self, create_config: dict[str, Any]) -> list[str]:
        extra_hosts = create_config.get("extra_hosts")
        if isinstance(extra_hosts, Mapping):
            return [
                f"{hostname}:{address}"
                for hostname, address in sorted(extra_hosts.items())
                if isinstance(hostname, str)
                and hostname.strip()
                and isinstance(address, str)
                and address.strip()
            ]
        if isinstance(extra_hosts, list):
            return [
                item
                for item in extra_hosts
                if isinstance(item, str) and item.strip() and ":" in item
            ]
        return []

    def _pod_networks_from_config(
        self,
        network_config: dict[str, Any],
        container_id: str | None,
    ) -> dict[str, dict[str, Any]]:
        endpoints = network_config.get("endpoints")
        if not isinstance(endpoints, dict):
            return {}
        networks: dict[str, dict[str, Any]] = {}
        dropped_empty_network_count = 0
        for network_name in sorted(endpoints):
            if not isinstance(network_name, str) or not network_name.strip():
                dropped_empty_network_count += 1
                continue
            endpoint = endpoints.get(network_name)
            if not isinstance(endpoint, dict):
                continue
            normalized_network_name = network_name.strip()
            options: dict[str, Any] = {}
            aliases = self._network_aliases(container_id, endpoint)
            if aliases:
                options["aliases"] = aliases
            ipv4_address = self._network_ipv4_address(endpoint)
            if ipv4_address:
                options["static_ips"] = [ipv4_address]
            ipv6_address = self._network_ipv6_address(endpoint)
            if ipv6_address:
                options["static_ips"] = [*options.get("static_ips", []), ipv6_address]
            networks[normalized_network_name] = options
        if dropped_empty_network_count:
            logger.warning(
                "Dropped empty Podman pod network entries before SDK payload: "
                "container_id=%s network_mode=%s dropped_empty_network_count=%s "
                "pod_payload_network_names=%s",
                self._safe_identifier(container_id),
                self._classify_network_mode(network_config.get("network_mode")),
                dropped_empty_network_count,
                sorted(networks),
            )
        return networks

    def _rename_grouped_container_for_replacement(self, container, backup_name: str) -> None:
        if hasattr(container, "rename"):
            container.rename(backup_name)
            return
        raise RuntimeError("podman container rename is required for pod-backed replacement")

    def _remove_replaced_grouped_container_backup(self, client, backup_name: str) -> None:
        try:
            backup_container = client.containers.get(backup_name)
        except Exception:
            return
        try:
            self._remove_container_if_present(backup_container)
        except Exception as exc:
            if not self._is_sdk_json_decode_error(exc):
                raise
            try:
                client.containers.get(backup_name)
            except Exception:
                return
            raise

    def _recover_renamed_grouped_backups(
        self,
        client,
        renamed_backups: list[tuple[str, str]],
    ) -> str | None:
        failures: list[str] = []
        for backup_name, original_name in reversed(renamed_backups):
            try:
                backup_container = client.containers.get(backup_name)
            except Exception:
                continue
            try:
                try:
                    existing = client.containers.get(original_name)
                except Exception:
                    existing = None
                if existing is not None:
                    self._remove_container_if_present(existing)
                self._rename_grouped_container_for_replacement(backup_container, original_name)
                backup_container.start()
            except Exception as exc:
                failures.append(f"{backup_name}: {exc}")
        return "; ".join(failures) or None

    def _remove_container_if_present(self, container) -> None:
        # Force-remove so a still-running container doesn't block the
        # create call with a confusing "name already in use" error.
        # Older SDKs without the ``force`` keyword fall back to explicit
        # stop+remove.
        try:
            container.remove(force=True)
            return
        except TypeError:
            # SDK signature doesn't accept ``force``. Fall through.
            pass
        except Exception:
            # Remove failed — most commonly because the container is
            # still running. Fall through to stop+remove and let the
            # final remove propagate its error.
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

    def _is_not_found_error(self, exc: Exception) -> bool:
        for err in self._iter_exception_chain(exc):
            if err.__class__.__name__ == "NotFound":
                return True
            response = getattr(err, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code == 404:
                return True
            if "404" in str(err) and "not found" in str(err).lower():
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

    def _create_podman_grouped_container(self, client, create_config: dict[str, Any]):
        return self._create_podman_container(client, create_config)

    def _create_podman_container(self, client, create_config: dict[str, Any]):
        create_kwargs = self._podman_container_create_config(create_config)
        source_named_volume_names = self._podman_source_named_volume_names(create_kwargs)
        payload = self._render_podman_create_payload(create_kwargs)
        raw_storage_shape = self._podman_storage_shape_summary(payload)
        raw_relative_storage_destination_count = self._podman_relative_storage_destination_count(
            payload
        )
        raw_relative_volume_mount_destination_count = (
            self._podman_relative_volume_mount_destination_count(payload)
        )
        dropped_rendered_volume_count = self._sanitize_podman_rendered_create_payload(
            payload,
            source_named_volume_names=source_named_volume_names,
        )
        final_payload_normalized_count = self._sanitize_podman_final_mount_payload(payload)
        storage_shape = self._podman_storage_shape_summary(payload)
        relative_storage_destination_count = self._podman_relative_storage_destination_count(
            payload
        )
        relative_volume_mount_destination_count = (
            self._podman_relative_volume_mount_destination_count(payload)
        )
        relative_storage_destination_normalized_count = max(
            0,
            raw_relative_storage_destination_count - relative_storage_destination_count,
        )

        api_client = getattr(client, "api", None)
        if api_client is None or not hasattr(api_client, "post"):
            return client.containers.create(**create_kwargs)

        compatibility = self._podman_low_level_create_compatibility(api_client)
        logger.info(
            "Podman low-level create payload summary: create_endpoint=%s "
            "compatible_requested=%s compatible_accepted=%s container_name=%s "
            "payload_keys=%s restart_policy_type=%s restart_tries_type=%s "
            "volumes_type=%s raw_volumes_count=%s source_named_volume_count=%s "
            "dropped_rendered_volume_count=%s raw_mounts_count=%s raw_tmpfs_count=%s "
            "raw_mount_destination_is_blank=%s raw_named_volume_dest_is_blank=%s "
            "raw_volumes_with_blank_name_count=%s raw_volumes_with_missing_name_count=%s "
            "raw_volumes_with_blank_dest_count=%s raw_volumes_with_missing_dest_count=%s "
            "raw_volume_entry_key_patterns=%s raw_mount_key_patterns_by_type=%s "
            "raw_mount_target_key_count=%s raw_mount_destination_key_count=%s "
            "raw_mount_dest_key_count=%s raw_volume_dest_key_count=%s "
            "raw_volume_destination_key_count=%s raw_volume_mount_count=%s "
            "raw_relative_storage_destination_count=%s "
            "raw_relative_volume_mount_destination_count=%s "
            "relative_storage_destination_normalized_count=%s "
            "final_payload_normalized_count=%s volumes_count=%s "
            "mounts_count=%s tmpfs_count=%s volume_mount_count=%s "
            "relative_storage_destination_count=%s "
            "relative_volume_mount_destination_count=%s "
            "mount_destination_is_blank=%s named_volume_dest_is_blank=%s "
            "volumes_with_blank_name_count=%s volumes_with_missing_name_count=%s "
            "volumes_with_blank_dest_count=%s volumes_with_missing_dest_count=%s "
            "volume_entry_key_patterns=%s mount_key_patterns_by_type=%s "
            "mount_target_key_count=%s mount_destination_key_count=%s mount_dest_key_count=%s "
            "volume_dest_key_count=%s volume_destination_key_count=%s has_work_dir=%s "
            "work_dir_is_blank=%s has_rootfs=%s rootfs_is_blank=%s volumes_from_count=%s "
            "has_blank_volumes_from=%s",
            compatibility["endpoint"],
            compatibility["compatible_requested"],
            compatibility["compatible_accepted"],
            self._safe_identifier(payload.get("name")),
            self._safe_create_config_keys(payload),
            self._safe_payload_value_type(payload.get("restart_policy")),
            self._safe_payload_value_type(payload.get("restart_tries")),
            self._safe_payload_value_type(payload.get("volumes")),
            raw_storage_shape["volumes_count"],
            len(source_named_volume_names),
            dropped_rendered_volume_count,
            raw_storage_shape["mounts_count"],
            raw_storage_shape["tmpfs_count"],
            raw_storage_shape["mount_destination_is_blank"],
            raw_storage_shape["named_volume_dest_is_blank"],
            raw_storage_shape["volumes_with_blank_name_count"],
            raw_storage_shape["volumes_with_missing_name_count"],
            raw_storage_shape["volumes_with_blank_dest_count"],
            raw_storage_shape["volumes_with_missing_dest_count"],
            raw_storage_shape["volume_entry_key_patterns"],
            raw_storage_shape["mount_key_patterns_by_type"],
            raw_storage_shape["mount_target_key_count"],
            raw_storage_shape["mount_destination_key_count"],
            raw_storage_shape["mount_dest_key_count"],
            raw_storage_shape["volume_dest_key_count"],
            raw_storage_shape["volume_destination_key_count"],
            raw_storage_shape["volume_mount_count"],
            raw_relative_storage_destination_count,
            raw_relative_volume_mount_destination_count,
            relative_storage_destination_normalized_count,
            final_payload_normalized_count,
            storage_shape["volumes_count"],
            storage_shape["mounts_count"],
            storage_shape["tmpfs_count"],
            storage_shape["volume_mount_count"],
            relative_storage_destination_count,
            relative_volume_mount_destination_count,
            storage_shape["mount_destination_is_blank"],
            storage_shape["named_volume_dest_is_blank"],
            storage_shape["volumes_with_blank_name_count"],
            storage_shape["volumes_with_missing_name_count"],
            storage_shape["volumes_with_blank_dest_count"],
            storage_shape["volumes_with_missing_dest_count"],
            storage_shape["volume_entry_key_patterns"],
            storage_shape["mount_key_patterns_by_type"],
            storage_shape["mount_target_key_count"],
            storage_shape["mount_destination_key_count"],
            storage_shape["mount_dest_key_count"],
            storage_shape["volume_dest_key_count"],
            storage_shape["volume_destination_key_count"],
            self._has_podman_work_dir_field(payload),
            self._podman_work_dir_is_blank(payload),
            "rootfs" in payload,
            self._string_field_is_blank(payload.get("rootfs")),
            self._safe_payload_item_count(payload.get("volumes_from")),
            self._podman_volumes_from_has_blank(payload),
        )

        response = self._post_podman_libpod_create(api_client, payload, compatibility=compatibility)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        response_payload = response.json() if hasattr(response, "json") else {}
        container_id = response_payload.get("Id") if isinstance(response_payload, Mapping) else None
        if not isinstance(container_id, str) or not container_id.strip():
            raise RuntimeError("podman create response missing container id")
        return client.containers.get(container_id)

    def _podman_low_level_create_compatibility_summary(self, client) -> tuple[bool, bool]:
        api_client = getattr(client, "api", None)
        if api_client is None or not hasattr(api_client, "post"):
            return False, False
        compatibility = self._podman_low_level_create_compatibility(api_client)
        return compatibility["compatible_requested"], compatibility["compatible_accepted"]

    def _podman_low_level_create_compatibility(self, api_client) -> dict[str, Any]:
        compatible_supported = self._api_post_supports_compatible(api_client)
        return {
            "endpoint": PODMAN_LIBPOD_CONTAINER_CREATE_ENDPOINT,
            "compatible_requested": False,
            "compatible_accepted": compatible_supported,
            "compatible_supported": compatible_supported,
        }

    def _api_post_supports_compatible(self, api_client) -> bool:
        try:
            signature = inspect.signature(api_client.post)
        except (TypeError, ValueError):
            return False
        if "compatible" in signature.parameters:
            return True
        return any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    def _post_podman_libpod_create(
        self,
        api_client,
        payload: dict[str, Any],
        *,
        compatibility: dict[str, Any],
    ):
        headers = {"content-type": "application/json"}
        data = json.dumps(payload, sort_keys=True)
        endpoint = compatibility["endpoint"]
        if compatibility["compatible_supported"]:
            return api_client.post(
                endpoint,
                compatible=False,
                headers=headers,
                data=data,
            )
        manual_url = self._podman_libpod_manual_url(api_client, endpoint)
        if manual_url is None or not hasattr(api_client, "request"):
            raise RuntimeError(
                "podman-py API client cannot target libpod container create without compatible=False"
            )
        return api_client.request(
            "POST",
            manual_url,
            headers=headers,
            data=data,
        )

    def _podman_libpod_manual_url(self, api_client, endpoint: str) -> str | None:
        base_url = getattr(api_client, "base_url", None)
        path_prefix = getattr(api_client, "path_prefix", None)
        if base_url is None or not isinstance(path_prefix, str) or not path_prefix.strip():
            return None
        netloc = getattr(base_url, "netloc", None)
        if not isinstance(netloc, str):
            return None
        path = urllib.parse.urljoin(path_prefix, endpoint.lstrip("/"))
        scheme = "https" if getattr(api_client, "verify", None) else "http"
        return urllib.parse.ParseResult(
            scheme,
            netloc,
            path,
            getattr(base_url, "params", ""),
            getattr(base_url, "query", ""),
            getattr(base_url, "fragment", ""),
        ).geturl()

    def _render_podman_create_payload(self, create_config: dict[str, Any]) -> dict[str, Any]:
        payload = dict(create_config)
        command = payload.get("command")
        if isinstance(command, str):
            payload["command"] = [command]

        working_dir = payload.pop("working_dir", None)
        if working_dir is not None:
            payload["work_dir"] = working_dir

        networks = payload.pop("networks", None)
        if networks is not None:
            payload["networks"] = networks
        network = payload.pop("network", None)
        if network is not None:
            payload["cni_networks"] = [network]

        pod = payload.get("pod")
        if pod is not None and not isinstance(pod, str):
            pod_id = getattr(pod, "id", None)
            pod_name = getattr(pod, "name", None)
            if isinstance(pod_id, str) and pod_id.strip():
                payload["pod"] = pod_id.strip()
            elif isinstance(pod_name, str) and pod_name.strip():
                payload["pod"] = pod_name.strip()

        network_mode = payload.pop("network_mode", None)
        if isinstance(network_mode, str) and network_mode.strip():
            details = network_mode.split(":", 1)
            if len(details) == 2 and details[0] == "ns" and details[1]:
                payload["netns"] = {"nsmode": "path", "value": details[1]}
            else:
                payload["netns"] = {"nsmode": network_mode}

        return self._filter_empty_podman_payload_values(payload)

    def _filter_empty_podman_payload_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        filtered: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, dict):
                nested = self._filter_empty_podman_payload_values(value)
                if nested or value == {}:
                    filtered[key] = nested
                continue
            if isinstance(value, list | tuple | set):
                normalized_values = [item for item in value if item is not None]
                if normalized_values:
                    filtered[key] = normalized_values
                continue
            filtered[key] = value
        return filtered

    def _sanitize_podman_rendered_create_payload(
        self,
        payload: dict[str, Any],
        *,
        source_named_volume_names: set[str],
    ) -> int:
        self._remove_blank_podman_scalar_fields(payload, ("work_dir", "working_dir"))

        netns = payload.get("netns")
        if isinstance(netns, Mapping):
            nsmode = netns.get("nsmode")
            if isinstance(nsmode, str) and not nsmode.strip():
                payload.pop("netns", None)
        elif netns in (None, ""):
            payload.pop("netns", None)

        for key in ("cni_networks", "networks"):
            value = payload.get(key)
            if isinstance(value, list):
                normalized = [
                    item for item in value if not (isinstance(item, str) and not item.strip())
                ]
                if normalized:
                    payload[key] = normalized
                else:
                    payload.pop(key, None)
            elif isinstance(value, dict):
                normalized = {
                    item_key.strip(): item_value
                    for item_key, item_value in value.items()
                    if isinstance(item_key, str) and item_key.strip()
                }
                if normalized:
                    payload[key] = normalized
                else:
                    payload.pop(key, None)

        self._normalize_podman_rendered_restart_policy(payload)
        return self._normalize_podman_rendered_storage_fields(
            payload,
            source_named_volume_names=source_named_volume_names,
        )

    def _normalize_podman_rendered_storage_fields(
        self,
        payload: dict[str, Any],
        *,
        source_named_volume_names: set[str],
    ) -> int:
        dropped_rendered_volume_count = 0
        existing_mounts = payload.get("mounts")
        if isinstance(existing_mounts, list):
            normalized_mounts, normalized_volumes = self._normalize_podman_rendered_mounts(
                existing_mounts
            )
        else:
            normalized_mounts = []
            normalized_volumes = []

        volumes = payload.get("volumes")
        if isinstance(volumes, Mapping):
            for source, config in volumes.items():
                if (
                    not isinstance(source, str)
                    or not source.strip()
                    or not isinstance(config, Mapping)
                ):
                    continue
                destination = (
                    config.get("bind")
                    or config.get("target")
                    or config.get("dest")
                    or config.get("destination")
                )
                destination = self._normalize_podman_container_mount_destination(destination)
                if destination is None:
                    continue

                options = self._podman_volume_options_from_config(config)
                if self._is_podman_named_volume_source(source):
                    volume_name = source.strip()
                    if volume_name in source_named_volume_names:
                        normalized_volumes.append(
                            self._podman_named_volume_payload(
                                name=volume_name,
                                destination=destination,
                                options=options,
                            )
                        )
                    else:
                        dropped_rendered_volume_count += 1
                    continue

                mount: dict[str, Any] = {
                    "type": "bind",
                    "source": source,
                    "destination": destination,
                }
                if options:
                    mount["options"] = options
                normalized_mounts.append(mount)
        elif isinstance(volumes, list):
            normalized_named_volumes, dropped_rendered_volume_count = (
                self._normalize_podman_rendered_named_volumes(
                    volumes,
                    source_named_volume_names=source_named_volume_names,
                )
            )
            normalized_volumes.extend(normalized_named_volumes)

        if normalized_volumes:
            payload["volumes"] = normalized_volumes
        else:
            payload.pop("volumes", None)

        if normalized_mounts:
            payload["mounts"] = normalized_mounts
        elif "mounts" in payload:
            payload.pop("mounts", None)

        return dropped_rendered_volume_count

    def _normalize_podman_rendered_mounts(
        self, mounts: list[Any]
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        normalized_mounts: list[Any] = []
        normalized_volumes: list[dict[str, Any]] = []
        for mount in mounts:
            if not isinstance(mount, Mapping):
                normalized_mounts.append(mount)
                continue
            if self._podman_storage_destination_has_blank(mount, PODMAN_MOUNT_DESTINATION_KEYS):
                continue
            destination = self._podman_storage_destination_value(
                mount,
                PODMAN_MOUNT_DESTINATION_KEYS,
            )
            destination = self._normalize_podman_container_mount_destination(destination)
            if destination is None:
                continue
            source = mount.get("source")
            if mount.get("type") == "volume" and isinstance(source, str) and source.strip():
                options = mount.get("options")
                normalized_volumes.append(
                    self._podman_named_volume_payload(
                        name=source.strip(),
                        destination=destination,
                        options=options if isinstance(options, list) else None,
                    )
                )
                continue
            normalized_mount = dict(mount)
            output_key = self._podman_mount_destination_output_key(normalized_mount)
            normalized_mount[output_key] = destination
            for key in PODMAN_MOUNT_DESTINATION_KEYS:
                if key != output_key:
                    normalized_mount.pop(key, None)
            normalized_mounts.append(normalized_mount)
        return normalized_mounts, normalized_volumes

    def _normalize_podman_rendered_named_volumes(
        self,
        volumes: list[Any],
        *,
        source_named_volume_names: set[str],
    ) -> tuple[list[dict[str, Any]], int]:
        normalized: list[dict[str, Any]] = []
        dropped_count = 0
        for volume in volumes:
            if not isinstance(volume, Mapping):
                continue
            if self._podman_named_volume_name_has_blank(volume):
                continue
            name = self._podman_named_volume_name_value(volume)
            if name is None:
                continue
            if self._podman_storage_destination_has_blank(
                volume, PODMAN_NAMED_VOLUME_DESTINATION_KEYS
            ):
                continue
            destination = self._podman_storage_destination_value(
                volume, PODMAN_NAMED_VOLUME_DESTINATION_KEYS
            )
            destination = self._normalize_podman_container_mount_destination(destination)
            if destination is None:
                continue
            if name not in source_named_volume_names:
                dropped_count += 1
                continue
            options = volume.get("options") or volume.get("Options")
            normalized.append(
                self._podman_named_volume_payload(
                    name=name,
                    destination=destination,
                    options=options if isinstance(options, list) else None,
                )
            )
        return normalized, dropped_count

    def _podman_named_volume_payload(
        self,
        *,
        name: str,
        destination: str,
        options: list[Any] | None = None,
    ) -> dict[str, Any]:
        volume: dict[str, Any] = {
            "Name": name,
            "Dest": destination,
        }
        normalized_options = [
            item for item in options or [] if isinstance(item, str) and item.strip()
        ]
        if normalized_options:
            volume["Options"] = normalized_options
        return volume

    def _podman_source_named_volume_names(self, create_config: Mapping[str, Any]) -> set[str]:
        volumes = create_config.get("volumes")
        if not isinstance(volumes, Mapping):
            return set()
        return {
            source.strip()
            for source in volumes
            if isinstance(source, str)
            and source.strip()
            and self._is_podman_named_volume_source(source)
        }

    def _podman_named_volume_name_value(self, volume: Mapping[str, Any]) -> str | None:
        name = volume.get("name") or volume.get("Name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def _podman_named_volume_name_has_blank(self, volume: Mapping[str, Any]) -> bool:
        return any(
            key in volume and self._string_field_is_blank(volume.get(key))
            for key in ("name", "Name")
        )

    def _podman_mount_destination(self, mount: Mapping[str, Any]) -> Any:
        return self._podman_storage_destination_value(mount, PODMAN_MOUNT_DESTINATION_KEYS)

    def _podman_mount_destination_output_key(self, mount: Mapping[str, Any]) -> str:
        return "destination"

    def _podman_volumes_from_has_blank(self, payload: Mapping[str, Any]) -> bool:
        volumes_from = payload.get("volumes_from")
        if not isinstance(volumes_from, list | tuple | set):
            return False
        return any(isinstance(item, str) and not item.strip() for item in volumes_from)

    def _podman_storage_destination_value(
        self,
        payload: Mapping[str, Any],
        keys: tuple[str, ...],
    ) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _normalize_podman_container_mount_destination(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        destination = value.strip()
        if not destination:
            return None
        if destination.startswith("/"):
            return destination
        return f"/{destination.lstrip('/')}"

    def _podman_relative_storage_destination_count(self, payload: Mapping[str, Any]) -> int:
        count = 0
        mounts = payload.get("mounts")
        if isinstance(mounts, list):
            count += sum(
                1
                for mount in mounts
                if isinstance(mount, Mapping)
                and self._podman_storage_destination_is_relative(
                    mount,
                    PODMAN_MOUNT_DESTINATION_KEYS,
                )
            )
        volumes = payload.get("volumes")
        if isinstance(volumes, Mapping):
            count += sum(
                1
                for config in volumes.values()
                if isinstance(config, Mapping)
                and self._podman_storage_destination_is_relative(
                    config,
                    ("bind", *PODMAN_MOUNT_DESTINATION_KEYS),
                )
            )
        elif isinstance(volumes, list):
            count += sum(
                1
                for volume in volumes
                if isinstance(volume, Mapping)
                and self._podman_storage_destination_is_relative(
                    volume,
                    PODMAN_NAMED_VOLUME_DESTINATION_KEYS,
                )
            )
        return count

    def _podman_relative_volume_mount_destination_count(self, payload: Mapping[str, Any]) -> int:
        mounts = payload.get("mounts")
        if not isinstance(mounts, list):
            return 0
        return sum(
            1
            for mount in mounts
            if isinstance(mount, Mapping)
            and mount.get("type") == "volume"
            and self._podman_storage_destination_is_relative(
                mount,
                PODMAN_MOUNT_DESTINATION_KEYS,
            )
        )

    def _podman_storage_destination_is_relative(
        self,
        payload: Mapping[str, Any],
        keys: tuple[str, ...],
    ) -> bool:
        destination = self._podman_storage_destination_value(payload, keys)
        return isinstance(destination, str) and not destination.strip().startswith("/")

    def _sanitize_podman_final_mount_payload(self, payload: dict[str, Any]) -> int:
        mounts = payload.get("mounts")
        if not isinstance(mounts, list):
            return 0

        normalized_mounts: list[Any] = []
        normalized_count = 0
        for mount in mounts:
            if not isinstance(mount, Mapping):
                normalized_mounts.append(mount)
                continue
            destination = self._podman_storage_destination_value(
                mount,
                PODMAN_MOUNT_DESTINATION_KEYS,
            )
            absolute_destination = self._normalize_podman_container_mount_destination(destination)
            if absolute_destination is None:
                continue
            normalized_mount = dict(mount)
            original_destination_values = {
                key: normalized_mount.get(key)
                for key in PODMAN_MOUNT_DESTINATION_KEYS
                if key in normalized_mount
            }
            output_key = self._podman_mount_destination_output_key(normalized_mount)
            normalized_mount[output_key] = absolute_destination
            for key in PODMAN_MOUNT_DESTINATION_KEYS:
                if key != output_key:
                    normalized_mount.pop(key, None)
            if original_destination_values != {output_key: absolute_destination}:
                normalized_count += 1
            normalized_mounts.append(normalized_mount)

        if normalized_mounts:
            payload["mounts"] = normalized_mounts
        else:
            payload.pop("mounts", None)
        return normalized_count

    def _podman_storage_destination_has_blank(
        self,
        payload: Mapping[str, Any],
        keys: tuple[str, ...],
    ) -> bool:
        return any(key in payload and self._string_field_is_blank(payload.get(key)) for key in keys)

    def _podman_storage_shape_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        mounts = payload.get("mounts")
        volumes = payload.get("volumes")
        mount_entries = mounts if isinstance(mounts, list) else []
        volume_entries = volumes if isinstance(volumes, list) else []
        return {
            "mounts_count": len(mount_entries),
            "volumes_count": len(volume_entries),
            "tmpfs_count": sum(
                1
                for mount in mount_entries
                if isinstance(mount, Mapping) and mount.get("type") == "tmpfs"
            ),
            "volume_mount_count": sum(
                1
                for mount in mount_entries
                if isinstance(mount, Mapping) and mount.get("type") == "volume"
            ),
            "mount_destination_is_blank": any(
                isinstance(mount, Mapping)
                and self._podman_storage_destination_has_blank(
                    mount,
                    PODMAN_MOUNT_DESTINATION_KEYS,
                )
                for mount in mount_entries
            ),
            "named_volume_dest_is_blank": any(
                isinstance(volume, Mapping)
                and self._podman_storage_destination_has_blank(
                    volume, PODMAN_NAMED_VOLUME_DESTINATION_KEYS
                )
                for volume in volume_entries
            ),
            "mount_target_key_count": self._count_storage_entries_with_key(mount_entries, "target"),
            "mount_destination_key_count": self._count_storage_entries_with_key(
                mount_entries,
                "destination",
            ),
            "mount_dest_key_count": self._count_storage_entries_with_key(mount_entries, "dest"),
            "volume_dest_key_count": self._count_storage_entries_with_key(volume_entries, "Dest")
            + self._count_storage_entries_with_key(volume_entries, "dest"),
            "volume_destination_key_count": self._count_storage_entries_with_key(
                volume_entries,
                "destination",
            ),
            "volumes_with_blank_name_count": self._count_named_volumes_with_blank_name(
                volume_entries
            ),
            "volumes_with_missing_name_count": self._count_named_volumes_with_missing_name(
                volume_entries
            ),
            "volumes_with_blank_dest_count": self._count_named_volumes_with_blank_dest(
                volume_entries
            ),
            "volumes_with_missing_dest_count": self._count_named_volumes_with_missing_dest(
                volume_entries
            ),
            "volume_entry_key_patterns": self._storage_entry_key_patterns(volume_entries),
            "mount_key_patterns_by_type": self._mount_key_patterns_by_type(mount_entries),
        }

    def _count_storage_entries_with_key(self, entries: list[Any], key: str) -> int:
        return sum(1 for entry in entries if isinstance(entry, Mapping) and key in entry)

    def _count_named_volumes_with_blank_name(self, entries: list[Any]) -> int:
        return sum(
            1
            for entry in entries
            if isinstance(entry, Mapping) and self._podman_named_volume_name_has_blank(entry)
        )

    def _count_named_volumes_with_missing_name(self, entries: list[Any]) -> int:
        return sum(
            1
            for entry in entries
            if isinstance(entry, Mapping) and "name" not in entry and "Name" not in entry
        )

    def _count_named_volumes_with_blank_dest(self, entries: list[Any]) -> int:
        return sum(
            1
            for entry in entries
            if isinstance(entry, Mapping)
            and self._podman_storage_destination_has_blank(
                entry, PODMAN_NAMED_VOLUME_DESTINATION_KEYS
            )
        )

    def _count_named_volumes_with_missing_dest(self, entries: list[Any]) -> int:
        return sum(
            1
            for entry in entries
            if isinstance(entry, Mapping)
            and self._podman_storage_destination_value(entry, PODMAN_NAMED_VOLUME_DESTINATION_KEYS)
            is None
            and not self._podman_storage_destination_has_blank(
                entry, PODMAN_NAMED_VOLUME_DESTINATION_KEYS
            )
        )

    def _storage_entry_key_patterns(self, entries: list[Any]) -> dict[str, int]:
        patterns: dict[str, int] = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                patterns["<non-mapping>"] = patterns.get("<non-mapping>", 0) + 1
                continue
            keys = sorted(key for key in entry if isinstance(key, str))
            pattern = ",".join(keys) if keys else "<empty>"
            patterns[pattern] = patterns.get(pattern, 0) + 1
        return patterns

    def _mount_key_patterns_by_type(self, entries: list[Any]) -> dict[str, dict[str, int]]:
        patterns: dict[str, dict[str, int]] = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                mount_type = "<non-mapping>"
                pattern = "<non-mapping>"
            else:
                raw_mount_type = entry.get("type")
                mount_type = raw_mount_type if isinstance(raw_mount_type, str) else "<missing>"
                keys = sorted(key for key in entry if isinstance(key, str))
                pattern = ",".join(keys) if keys else "<empty>"
            type_patterns = patterns.setdefault(mount_type, {})
            type_patterns[pattern] = type_patterns.get(pattern, 0) + 1
        return patterns

    def _podman_volume_options_from_config(self, config: Mapping[str, Any]) -> list[str]:
        options: list[str] = []
        extended_mode = config.get("extended_mode")
        if isinstance(extended_mode, list):
            options.extend(item for item in extended_mode if isinstance(item, str) and item.strip())

        mode = config.get("mode")
        if isinstance(mode, str) and mode.strip():
            options.append(mode.strip())

        return options

    def _is_podman_named_volume_source(self, source: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source.strip()))

    def _normalize_podman_rendered_restart_policy(self, payload: dict[str, Any]) -> None:
        restart_policy = payload.get("restart_policy")
        if not isinstance(restart_policy, Mapping):
            if isinstance(restart_policy, str) and not restart_policy.strip():
                payload.pop("restart_policy", None)
            return

        restart_name = restart_policy.get("Name") or restart_policy.get("name")
        if isinstance(restart_name, str) and restart_name.strip():
            payload["restart_policy"] = restart_name.strip()
        else:
            payload.pop("restart_policy", None)

        restart_tries = restart_policy.get("MaximumRetryCount")
        if restart_tries is None:
            restart_tries = restart_policy.get("maximum_retry_count")
        if restart_tries is None:
            restart_tries = restart_policy.get("restart_tries")
        if isinstance(restart_tries, bool):
            return
        if isinstance(restart_tries, int):
            payload["restart_tries"] = restart_tries
            return
        if isinstance(restart_tries, str) and restart_tries.strip().isdigit():
            payload["restart_tries"] = int(restart_tries.strip())

    def _podman_grouped_create_config_for_spec(
        self,
        spec: GroupedRuntimeRecreateSpec,
        *,
        client=None,
    ) -> dict[str, Any]:
        create_config = dict(spec.create_config)
        create_config = self._apply_podman_host_config_preservation(create_config, spec.host_config)
        create_config = self._podman_container_create_config(create_config)
        if spec.pod_id or spec.pod_name:
            self._prepare_create_config_for_pod_membership(create_config)
        return self._apply_pod_membership_to_create_config(
            create_config,
            pod_name=spec.pod_name,
            pod_id=spec.pod_id,
            client=client,
        )

    def _podman_grouped_create_config_for_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        client=None,
    ) -> dict[str, Any]:
        create_config = dict(snapshot.get("create_config") or {})
        host_config = (
            snapshot.get("host_config") if isinstance(snapshot.get("host_config"), dict) else {}
        )
        create_config = self._apply_podman_host_config_preservation(create_config, host_config)
        pod_name = snapshot.get("pod_name") if isinstance(snapshot.get("pod_name"), str) else None
        pod_id = snapshot.get("pod_id") if isinstance(snapshot.get("pod_id"), str) else None
        if pod_name or pod_id:
            self._prepare_create_config_for_pod_membership(create_config)
        return self._apply_pod_membership_to_create_config(
            create_config,
            pod_name=pod_name,
            pod_id=pod_id,
            client=client,
        )

    def _podman_container_create_config(
        self,
        create_config: dict[str, Any],
    ) -> dict[str, Any]:
        sanitized = dict(create_config)
        self._sanitize_podman_create_config(sanitized)
        self._filter_podman_exposed_only_ports(sanitized)
        self._convert_podman_tmpfs_to_mounts(sanitized)
        self._normalize_podman_security_options(sanitized)
        self._normalize_podman_cpu_limits(sanitized)
        self._normalize_podman_stop_signal(sanitized)
        extra_hosts = sanitized.get("extra_hosts")
        if isinstance(extra_hosts, list):
            normalized_extra_hosts: dict[str, str] = {}
            for item in extra_hosts:
                if not isinstance(item, str) or ":" not in item:
                    continue
                hostname, _, address = item.partition(":")
                hostname = hostname.strip()
                address = address.strip()
                if hostname and address:
                    normalized_extra_hosts[hostname] = address
            if normalized_extra_hosts:
                sanitized["extra_hosts"] = normalized_extra_hosts
            else:
                sanitized.pop("extra_hosts", None)
        return sanitized

    def _sanitize_podman_create_config(self, create_config: dict[str, Any]) -> None:
        network_mode = create_config.get("network_mode")
        if isinstance(network_mode, str) and not network_mode.strip():
            create_config.pop("network_mode", None)

        for mode_key in ("ipc_mode", "pid_mode", "userns_mode", "uts_mode"):
            mode_value = create_config.get(mode_key)
            if isinstance(mode_value, str) and not mode_value.strip():
                create_config.pop(mode_key, None)

        network = create_config.get("network")
        if isinstance(network, str):
            if network.strip():
                create_config["network"] = network.strip()
            else:
                create_config.pop("network", None)

        self._sanitize_podman_namespace_payloads(create_config)
        self._remove_blank_podman_scalar_fields(
            create_config,
            ("working_dir", "work_dir", "hostname", "user", "stop_signal"),
        )

        networks = create_config.get("networks")
        if isinstance(networks, list):
            dropped_empty_network_count = sum(
                1
                for network_name in networks
                if isinstance(network_name, str) and not network_name.strip()
            )
            normalized_networks = [
                network_name.strip()
                for network_name in networks
                if isinstance(network_name, str) and network_name.strip()
            ]
            if normalized_networks:
                create_config["networks"] = normalized_networks
            else:
                create_config.pop("networks", None)
            if dropped_empty_network_count:
                logger.warning(
                    "Dropped empty Podman container network entries before SDK payload: "
                    "container_name=%s network_mode=%s dropped_empty_network_count=%s "
                    "network_names=%s create_config_keys=%s",
                    self._safe_identifier(create_config.get("name")),
                    self._classify_network_mode(create_config.get("network_mode")),
                    dropped_empty_network_count,
                    self._safe_network_names(normalized_networks),
                    self._safe_create_config_keys(create_config),
                )
        elif isinstance(networks, dict):
            dropped_empty_network_count = sum(
                1
                for network_name in networks
                if isinstance(network_name, str) and not network_name.strip()
            )
            normalized_network_map = {
                network_name.strip(): options
                for network_name, options in networks.items()
                if isinstance(network_name, str) and network_name.strip()
            }
            if normalized_network_map:
                create_config["networks"] = normalized_network_map
            else:
                create_config.pop("networks", None)
            if dropped_empty_network_count:
                logger.warning(
                    "Dropped empty Podman container network entries before SDK payload: "
                    "container_name=%s network_mode=%s dropped_empty_network_count=%s "
                    "network_names=%s create_config_keys=%s",
                    self._safe_identifier(create_config.get("name")),
                    self._classify_network_mode(create_config.get("network_mode")),
                    dropped_empty_network_count,
                    self._safe_network_names(normalized_network_map),
                    self._safe_create_config_keys(create_config),
                )

    def _sanitize_podman_namespace_payloads(self, create_config: dict[str, Any]) -> None:
        for namespace_key in (
            "netns",
            "utsns",
            "ipcns",
            "pidns",
            "cgroupns",
            "userns",
        ):
            namespace_payload = create_config.get(namespace_key)
            if not isinstance(namespace_payload, Mapping):
                if namespace_payload in (None, ""):
                    create_config.pop(namespace_key, None)
                continue
            namespace_mode = namespace_payload.get("nsmode")
            namespace_value = namespace_payload.get("value")
            if isinstance(namespace_mode, str) and not namespace_mode.strip():
                create_config.pop(namespace_key, None)
                continue
            if namespace_mode is None and namespace_value in (None, ""):
                create_config.pop(namespace_key, None)

    def _remove_blank_podman_scalar_fields(
        self,
        payload: dict[str, Any],
        keys: tuple[str, ...],
    ) -> None:
        for key in keys:
            if self._string_field_is_blank(payload.get(key)):
                payload.pop(key, None)

    def _filter_podman_exposed_only_ports(self, create_config: dict[str, Any]) -> None:
        ports = create_config.get("ports")
        if not isinstance(ports, dict):
            return
        bound_ports = {port: binding for port, binding in ports.items() if binding is not None}
        if bound_ports:
            create_config["ports"] = bound_ports
        else:
            create_config.pop("ports", None)

    def _convert_podman_tmpfs_to_mounts(self, create_config: dict[str, Any]) -> None:
        tmpfs = create_config.pop("tmpfs", None)
        if not isinstance(tmpfs, Mapping):
            return
        mounts = list(create_config.get("mounts") or [])
        for target, raw_options in tmpfs.items():
            destination = self._normalize_podman_container_mount_destination(target)
            if destination is None:
                continue
            mount: dict[str, Any] = {"type": "tmpfs", "destination": destination}
            if isinstance(raw_options, str):
                for option in raw_options.split(","):
                    key, separator, value = option.strip().partition("=")
                    if separator and key == "size" and value:
                        mount["size"] = value
                        break
            mounts.append(mount)
        if mounts:
            create_config["mounts"] = mounts

    def _normalize_podman_security_options(self, create_config: dict[str, Any]) -> None:
        security_opt = create_config.get("security_opt")
        if not isinstance(security_opt, list):
            return
        normalized_security_opt: list[Any] = []
        no_new_privileges = False
        for item in security_opt:
            if isinstance(item, str) and item.strip() in {
                "no-new-privileges",
                "no-new-privileges:true",
            }:
                no_new_privileges = True
                continue
            normalized_security_opt.append(item)
        if no_new_privileges:
            create_config["no_new_privileges"] = True
        if normalized_security_opt:
            create_config["security_opt"] = normalized_security_opt
        else:
            create_config.pop("security_opt", None)

    def _normalize_podman_cpu_limits(self, create_config: dict[str, Any]) -> None:
        nano_cpus = create_config.pop("nano_cpus", None)
        if not isinstance(nano_cpus, int) or nano_cpus <= 0:
            return
        if "cpu_quota" in create_config or "cpu_period" in create_config:
            return
        cpu_period = 100_000
        create_config["cpu_period"] = cpu_period
        create_config["cpu_quota"] = max(1, round(nano_cpus * cpu_period / 1_000_000_000))

    def _normalize_podman_stop_signal(self, create_config: dict[str, Any]) -> None:
        stop_signal = create_config.get("stop_signal")
        if not isinstance(stop_signal, str):
            return
        normalized = stop_signal.strip().upper()
        if not normalized or normalized.isdigit():
            return
        if not normalized.startswith("SIG"):
            normalized = f"SIG{normalized}"
        signal_number = getattr(signal, normalized, None)
        if isinstance(signal_number, signal.Signals):
            create_config["stop_signal"] = int(signal_number)

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
                container_path = self._normalize_podman_container_mount_destination(container_path)
                if container_path is None:
                    continue
                option_tokens = [
                    token.strip() for token in (raw_mode or "").split(",") if token.strip()
                ]
                if host_path.startswith("/"):
                    mount_payload: dict[str, Any] = {
                        "type": "bind",
                        "source": host_path,
                        "destination": container_path,
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

                normalized_volumes[host_path] = {
                    "bind": container_path,
                    "mode": "ro" if "ro" in option_tokens else "rw",
                }
                updated_volumes = True
            if updated_volumes and normalized_volumes:
                create_config["volumes"] = normalized_volumes
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
            tag = log_config.get("Tag")
            if isinstance(tag, str) and tag.strip():
                config_options = dict(normalized_log_config.get("Config") or {})
                log_options = dict(config_options.get("options") or {})
                log_options["tag"] = tag.strip()
                config_options["options"] = log_options
                normalized_log_config["Config"] = config_options
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

    def _prepare_create_config_for_pod_membership(self, create_config: dict[str, Any]) -> None:
        create_config.pop("hostname", None)
        create_config.pop("network", None)
        create_config.pop("networks", None)
        create_config.pop("network_mode", None)
        create_config.pop("network_options", None)
        create_config.pop("ports", None)
        create_config.pop("exposed_ports", None)
        create_config.pop("extra_hosts", None)
        create_config.pop("dns", None)
        create_config.pop("dns_search", None)
        create_config.pop("dns_opt", None)
        create_config.pop("shm_size", None)

    def _restore_container_networks(
        self,
        client,
        new_container,
        spec: GroupedRuntimeRecreateSpec,
        *,
        phase: str | None = None,
    ) -> None:
        if self._is_pod_backed_grouped_spec(spec):
            return
        self._restore_container_networks_for_payload(
            client,
            new_container,
            container_id=spec.container_id,
            network_config=spec.network_config,
            phase=phase,
        )

    def _restore_container_networks_from_snapshot(
        self,
        client,
        new_container,
        snapshot: dict[str, Any],
        *,
        phase: str | None = None,
    ) -> None:
        if self._snapshot_has_pod_membership(snapshot):
            return
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
            phase=phase,
        )

    def _is_pod_backed_grouped_spec(self, spec: GroupedRuntimeRecreateSpec) -> bool:
        return self._has_non_empty_string(spec.pod_name) or self._has_non_empty_string(spec.pod_id)

    def _snapshot_has_pod_membership(self, snapshot: Mapping[str, Any]) -> bool:
        return self._has_non_empty_string(snapshot.get("pod_name")) or self._has_non_empty_string(
            snapshot.get("pod_id")
        )

    def _has_non_empty_string(self, value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def _restore_container_networks_for_payload(
        self,
        client,
        new_container,
        *,
        container_id: str | None,
        network_config: dict[str, Any],
        phase: str | None = None,
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

        normalized_endpoint_names: set[str] = set()
        dropped_empty_network_count = 0
        for network_name in sorted(endpoints):
            if not isinstance(network_name, str) or not network_name.strip():
                dropped_empty_network_count += 1
                continue
            endpoint = endpoints.get(network_name)
            if not isinstance(endpoint, dict):
                continue
            normalized_network_name = network_name.strip()
            normalized_endpoint_names.add(normalized_network_name)
            logger.debug(
                "Podman network restore SDK boundary: phase=%s boundary=networks.get "
                "container_name=%s container_id=%s network_mode=%s endpoint_network=%s",
                phase or "unknown",
                self._safe_identifier(getattr(new_container, "name", None)),
                self._safe_identifier(container_id),
                self._classify_network_mode(network_mode),
                normalized_network_name,
            )
            network = networks.get(normalized_network_name)
            if hasattr(network, "disconnect"):
                try:
                    network.disconnect(new_container, force=True)
                except Exception:
                    pass
            connect_kwargs = self._network_connect_kwargs(container_id, endpoint)
            logger.debug(
                "Podman network restore SDK boundary: phase=%s boundary=networks.connect "
                "container_name=%s container_id=%s network_mode=%s endpoint_network=%s "
                "connect_kwarg_keys=%s",
                phase or "unknown",
                self._safe_identifier(getattr(new_container, "name", None)),
                self._safe_identifier(container_id),
                self._classify_network_mode(network_mode),
                normalized_network_name,
                sorted(connect_kwargs),
            )
            network.connect(new_container, **connect_kwargs)

        if dropped_empty_network_count:
            logger.warning(
                "Dropped empty Podman restore network entries before SDK calls: phase=%s "
                "container_name=%s container_id=%s network_mode=%s "
                "dropped_empty_network_count=%s network_endpoint_names=%s",
                phase or "unknown",
                self._safe_identifier(getattr(new_container, "name", None)),
                self._safe_identifier(container_id),
                self._classify_network_mode(network_mode),
                dropped_empty_network_count,
                sorted(normalized_endpoint_names),
            )

        if "podman" not in normalized_endpoint_names:
            try:
                default_network = networks.get("podman")
                default_network.disconnect(new_container, force=True)
            except Exception:
                pass

    def _log_podman_grouped_create_boundary(
        self,
        phase: str,
        spec: GroupedRuntimeRecreateSpec,
        create_config: dict[str, Any],
        *,
        boundary: str,
        create_strategy: str,
        create_endpoint: str,
        create_compatible_requested: bool,
        create_compatible_accepted: bool,
        pod_join_strategy: str | None = None,
    ) -> None:
        pod_join_shape = self._pod_join_shape_summary(
            create_config,
            pod_name=spec.pod_name,
            pod_id=spec.pod_id,
            pod_relation_payload=spec.pod_relation_payload,
            pod_join_strategy=pod_join_strategy,
        )
        logger.info(
            "Podman grouped compose SDK boundary: phase=%s boundary=%s create_strategy=%s "
            "create_endpoint=%s compatible_requested=%s compatible_accepted=%s "
            "container_name=%s container_id=%s pod_name=%s pod_id=%s "
            "pod_value_class=%s pod_join_strategy=%s has_infra_id=%s "
            "netns_nsmode=%s netns_value_class=%s "
            "has_network_mode=%s network_mode_is_blank=%s network_mode=%s "
            "network_mode_avoidance=%s "
            "has_network=%s network_is_blank=%s has_networks=%s network_names=%s "
            "has_work_dir=%s work_dir_is_blank=%s "
            "network_endpoint_names=%s dropped_empty_network_count=%s create_config_keys=%s",
            phase,
            boundary,
            create_strategy,
            create_endpoint,
            create_compatible_requested,
            create_compatible_accepted,
            self._safe_identifier(spec.container_name),
            self._safe_identifier(spec.container_id),
            self._safe_identifier(spec.pod_name),
            self._safe_identifier(spec.pod_id),
            pod_join_shape["pod_value_class"],
            pod_join_shape["pod_join_strategy"],
            pod_join_shape["has_infra_id"],
            pod_join_shape["netns_nsmode"],
            pod_join_shape["netns_value_class"],
            "network_mode" in create_config,
            self._network_mode_is_blank(create_config.get("network_mode")),
            self._classify_network_mode(create_config.get("network_mode")),
            self._podman_network_mode_avoidance(create_config),
            "network" in create_config,
            self._network_mode_is_blank(create_config.get("network")),
            "networks" in create_config,
            self._safe_network_names(create_config.get("networks")),
            self._has_podman_work_dir_field(create_config),
            self._podman_work_dir_is_blank(create_config),
            self._safe_network_names(self._endpoint_names_from_network_config(spec.network_config)),
            self._dropped_empty_endpoint_count(spec.network_config),
            self._safe_create_config_keys(create_config),
        )

    def _log_podman_grouped_recovery_create_boundary(
        self,
        snapshot: dict[str, Any],
        create_config: dict[str, Any],
        *,
        boundary: str,
        create_strategy: str,
        create_endpoint: str,
        create_compatible_requested: bool,
        create_compatible_accepted: bool,
    ) -> None:
        network_config = snapshot.get("network_config") if isinstance(snapshot, Mapping) else None
        pod_relation_payload = (
            snapshot.get("pod_relation_payload") if isinstance(snapshot, Mapping) else None
        )
        pod_join_shape = self._pod_join_shape_summary(
            create_config,
            pod_name=snapshot.get("pod_name"),
            pod_id=snapshot.get("pod_id"),
            pod_relation_payload=(
                pod_relation_payload if isinstance(pod_relation_payload, dict) else {}
            ),
        )
        logger.info(
            "Podman grouped compose SDK boundary: phase=recovery boundary=%s create_strategy=%s "
            "create_endpoint=%s compatible_requested=%s compatible_accepted=%s "
            "container_name=%s container_id=%s pod_name=%s pod_id=%s "
            "pod_value_class=%s pod_join_strategy=%s has_infra_id=%s "
            "netns_nsmode=%s netns_value_class=%s "
            "has_network_mode=%s network_mode_is_blank=%s network_mode=%s "
            "network_mode_avoidance=%s "
            "has_network=%s network_is_blank=%s has_networks=%s network_names=%s "
            "has_work_dir=%s work_dir_is_blank=%s "
            "network_endpoint_names=%s dropped_empty_network_count=%s create_config_keys=%s",
            boundary,
            create_strategy,
            create_endpoint,
            create_compatible_requested,
            create_compatible_accepted,
            self._safe_identifier(snapshot.get("container_name")),
            self._safe_identifier(snapshot.get("container_id")),
            self._safe_identifier(snapshot.get("pod_name")),
            self._safe_identifier(snapshot.get("pod_id")),
            pod_join_shape["pod_value_class"],
            pod_join_shape["pod_join_strategy"],
            pod_join_shape["has_infra_id"],
            pod_join_shape["netns_nsmode"],
            pod_join_shape["netns_value_class"],
            "network_mode" in create_config,
            self._network_mode_is_blank(create_config.get("network_mode")),
            self._classify_network_mode(create_config.get("network_mode")),
            self._podman_network_mode_avoidance(create_config),
            "network" in create_config,
            self._network_mode_is_blank(create_config.get("network")),
            "networks" in create_config,
            self._safe_network_names(create_config.get("networks")),
            self._has_podman_work_dir_field(create_config),
            self._podman_work_dir_is_blank(create_config),
            self._safe_network_names(
                self._endpoint_names_from_network_config(
                    network_config if isinstance(network_config, dict) else {}
                )
            ),
            self._dropped_empty_endpoint_count(
                network_config if isinstance(network_config, dict) else {}
            ),
            self._safe_create_config_keys(create_config),
        )

    def _log_podman_grouped_pod_create_boundary(
        self,
        phase: str,
        spec: GroupedRuntimeRecreateSpec,
        pod_name: str,
        pod_payload: dict[str, Any],
        *,
        boundary: str,
    ) -> None:
        networks = pod_payload.get("networks")
        logger.info(
            "Podman grouped compose SDK boundary: phase=%s boundary=%s "
            "container_name=%s container_id=%s pod_name=%s pod_id=%s "
            "pod_payload_network_names=%s dropped_empty_network_count=%s pod_payload_keys=%s",
            phase,
            boundary,
            self._safe_identifier(spec.container_name),
            self._safe_identifier(spec.container_id),
            self._safe_identifier(pod_name),
            self._safe_identifier(spec.pod_id),
            self._safe_network_names(networks if isinstance(networks, Mapping) else {}),
            self._dropped_empty_endpoint_count(spec.network_config),
            sorted(pod_payload),
        )

    def _endpoint_names_from_network_config(self, network_config: dict[str, Any]) -> list[str]:
        endpoints = network_config.get("endpoints")
        if not isinstance(endpoints, Mapping):
            return []
        return [name for name in endpoints if isinstance(name, str)]

    def _dropped_empty_endpoint_count(self, network_config: dict[str, Any]) -> int:
        endpoints = network_config.get("endpoints")
        if not isinstance(endpoints, Mapping):
            return 0
        dropped_count = network_config.get("dropped_empty_network_count")
        if isinstance(dropped_count, int) and dropped_count > 0:
            return dropped_count
        return sum(1 for name in endpoints if isinstance(name, str) and not name.strip())

    def _safe_create_config_keys(self, create_config: dict[str, Any]) -> list[str]:
        return sorted(key for key in create_config if isinstance(key, str))

    def _pod_join_shape_summary(
        self,
        create_config: Mapping[str, Any],
        *,
        pod_name: Any,
        pod_id: Any,
        pod_relation_payload: Mapping[str, Any] | None,
        pod_join_strategy: str | None = None,
    ) -> dict[str, Any]:
        pod_value = create_config.get("pod")
        netns = create_config.get("netns")
        netns_nsmode = None
        netns_value_class = "missing"
        if isinstance(netns, Mapping):
            nsmode = netns.get("nsmode")
            if isinstance(nsmode, str) and nsmode.strip():
                netns_nsmode = nsmode.strip()
            netns_value_class = self._safe_payload_value_type(netns.get("value"))

        return {
            "pod_value_class": self._safe_payload_value_type(pod_value),
            "pod_join_strategy": pod_join_strategy
            or self._pod_join_strategy(
                pod_value,
                pod_name=pod_name,
                pod_id=pod_id,
            ),
            "has_infra_id": self._pod_relation_has_infra_id(pod_relation_payload),
            "netns_nsmode": netns_nsmode,
            "netns_value_class": netns_value_class,
        }

    def _pod_join_strategy(self, pod_value: Any, *, pod_name: Any, pod_id: Any) -> str:
        pod_value_name = getattr(pod_value, "name", None)
        if isinstance(pod_value_name, str) and pod_value_name.strip():
            return "resolved_pod_name"
        if self._has_non_empty_string(pod_value) and self._has_non_empty_string(pod_name):
            if pod_value == pod_name:
                return "pod_name"
            return "resolved_pod_name"
        if self._has_non_empty_string(pod_value) and self._has_non_empty_string(pod_id):
            if pod_value == pod_id:
                return "pod_id_fallback"
            return "resolved_pod_name"
        if self._has_non_empty_string(pod_value):
            return "explicit_pod_string"
        return "none"

    def _pod_relation_has_infra_id(self, pod_relation_payload: Mapping[str, Any] | None) -> bool:
        if not isinstance(pod_relation_payload, Mapping):
            return False
        return self._has_non_empty_string(pod_relation_payload.get("pod_infra_id"))

    def _safe_payload_value_type(self, value: Any) -> str:
        if value is None:
            return "missing"
        return type(value).__name__

    def _safe_payload_item_count(self, value: Any) -> int:
        if isinstance(value, Mapping):
            return len(value)
        if isinstance(value, list | tuple | set):
            return len(value)
        return 0

    def _has_podman_work_dir_field(self, payload: Mapping[str, Any]) -> bool:
        return "work_dir" in payload or "working_dir" in payload

    def _podman_work_dir_is_blank(self, payload: Mapping[str, Any]) -> bool:
        return self._string_field_is_blank(payload.get("work_dir")) or self._string_field_is_blank(
            payload.get("working_dir")
        )

    def _safe_network_names(self, network_names: Any) -> list[str]:
        if isinstance(network_names, Mapping):
            candidates = network_names.keys()
        elif isinstance(network_names, list | tuple | set):
            candidates = network_names
        else:
            return []
        sanitized: list[str] = []
        for network_name in candidates:
            if not isinstance(network_name, str) or not network_name.strip():
                continue
            sanitized.append(network_name.strip())
        return sorted(sanitized)

    def _safe_identifier(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _safe_exception_message(self, exc: Exception) -> str:
        message = str(exc).strip()
        if not message:
            return ""
        for pattern in (
            r"(?i)(password|passwd|token|secret|api[_-]?key|access[_-]?key)=\S+",
            r"(?i)(password|passwd|token|secret|api[_-]?key|access[_-]?key)\s*[:=]\s*[^\s,;]+",
        ):
            message = re.sub(pattern, r"\1=***REDACTED***", message)
        return message[:500]

    def _network_mode_is_blank(self, value: Any) -> bool:
        return self._string_field_is_blank(value)

    def _string_field_is_blank(self, value: Any) -> bool:
        return isinstance(value, str) and not value.strip()

    def _classify_network_mode(self, value: Any) -> str:
        if value is None:
            return "missing"
        if not isinstance(value, str):
            return "other"
        normalized = value.strip()
        if not normalized:
            return "blank"
        if normalized in {"host", "bridge", "none"}:
            return normalized
        if normalized.startswith("container:"):
            return "container"
        return "other"

    def _podman_network_mode_avoidance(self, create_config: dict[str, Any]) -> str:
        if "network_mode" in create_config:
            return "preserved"
        return "omitted_for_low_level_create"

    def _network_connect_kwargs(
        self,
        container_id: str | None,
        endpoint: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}

        normalized_aliases = self._network_aliases(container_id, endpoint)
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

        if "ipv4_address" not in kwargs:
            ipv4_address = self._network_ipv4_address(endpoint)
            if ipv4_address:
                kwargs["ipv4_address"] = ipv4_address
        if "ipv6_address" not in kwargs:
            ipv6_address = self._network_ipv6_address(endpoint)
            if ipv6_address:
                kwargs["ipv6_address"] = ipv6_address

        return kwargs

    def _network_ipv4_address(self, endpoint: dict[str, Any]) -> str | None:
        ipam_config = endpoint.get("IPAMConfig")
        if isinstance(ipam_config, dict):
            ipv4_address = ipam_config.get("IPv4Address")
            if isinstance(ipv4_address, str) and ipv4_address.strip():
                return ipv4_address
        ipv4_address = endpoint.get("IPAddress")
        if isinstance(ipv4_address, str) and ipv4_address.strip():
            return ipv4_address
        return None

    def _network_ipv6_address(self, endpoint: dict[str, Any]) -> str | None:
        ipam_config = endpoint.get("IPAMConfig")
        if isinstance(ipam_config, dict):
            ipv6_address = ipam_config.get("IPv6Address")
            if isinstance(ipv6_address, str) and ipv6_address.strip():
                return ipv6_address
        ipv6_address = endpoint.get("GlobalIPv6Address")
        if isinstance(ipv6_address, str) and ipv6_address.strip():
            return ipv6_address
        return None

    def _network_aliases(
        self,
        container_id: str | None,
        endpoint: dict[str, Any],
    ) -> list[str]:
        aliases = endpoint.get("Aliases")
        if not isinstance(aliases, list):
            return []
        container_id_aliases = {
            container_id or "",
            (container_id or "")[:12],
        }
        normalized_aliases: list[str] = []
        for alias in aliases:
            if (
                isinstance(alias, str)
                and alias.strip()
                and alias not in container_id_aliases
                and alias not in normalized_aliases
            ):
                normalized_aliases.append(alias)
        return normalized_aliases

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
        client=None,
    ) -> dict[str, Any]:
        if isinstance(pod_name, str) and pod_name.strip():
            create_config["pod"] = pod_name.strip()
            return create_config
        if not isinstance(pod_id, str) or not pod_id.strip():
            return create_config

        normalized_pod_id = pod_id.strip()
        create_config["pod"] = self._resolve_pod_reference_for_create(client, normalized_pod_id)
        return create_config

    def _resolve_pod_reference_for_create(self, client, pod_ref: str) -> str:
        pods = getattr(client, "pods", None)
        if pods is None or not hasattr(pods, "get"):
            return pod_ref
        try:
            pod = pods.get(pod_ref)
        except Exception:
            return pod_ref
        pod_name = getattr(pod, "name", None)
        if isinstance(pod_name, str) and pod_name.strip():
            return pod_name.strip()
        pod_id = getattr(pod, "id", None)
        if isinstance(pod_id, str) and pod_id.strip():
            return pod_id.strip()
        return pod_ref

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
