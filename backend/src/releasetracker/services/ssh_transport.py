"""Pinned, single-hop SSH sessions. No local SSH config, agent or key discovery."""

from __future__ import annotations

import asyncio
import shlex
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import asyncssh

from .runtime_credentials import materialize_runtime_connection_credentials
from .runtime_policy import runtime_operation_policy
from .ssh_config import parse_host_key, validate_ssh_relationship, validate_ssh_secrets

MAX_OUTPUT_BYTES = 2 * 1024 * 1024


class SSHOperationError(ValueError):
    def __init__(self, stage: str, reason: str):
        self.stage = stage
        self.reason = reason
        super().__init__(f"SSH {stage}: {reason}")


def _translate_error(stage: str, error: Exception) -> SSHOperationError:
    if isinstance(error, asyncssh.HostKeyNotVerifiable):
        reason = "host_key_mismatch"
    elif isinstance(error, asyncssh.PermissionDenied):
        reason = "authentication_failed"
    elif isinstance(error, asyncssh.ChannelOpenError):
        reason = "forwarding_or_target_unavailable"
    elif isinstance(error, TimeoutError):
        reason = "timeout"
    elif isinstance(error, (OSError, asyncssh.Error)):
        reason = "connection_failed"
    else:
        reason = "invalid_configuration"
    # Never expose server banners, stderr, URLs or credential values in exceptions.
    return SSHOperationError(stage, reason)


def _connect_options(connection) -> dict:
    config = connection.config
    key_text = config.get("host_key")
    if not key_text:
        raise SSHOperationError("identity", "host_key_confirmation_required")
    key = parse_host_key(key_text)
    secrets = connection.secrets
    validate_ssh_secrets(secrets)
    keys = []
    if secrets["auth_method"] == "private_key":
        keys = [
            asyncssh.import_private_key(
                secrets["private_key"], passphrase=secrets.get("passphrase") or None
            )
        ]
    return {
        "config": None,
        "username": config["username"],
        "known_hosts": ([key], [], []),
        "client_keys": keys,
        "password": secrets.get("password") if secrets["auth_method"] == "password" else None,
        "agent_path": None,
        "agent_forwarding": False,
        "kbdint_auth": False,
        "preferred_auth": "publickey" if keys else "password",
        "connect_timeout": runtime_operation_policy(connection).read_timeout_seconds,
        "login_timeout": runtime_operation_policy(connection).read_timeout_seconds,
        "keepalive_interval": 15,
        "keepalive_count_max": 3,
    }


async def _connect(stack, connection, *, tunnel=None, stage="target"):
    try:
        options = await asyncio.to_thread(_connect_options, connection)
        return await stack.enter_async_context(
            asyncssh.connect(
                connection.config["host"],
                connection.config.get("port", 22),
                tunnel=tunnel,
                **options,
            )
        )
    except SSHOperationError:
        raise
    except Exception as exc:
        raise _translate_error(stage, exc) from None


async def _prepare(storage, connection):
    if connection.type != "ssh" or not connection.enabled:
        raise SSHOperationError("configuration", "enabled_ssh_connection_required")
    await validate_ssh_relationship(storage, connection)
    target = await materialize_runtime_connection_credentials(storage, connection)
    proxy = None
    proxy_id = connection.config.get("proxy_connection_id")
    if proxy_id is not None:
        proxy = await storage.get_runtime_connection(proxy_id)
        await validate_ssh_relationship(storage, proxy)
        proxy = await materialize_runtime_connection_credentials(storage, proxy)
    return target, proxy


@dataclass
class SSHCommandResult:
    exit_status: int
    stdout: str
    stderr: str


class SSHSession:
    def __init__(self, connection, config):
        self.connection = connection
        self.policy = runtime_operation_policy(config)

    async def run(self, argv: list[str], *, cwd: str | None = None, write=False, input_data=None):
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("Invalid SSH command arguments")
        command = shlex.join(argv)
        if cwd is not None:
            command = f"cd -- {shlex.quote(cwd)} && {command}"
        timeout = self.policy.write_timeout_seconds if write else self.policy.read_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                async with self.connection.create_process(command, encoding=None) as process:

                    async def read_bounded(stream):
                        result = bytearray()
                        while chunk := await stream.read(65536):
                            result.extend(chunk)
                            if len(result) > MAX_OUTPUT_BYTES:
                                raise SSHOperationError("command", "output_limit_exceeded")
                        return result.decode("utf-8", errors="replace")

                    async def send_input():
                        if input_data is not None:
                            process.stdin.write(input_data)
                            await process.stdin.drain()
                        process.stdin.write_eof()

                    stdout, stderr, _ = await asyncio.gather(
                        read_bounded(process.stdout), read_bounded(process.stderr), send_input()
                    )
                    await process.wait_closed()
                    return SSHCommandResult(process.exit_status, stdout, stderr)
        except SSHOperationError:
            raise
        except Exception as exc:
            if write:
                raise SSHOperationError(
                    "command", "outcome_unknown_reconcile_before_retry"
                ) from None
            raise _translate_error("command", exc) from None

    @asynccontextmanager
    async def sftp(self):
        try:
            async with asyncio.timeout(self.policy.read_timeout_seconds):
                client = await self.connection.start_sftp_client()
            async with client:
                yield client
        except (OSError, asyncssh.Error, TimeoutError) as exc:
            raise _translate_error("sftp", exc) from None


@asynccontextmanager
async def open_ssh_session(storage, connection):
    target, proxy = await _prepare(storage, connection)
    async with AsyncExitStack() as stack:
        tunnel = await _connect(stack, proxy, stage="proxy") if proxy else None
        client = await _connect(stack, target, tunnel=tunnel)
        yield SSHSession(client, target)


async def discover_host_key(storage, connection) -> dict:
    """Explicit unauthenticated target key discovery, never a trusted connection."""
    target, proxy = await _prepare(storage, connection)
    async with AsyncExitStack() as stack:
        tunnel = await _connect(stack, proxy, stage="proxy") if proxy else None
        try:
            async with asyncio.timeout(runtime_operation_policy(target).read_timeout_seconds):
                key = await asyncssh.get_server_host_key(
                    target.config["host"],
                    target.config.get("port", 22),
                    tunnel=tunnel,
                    config=None,
                    proxy_command=None,
                )
            if key is None:
                raise SSHOperationError("identity", "host_key_unavailable")
            return {
                "host_key": key.export_public_key().decode().strip(),
                "fingerprint": key.get_fingerprint(),
                "verified": False,
            }
        except SSHOperationError:
            raise
        except Exception as exc:
            raise _translate_error("identity", exc) from None


async def test_ssh_connection(storage, connection) -> dict:
    async with open_ssh_session(storage, connection) as session:
        result = await session.run(["true"])
        if result.exit_status != 0:
            raise SSHOperationError("command", "command_execution_denied")
        async with session.sftp() as sftp:
            async with asyncio.timeout(session.policy.read_timeout_seconds):
                await sftp.realpath(".")
    return {
        "success": True,
        "proxy": connection.config.get("proxy_connection_id") is not None,
        "host_key_verified": True,
        "command": True,
        "sftp": True,
    }
