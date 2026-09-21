"""Verified runtime identity independent of SSH route, aliases and Compose entrypoint."""

import asyncio
import hashlib
import json
import re

from .ssh_compose import TOOLS
from .ssh_transport import SSHOperationError, open_ssh_session
from ..storage import sqlite_compose_ownership as ownership


def _key(parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


async def runtime_identity(session, engine):
    result = await session.run([engine, "info", "--format", "{{json .}}"])
    try:
        if result.exit_status:
            raise ValueError()
        info = json.loads(result.stdout)
        # podman-docker compatibility is identified from the actual engine response.
        host = info.get("host")
        store = info.get("store")
        if isinstance(host, dict) and isinstance(store, dict):
            if host.get("serviceIsRemote") is not False:
                raise ValueError()  # remote Podman needs a server-side identity, not the SSH client's
            root = store["graphRoot"]
            if not isinstance(root, str) or not root.startswith("/"):
                raise ValueError()
            machine = await session.run(["cat", "/etc/machine-id"])
            if machine.exit_status or not re.fullmatch(r"[0-9a-fA-F]{32}", machine.stdout.strip()):
                raise ValueError()
            return _key(["podman", machine.stdout.strip().lower(), root])
        daemon = info.get("ID")
        if (
            engine != "docker"
            or not isinstance(daemon, str)
            or not daemon.strip()
            or len(daemon) > 256
        ):
            raise ValueError()
        return _key(["docker", daemon])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SSHOperationError("identity", "runtime_identity_unavailable") from None


async def identify(session, target):
    key = await runtime_identity(session, TOOLS[target.tool][1])
    async with session.sftp() as sftp:
        async with asyncio.timeout(session.policy.read_timeout_seconds):
            directory = await sftp.realpath(target.working_dir)
    return {"runtime_key": key, "project": target.project, "working_dir": directory}


async def verify_session(storage, executor, session, target, *, recovery=False):
    proof = await identify(session, target)
    await ownership.assert_owner(storage, executor, proof, recovery=recovery)
    return proof


async def prepare(storage, connection, executor, existing=None):
    try:
        async with asyncio.timeout(60):
            await _prepare(storage, connection, executor, existing)
    except TimeoutError:
        raise SSHOperationError("identity", "project_verification_timeout") from None


async def _prepare(storage, connection, executor, existing=None):
    from .ssh_compose_plan import SSHComposeTarget

    if existing and (
        existing.runtime_type != "ssh"
        or existing.runtime_connection_id != executor.runtime_connection_id
        or any(
            existing.target_ref.get(k) != executor.target_ref.get(k)
            for k in ownership.IMMUTABLE_TARGET_FIELDS
        )
    ):
        raise ownership.ComposeOwnershipConflict(
            "SSH Compose project is fixed; delete the executor before binding another project"
        )
    target = SSHComposeTarget.model_validate(executor.target_ref)
    async with open_ssh_session(storage, connection) as session:
        proof = await identify(session, target)
    others = await ownership.occupants(storage, proof["runtime_key"], target.project)
    if any(row["executor_id"] != executor.id for row in others):
        raise ownership.ComposeOwnershipConflict(
            "Compose project is already managed by another executor"
        )
    # Legacy reservations cannot silently lose to the first executor to be edited.
    # Verify same-name unverified targets, including other SSH routes, before claiming.
    legacy = [
        e
        for e in await storage.get_all_executor_configs()
        if e.runtime_type == "ssh"
        and e.id != executor.id
        and e.target_ref.get("project") == target.project
    ]
    if len(legacy) > 32:
        raise ownership.ComposeOwnershipConflict(
            "Too many legacy projects; resolve ownership before enabling updates"
        )
    for other in legacy:
        claim = await ownership.get(storage, other.id)
        if claim and claim["runtime_key"]:
            continue
        conn = await storage.get_runtime_connection(other.runtime_connection_id)
        try:
            async with open_ssh_session(storage, conn) as session:
                observed = await identify(
                    session, SSHComposeTarget.model_validate(other.target_ref)
                )
        except Exception:
            raise ownership.ComposeOwnershipConflict(
                "Another legacy Compose project requires identity verification"
            ) from None
        if observed["runtime_key"] == proof["runtime_key"]:
            raise ownership.ComposeOwnershipConflict(
                "Legacy Compose project conflict; remove duplicate executors before confirming ownership"
            )
    executor._ssh_ownership = proof


async def annotate(storage, result, identities, connection_id=None):
    legacy = []
    if connection_id is not None:
        legacy = [
            e
            for e in await storage.get_all_executor_configs()
            if e.runtime_type == "ssh" and e.runtime_connection_id == connection_id
        ]
    for item in result["items"]:
        key = identities.get(item["engine"])
        owners = await ownership.occupants(storage, key, item["project"]) if key else []
        item["owner"] = (
            {"executor_id": owners[0]["executor_id"], "name": owners[0]["name"]} if owners else None
        )
        if not owners:
            unresolved = [e for e in legacy if e.target_ref.get("project") == item["project"]]
            if unresolved:
                item["owner"] = {"executor_id": unresolved[0].id, "name": unresolved[0].name}
        item["ownership_verified"] = bool(key)
    return result
