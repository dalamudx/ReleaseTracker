from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DOCKER_CREATE_MOUNT_TYPES = {"bind", "volume", "tmpfs", "npipe"}


@dataclass(frozen=True)
class GroupedRuntimeRecreateSpec:
    runtime_type: str
    container_id: str | None
    container_name: str | None
    compose_project: str | None
    compose_service: str | None
    current_image: str | None
    target_image: str
    create_config: dict[str, Any]
    host_config: dict[str, Any]
    network_config: dict[str, Any]
    snapshot_payload: dict[str, Any]
    restore_payload: dict[str, Any]
    pod_id: str | None
    pod_name: str | None
    pod_relation_payload: dict[str, Any]
    dependencies: tuple[str, ...]


def build_grouped_runtime_recreate_spec(
    container,
    *,
    runtime_type: str,
    target_image: str,
    current_image: str | None,
    compose_project: str | None = None,
    compose_service: str | None = None,
    pod_id: str | None = None,
    pod_name: str | None = None,
    pod_relation_payload: dict[str, Any] | None = None,
    create_config_labels_override: dict[str, str] | None = None,
) -> GroupedRuntimeRecreateSpec:
    attrs = getattr(container, "attrs", {}) or {}
    config = attrs.get("Config") if isinstance(attrs, dict) else {}
    host_config = attrs.get("HostConfig") if isinstance(attrs, dict) else {}
    network_settings = attrs.get("NetworkSettings") if isinstance(attrs, dict) else {}
    config = config if isinstance(config, dict) else {}
    host_config = host_config if isinstance(host_config, dict) else {}
    network_settings = network_settings if isinstance(network_settings, dict) else {}

    create_config = _extract_create_kwargs(
        container,
        target_image,
        config,
        host_config,
        runtime_type=runtime_type,
    )
    snapshot_create_config = _extract_create_kwargs(
        container,
        current_image or target_image,
        config,
        host_config,
        runtime_type=runtime_type,
    )
    if isinstance(create_config_labels_override, dict) and create_config_labels_override:
        merged_create_labels = dict(create_config.get("labels") or {})
        merged_snapshot_labels = dict(snapshot_create_config.get("labels") or {})
        merged_create_labels.update(create_config_labels_override)
        merged_snapshot_labels.update(create_config_labels_override)
        create_config["labels"] = merged_create_labels
        snapshot_create_config["labels"] = merged_snapshot_labels
    network_config = _extract_network_config(host_config, network_settings)
    dependencies = _extract_dependencies(host_config, config)
    normalized_pod_relation_payload = (
        dict(pod_relation_payload) if isinstance(pod_relation_payload, dict) else {}
    )

    snapshot_payload = {
        "runtime_type": runtime_type,
        "container_id": getattr(container, "id", None),
        "container_name": getattr(container, "name", None),
        "image": current_image,
        "create_config": dict(snapshot_create_config),
        "host_config": dict(host_config),
        "network_config": dict(network_config),
        "pod_id": pod_id,
        "pod_name": pod_name,
        "pod_relation_payload": dict(normalized_pod_relation_payload),
        "dependencies": list(dependencies),
    }
    restore_payload = {
        "create_config": dict(create_config),
        "host_config": dict(host_config),
        "network_config": dict(network_config),
        "pod_id": pod_id,
        "pod_name": pod_name,
        "pod_relation_payload": dict(normalized_pod_relation_payload),
    }

    return GroupedRuntimeRecreateSpec(
        runtime_type=runtime_type,
        container_id=getattr(container, "id", None),
        container_name=getattr(container, "name", None),
        compose_project=compose_project,
        compose_service=compose_service,
        current_image=current_image,
        target_image=target_image,
        create_config=create_config,
        host_config=host_config,
        network_config=network_config,
        snapshot_payload=snapshot_payload,
        restore_payload=restore_payload,
        pod_id=pod_id,
        pod_name=pod_name,
        pod_relation_payload=normalized_pod_relation_payload,
        dependencies=dependencies,
    )


def _extract_create_kwargs(
    container,
    image: str,
    config: dict[str, Any],
    host_config: dict[str, Any],
    *,
    runtime_type: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"image": image}

    name = getattr(container, "name", None)
    if name:
        kwargs["name"] = name

    env = config.get("Env")
    if env:
        kwargs["environment"] = env

    entrypoint = config.get("Entrypoint")
    if entrypoint:
        kwargs["entrypoint"] = entrypoint

    cmd = config.get("Cmd")
    if cmd:
        kwargs["command"] = cmd

    user = config.get("User")
    if user:
        kwargs["user"] = user

    working_dir = config.get("WorkingDir")
    if working_dir:
        kwargs["working_dir"] = working_dir

    hostname = config.get("Hostname")
    if hostname:
        kwargs["hostname"] = hostname

    domainname = config.get("Domainname")
    if isinstance(domainname, str) and domainname.strip():
        kwargs["domainname"] = domainname

    exposed_ports = _extract_exposed_only_ports(config, host_config)
    if exposed_ports:
        kwargs["_releasetracker_exposed_ports"] = exposed_ports

    tty = config.get("Tty")
    if isinstance(tty, bool):
        kwargs["tty"] = tty

    open_stdin = config.get("OpenStdin")
    if isinstance(open_stdin, bool):
        kwargs["stdin_open"] = open_stdin

    stop_signal = config.get("StopSignal")
    if isinstance(stop_signal, str) and stop_signal.strip():
        kwargs["stop_signal"] = stop_signal

    if runtime_type == "podman":
        stop_timeout = config.get("StopTimeout")
        if isinstance(stop_timeout, int) and stop_timeout >= 0:
            kwargs["stop_timeout"] = stop_timeout

    healthcheck = config.get("Healthcheck")
    if healthcheck and isinstance(healthcheck, dict):
        kwargs["healthcheck"] = dict(healthcheck)

    normalized_ports: dict[str, Any] = {}
    port_bindings = host_config.get("PortBindings")
    if port_bindings and isinstance(port_bindings, dict):
        for proto_port, bindings in port_bindings.items():
            if not bindings or not isinstance(bindings, list):
                continue
            normalized_bindings: list[tuple[str, int]] = []
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                host_ip = binding.get("HostIp", "")
                host_port_str = binding.get("HostPort", "")
                if not host_port_str:
                    continue
                try:
                    host_port_int = int(host_port_str)
                except ValueError:
                    continue
                normalized_bindings.append((host_ip, host_port_int))
            if not normalized_bindings:
                continue
            normalized_ports[proto_port] = (
                normalized_bindings[0] if len(normalized_bindings) == 1 else normalized_bindings
            )
    if normalized_ports:
        kwargs["ports"] = normalized_ports

    bind_targets = _extract_bind_targets(host_config)
    binds = host_config.get("Binds")
    if binds and isinstance(binds, list):
        normalized_volumes: dict[str, Any] = {}
        for bind in binds:
            if not isinstance(bind, str):
                continue
            parts = bind.split(":")
            if len(parts) < 2:
                continue
            host_path = parts[0]
            container_path = parts[1]
            raw_mode = parts[2] if len(parts) >= 3 else "rw"
            mode = "ro" if "ro" in raw_mode.split(",") else "rw"
            normalized_volumes[host_path] = {"bind": container_path, "mode": mode}
        if normalized_volumes:
            kwargs["volumes"] = normalized_volumes

    mounts = _extract_docker_create_mounts(host_config, bind_targets=bind_targets)
    if mounts:
        kwargs["mounts"] = mounts

    restart_policy = host_config.get("RestartPolicy")
    if restart_policy and isinstance(restart_policy, dict) and restart_policy.get("Name"):
        kwargs["restart_policy"] = restart_policy

    log_config = host_config.get("LogConfig")
    if log_config and isinstance(log_config, dict):
        kwargs["log_config"] = dict(log_config)

    network_mode = host_config.get("NetworkMode")
    if network_mode:
        kwargs["network_mode"] = network_mode

    extra_hosts = host_config.get("ExtraHosts")
    if extra_hosts and isinstance(extra_hosts, list):
        kwargs["extra_hosts"] = list(extra_hosts)

    dns = host_config.get("Dns")
    if dns and isinstance(dns, list):
        kwargs["dns"] = list(dns)

    dns_search = host_config.get("DnsSearch")
    if dns_search and isinstance(dns_search, list):
        kwargs["dns_search"] = list(dns_search)

    tmpfs = host_config.get("Tmpfs")
    if tmpfs and isinstance(tmpfs, dict):
        kwargs["tmpfs"] = dict(tmpfs)

    ulimits = host_config.get("Ulimits")
    if ulimits and isinstance(ulimits, list):
        kwargs["ulimits"] = list(ulimits)

    security_opt = host_config.get("SecurityOpt")
    if security_opt and isinstance(security_opt, list):
        kwargs["security_opt"] = list(security_opt)

    cap_add = host_config.get("CapAdd")
    if cap_add and isinstance(cap_add, list):
        kwargs["cap_add"] = list(cap_add)

    cap_drop = host_config.get("CapDrop")
    if cap_drop and isinstance(cap_drop, list):
        kwargs["cap_drop"] = list(cap_drop)

    devices = host_config.get("Devices")
    if devices and isinstance(devices, list):
        kwargs["devices"] = list(devices)

    pids_limit = host_config.get("PidsLimit")
    if isinstance(pids_limit, int) and pids_limit > 0:
        kwargs["pids_limit"] = pids_limit

    memory = host_config.get("Memory")
    if isinstance(memory, int) and memory > 0:
        kwargs["mem_limit"] = memory

    memory_reservation = host_config.get("MemoryReservation")
    if isinstance(memory_reservation, int) and memory_reservation > 0:
        kwargs["mem_reservation"] = memory_reservation

    cpu_shares = host_config.get("CpuShares")
    if isinstance(cpu_shares, int) and cpu_shares > 0:
        kwargs["cpu_shares"] = cpu_shares

    nano_cpus = host_config.get("NanoCpus")
    if isinstance(nano_cpus, int) and nano_cpus > 0:
        kwargs["nano_cpus"] = nano_cpus

    cpu_period = host_config.get("CpuPeriod")
    if isinstance(cpu_period, int) and cpu_period > 0:
        kwargs["cpu_period"] = cpu_period

    cpu_quota = host_config.get("CpuQuota")
    if isinstance(cpu_quota, int) and cpu_quota > 0:
        kwargs["cpu_quota"] = cpu_quota

    sysctls = host_config.get("Sysctls") or host_config.get("Sysctl")
    if isinstance(sysctls, dict) and sysctls:
        kwargs["sysctls"] = dict(sysctls)

    shm_size = host_config.get("ShmSize")
    if isinstance(shm_size, int) and shm_size > 0:
        kwargs["shm_size"] = shm_size

    init = host_config.get("Init")
    if isinstance(init, bool):
        kwargs["init"] = init

    labels = config.get("Labels")
    if labels:
        kwargs["labels"] = labels

    return kwargs


def _extract_exposed_only_ports(config: dict[str, Any], host_config: dict[str, Any]) -> list[str]:
    exposed_ports = config.get("ExposedPorts")
    if not isinstance(exposed_ports, dict):
        return []

    port_bindings = host_config.get("PortBindings")
    bound_ports = set(port_bindings) if isinstance(port_bindings, dict) else set()
    return sorted(
        port
        for port in exposed_ports
        if isinstance(port, str) and port.strip() and port not in bound_ports
    )


def _extract_bind_targets(host_config: dict[str, Any]) -> set[str]:
    binds = host_config.get("Binds")
    if not isinstance(binds, list):
        return set()

    targets: set[str] = set()
    for bind in binds:
        if not isinstance(bind, str):
            continue
        parts = bind.split(":")
        if len(parts) < 2:
            continue
        container_path = parts[1]
        if isinstance(container_path, str) and container_path.strip():
            targets.add(container_path.strip())
    return targets


def _extract_docker_create_mounts(
    host_config: dict[str, Any], *, bind_targets: set[str]
) -> list[dict[str, Any]]:
    raw_mounts = host_config.get("Mounts")
    if not isinstance(raw_mounts, list):
        return []

    mounts: list[dict[str, Any]] = []
    for raw_mount in raw_mounts:
        if not isinstance(raw_mount, dict):
            continue
        target = raw_mount.get("Target") or raw_mount.get("Destination")
        if not isinstance(target, str) or not target.strip():
            continue
        target = target.strip()
        if target in bind_targets:
            continue
        mount_type = raw_mount.get("Type")
        if not isinstance(mount_type, str):
            continue
        mount_type = mount_type.strip().lower()
        if mount_type not in DOCKER_CREATE_MOUNT_TYPES:
            continue

        source = raw_mount.get("Source") or raw_mount.get("Name")
        if mount_type in {"bind", "volume", "npipe"} and (
            not isinstance(source, str) or not source.strip()
        ):
            continue

        mount: dict[str, Any] = {"Target": target, "Type": mount_type}
        if isinstance(source, str) and source.strip():
            mount["Source"] = source.strip()
        elif mount_type == "tmpfs":
            mount["Source"] = None

        read_only = _extract_docker_mount_read_only(raw_mount)
        if read_only is not None:
            mount["ReadOnly"] = read_only

        bind_options = _extract_docker_bind_options(raw_mount)
        if bind_options:
            mount["BindOptions"] = bind_options
        volume_options = _extract_docker_volume_options(raw_mount)
        if volume_options:
            mount["VolumeOptions"] = volume_options
        tmpfs_options = _extract_docker_tmpfs_options(raw_mount)
        if tmpfs_options:
            mount["TmpfsOptions"] = tmpfs_options

        mounts.append(mount)
    return mounts


def _extract_docker_mount_read_only(raw_mount: dict[str, Any]) -> bool | None:
    read_only = raw_mount.get("ReadOnly")
    if isinstance(read_only, bool):
        return read_only
    rw = raw_mount.get("RW")
    if isinstance(rw, bool):
        return not rw
    return None


def _extract_docker_bind_options(raw_mount: dict[str, Any]) -> dict[str, Any]:
    bind_options = raw_mount.get("BindOptions")
    if not isinstance(bind_options, dict):
        return {}
    propagation = bind_options.get("Propagation")
    if isinstance(propagation, str) and propagation.strip():
        return {"Propagation": propagation.strip()}
    return {}


def _extract_docker_volume_options(raw_mount: dict[str, Any]) -> dict[str, Any]:
    volume_options = raw_mount.get("VolumeOptions")
    if not isinstance(volume_options, dict):
        return {}

    normalized: dict[str, Any] = {}
    no_copy = volume_options.get("NoCopy")
    if isinstance(no_copy, bool):
        normalized["NoCopy"] = no_copy
    labels = volume_options.get("Labels")
    if isinstance(labels, dict) and labels:
        normalized["Labels"] = dict(labels)
    driver_config = volume_options.get("DriverConfig")
    if isinstance(driver_config, dict) and driver_config:
        normalized["DriverConfig"] = dict(driver_config)
    return normalized


def _extract_docker_tmpfs_options(raw_mount: dict[str, Any]) -> dict[str, Any]:
    tmpfs_options = raw_mount.get("TmpfsOptions")
    if not isinstance(tmpfs_options, dict):
        return {}

    normalized: dict[str, Any] = {}
    size_bytes = tmpfs_options.get("SizeBytes")
    if isinstance(size_bytes, int) and size_bytes > 0:
        normalized["SizeBytes"] = size_bytes
    mode = tmpfs_options.get("Mode")
    if isinstance(mode, int) and mode >= 0:
        normalized["Mode"] = mode
    return normalized


def _extract_network_config(
    host_config: dict[str, Any], network_settings: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    network_mode = host_config.get("NetworkMode")
    if network_mode:
        result["network_mode"] = network_mode
    networks = network_settings.get("Networks")
    if isinstance(networks, dict) and networks:
        dropped_empty_network_count = sum(
            1
            for name, endpoint in networks.items()
            if isinstance(name, str) and not name.strip() and isinstance(endpoint, dict)
        )
        result["endpoints"] = {
            name: dict(endpoint)
            for name, endpoint in networks.items()
            if isinstance(name, str) and name.strip() and isinstance(endpoint, dict)
        }
        if dropped_empty_network_count:
            result["dropped_empty_network_count"] = dropped_empty_network_count
    return result


def _extract_dependencies(host_config: dict[str, Any], config: dict[str, Any]) -> tuple[str, ...]:
    collected: list[str] = []

    links = host_config.get("Links")
    if isinstance(links, list):
        for item in links:
            dependency = _normalize_dependency_ref(item)
            if dependency and dependency not in collected:
                collected.append(dependency)

    network_mode = host_config.get("NetworkMode")
    if isinstance(network_mode, str) and network_mode.startswith("container:"):
        dependency = _normalize_dependency_ref(network_mode.partition(":")[2])
        if dependency and dependency not in collected:
            collected.append(dependency)

    labels = config.get("Labels") if isinstance(config, dict) else None
    if isinstance(labels, dict):
        depends_on = labels.get("com.docker.compose.depends_on") or labels.get(
            "io.podman.compose.depends_on"
        )
        if isinstance(depends_on, str):
            for raw_dependency in depends_on.split(","):
                dependency = _normalize_dependency_ref(raw_dependency.partition(":")[0])
                if dependency and dependency not in collected:
                    collected.append(dependency)

    return tuple(collected)


def _normalize_dependency_ref(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if ":" in candidate:
        candidate = candidate.split(":", 1)[0]
    if "/" in candidate:
        candidate = candidate.rsplit("/", 1)[-1]
    return candidate.strip() or None
