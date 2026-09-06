from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PODMAN_MOUNT_DESTINATION_KEYS = ("destination", "target", "dest", "Destination", "Target", "Dest")
PODMAN_NAMED_VOLUME_DESTINATION_KEYS = (
    "Dest",
    "dest",
    "destination",
    "Destination",
    "Target",
    "target",
)


class PodmanPayloadRenderer:
    """Render and normalize Podman libpod container-create payloads."""

    def _render_podman_create_payload(self, create_config: dict[str, Any]) -> dict[str, Any]:
        payload = dict(create_config)
        command = payload.get("command")
        if isinstance(command, str):
            payload["command"] = [command]

        self._render_podman_environment(payload)
        self._render_podman_healthcheck(payload)
        self._render_podman_stdio(payload)
        self._render_podman_workdir(payload)
        self._render_podman_networking(payload)
        self._render_podman_logging(payload)
        self._render_podman_storage(payload)
        self._render_podman_resources(payload)
        self._render_podman_security(payload)
        self._render_podman_devices(payload)
        self._render_podman_pod(payload)

        return self._filter_empty_podman_payload_values(payload)

    def _render_podman_environment(self, payload: dict[str, Any]) -> None:
        environment = payload.pop("environment", None)
        if environment is None:
            return
        if isinstance(environment, Mapping):
            payload["env"] = dict(environment)
            return
        if not isinstance(environment, list):
            return
        env: dict[str, str] = {}
        for item in environment:
            if not isinstance(item, str) or "=" not in item:
                continue
            key, _, value = item.partition("=")
            if key.strip():
                env[key] = value
        if env:
            payload["env"] = env

    def _render_podman_healthcheck(self, payload: dict[str, Any]) -> None:
        healthcheck = payload.pop("healthcheck", None)
        if isinstance(healthcheck, Mapping) and healthcheck:
            payload["healthconfig"] = dict(healthcheck)

    def _render_podman_stdio(self, payload: dict[str, Any]) -> None:
        tty = payload.pop("tty", None)
        if isinstance(tty, bool):
            payload["terminal"] = tty
        stdin_open = payload.pop("stdin_open", None)
        if isinstance(stdin_open, bool):
            payload["stdin"] = stdin_open

    def _render_podman_workdir(self, payload: dict[str, Any]) -> None:
        working_dir = payload.pop("working_dir", None)
        if working_dir is not None:
            payload["work_dir"] = working_dir

    def _render_podman_networking(self, payload: dict[str, Any]) -> None:
        networks = payload.pop("networks", None)
        if networks is not None:
            payload["networks"] = networks
        network = payload.pop("network", None)
        if network is not None:
            payload["cni_networks"] = [network]

        exposed_ports = payload.pop("exposed_ports", None)
        expose_payload = self._podman_expose_payload_from_exposed_ports(exposed_ports)
        if expose_payload:
            payload["expose"] = expose_payload

        dns = payload.pop("dns", None)
        if isinstance(dns, list) and dns:
            payload["dns_server"] = list(dns)
        dns_opt = payload.pop("dns_opt", None)
        if isinstance(dns_opt, list) and dns_opt:
            payload["dns_option"] = list(dns_opt)
        dns_search = payload.get("dns_search")
        if isinstance(dns_search, list) and dns_search:
            payload["dns_search"] = list(dns_search)

        extra_hosts = payload.pop("extra_hosts", None)
        hostadd = self._pod_extra_hosts_from_create_config({"extra_hosts": extra_hosts})
        if hostadd:
            payload["hostadd"] = hostadd

        ports = payload.pop("ports", None)
        portmappings = self._pod_portmappings_from_create_config({"ports": ports})
        if portmappings:
            payload["portmappings"] = portmappings

        network_mode = payload.pop("network_mode", None)
        if isinstance(network_mode, str) and network_mode.strip():
            details = network_mode.split(":", 1)
            if len(details) == 2 and details[0] == "ns" and details[1]:
                payload["netns"] = {"nsmode": "path", "value": details[1]}
            else:
                payload["netns"] = {"nsmode": network_mode}

    def _render_podman_logging(self, payload: dict[str, Any]) -> None:
        log_config = payload.pop("log_config", None)
        if not isinstance(log_config, Mapping):
            return
        log_payload: dict[str, Any] = {}
        driver = log_config.get("Type") or log_config.get("driver")
        if isinstance(driver, str) and driver.strip():
            log_payload["driver"] = driver.strip()
        config = log_config.get("Config") or log_config.get("config")
        if isinstance(config, Mapping):
            log_options: dict[str, Any] = {}
            for source_key, target_key in (
                ("path", "path"),
                ("size", "size"),
                ("options", "options"),
                ("tag", "tag"),
            ):
                value = config.get(source_key)
                if value not in (None, ""):
                    log_payload[target_key] = dict(value) if isinstance(value, Mapping) else value
            for key, value in config.items():
                if key in {"path", "size", "options", "tag"} or value in (None, ""):
                    continue
                log_options[key] = dict(value) if isinstance(value, Mapping) else value
            if log_options:
                options = dict(log_payload.get("options") or {})
                options.update(log_options)
                log_payload["options"] = options
        tag = log_config.get("Tag") or log_config.get("tag")
        if isinstance(tag, str) and tag.strip():
            options = dict(log_payload.get("options") or {})
            options["tag"] = tag.strip()
            log_payload["options"] = options
        if log_payload:
            payload["log_configuration"] = log_payload

    def _render_podman_storage(self, payload: dict[str, Any]) -> None:
        mounts = payload.get("mounts")
        if isinstance(mounts, list):
            rendered_mounts: list[dict[str, Any]] = []
            for mount in mounts:
                if not isinstance(mount, Mapping):
                    continue
                rendered = self._render_podman_mount(mount)
                if rendered is not None:
                    rendered_mounts.append(rendered)
            if rendered_mounts:
                payload["mounts"] = rendered_mounts
            else:
                payload.pop("mounts", None)

    def _render_podman_mount(self, mount: Mapping[str, Any]) -> dict[str, Any] | None:
        destination = self._podman_storage_destination_value(mount, PODMAN_MOUNT_DESTINATION_KEYS)
        destination = self._normalize_podman_container_mount_destination(destination)
        if destination is None:
            return None
        rendered: dict[str, Any] = {
            "type": mount.get("type"),
            "destination": destination,
            "options": [],
        }
        source = mount.get("source")
        if isinstance(source, str) and source.strip():
            rendered["source"] = source
        options = rendered["options"]
        raw_options = mount.get("options")
        if isinstance(raw_options, list):
            for option in raw_options:
                if isinstance(option, str) and option.strip():
                    self._append_unique_podman_option(options, option.strip())
        elif isinstance(raw_options, str) and raw_options.strip():
            for option in raw_options.split(","):
                if option.strip():
                    self._append_unique_podman_option(options, option.strip())
        if mount.get("read_only") is True:
            self._append_unique_podman_option(options, "ro")
        for source_key, target_option in (("propagation", None), ("relabel", None)):
            value = mount.get(source_key)
            if isinstance(value, str) and value.strip():
                self._append_unique_podman_option(
                    options, value.strip() if target_option is None else target_option
                )
        size = mount.get("size")
        if isinstance(size, str | int) and str(size).strip():
            self._append_unique_podman_option(options, f"size={size}")
        if not options:
            rendered.pop("options", None)
        return rendered

    def _append_unique_podman_option(self, options: list[str], option: str) -> None:
        if option not in options:
            options.append(option)

    def _render_podman_resources(self, payload: dict[str, Any]) -> None:
        resource_limits: dict[str, Any] = {}
        pids_limit = payload.pop("pids_limit", None)
        if isinstance(pids_limit, int) and pids_limit > 0:
            resource_limits["pids"] = {"limit": pids_limit}

        cpu_fields = {
            "cpus": payload.pop("cpuset_cpus", None),
            "mems": payload.pop("cpuset_mems", None),
            "period": payload.pop("cpu_period", None),
            "quota": payload.pop("cpu_quota", None),
            "realtimePeriod": payload.pop("cpu_rt_period", None),
            "realtimeRuntime": payload.pop("cpu_rt_runtime", None),
            "shares": payload.pop("cpu_shares", None),
        }
        cpu_limits = {key: value for key, value in cpu_fields.items() if value not in (None, "")}
        if cpu_limits:
            resource_limits["cpu"] = cpu_limits

        memory_fields = {
            "disableOOMKiller": payload.pop("oom_kill_disable", None),
            "kernel": payload.pop("kernel_memory", None),
            "kernelTCP": payload.pop("kernel_memory_tcp", None),
            "limit": payload.pop("mem_limit", None),
            "reservation": payload.pop("mem_reservation", None),
            "swap": payload.pop("memswap_limit", None),
            "swappiness": payload.pop("mem_swappiness", None),
            "useHierarchy": payload.pop("mem_use_hierarchy", None),
        }
        memory_limits = {
            key: value for key, value in memory_fields.items() if value not in (None, "")
        }
        if memory_limits:
            resource_limits["memory"] = memory_limits

        if resource_limits:
            payload["resource_limits"] = resource_limits

        ulimits = payload.pop("ulimits", None)
        if isinstance(ulimits, list):
            r_limits: list[dict[str, Any]] = []
            for item in ulimits:
                if not isinstance(item, Mapping):
                    continue
                name = item.get("Name") or item.get("type")
                hard = item.get("Hard") if "Hard" in item else item.get("hard")
                soft = item.get("Soft") if "Soft" in item else item.get("soft")
                if isinstance(name, str) and name.strip():
                    r_limits.append({"type": name.strip(), "hard": hard, "soft": soft})
            if r_limits:
                payload["r_limits"] = r_limits

    def _render_podman_security(self, payload: dict[str, Any]) -> None:
        security_opt = payload.pop("security_opt", None)
        if isinstance(security_opt, list) and security_opt:
            payload["selinux_opts"] = list(security_opt)

    def _render_podman_devices(self, payload: dict[str, Any]) -> None:
        devices = payload.get("devices")
        if not isinstance(devices, list):
            return
        rendered_devices: list[dict[str, Any]] = []
        for device in devices:
            if isinstance(device, Mapping):
                rendered_devices.append(dict(device))
            elif isinstance(device, str) and device.strip():
                rendered_devices.append({"path": device})
        if rendered_devices:
            payload["devices"] = rendered_devices
        else:
            payload.pop("devices", None)

    def _render_podman_pod(self, payload: dict[str, Any]) -> None:
        pod = payload.get("pod")
        if pod is None or isinstance(pod, str):
            return
        pod_id = getattr(pod, "id", None)
        pod_name = getattr(pod, "name", None)
        if isinstance(pod_id, str) and pod_id.strip():
            payload["pod"] = pod_id.strip()
        elif isinstance(pod_name, str) and pod_name.strip():
            payload["pod"] = pod_name.strip()

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
