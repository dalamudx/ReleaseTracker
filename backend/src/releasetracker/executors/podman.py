from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import signal
import urllib.parse
from collections import defaultdict
from collections.abc import Iterator, Mapping
from typing import Any

from ..services.runtime_policy import runtime_operation_policy
from .base import (
    RuntimeMutationError,
    RuntimeUpdateResult,
    offload_blocking_runtime_adapter_methods,
)
from .compose_runtime_update import GroupedRuntimeRecreateSpec, build_grouped_runtime_recreate_spec
from . import podman_compose
from .container_runtime import _ContainerRuntimeAdapter
from .podman_payload_renderer import PodmanPayloadRenderer
from .podman_pod_recovery import (
    PODMAN_NAMESPACE_PAYLOAD_KEYS,
    PODMAN_SNAPSHOT_POD_REFERENCE_KEYS,
    PodmanPodRecovery,
)

logger = logging.getLogger(__name__)

PODMAN_LIBPOD_CONTAINER_CREATE_ENDPOINT = "containers/create"


@offload_blocking_runtime_adapter_methods
class PodmanRuntimeAdapter(PodmanPodRecovery, PodmanPayloadRenderer, _ContainerRuntimeAdapter):
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
        return await podman_compose.update_compose_services(self, target_ref, service_target_images)

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
            timeout=runtime_operation_policy(self.runtime_connection).write_timeout_seconds,
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

    async def get_current_image(self, target_ref: dict[str, Any]) -> str:
        if target_ref.get("mode") == "docker_compose":
            service_images = await self.fetch_compose_service_images(target_ref)
            if not service_images:
                raise ValueError("Unable to resolve Podman Compose service images")
            return self._compose_snapshot_image_summary(service_images)
        return await super().get_current_image(target_ref)

    async def capture_snapshot(
        self, target_ref: dict[str, Any], current_image: str
    ) -> dict[str, Any]:
        if target_ref.get("mode") == "docker_compose":
            return await self._capture_compose_snapshot(target_ref, current_image)

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
        if target_ref.get("mode") == "docker_compose":
            self._validate_compose_snapshot(target_ref, snapshot)
            return

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

    async def _capture_compose_snapshot(
        self,
        target_ref: dict[str, Any],
        current_image: str,
    ) -> dict[str, Any]:
        return await podman_compose._capture_compose_snapshot(self, target_ref, current_image)

    def _validate_compose_snapshot(
        self,
        target_ref: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        return podman_compose._validate_compose_snapshot(self, target_ref, snapshot)

    def _validate_compose_container_snapshot(self, snapshot: Any) -> None:
        return podman_compose._validate_compose_container_snapshot(self, snapshot)

    async def _recover_compose_from_snapshot(
        self,
        target_ref: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> RuntimeUpdateResult:
        return await podman_compose._recover_compose_from_snapshot(self, target_ref, snapshot)

    @staticmethod
    def _compose_snapshot_image_summary(service_images: dict[str, str]) -> str:
        return podman_compose._compose_snapshot_image_summary(service_images)

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

    def _grouped_spec_pod_key(self, spec: GroupedRuntimeRecreateSpec) -> str | None:
        identifier = spec.pod_id or spec.pod_name
        if not isinstance(identifier, str) or not identifier.strip():
            return None
        return identifier.strip()

    def _group_pod_backed_specs_by_pod_key(
        self,
        specs: list[GroupedRuntimeRecreateSpec],
    ) -> dict[str, list[GroupedRuntimeRecreateSpec]]:
        grouped: dict[str, list[GroupedRuntimeRecreateSpec]] = defaultdict(list)
        for spec in specs:
            pod_key = self._grouped_spec_pod_key(spec)
            if pod_key is not None:
                grouped[pod_key].append(spec)
        return dict(grouped)

    def _create_podman_grouped_container(self, client, create_config: dict[str, Any]):
        return self._create_podman_container(client, create_config)

    def _create_podman_container(self, client, create_config: dict[str, Any]):
        create_kwargs = self._podman_container_create_config(create_config)
        source_named_volume_names = self._podman_source_named_volume_names(create_kwargs)
        payload = self._render_podman_create_payload(create_kwargs)
        self._sanitize_podman_rendered_create_payload(
            payload,
            source_named_volume_names=source_named_volume_names,
        )
        self._sanitize_podman_final_mount_payload(payload)

        api_client = getattr(client, "api", None)
        if api_client is None or not hasattr(api_client, "post"):
            return client.containers.create(**create_kwargs)

        compatibility = self._podman_low_level_create_compatibility(api_client)
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
            self._prepare_create_config_for_pod_membership(
                create_config,
                pod_relation_payload=spec.pod_relation_payload,
            )
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
        pod_ref_override: Any | None = None,
    ) -> dict[str, Any]:
        create_config = dict(snapshot.get("create_config") or {})
        host_config = (
            snapshot.get("host_config") if isinstance(snapshot.get("host_config"), dict) else {}
        )
        create_config = self._apply_podman_host_config_preservation(create_config, host_config)
        pod_name = snapshot.get("pod_name") if isinstance(snapshot.get("pod_name"), str) else None
        pod_id = snapshot.get("pod_id") if isinstance(snapshot.get("pod_id"), str) else None
        pod_relation_payload = snapshot.get("pod_relation_payload")
        if not isinstance(pod_relation_payload, Mapping):
            pod_relation_payload = None
        if pod_name or pod_id or pod_ref_override is not None:
            self._prepare_create_config_for_pod_membership(
                create_config,
                pod_relation_payload=pod_relation_payload,
            )
        stable_pod_names = {
            pod_name_candidate
            for pod_name_candidate in (pod_name, self._snapshot_pod_name([snapshot]))
            if isinstance(pod_name_candidate, str) and pod_name_candidate.strip()
        }
        stale_pod_ids = set(self._snapshot_pod_ids([snapshot])) - stable_pod_names
        if pod_ref_override is not None:
            return self._apply_snapshot_pod_ref_override(
                create_config,
                pod_ref_override,
                stale_pod_ids=stale_pod_ids,
            )
        create_config = self._apply_pod_membership_to_create_config(
            create_config,
            pod_name=pod_name,
            pod_id=pod_id,
            client=client,
        )
        if create_config.get("pod") is not None:
            return self._apply_snapshot_pod_ref_override(
                create_config,
                create_config["pod"],
                stale_pod_ids=stale_pod_ids,
            )
        return create_config

    def _podman_container_create_config(
        self,
        create_config: dict[str, Any],
    ) -> dict[str, Any]:
        sanitized = dict(create_config)
        self._preserve_podman_exposed_only_ports(sanitized)
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

    def _apply_snapshot_pod_ref_override(
        self,
        create_config: dict[str, Any],
        pod_ref_override: Any,
        *,
        stale_pod_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        self._sanitize_snapshot_pod_references(create_config, stale_pod_ids=stale_pod_ids)
        create_config["pod"] = pod_ref_override
        return create_config

    def _sanitize_snapshot_pod_references(
        self,
        payload: Any,
        *,
        stale_pod_ids: set[str] | None = None,
    ) -> None:
        stale_pod_ids = stale_pod_ids or set()
        if isinstance(payload, dict):
            for key in list(payload):
                value = payload.get(key)
                if key in PODMAN_SNAPSHOT_POD_REFERENCE_KEYS:
                    payload.pop(key, None)
                    continue
                if key in PODMAN_NAMESPACE_PAYLOAD_KEYS and self._podman_namespace_targets_pod(
                    value
                ):
                    payload.pop(key, None)
                    continue
                if isinstance(value, str) and value.strip() in stale_pod_ids:
                    payload.pop(key, None)
                    continue
                self._sanitize_snapshot_pod_references(value, stale_pod_ids=stale_pod_ids)
            return
        if isinstance(payload, list):
            payload[:] = [
                item
                for item in payload
                if not (isinstance(item, str) and item.strip() in stale_pod_ids)
            ]
            for item in payload:
                self._sanitize_snapshot_pod_references(item, stale_pod_ids=stale_pod_ids)

    def _podman_namespace_targets_pod(self, value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        namespace_mode = value.get("nsmode")
        namespace_value = value.get("value")
        if isinstance(namespace_mode, str) and namespace_mode.strip() == "pod":
            return True
        return isinstance(namespace_value, str) and namespace_value.strip().startswith("pod:")

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
                    "network_names=%s",
                    self._safe_identifier(create_config.get("name")),
                    self._classify_network_mode(create_config.get("network_mode")),
                    dropped_empty_network_count,
                    self._safe_network_names(normalized_networks),
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
                    "network_names=%s",
                    self._safe_identifier(create_config.get("name")),
                    self._classify_network_mode(create_config.get("network_mode")),
                    dropped_empty_network_count,
                    self._safe_network_names(normalized_network_map),
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

    def _preserve_podman_exposed_only_ports(self, create_config: dict[str, Any]) -> None:
        exposed_ports = create_config.pop("_releasetracker_exposed_ports", None)
        normalized_exposed_ports = self._normalize_podman_exposed_ports(exposed_ports)
        if not normalized_exposed_ports:
            return

        existing_exposed_ports = self._normalize_podman_exposed_ports(
            create_config.get("exposed_ports")
        )
        merged_exposed_ports = [*existing_exposed_ports]
        for port in normalized_exposed_ports:
            if port not in merged_exposed_ports:
                merged_exposed_ports.append(port)
        create_config["exposed_ports"] = merged_exposed_ports

    def _normalize_podman_exposed_ports(self, exposed_ports: Any) -> list[str]:
        if isinstance(exposed_ports, Mapping):
            candidates = exposed_ports.keys()
        elif isinstance(exposed_ports, list | tuple | set):
            candidates = exposed_ports
        elif isinstance(exposed_ports, str):
            candidates = [exposed_ports]
        else:
            return []

        normalized: list[str] = []
        for candidate in candidates:
            port, protocol = self._split_port_protocol(candidate)
            if port is None:
                continue
            port_spec = f"{port}/{protocol}"
            if port_spec not in normalized:
                normalized.append(port_spec)
        return normalized

    def _podman_expose_payload_from_exposed_ports(self, exposed_ports: Any) -> dict[int, str]:
        expose_payload: dict[int, str] = {}
        for port_spec in self._normalize_podman_exposed_ports(exposed_ports):
            port, protocol = self._split_port_protocol(port_spec)
            if port is not None:
                expose_payload[port] = protocol
        return expose_payload

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
                    if option_tokens:
                        mount_payload["options"] = list(option_tokens)
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

                volume_payload: dict[str, Any] = {"bind": container_path}
                if option_tokens:
                    volume_payload["extended_mode"] = list(option_tokens)
                else:
                    volume_payload["mode"] = "rw"
                normalized_volumes[host_path] = volume_payload
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

    def _prepare_create_config_for_pod_membership(
        self,
        create_config: dict[str, Any],
        *,
        pod_relation_payload: Mapping[str, Any] | None = None,
    ) -> None:
        if self._pod_relation_payload_uses_container_networking(pod_relation_payload):
            return
        create_config.pop("hostname", None)
        create_config.pop("network", None)
        create_config.pop("networks", None)
        create_config.pop("network_mode", None)
        create_config.pop("network_options", None)
        create_config.pop("ports", None)
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
        if self._is_pod_backed_grouped_spec(
            spec,
        ) and not self._pod_relation_payload_uses_container_networking(spec.pod_relation_payload):
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
        pod_relation_payload = snapshot.get("pod_relation_payload")
        if not isinstance(pod_relation_payload, Mapping):
            pod_relation_payload = None
        if self._snapshot_has_pod_membership(
            snapshot,
        ) and not self._pod_relation_payload_uses_container_networking(pod_relation_payload):
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
        return self._snapshot_pod_name([dict(snapshot)]) is not None or bool(
            self._snapshot_pod_ids([dict(snapshot)])
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
            network = networks.get(normalized_network_name)
            if hasattr(network, "disconnect"):
                try:
                    network.disconnect(new_container, force=True)
                except Exception:
                    pass
            self._connect_podman_network(
                client,
                network,
                new_container,
                normalized_network_name,
                container_id=container_id,
                endpoint=endpoint,
                phase=phase or "unknown",
            )

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
        credential_key = r"[A-Za-z0-9_.-]*(?:password|passwd|token|secret|api[_-]?key|access[_-]?key)[A-Za-z0-9_.-]*"
        for pattern in (
            rf"(?i)([\"']){credential_key}\1\s*:\s*([\"']).*?\2",
            rf"(?i)([\"']){credential_key}\1\s*:\s*[^\s,}}]+",
            rf"(?i)\b{credential_key}\s*[:=]\s*[^\s,;}}]+",
        ):
            message = re.sub(pattern, "credential=***REDACTED***", message)
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

    def _connect_podman_network(
        self,
        client,
        network,
        new_container,
        network_name: str,
        *,
        container_id: str | None,
        endpoint: dict[str, Any],
        phase: str,
    ) -> None:
        raw_api_post = self._raw_network_connect_post(client)
        if raw_api_post is not None:
            connect_payload = self._raw_network_connect_body(
                new_container,
                container_id=container_id,
                endpoint=endpoint,
            )
            raw_api_post(
                f"networks/{network_name}/connect",
                compatible=False,
                data=json.dumps(connect_payload),
                headers={"Content-Type": "application/json"},
            )
            return

        connect_kwargs = self._network_connect_kwargs(container_id, endpoint)
        network.connect(new_container, **connect_kwargs)

    def _raw_network_connect_post(self, client):
        api = getattr(client, "api", None)
        post = getattr(api, "post", None)
        if callable(post):
            return post
        return None

    def _raw_network_connect_body(
        self,
        new_container,
        *,
        container_id: str | None,
        endpoint: dict[str, Any],
    ) -> dict[str, Any]:
        body = {"container": self._container_connect_identifier(new_container)}
        aliases = self._network_aliases(container_id, endpoint)
        if aliases:
            body["aliases"] = aliases
        static_ips = self._network_static_ips(endpoint)
        if static_ips:
            body["static_ips"] = static_ips
        return body

    def _network_static_ips(self, endpoint: dict[str, Any]) -> list[str]:
        static_ips: list[str] = []
        ipv4_address = self._network_ipv4_address(endpoint)
        if ipv4_address:
            static_ips.append(ipv4_address)
        ipv6_address = self._network_ipv6_address(endpoint)
        if ipv6_address:
            static_ips.append(ipv6_address)
        return static_ips

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
            if not isinstance(alias, str):
                continue
            normalized_alias = alias.strip()
            if not normalized_alias or normalized_alias in container_id_aliases:
                continue
            if normalized_alias not in normalized_aliases:
                normalized_aliases.append(normalized_alias)
        return normalized_aliases

    def _container_connect_identifier(self, container) -> str:
        container_id = getattr(container, "id", None)
        if isinstance(container_id, str) and container_id.strip():
            return container_id.strip()
        container_name = getattr(container, "name", None)
        if isinstance(container_name, str) and container_name.strip():
            return container_name.strip()
        raise ValueError("replacement container missing id/name for network restore")

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
        client = self._get_client()
        specs_by_service = self._build_all_grouped_runtime_recreate_specs_by_service(
            service_containers,
            update_plan,
            client=client,
        )
        selected_services = self._expand_grouped_recreate_services_for_dependents(
            specs_by_service,
            set(update_plan),
        )
        selected_services = self._expand_grouped_recreate_services_for_pod_siblings(
            specs_by_service,
            set(selected_services),
        )
        selected_specs = [
            spec for service in selected_services for spec in specs_by_service.get(service, [])
        ]
        return self._order_grouped_runtime_recreate_specs(selected_specs)

    def _build_all_grouped_runtime_recreate_specs_by_service(
        self,
        service_containers: dict[str, list[Any]],
        update_plan: dict[str, str],
        *,
        client,
    ) -> dict[str, list[GroupedRuntimeRecreateSpec]]:
        specs_by_service: dict[str, list[GroupedRuntimeRecreateSpec]] = {}
        for service, containers in sorted(service_containers.items()):
            service_specs: list[GroupedRuntimeRecreateSpec] = []
            target_image = update_plan.get(service)
            for container in containers:
                current_image = self._extract_image(container)
                if target_image is None:
                    target_image = current_image
                if not isinstance(target_image, str) or not target_image.strip():
                    continue
                attrs = getattr(container, "attrs", {}) or {}
                labels = self._get_compose_labels_with_fallback(container, client)
                compose_project = None
                if labels:
                    compose_project = labels.get("com.docker.compose.project") or labels.get(
                        "io.podman.compose.project"
                    )
                compose_label_overrides = self._extract_compose_label_overrides(labels)
                pod_relation_payload = self._extract_pod_relation_payload(attrs)
                pod_relation_payload = self._merge_pod_inspect_relation_payload(
                    pod_relation_payload,
                    client,
                )
                pod_name_from_labels = self._pod_name_from_compose_labels(labels)
                if pod_name_from_labels is not None:
                    pod_relation_payload.setdefault("pod_name", pod_name_from_labels)
                service_specs.append(
                    build_grouped_runtime_recreate_spec(
                        container,
                        runtime_type=self.runtime_connection.type,
                        target_image=target_image,
                        current_image=current_image,
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
            if service_specs:
                specs_by_service[service] = service_specs
        return specs_by_service

    def _expand_grouped_recreate_services_for_dependents(
        self,
        specs_by_service: dict[str, list[GroupedRuntimeRecreateSpec]],
        update_services: set[str],
    ) -> list[str]:
        selected = set(update_services)
        changed = True
        while changed:
            changed = False
            for service, specs in specs_by_service.items():
                if service in selected:
                    continue
                if any(
                    self._grouped_spec_depends_on_selected_service(spec, selected, specs_by_service)
                    for spec in specs
                ):
                    selected.add(service)
                    changed = True
        return sorted(selected)

    def _expand_grouped_recreate_services_for_pod_siblings(
        self,
        specs_by_service: dict[str, list[GroupedRuntimeRecreateSpec]],
        selected_services: set[str],
    ) -> list[str]:
        selected_pod_keys = {
            pod_key
            for service in selected_services
            for spec in specs_by_service.get(service, [])
            if (pod_key := self._grouped_spec_pod_key(spec)) is not None
        }
        if not selected_pod_keys:
            return sorted(selected_services)

        expanded = set(selected_services)
        for service, specs in specs_by_service.items():
            if service in expanded:
                continue
            if any(self._grouped_spec_pod_key(spec) in selected_pod_keys for spec in specs):
                expanded.add(service)
        return sorted(expanded)

    def _grouped_spec_depends_on_selected_service(
        self,
        spec: GroupedRuntimeRecreateSpec,
        selected_services: set[str],
        specs_by_service: dict[str, list[GroupedRuntimeRecreateSpec]],
    ) -> bool:
        for dependency in spec.dependencies:
            if dependency in selected_services:
                return True
            for selected_service in selected_services:
                for selected_spec in specs_by_service.get(selected_service, []):
                    if dependency in self._grouped_spec_identity_refs(selected_spec):
                        return True
        return False

    def _grouped_spec_identity_refs(self, spec: GroupedRuntimeRecreateSpec) -> set[str]:
        refs = {spec.compose_service, spec.container_name, spec.container_id}
        normalized_refs = {ref for ref in refs if isinstance(ref, str) and ref.strip()}
        if spec.container_name:
            normalized_refs.add(spec.container_name.removeprefix("/"))
        return normalized_refs

    def _order_grouped_runtime_recreate_specs(
        self,
        specs: list[GroupedRuntimeRecreateSpec],
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
                name_to_key[spec.container_name.removeprefix("/")] = key
            if spec.compose_service:
                service_to_keys[spec.compose_service].append(key)

        for key, spec in key_to_spec.items():
            resolved_dependencies: set[str] = set()
            for dependency in spec.dependencies:
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

    def _validate_podman_grouped_recreate_specs(
        self,
        specs: list[GroupedRuntimeRecreateSpec],
        *,
        target_pod_id: str | None,
    ) -> None:
        if not specs:
            raise ValueError("grouped recreate plan is empty")
        for spec in specs:
            if not spec.create_config:
                raise ValueError(
                    f"Podman cannot recreate compose container '{spec.container_name or spec.container_id}' without a restorable create configuration"
                )
            if not isinstance(spec.target_image, str) or not spec.target_image.strip():
                raise ValueError("compose target images must be non-empty strings")
            if target_pod_id is None:
                continue
            spec_pod_id = spec.pod_id or spec.pod_name
            if not isinstance(spec_pod_id, str) or not spec_pod_id.strip():
                raise ValueError(
                    "Podman compose grouped pod-aware update requires pod-backed services. "
                    f"Service '{spec.compose_service}' includes non-pod container '{spec.container_id or ''}'."
                )

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

    def _pod_name_from_compose_labels(self, labels: Mapping[str, Any]) -> str | None:
        if not isinstance(labels, Mapping):
            return None
        for key in ("io.podman.compose.pod", "io.podman.compose.pod_name"):
            value = labels.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _merge_pod_inspect_relation_payload(
        self,
        pod_relation_payload: Mapping[str, Any],
        client,
    ) -> dict[str, Any]:
        payload = dict(pod_relation_payload)
        pod_ref = payload.get("pod_id") or payload.get("pod_name")
        if not isinstance(pod_ref, str) or not pod_ref.strip():
            return payload

        pods = getattr(client, "pods", None)
        if pods is None or not hasattr(pods, "get"):
            return payload
        try:
            pod = pods.get(pod_ref.strip())
        except Exception:
            return payload

        pod_metadata = self._extract_pod_object_identity_payload(pod)
        pod_attrs = getattr(pod, "attrs", None)
        if isinstance(pod_attrs, Mapping):
            pod_metadata.update(self._extract_pod_relation_payload(pod_attrs))
        for key, value in pod_metadata.items():
            if key in {"pod_id", "pod_name"}:
                payload.setdefault(key, value)
            else:
                payload[key] = value
        return payload

    def _extract_pod_object_identity_payload(self, pod: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        pod_attrs = getattr(pod, "attrs", None)
        if isinstance(pod_attrs, Mapping):
            for source_key in ("Id", "ID", "id"):
                value = pod_attrs.get(source_key)
                if isinstance(value, str) and value.strip():
                    payload.setdefault("pod_id", value.strip())
                    break
            for source_key in ("Name", "name"):
                value = pod_attrs.get(source_key)
                if isinstance(value, str) and value.strip():
                    payload.setdefault("pod_name", value.strip())
                    break

        pod_id = getattr(pod, "id", None)
        if isinstance(pod_id, str) and pod_id.strip():
            payload.setdefault("pod_id", pod_id.strip())
        pod_name = getattr(pod, "name", None)
        if isinstance(pod_name, str) and pod_name.strip():
            payload.setdefault("pod_name", pod_name.strip())
        return payload

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
            ("InfraId", "pod_infra_id"),
            ("InfraName", "pod_infra_name"),
        ):
            value = attrs.get(source_key)
            if isinstance(value, str) and value.strip() and target_key not in payload:
                payload[target_key] = value.strip()

        create_infra = self._extract_pod_create_infra(attrs)
        if create_infra is not None:
            payload["pod_create_infra"] = create_infra

        shared_namespaces = self._extract_pod_shared_namespaces(attrs)
        if shared_namespaces is not None:
            payload["pod_shared_namespaces"] = list(shared_namespaces)

        return payload

    def _extract_pod_create_infra(self, attrs: Mapping[str, Any]) -> bool | None:
        for key in ("CreateInfra", "createInfra", "create_infra", "infra"):
            value = attrs.get(key)
            normalized = self._coerce_optional_bool(value)
            if normalized is not None:
                return normalized
        if self._has_non_empty_string(attrs.get("PodInfraId")) or self._has_non_empty_string(
            attrs.get("InfraId")
        ):
            return True
        return None

    def _coerce_optional_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return None

    def _extract_pod_shared_namespaces(self, attrs: Mapping[str, Any]) -> tuple[str, ...] | None:
        for key in (
            "SharedNamespaces",
            "shared_namespaces",
            "sharedNamespaces",
            "Share",
            "share",
        ):
            if key not in attrs:
                continue
            return self._normalize_pod_shared_namespaces(attrs.get(key))
        return None

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
