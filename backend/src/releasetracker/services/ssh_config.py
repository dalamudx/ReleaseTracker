"""SSH configuration rules shared by persistence, probes and execution."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncssh


def validate_ssh_config(config: dict[str, Any], credential_id: int | None) -> None:
    allowed = {
        "host",
        "port",
        "username",
        "host_key",
        "proxy_connection_id",
        "allow_proxy",
        "operation_policy",
    }
    if set(config) - allowed:
        raise ValueError("Unknown SSH configuration keys")
    for field in ("host", "username"):
        value = config.get(field)
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 255
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError(f"SSH config.{field} must be a non-empty value without whitespace")
    if any(c in config["host"] for c in "/[]@") or config["host"].startswith("-"):
        raise ValueError("SSH host must be a hostname or IP address, not a URL")
    port = config.get("port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    if type(credential_id) is not int or credential_id <= 0:
        raise ValueError("SSH connection requires credential_id")
    proxy = config.get("proxy_connection_id")
    if proxy is not None and (type(proxy) is not int or proxy <= 0):
        raise ValueError("SSH proxy_connection_id must be a positive integer")
    if type(config.get("allow_proxy", False)) is not bool:
        raise ValueError("SSH allow_proxy must be a boolean")
    if proxy is not None and config.get("allow_proxy"):
        raise ValueError("Only direct SSH connections may act as a proxy (one hop maximum)")
    key = config.get("host_key", "")
    if not isinstance(key, str) or len(key) > 16384:
        raise ValueError("Invalid SSH host public key")
    if key:
        parse_host_key(key)


def parse_host_key(value: str) -> asyncssh.SSHKey:
    try:
        if "PRIVATE" in value or "\n" in value.strip():
            raise ValueError()
        return asyncssh.import_public_key(value.strip())
    except (ValueError, asyncssh.KeyImportError):
        raise ValueError("Invalid SSH host public key; paste a single OpenSSH public key") from None


def validate_ssh_secrets(secrets: dict[str, Any]) -> None:
    if set(secrets) - {"auth_method", "password", "private_key", "passphrase"}:
        raise ValueError("Unknown SSH credential fields")
    method = secrets.get("auth_method")
    if method not in {"password", "private_key"}:
        raise ValueError("SSH auth_method must be password or private_key")
    for value in secrets.values():
        if not isinstance(value, str) or len(value) > 65536:
            raise ValueError("SSH credential fields must be bounded strings")
    required = "password" if method == "password" else "private_key"
    if not secrets.get(required):
        raise ValueError(f"SSH credential requires {required}")
    if method == "private_key":
        try:
            asyncssh.import_private_key(
                secrets["private_key"], passphrase=secrets.get("passphrase") or None
            )
        except (ValueError, asyncssh.KeyImportError):
            raise ValueError("Invalid SSH private key or passphrase") from None


async def ssh_dependents(storage, connection_id: int) -> list[dict[str, Any]]:
    db = await storage._get_connection()
    rows = await (
        await db.execute("SELECT id, name, config FROM runtime_connections WHERE type = 'ssh'")
    ).fetchall()
    return [
        {"id": row[0], "name": row[1]}
        for row in rows
        if storage._load_json(row[2]).get("proxy_connection_id") == connection_id
    ]


async def validate_ssh_relationship(storage, connection, *, connection_id=None) -> None:
    """Called under the storage mutation lock and again before network I/O."""
    connection_id = connection_id if connection_id is not None else connection.id
    dependents = await ssh_dependents(storage, connection_id) if connection_id is not None else []
    if dependents and (
        connection.type != "ssh"
        or not connection.enabled
        or not connection.config.get("allow_proxy")
        or connection.config.get("proxy_connection_id") is not None
    ):
        raise ValueError("SSH proxy is referenced by other connections; unlink them first")
    if connection.type != "ssh":
        return
    validate_ssh_config(connection.config, connection.credential_id)
    credential = await storage.get_credential(connection.credential_id)
    if credential is None or credential.type != "ssh":
        raise ValueError("SSH connection requires an existing SSH credential")
    await asyncio.to_thread(validate_ssh_secrets, credential.secrets)
    proxy_id = connection.config.get("proxy_connection_id")
    if proxy_id is None:
        return
    if proxy_id == connection_id:
        raise ValueError("An SSH connection cannot use itself as a proxy")
    proxy = await storage.get_runtime_connection(proxy_id)
    if (
        proxy is None
        or proxy.type != "ssh"
        or not proxy.enabled
        or not proxy.config.get("allow_proxy")
    ):
        raise ValueError("SSH proxy must be an enabled SSH connection allowing proxy use")
    if proxy.config.get("proxy_connection_id") is not None:
        raise ValueError("SSH proxy chains are not supported (one hop maximum)")
