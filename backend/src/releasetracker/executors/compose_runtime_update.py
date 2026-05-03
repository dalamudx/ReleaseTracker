from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    create_config = _extract_create_kwargs(container, target_image, config, host_config)
    snapshot_create_config = _extract_create_kwargs(
        container,
        current_image or target_image,
        config,
        host_config,
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

    healthcheck = config.get("Healthcheck")
    if healthcheck and isinstance(healthcheck, dict):
        kwargs["healthcheck"] = dict(healthcheck)

    exposed_ports = config.get("ExposedPorts")
    normalized_ports: dict[str, Any] = {}
    if exposed_ports and isinstance(exposed_ports, dict):
        for proto_port in exposed_ports:
            if isinstance(proto_port, str) and proto_port.strip():
                normalized_ports[proto_port] = None

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

    labels = config.get("Labels")
    if labels:
        kwargs["labels"] = labels

    return kwargs


def _extract_network_config(
    host_config: dict[str, Any], network_settings: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    network_mode = host_config.get("NetworkMode")
    if network_mode:
        result["network_mode"] = network_mode
    networks = network_settings.get("Networks")
    if isinstance(networks, dict) and networks:
        result["endpoints"] = {
            name: dict(endpoint)
            for name, endpoint in networks.items()
            if isinstance(name, str) and name.strip() and isinstance(endpoint, dict)
        }
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
