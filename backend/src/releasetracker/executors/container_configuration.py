"""Lossless observed options: no defaults, no anonymous-volume reallocation."""

from copy import deepcopy

# These are create-time SDK options, not arbitrary inspect/status fields.
HOST_FIELDS = {
    "Privileged": "privileged",
    "ReadonlyRootfs": "read_only",
    "AutoRemove": "auto_remove",
    "PublishAllPorts": "publish_all_ports",
    "DnsOptions": "dns_opt",
    "CpusetCpus": "cpuset_cpus",
    "CpusetMems": "cpuset_mems",
    "MemorySwap": "memswap_limit",
    "MemorySwappiness": "mem_swappiness",
    "OomKillDisable": "oom_kill_disable",
    "OomScoreAdj": "oom_score_adj",
    "PidMode": "pid_mode",
    "IpcMode": "ipc_mode",
    "UsernsMode": "userns_mode",
    "UTSMode": "uts_mode",
    "CgroupParent": "cgroup_parent",
    "CgroupnsMode": "cgroupns",
    "Runtime": "runtime",
    "VolumesFrom": "volumes_from",
    "Links": "links",
    "GroupAdd": "group_add",
    "StorageOpt": "storage_opt",
    "DeviceRequests": "device_requests",
    "DeviceCgroupRules": "device_cgroup_rules",
    "BlkioWeight": "blkio_weight",
    "BlkioWeightDevice": "blkio_weight_device",
    "BlkioDeviceReadBps": "device_read_bps",
    "BlkioDeviceWriteBps": "device_write_bps",
    "BlkioDeviceReadIOps": "device_read_iops",
    "BlkioDeviceWriteIOps": "device_write_iops",
    "CpuRealtimePeriod": "cpu_rt_period",
    "CpuRealtimeRuntime": "cpu_rt_runtime",
    "CpuCount": "cpu_count",
    "CpuPercent": "cpu_percent",
    "Isolation": "isolation",
}


def preserve_host_fields(output, host, runtime):
    for field in ("ContainerIDFile", "LxcConf"):
        if host.get(field):
            raise ValueError("Container configuration cannot be safely preserved: " + field)
    for source, target in HOST_FIELDS.items():
        value = host.get(source)
        # Most inspect zeros/empty containers are engine defaults. Swappiness
        # zero explicitly disables swapping; MemorySwap=-1 is unlimited.
        if value is None or value == "" or value == [] or value == {}:
            continue
        if value is False or value == 0 and source != "MemorySwappiness":
            continue
        if runtime == "podman" and source == "Runtime":
            # Podman reports its configured OCI runtime, not Docker's Runtime.
            continue
        output[target] = deepcopy(value)
    if runtime == "docker":
        for source, target in (
            ("MaskedPaths", "masked_paths"),
            ("ReadonlyPaths", "readonly_paths"),
        ):
            if isinstance(host.get(source), list):
                output[target] = deepcopy(host[source])
    if host.get("PidsLimit") == -1:
        output["pids_limit"] = -1
    if host.get("CpuQuota") == -1:
        output["cpu_quota"] = -1


def verify_image_defaults(client, config):
    """Do not silently acquire additional image settings during an image-only update.

    Docker merges image ENV/labels/ports/volumes even with explicit empty values.
    Reject that change before stop, until it has an explicit supported policy.
    Lightweight custom SDK facades without image inspection keep their contract.
    """
    getter = getattr(getattr(client, "images", None), "get", None)
    if not callable(getter):
        return
    image = getter(config["image"])
    attrs = getattr(image, "attrs", None)
    defaults = attrs.get("Config") if isinstance(attrs, dict) else None
    if not isinstance(defaults, dict):
        raise ValueError("Target image configuration cannot be verified")
    for source, key in (
        ("Entrypoint", "entrypoint"),
        ("Cmd", "command"),
        ("User", "user"),
        ("WorkingDir", "working_dir"),
        ("Healthcheck", "healthcheck"),
        ("StopSignal", "stop_signal"),
    ):
        if defaults.get(source) and not config.get(key):
            raise ValueError("Target image would introduce unreviewed configuration: " + source)
    health = config.get("healthcheck") or {}
    image_health = defaults.get("Healthcheck") or {}
    for field in ("Interval", "Timeout", "Retries", "StartPeriod", "StartInterval"):
        if image_health.get(field) and not health.get(field):
            raise ValueError("Target image would introduce unreviewed healthcheck configuration")
    environment = config.get("environment") or []
    names = (
        set(environment)
        if isinstance(environment, dict)
        else {item.partition("=")[0] for item in environment if isinstance(item, str)}
    )
    if any(item.partition("=")[0] not in names for item in defaults.get("Env") or []):
        raise ValueError("Target image would introduce unreviewed environment variables")
    if set(defaults.get("Labels") or {}) - set(config.get("labels") or {}):
        raise ValueError("Target image would introduce unreviewed labels")
    exposed = set(config.get("ports") or {}) | set(
        config.get("_releasetracker_exposed_ports") or config.get("exposed_ports") or []
    )
    if set(defaults.get("ExposedPorts") or {}) - exposed:
        raise ValueError("Target image would introduce unreviewed exposed ports")
    destinations = {
        mount.get("Target") or mount.get("destination") or mount.get("target")
        for mount in config.get("mounts") or []
    }
    volumes = config.get("volumes") or {}
    if isinstance(volumes, dict):
        destinations.update(item.get("bind") for item in volumes.values())
    else:
        destinations.update(
            item.split(":")[1] for item in volumes if isinstance(item, str) and ":" in item
        )
    destinations.update((config.get("tmpfs") or {}).keys())
    if set(defaults.get("Volumes") or {}) - destinations:
        raise ValueError("Target image would introduce unreviewed anonymous volumes")


def verify_existing_volumes(client, config):
    """Never let native create turn a missing historical volume into an empty one."""
    getter = getattr(getattr(client, "volumes", None), "get", None)
    if not callable(getter):
        return
    sources = set()
    for mount in config.get("mounts") or []:
        if (mount.get("Type") or mount.get("type")) == "volume":
            source = mount.get("Source") or mount.get("source")
            if source:
                sources.add(source)
    volumes = config.get("volumes") or {}
    if isinstance(volumes, dict):
        sources.update(name for name in volumes if not name.startswith(("/", ".")))
    else:
        sources.update(
            bind.split(":")[0]
            for bind in volumes
            if isinstance(bind, str) and ":" in bind and not bind.startswith("/")
        )
    for name in sources:
        try:
            getter(name)
        except Exception:
            raise ValueError(
                "Existing container volume is unavailable; refusing to create replacement data"
            ) from None


def configuration_projection(adapter, config):
    """Compare effective creation semantics, not inspect IDs/counters or spellings."""
    import json
    from ..services.deployment_plan import MARKER_KEYS

    value = deepcopy(config)
    value.pop("image", None)
    value.pop("use_config_proxy", None)
    labels = value.get("labels") or {}
    value["labels"] = {key: item for key, item in labels.items() if key not in MARKER_KEYS}
    env = value.get("environment") or []
    if isinstance(env, list) and len({item.partition("=")[0] for item in env}) == len(env):
        value["environment"] = {item.partition("=")[0]: item.partition("=")[2] for item in env}
    for field in ("entrypoint", "command"):
        if value.get(field) in (None, []):
            value.pop(field, None)
    if adapter.runtime_connection.type == "podman":
        value["image"] = "comparison-only"
        _, value = adapter._podman_create_arguments(value)
        value.pop("image", None)
        value.pop("pod", None)
    # Mount and option ordering is irrelevant; argv ordering is not.
    for mount in value.get("mounts") or []:
        if "Type" in mount:
            mount.setdefault("ReadOnly", False)
    for field in ("mounts", "volumes"):
        if isinstance(value.get(field), list):
            value[field] = sorted(value[field], key=lambda item: json.dumps(item, sort_keys=True))
    return json.loads(json.dumps(value, sort_keys=True))


def verify_created_configuration(adapter, container, expected):
    """Native SDK readback; custom update facades retain their own contract."""
    client = adapter._get_client()
    if not type(client).__module__.startswith(("docker.", "podman.")):
        return
    current = client.containers.get(container.id)
    actual = adapter._extract_create_kwargs(current, expected["image"])
    from ..services.deployment_plan import MANAGED_MARKERS

    markers = MANAGED_MARKERS.get()
    if markers:
        # Creating through the task queue intentionally applies four markers,
        # including deployment-id. Verify those exact values; do not blanket
        # ignore reserved prefixes or permit unrelated label changes.
        if any((actual.get("labels") or {}).get(key) != value for key, value in markers.items()):
            raise RuntimeError(
                "Recreated container management markers do not match the approved deployment"
            )
        expected = deepcopy(expected)
        expected["labels"] = {**(expected.get("labels") or {}), **markers}
    observed = configuration_projection(adapter, actual)
    reviewed = configuration_projection(adapter, expected)
    if observed != reviewed:
        # Only fixed top-level field names, never values or nested secret keys.
        fields = sorted(
            key for key in set(observed) | set(reviewed) if observed.get(key) != reviewed.get(key)
        )
        raise RuntimeError(
            "Recreated container configuration does not match the reviewed configuration: "
            + ", ".join(fields)
        )


def preserve_mount_identity(attrs, host):
    result = deepcopy(host)
    actual = {m.get("Destination"): m for m in attrs.get("Mounts") or [] if isinstance(m, dict)}
    ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
    bindings = result.get("PortBindings") or {}
    for port, values in bindings.items():
        for item in values or []:
            if item.get("HostPort") not in (None, "", 0, "0"):
                continue
            resolved = [
                v
                for v in ports.get(port) or []
                if (v.get("HostIp") == item.get("HostIp") or not item.get("HostIp"))
                and v.get("HostPort") not in (None, "", 0, "0")
            ]
            if not resolved:
                raise ValueError("published mount/port identity unavailable")
            item["HostPort"] = resolved[0]["HostPort"]
    if host.get("PublishAllPorts"):
        for port, values in ports.items():
            if port not in bindings and values:
                bindings[port] = deepcopy(values)
    if bindings:
        result["PortBindings"] = bindings
    explicit = list(result.get("Mounts") or [])
    targets = set()
    for bind in result.get("Binds") or []:
        if isinstance(bind, str) and ":" in bind:
            targets.add(bind.split(":")[1])
    targets.update((result.get("Tmpfs") or {}).keys())
    for mount in explicit:
        if not isinstance(mount, dict):
            raise ValueError("invalid inspected mount")
        target = mount.get("Target") or mount.get("Destination")
        targets.add(target)
        if mount.get("Type") == "volume" and not mount.get("Source"):
            source = (actual.get(target) or {}).get("Name")
            if not source:
                raise ValueError("anonymous mount volume identity unavailable")
            mount["Source"] = source
    for target, mount in actual.items():
        if mount.get("Type") != "volume" or target in targets:
            continue
        name = mount.get("Name")
        if not name or not target:
            raise ValueError("anonymous mount volume identity unavailable")
        explicit.append(
            {
                "Type": "volume",
                "Source": name,
                "Target": target,
                "ReadOnly": mount.get("RW") is False,
            }
        )
    if explicit:
        result["Mounts"] = explicit
    return result
