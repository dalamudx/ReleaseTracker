from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .base import RuntimeUpdateResult
from . import container_recovery
from .compose_runtime_update import GroupedRuntimeRecreateSpec

logger = logging.getLogger("releasetracker.executors.podman")

PODMAN_LIBPOD_POD_CREATE_ENDPOINT = "pods/create"
PODMAN_DEFAULTED_INFRA_PAYLOAD_KEY = "_releasetracker_defaulted_infra"
PODMAN_SNAPSHOT_POD_REFERENCE_KEYS = frozenset({"pod", "pod_id", "podId", "Pod", "PodID", "PodId"})
PODMAN_SNAPSHOT_POD_NAME_KEYS = frozenset({"pod_name", "podName", "PodName"})
PODMAN_NAMESPACE_PAYLOAD_KEYS = frozenset(
    {"netns", "utsns", "ipcns", "pidns", "cgroupns", "userns"}
)


@dataclass(frozen=True)
class PodmanPodTopology:
    create_infra: bool | None = None
    shared_namespaces: tuple[str, ...] | None = None


@dataclass(frozen=True)
class PodmanCreatedPodReference:
    name: str
    id: str


class PodmanPodRecovery:
    """Recover grouped Podman containers and rebuild their pod topology."""

    async def _recover_grouped_container_from_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        client=None,
        pod_ref_override: Any | None = None,
    ) -> RuntimeUpdateResult:
        create_config = snapshot.get("create_config")
        if not isinstance(create_config, dict) or not create_config:
            raise ValueError("snapshot.create_config must be a non-empty dict")
        if create_config.get("image") != snapshot.get("image"):
            raise ValueError("snapshot.create_config.image must match snapshot.image")

        client = client or self._get_client()
        recovered_image = snapshot.get("image") if isinstance(snapshot.get("image"), str) else None
        image_id = container_recovery.prepare_image(self, snapshot)

        restorable_create_config = self._podman_grouped_create_config_for_snapshot(
            snapshot,
            client=client,
            pod_ref_override=pod_ref_override,
        )
        restorable_create_config["image"] = image_id
        self._cleanup_grouped_replacement_conflict(
            client,
            snapshot,
            restorable_create_config,
        )

        restorable_create_config = self._podman_container_create_config(restorable_create_config)

        recovered_container = None
        try:
            recovered_container = self._create_podman_grouped_container(
                client,
                restorable_create_config,
            )
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

        await container_recovery.verify_container(self, recovered_container, snapshot)
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=recovered_image,
            message="runtime recovered from snapshot; native readiness verified",
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
        except Exception as exc:
            if self.is_target_missing_error(exc):
                return None
            raise

        # Recreate even when the tag matches: tags do not identify artifacts.

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
        return self._recreate_pod_for_grouped_specs(
            client,
            [spec],
            pod_name_hint=pod_name_hint,
        )

    def _recreate_pod_for_grouped_specs(
        self,
        client,
        specs: list[GroupedRuntimeRecreateSpec],
        *,
        pod_name_hint: str | None = None,
    ) -> tuple[Any | None, str | None]:
        if not specs:
            return None, None
        spec = specs[0]
        pod_ref = spec.pod_name or spec.pod_id
        if not isinstance(pod_ref, str) or not pod_ref.strip():
            return None, None
        pod_name = self._pod_name_from_grouped_specs(specs)
        if pod_name is None:
            pod_name = (
                pod_name_hint if isinstance(pod_name_hint, str) and pod_name_hint.strip() else None
            )
        if pod_name is None:
            pod_name = self._resolve_existing_pod_name(client, pod_ref.strip()) or pod_ref.strip()
        try:
            client.pods.remove(pod_ref.strip(), force=True)
        except Exception:
            pass
        pod_payload = self._pod_create_payload_from_specs(specs)
        pod_payload["name"] = pod_name
        pod_name = pod_payload.pop("name")
        created_pod = self._create_podman_pod(client, pod_name, pod_payload)
        if created_pod is not None:
            return created_pod, "recreated_pod_object"
        resolved_pod = self._resolve_pod_object_after_create(client, pod_name)
        if resolved_pod is not None:
            return resolved_pod, "recreated_pod_object"
        return pod_name, "recreated_pod_name"

    def _pod_name_from_grouped_specs(self, specs: list[GroupedRuntimeRecreateSpec]) -> str | None:
        for spec in specs:
            if isinstance(spec.pod_name, str) and spec.pod_name.strip():
                return spec.pod_name.strip()
        return None

    def _create_podman_pod(self, client, pod_name: str, pod_payload: dict[str, Any]):
        if self._pod_payload_requires_low_level_create(pod_payload):
            return self._create_podman_pod_with_low_level_api(client, pod_name, pod_payload)
        high_level_payload = dict(pod_payload)
        high_level_payload.pop(PODMAN_DEFAULTED_INFRA_PAYLOAD_KEY, None)
        return client.pods.create(pod_name, **high_level_payload)

    def _pod_payload_requires_low_level_create(self, pod_payload: Mapping[str, Any]) -> bool:
        explicit_no_infra = pod_payload.get("no_infra") is True
        explicit_infra_false = (
            pod_payload.get("infra") is False
            and pod_payload.get(PODMAN_DEFAULTED_INFRA_PAYLOAD_KEY) is not True
        )
        return explicit_no_infra or explicit_infra_false

    def _create_podman_pod_with_low_level_api(
        self,
        client,
        pod_name: str,
        pod_payload: Mapping[str, Any],
    ):
        api_client = getattr(client, "api", None)
        if api_client is None or not hasattr(api_client, "post"):
            raise RuntimeError("podman-py API client cannot create pod through native libpod API")
        payload = self._render_libpod_pod_create_payload({"name": pod_name, **dict(pod_payload)})
        compatibility = self._podman_low_level_pod_create_compatibility(api_client)
        response = self._post_podman_libpod_pod_create(
            api_client, payload, compatibility=compatibility
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        response_payload = response.json() if hasattr(response, "json") else {}
        pod_id = response_payload.get("Id") if isinstance(response_payload, Mapping) else None
        if isinstance(pod_id, str) and pod_id.strip():
            return PodmanCreatedPodReference(name=pod_name, id=pod_id.strip())
        return PodmanCreatedPodReference(name=pod_name, id=pod_name)

    def _podman_low_level_pod_create_compatibility(self, api_client) -> dict[str, Any]:
        compatible_supported = self._api_post_supports_compatible(api_client)
        return {
            "endpoint": PODMAN_LIBPOD_POD_CREATE_ENDPOINT,
            "compatible_requested": False,
            "compatible_accepted": compatible_supported,
            "compatible_supported": compatible_supported,
        }

    def _render_libpod_pod_create_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        rendered = dict(payload)
        if rendered.get("infra") is False:
            rendered["no_infra"] = True
        if rendered.get("no_infra") is True:
            rendered.pop("infra", None)
            rendered.pop("share", None)
            for key in (
                "networks",
                "portmappings",
                "dns",
                "dns_search",
                "dns_option",
                "hostadd",
                "hostname",
                "shm_size",
                PODMAN_DEFAULTED_INFRA_PAYLOAD_KEY,
            ):
                rendered.pop(key, None)
        return rendered

    def _post_podman_libpod_pod_create(
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
                "podman-py API client cannot target libpod pod create without compatible=False"
            )
        return api_client.request(
            "POST",
            manual_url,
            headers=headers,
            data=data,
        )

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
        if pod is None:
            return None
        identity = self._extract_pod_object_identity_payload(pod)
        pod_name = identity.get("pod_name")
        if isinstance(pod_name, str) and pod_name.strip():
            return pod_name.strip()
        return None

    def _pod_create_payload_from_spec(self, spec: GroupedRuntimeRecreateSpec) -> dict[str, Any]:
        return self._pod_create_payload_from_specs([spec])

    def _recover_snapshot_pods(
        self,
        client,
        snapshots: list[Any],
    ) -> dict[str, Any]:
        grouped = self._group_pod_backed_snapshots_by_pod_key(snapshots)
        current_pod_refs: dict[str, Any] = {}
        for pod_key, pod_snapshots in grouped.items():
            if pod_key in current_pod_refs:
                continue
            pod_ref = self._ensure_snapshot_pod(client, pod_snapshots)
            if pod_ref is not None:
                for alias in self._snapshot_pod_aliases(pod_snapshots):
                    current_pod_refs.setdefault(alias, pod_ref)
        return current_pod_refs

    def _resolve_current_pod_ref_from_snapshot_containers(
        self,
        client,
        snapshots: list[dict[str, Any]],
    ) -> Any | None:
        for snapshot in snapshots:
            container = self._resolve_current_container_for_snapshot(client, snapshot)
            if container is None:
                continue
            pod_ref = self._current_pod_ref_from_container(client, container)
            if pod_ref is not None:
                return pod_ref
        return None

    def _resolve_current_container_for_snapshot(self, client, snapshot: Mapping[str, Any]):
        for container_ref in self._snapshot_container_ref_candidates(snapshot):
            try:
                return client.containers.get(container_ref)
            except Exception:
                continue
        return self._resolve_current_container_for_snapshot_labels(client, snapshot)

    def _resolve_current_container_for_snapshot_labels(self, client, snapshot: Mapping[str, Any]):
        compose_project = self._snapshot_compose_project(snapshot)
        compose_service = self._snapshot_compose_service(snapshot)
        if compose_project is None or compose_service is None:
            return None
        try:
            containers = client.containers.list(all=True)
        except Exception:
            return None
        for container in containers:
            full_container = self._get_full_container_with_fallback(container, client)
            labels = self._get_compose_labels_with_fallback(full_container, client)
            label_project = labels.get("com.docker.compose.project") or labels.get(
                "io.podman.compose.project"
            )
            label_service = labels.get("com.docker.compose.service")
            if label_project == compose_project and label_service == compose_service:
                return full_container
        return None

    def _snapshot_compose_project(self, snapshot: Mapping[str, Any]) -> str | None:
        value = snapshot.get("compose_project")
        if isinstance(value, str) and value.strip():
            return value.strip()
        labels = self._snapshot_create_config_labels(snapshot)
        for key in ("com.docker.compose.project", "io.podman.compose.project"):
            label_value = labels.get(key)
            if isinstance(label_value, str) and label_value.strip():
                return label_value.strip()
        return None

    def _snapshot_compose_service(self, snapshot: Mapping[str, Any]) -> str | None:
        value = snapshot.get("compose_service")
        if isinstance(value, str) and value.strip():
            return value.strip()
        labels = self._snapshot_create_config_labels(snapshot)
        label_value = labels.get("com.docker.compose.service")
        if isinstance(label_value, str) and label_value.strip():
            return label_value.strip()
        return None

    def _snapshot_create_config_labels(self, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        create_config = snapshot.get("create_config")
        if not isinstance(create_config, Mapping):
            return {}
        labels = create_config.get("labels") or create_config.get("Labels")
        if isinstance(labels, Mapping):
            return labels
        return {}

    def _snapshot_container_ref_candidates(self, snapshot: Mapping[str, Any]) -> list[str]:
        candidates: list[str] = []
        for value in (
            snapshot.get("container_name"),
            self._snapshot_create_config_container_name(snapshot),
        ):
            if isinstance(value, str) and value.strip() and value.strip() not in candidates:
                candidates.append(value.strip())
        return candidates

    def _snapshot_create_config_container_name(self, snapshot: Mapping[str, Any]) -> str | None:
        create_config = snapshot.get("create_config")
        if not isinstance(create_config, Mapping):
            return None
        name = create_config.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def _current_pod_ref_from_container(self, client, container) -> Any | None:
        attrs = getattr(container, "attrs", {}) or {}
        if not isinstance(attrs, Mapping):
            return None

        pod_relation_payload = self._extract_pod_relation_payload(attrs)
        pod_name = pod_relation_payload.get("pod_name")
        if isinstance(pod_name, str) and pod_name.strip():
            pod = self._resolve_pod_object_after_create(client, pod_name.strip())
            if pod is not None:
                return pod
            return pod_name.strip()

        pod_id = pod_relation_payload.get("pod_id")
        if not isinstance(pod_id, str) or not pod_id.strip():
            return None
        pod = self._resolve_pod_object_after_create(client, pod_id.strip())
        if pod is not None:
            return pod
        return pod_id.strip()

    def _group_pod_backed_snapshots_by_pod_key(
        self,
        snapshots: list[Any],
    ) -> dict[str, list[dict[str, Any]]]:
        snapshot_items = [snapshot for snapshot in snapshots if isinstance(snapshot, dict)]
        pod_names_by_id: dict[str, str] = {}
        for snapshot in snapshot_items:
            pod_name = self._snapshot_pod_name([snapshot])
            if pod_name is None:
                continue
            for pod_id in self._snapshot_pod_ids([snapshot]):
                pod_names_by_id.setdefault(pod_id, pod_name)

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for snapshot in snapshot_items:
            pod_key = self._snapshot_pod_key(snapshot)
            for pod_id in self._snapshot_pod_ids([snapshot]):
                if pod_id in pod_names_by_id:
                    pod_key = pod_names_by_id[pod_id]
                    break
            if pod_key is not None:
                grouped[pod_key].append(snapshot)
        return dict(grouped)

    def _snapshot_pod_key(self, snapshot: Mapping[str, Any]) -> str | None:
        pod_name = self._snapshot_pod_name([dict(snapshot)])
        if pod_name is not None:
            return pod_name
        pod_ids = self._snapshot_pod_ids([dict(snapshot)])
        if pod_ids:
            return pod_ids[0]
        return None

    def _snapshot_pod_name(self, snapshots: list[dict[str, Any]]) -> str | None:
        for snapshot in snapshots:
            for candidate in self._iter_snapshot_pod_name_candidates(snapshot):
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return None

    def _snapshot_pod_ids(self, snapshots: list[dict[str, Any]]) -> list[str]:
        pod_ids: list[str] = []
        for snapshot in snapshots:
            for candidate in self._iter_snapshot_pod_id_candidates(snapshot):
                if (
                    isinstance(candidate, str)
                    and candidate.strip()
                    and candidate.strip() not in pod_ids
                ):
                    pod_ids.append(candidate.strip())
        return pod_ids

    def _iter_snapshot_pod_name_candidates(self, snapshot: Mapping[str, Any]) -> Iterator[Any]:
        yield snapshot.get("pod_name")
        pod_relation_payload = snapshot.get("pod_relation_payload")
        if isinstance(pod_relation_payload, Mapping):
            yield pod_relation_payload.get("pod_name")
        for payload_key in ("create_config", "CreateConfig", "podman_create", "config"):
            payload = snapshot.get(payload_key)
            if isinstance(payload, Mapping):
                yield from self._iter_pod_name_values_in_payload(payload)

    def _iter_snapshot_pod_id_candidates(self, snapshot: Mapping[str, Any]) -> Iterator[Any]:
        yield snapshot.get("pod_id")
        pod_relation_payload = snapshot.get("pod_relation_payload")
        if isinstance(pod_relation_payload, Mapping):
            yield pod_relation_payload.get("pod_id")
        for payload_key in (
            "create_config",
            "CreateConfig",
            "podman_create",
            "config",
            "host_config",
            "HostConfig",
        ):
            payload = snapshot.get(payload_key)
            if isinstance(payload, Mapping):
                yield from self._iter_pod_id_values_in_payload(payload)

    def _iter_pod_name_values_in_payload(self, payload: Mapping[str, Any]) -> Iterator[Any]:
        for key, value in payload.items():
            if key in PODMAN_SNAPSHOT_POD_NAME_KEYS:
                yield value
            if isinstance(value, Mapping):
                yield from self._iter_pod_name_values_in_payload(value)
            elif isinstance(value, list | tuple):
                for item in value:
                    if isinstance(item, Mapping):
                        yield from self._iter_pod_name_values_in_payload(item)

    def _iter_pod_id_values_in_payload(self, payload: Mapping[str, Any]) -> Iterator[Any]:
        for key, value in payload.items():
            if key in PODMAN_SNAPSHOT_POD_REFERENCE_KEYS:
                yield value
            if key in PODMAN_NAMESPACE_PAYLOAD_KEYS and isinstance(value, Mapping):
                namespace_value = value.get("value")
                namespace_mode = value.get("nsmode")
                if isinstance(namespace_mode, str) and namespace_mode.strip() == "pod":
                    yield namespace_value
                elif isinstance(namespace_value, str) and namespace_value.strip().startswith(
                    "pod:"
                ):
                    yield namespace_value.strip().split(":", 1)[1]
            if isinstance(value, Mapping):
                yield from self._iter_pod_id_values_in_payload(value)
            elif isinstance(value, list | tuple):
                for item in value:
                    if isinstance(item, Mapping):
                        yield from self._iter_pod_id_values_in_payload(item)
                    elif key in PODMAN_SNAPSHOT_POD_REFERENCE_KEYS:
                        yield item

    def _snapshot_pod_aliases(self, snapshots: list[dict[str, Any]]) -> list[str]:
        aliases: list[str] = []
        pod_name = self._snapshot_pod_name(snapshots)
        if pod_name is not None:
            aliases.append(pod_name)
        for pod_id in self._snapshot_pod_ids(snapshots):
            if pod_id not in aliases:
                aliases.append(pod_id)
        return aliases

    def _ensure_snapshot_pod(self, client, snapshots: list[dict[str, Any]]) -> Any | None:
        current_pod_ref = self._resolve_current_pod_ref_from_snapshot_containers(client, snapshots)
        if current_pod_ref is not None:
            return current_pod_ref

        pod_name = self._snapshot_pod_name(snapshots)
        if pod_name is not None:
            current_pod = self._resolve_pod_object_after_create(client, pod_name)
            if current_pod is not None:
                return current_pod
            return self._recreate_pod_for_snapshots(client, snapshots, pod_name=pod_name)

        if self._snapshot_pod_ids(snapshots):
            raise ValueError(
                "podman compose snapshot contains pod membership but current containers could not "
                "be resolved by stable container names and no stable pod name is available; "
                "refusing to recover using stale pod IDs"
            )
        return None

    def _recreate_pod_for_snapshots(
        self,
        client,
        snapshots: list[dict[str, Any]],
        *,
        pod_name: str,
    ) -> Any | None:
        for pod_ref in [pod_name, *self._snapshot_pod_ids(snapshots)]:
            try:
                client.pods.remove(pod_ref, force=True)
            except Exception:
                pass
        pod_payload = self._pod_create_payload_from_snapshots(snapshots)
        created_pod = self._create_podman_pod(client, pod_name, pod_payload)
        if created_pod is not None:
            return created_pod
        resolved_pod = self._resolve_pod_object_after_create(client, pod_name)
        if resolved_pod is not None:
            return resolved_pod
        return pod_name

    def _pod_create_payload_from_snapshots(self, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        if not snapshots:
            return {}
        topology = self._pod_topology_from_relation_payload(
            self._snapshot_pod_relation_payload(snapshots[0])
        )
        payload: dict[str, Any] = {}
        if topology.create_infra is not None:
            payload["infra"] = topology.create_infra
        else:
            payload["infra"] = False
            payload[PODMAN_DEFAULTED_INFRA_PAYLOAD_KEY] = True

        share_value = self._pod_share_payload_value(topology)
        if self._pod_topology_uses_container_networking(topology):
            payload["no_infra"] = True
            if topology.shared_namespaces == ():
                payload["shared_namespaces"] = []
            return payload

        for snapshot in snapshots:
            create_config = snapshot.get("create_config")
            if not isinstance(create_config, dict):
                continue
            hostname = create_config.get("hostname")
            if isinstance(hostname, str) and hostname.strip() and "hostname" not in payload:
                payload["hostname"] = hostname.strip()
            shm_size = create_config.get("shm_size")
            if isinstance(shm_size, int) and shm_size > 0 and "shm_size" not in payload:
                payload["shm_size"] = shm_size
            portmappings = self._pod_portmappings_from_create_config(create_config)
            if portmappings:
                payload.setdefault("portmappings", [])
                payload["portmappings"].extend(portmappings)
            extra_hosts = self._pod_extra_hosts_from_create_config(create_config)
            if extra_hosts:
                payload.setdefault("hostadd", [])
                payload["hostadd"].extend(extra_hosts)
            dns = create_config.get("dns")
            if isinstance(dns, list) and dns and "dns" not in payload:
                payload["dns"] = list(dns)
            dns_search = create_config.get("dns_search")
            if isinstance(dns_search, list) and dns_search and "dns_search" not in payload:
                payload["dns_search"] = list(dns_search)

        networks = self._pod_networks_from_snapshots(snapshots)
        if networks:
            if share_value is not None:
                payload["share"] = share_value
            payload["networks"] = networks
        return payload

    def _pod_networks_from_snapshots(
        self,
        snapshots: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        networks: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            network_config = snapshot.get("network_config")
            if not isinstance(network_config, dict):
                continue
            container_id = (
                snapshot.get("container_id")
                if isinstance(snapshot.get("container_id"), str)
                else None
            )
            for network_name, options in self._pod_networks_from_config(
                network_config,
                container_id,
            ).items():
                merged_options = networks.setdefault(network_name, {})
                for option_key, option_value in options.items():
                    if option_key == "aliases" and isinstance(option_value, list):
                        aliases = merged_options.setdefault("aliases", [])
                        for alias in option_value:
                            if alias not in aliases:
                                aliases.append(alias)
                        continue
                    if option_key == "static_ips" and isinstance(option_value, list):
                        static_ips = merged_options.setdefault("static_ips", [])
                        for static_ip in option_value:
                            if static_ip not in static_ips:
                                static_ips.append(static_ip)
                        continue
                    merged_options.setdefault(option_key, option_value)
        return networks

    def _snapshot_pod_relation_payload(
        self,
        snapshot: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        pod_relation_payload = snapshot.get("pod_relation_payload")
        if isinstance(pod_relation_payload, Mapping):
            return pod_relation_payload
        return None

    def _snapshot_pod_ref_override(
        self,
        snapshot: Mapping[str, Any],
        current_pod_refs: Mapping[str, Any],
    ) -> Any | None:
        pod_key = self._snapshot_pod_key(snapshot)
        if pod_key is None:
            return None
        return current_pod_refs.get(pod_key)

    def _pod_create_payload_from_specs(
        self,
        specs: list[GroupedRuntimeRecreateSpec],
    ) -> dict[str, Any]:
        if not specs:
            return {}
        spec = specs[0]
        pod_name = spec.pod_name or spec.pod_id
        topology = self._pod_topology_from_relation_payload(spec.pod_relation_payload)
        payload: dict[str, Any] = {"name": pod_name}
        if topology.create_infra is not None:
            payload["infra"] = topology.create_infra
        else:
            payload["infra"] = False
            payload[PODMAN_DEFAULTED_INFRA_PAYLOAD_KEY] = True

        share_value = self._pod_share_payload_value(topology)
        if self._pod_topology_uses_container_networking(topology):
            payload["no_infra"] = True
            if topology.shared_namespaces == ():
                payload["shared_namespaces"] = []
            return payload

        for candidate in specs:
            hostname = candidate.create_config.get("hostname")
            if isinstance(hostname, str) and hostname.strip() and "hostname" not in payload:
                payload["hostname"] = hostname.strip()
            shm_size = candidate.create_config.get("shm_size")
            if isinstance(shm_size, int) and shm_size > 0 and "shm_size" not in payload:
                payload["shm_size"] = shm_size
            portmappings = self._pod_portmappings_from_create_config(candidate.create_config)
            if portmappings:
                payload.setdefault("portmappings", [])
                payload["portmappings"].extend(portmappings)
            extra_hosts = self._pod_extra_hosts_from_create_config(candidate.create_config)
            if extra_hosts:
                payload.setdefault("hostadd", [])
                payload["hostadd"].extend(extra_hosts)
            dns = candidate.create_config.get("dns")
            if isinstance(dns, list) and dns and "dns" not in payload:
                payload["dns"] = list(dns)
            dns_search = candidate.create_config.get("dns_search")
            if isinstance(dns_search, list) and dns_search and "dns_search" not in payload:
                payload["dns_search"] = list(dns_search)

        networks = self._pod_networks_from_specs(specs)
        if networks:
            if share_value is not None:
                payload["share"] = share_value
            payload["networks"] = networks
        return payload

    def _pod_networks_from_specs(
        self,
        specs: list[GroupedRuntimeRecreateSpec],
    ) -> dict[str, dict[str, Any]]:
        networks: dict[str, dict[str, Any]] = {}
        for spec in specs:
            for network_name, options in self._pod_networks_from_config(
                spec.network_config,
                spec.container_id,
            ).items():
                merged_options = networks.setdefault(network_name, {})
                for option_key, option_value in options.items():
                    if option_key == "aliases" and isinstance(option_value, list):
                        aliases = merged_options.setdefault("aliases", [])
                        for alias in option_value:
                            if alias not in aliases:
                                aliases.append(alias)
                        continue
                    if option_key == "static_ips" and isinstance(option_value, list):
                        static_ips = merged_options.setdefault("static_ips", [])
                        for static_ip in option_value:
                            if static_ip not in static_ips:
                                static_ips.append(static_ip)
                        continue
                    merged_options.setdefault(option_key, option_value)
        return networks

    def _pod_topology_from_relation_payload(
        self,
        pod_relation_payload: Mapping[str, Any] | None,
    ) -> PodmanPodTopology:
        if not isinstance(pod_relation_payload, Mapping):
            return PodmanPodTopology()
        create_infra = self._coerce_optional_bool(
            pod_relation_payload.get("pod_create_infra"),
        )
        shared_namespaces_value = pod_relation_payload.get("pod_shared_namespaces")
        shared_namespaces = (
            self._normalize_pod_shared_namespaces(shared_namespaces_value)
            if shared_namespaces_value is not None
            else None
        )
        return PodmanPodTopology(
            create_infra=create_infra,
            shared_namespaces=shared_namespaces,
        )

    def _pod_share_payload_value(self, topology: PodmanPodTopology) -> str | None:
        if topology.shared_namespaces is None:
            return "net"
        return ",".join(topology.shared_namespaces)

    def _pod_topology_uses_container_networking(self, topology: PodmanPodTopology) -> bool:
        return topology.create_infra is False

    def _pod_relation_payload_uses_container_networking(
        self,
        pod_relation_payload: Mapping[str, Any] | None,
    ) -> bool:
        return self._pod_topology_uses_container_networking(
            self._pod_topology_from_relation_payload(pod_relation_payload),
        )

    def _normalize_pod_shared_namespaces(self, value: Any) -> tuple[str, ...]:
        candidates: list[Any]
        if isinstance(value, str):
            candidates = value.split(",")
        elif isinstance(value, list | tuple | set):
            candidates = list(value)
        else:
            return ()
        normalized: list[str] = []
        for item in candidates:
            if not isinstance(item, str):
                continue
            namespace = item.strip().lower()
            if namespace and namespace not in normalized:
                normalized.append(namespace)
        return tuple(normalized)

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
