"""Validation and fresh-install defaults for persistent instance policies."""

import os
from ipaddress import ip_network
from urllib.parse import urlsplit

INTEGER_POLICIES = {
    "system.mutation_concurrency": (1, 1, 3),
    "system.worker_poll_seconds": (2, 1, 60),
    "system.readiness_pull_grace_seconds": (1800, 0, 3600),
    "system.runtime_health_interval_seconds": (0, 0, 86400),
}
POLICY_DEFAULTS = {
    **{key: str(default) for key, (default, _, _) in INTEGER_POLICIES.items()},
    "system.online_restore": "true" if os.name == "posix" else "false",
    "system.pre_migration_backup": "true",
    "system.cors_origins": "",
    "system.forwarded_allow_ips": "127.0.0.1",
}
IMPORT_MARKER = "system.legacy_policy_imported"


def normalize_policy(key: str, value: str) -> str:
    value = value.strip()
    if key in INTEGER_POLICIES:
        _, low, high = INTEGER_POLICIES[key]
        if not value.isascii() or not value.isdecimal():
            raise ValueError(f"{key} must be an integer")
        number = int(value)
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        if key == "system.runtime_health_interval_seconds" and 0 < number < 300:
            raise ValueError("Runtime health interval must be 0 or 300–86400 seconds")
        return str(number)
    if key in {"system.online_restore", "system.pre_migration_backup"}:
        if value not in {"true", "false"}:
            raise ValueError(f"{key} must be true or false")
        if key == "system.online_restore" and value == "true" and os.name != "posix":
            raise ValueError("Online restore requires POSIX")
        return value
    entries = dict.fromkeys(v.strip() for v in value.splitlines() if v.strip())
    if key == "system.forwarded_allow_ips" and "*" in entries and len(entries) > 1:
        raise ValueError("Use * alone to trust all proxies")
    for entry in entries:
        if key == "system.cors_origins":
            origin = urlsplit(entry)
            if (
                origin.scheme not in {"http", "https"}
                or not origin.hostname
                or origin.username is not None
                or origin.password is not None
                or origin.path not in {"", "/"}
                or any(c in entry for c in "?#*,\\")
                or any(c.isspace() or ord(c) < 32 for c in entry)
            ):
                raise ValueError("CORS requires explicit HTTP(S) origins")
            _ = origin.port
        elif key == "system.forwarded_allow_ips":
            if entry != "*":
                try:
                    ip_network(entry)
                except ValueError:
                    raise ValueError("Trusted proxies must be IP addresses, CIDRs or *") from None
        else:
            raise ValueError("Unknown instance policy")
    return "\n".join(dict.fromkeys(entry.rstrip("/") for entry in entries))


async def read_integer_policy(storage, key: str) -> int:
    """Read a persisted integer policy, falling back to its default."""
    value = await storage.get_setting(key)
    if value is not None:
        try:
            return int(normalize_policy(key, value))
        except ValueError:
            pass
    return int(POLICY_DEFAULTS[key])
