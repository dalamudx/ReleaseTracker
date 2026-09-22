"""Operator-confirmed configuration recovery and remote lock reconciliation."""

import asyncio
import posixpath

import asyncssh

from .ssh_compose import read_project_file
from .ssh_compose_deploy import (
    _image_id,
    _write_staged,
    compose_args,
    read_state,
    replica_counts,
    verify_running,
)
from .ssh_compose_plan import SSHComposeTarget, fingerprint
from .ssh_compose_snapshot import load_snapshot
from .ssh_transport import SSHOperationError, open_ssh_session


async def recover_project(storage, executor, snapshot_id, action):
    payload = await load_snapshot(storage, executor.id, snapshot_id)
    connection = await storage.get_runtime_connection(executor.runtime_connection_id)
    target = SSHComposeTarget.model_validate(executor.target_ref)
    identity = (
        {
            k: connection.config.get(k)
            for k in ("host", "port", "username", "host_key", "proxy_connection_id")
        }
        if connection
        else None
    )
    if (
        not connection
        or not connection.enabled
        or payload.get("connection_id") != connection.id
        or payload.get("target") != target.model_dump()
        or payload.get("connection_identity") != identity
    ):
        raise SSHOperationError("recovery", "snapshot_host_or_project_changed")
    from .ssh_compose_ownership import verify_session

    async with open_ssh_session(storage, connection) as session:
        await verify_session(storage, executor, session, target, recovery=True)
        async with session.sftp() as sftp:
            owner_path = posixpath.join(payload["lock_path"], "owner")
            owner = await read_project_file(session, sftp, owner_path, optional=True)
            lock_absent = False
            if owner is None and action == "verify_and_unlock":
                # Idempotent completion after a crash between remote unlock and local commit.
                try:
                    async with asyncio.timeout(session.policy.read_timeout_seconds):
                        await sftp.lstat(payload["lock_path"])
                except asyncssh.SFTPNoSuchFile:
                    lock_absent = True
            if not lock_absent and owner != payload.get("lock_token"):
                raise SSHOperationError("recovery", "lock_owned_by_different_operation")
            changed_paths = {change["path"] for change in payload["changes"]}
            for path, expected in payload["inputs"].items():
                if path in changed_paths:
                    continue
                actual = await read_project_file(session, sftp, path, optional=expected is None)
                if fingerprint(actual) != expected:
                    raise SSHOperationError("recovery", "unrelated_project_file_changed")
            # Restored files and attempted files are both acceptable; external edits aren't.
            current = {}
            for change in payload["changes"]:
                text = await read_project_file(session, sftp, change["path"], optional=True)
                allowed = {fingerprint(change["before"]), change["after_sha256"]}
                if fingerprint(text) not in allowed:
                    raise SSHOperationError("recovery", "file_changed_since_deployment")
                current[change["path"]] = text
            if action == "restore_files":
                for change in payload["changes"]:
                    path, before = change["path"], change["before"]
                    # Restore exact captured bytes, including dotenv and comments.
                    # First-enrollment rollback removes newly introduced ownership;
                    # the next deployment must pass admission again.
                    restored = before
                    if current[path] == restored:
                        continue
                    if restored is None:
                        async with asyncio.timeout(session.policy.write_timeout_seconds):
                            await sftp.remove(path)
                    else:
                        staged = await _write_staged(session, sftp, path, restored, current[path])
                        async with asyncio.timeout(session.policy.write_timeout_seconds):
                            await sftp.posix_rename(staged, path)
                return {
                    "status": "files_restored",
                    "lock_retained": True,
                    "containers_restored": False,
                }
            if action != "verify_and_unlock":
                raise SSHOperationError("recovery", "invalid_action")
            _, env, rendered, _, override = await read_state(session, sftp, target)
            expected = {}
            for service in payload["previous_images"]:
                image = rendered.get("services", {}).get(service, {}).get("image")
                if not image:
                    raise SSHOperationError("recovery", "service_missing")
                expected[service] = await _image_id(session, target, image)
            args = compose_args(target, env, override_exists=override is not None)
            if not await verify_running(
                session, target, args, expected, replica_counts(rendered, expected)
            ):
                raise SSHOperationError("recovery", "containers_do_not_match_current_configuration")
            # No pull, up, down, or inferred retry here. The operator has reconciled the host.
            if not lock_absent:
                async with asyncio.timeout(session.policy.write_timeout_seconds):
                    await sftp.remove(owner_path)
                    await sftp.rmdir(payload["lock_path"])
    await storage.set_executor_snapshot_locked(executor.id, snapshot_id, locked=False)
    return {"status": "verified", "lock_retained": False, "containers_restored": False}
