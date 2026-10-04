"""Readable assertions over real Docker SDK wire conversion (not another renderer)."""

from copy import deepcopy
from releasetracker.executors.container_configuration import HOST_FIELDS


def high_level_view(raw):
    result = deepcopy(raw)
    host = result.pop("host_config")
    for field in ("ports", "volumes"):
        result.pop(field, None)
    mapping = {
        **HOST_FIELDS,
        "Binds": "volumes",
        "Mounts": "mounts",
        "RestartPolicy": "restart_policy",
        "NetworkMode": "network_mode",
        "ExtraHosts": "extra_hosts",
        "Dns": "dns",
        "DnsSearch": "dns_search",
        "Tmpfs": "tmpfs",
        "Ulimits": "ulimits",
        "SecurityOpt": "security_opt",
        "CapAdd": "cap_add",
        "CapDrop": "cap_drop",
        "Devices": "devices",
        "PidsLimit": "pids_limit",
        "Memory": "mem_limit",
        "MemoryReservation": "mem_reservation",
        "CpuShares": "cpu_shares",
        "NanoCpus": "nano_cpus",
        "CpuPeriod": "cpu_period",
        "CpuQuota": "cpu_quota",
        "Sysctls": "sysctls",
        "ShmSize": "shm_size",
        "Init": "init",
        "LogConfig": "log_config",
    }
    for key, value in host.items():
        if key in mapping and value not in (None, [], {}):
            result[mapping[key]] = deepcopy(value)
    if host.get("PortBindings"):
        bindings = {}
        for port, items in host["PortBindings"].items():
            values = [(item["HostIp"], int(item["HostPort"])) for item in items]
            bindings[port] = values[0] if len(values) == 1 else values
        result["ports"] = bindings
    if host.get("Binds"):
        binds = {}
        for item in host["Binds"]:
            source, target, *mode = item.split(":")
            binds[source] = {"bind": target, "mode": mode[0] if mode else "rw"}
        result["volumes"] = binds
    if host.get("Devices"):
        result["devices"] = [
            f"{item['PathOnHost']}:{item['PathInContainer']}:{item['CgroupPermissions']}"
            for item in host["Devices"]
        ]
    return result
