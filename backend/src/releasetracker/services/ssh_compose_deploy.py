"""Single-project SSH Compose execution. No automatic rollback or blind retry.

The remote lock is deliberately retained after an uncertain mutation. An operator
must inspect the project before removing it; disconnect is not proof of failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import stat
import uuid
from contextlib import asynccontextmanager

import asyncssh

from .ssh_compose import (
    TOOLS,
    extract_compose_service_markers,
    load_yaml,
    read_project_file,
)
from .deployment_plan import MANAGED_MARKERS
from .ssh_compose_plan import SSHComposeTarget, build_plan, fingerprint
from .ssh_transport import SSHOperationError, open_ssh_session


async def _command(session, args, target, *, write=False):
    result = await session.run(args, cwd=target.working_dir, write=write)
    if result.exit_status != 0:
        # Remote stdout/stderr may contain .env secrets: never propagate it.
        raise SSHOperationError("compose", "command_failed_check_remote_host")
    return result.stdout


def compose_args(target, env_paths, *, replacements=None, override_exists=False):
    replace = replacements or {}
    files = [target.path(p) for p in target.config_files]
    if target.write_strategy == "override" and override_exists:
        if target.override_file in files:
            raise SSHOperationError("plan", "managed_override_must_not_be_in_config_files")
        files.append(target.override_file)
    args = [*TOOLS[target.tool][0], "--project-name", target.project]
    for path in files:
        args += ["-f", replace.get(path, path)]
    for path in env_paths:
        args += ["--env-file", replace.get(path, path)]
    for profile in target.profiles:
        args += ["--profile", profile]
    return args


async def read_state(session, sftp, target):
    if (
        not target.env_files
        and posixpath.dirname(target.path(target.config_files[0])) != target.working_dir
    ):
        raise SSHOperationError("plan", "explicit_environment_files_required")
    files = {
        target.path(p): await read_project_file(session, sftp, target.path(p))
        for p in target.config_files
    }
    env_files = {}
    if target.env_files:
        for p in target.env_files:
            path = target.path(p)
            env_files[path] = await read_project_file(session, sftp, path)
    else:
        path = target.path(".env")
        value = await read_project_file(session, sftp, path, optional=True)
        if value is not None:
            env_files[path] = value
    for text in files.values():
        doc = await asyncio.to_thread(load_yaml, text)
        services = doc.get("services", {})
        if (
            not isinstance(services, dict)
            or "include" in doc
            or any(isinstance(s, dict) and "extends" in s for s in services.values())
        ):
            raise SSHOperationError("plan", "inherited_configuration_not_supported")
    override = None
    if target.write_strategy == "override":
        override = await read_project_file(session, sftp, target.override_file, optional=True)
    args = compose_args(target, env_files, override_exists=override is not None)
    rendered = await asyncio.to_thread(
        load_yaml, await _command(session, [*args, "config"], target)
    )
    env_text = await _command(session, ["env", "-0"], target)
    environment = dict(e.split("=", 1) for e in env_text.split("\0") if "=" in e)
    if any(k in environment for k in ("COMPOSE_ENV_FILES", "COMPOSE_DISABLE_ENV_FILE")):
        raise SSHOperationError("plan", "explicit_environment_files_required")
    return files, env_files, rendered, environment, override


async def make_plan(session, sftp, target, targets):
    files, env_files, rendered, environment, override = await read_state(session, sftp, target)
    plan = await asyncio.to_thread(
        build_plan,
        target,
        files,
        rendered,
        env_files,
        environment,
        targets,
        override=override,
        managed_markers=MANAGED_MARKERS.get(),
    )
    # Detect an externally-created .env between preview, pull and commit.
    if not target.env_files and not env_files:
        plan.files[target.path(".env")] = None
    return plan, list(env_files)


async def analyze_update_project(storage, connection, target):
    from .ssh_compose import image_provenance, probe_compose_tools

    async with open_ssh_session(storage, connection) as session:
        tools = await probe_compose_tools(session)
        if not any(item["tool"] == target.tool and item["available"] for item in tools):
            raise SSHOperationError("compose", "selected_tool_or_engine_unavailable")
        async with session.sftp() as sftp:
            files, env, rendered, process, override = await read_state(session, sftp, target)
            if override is not None:
                files[target.override_file] = override
            services = await asyncio.to_thread(image_provenance, files, rendered, env, process)
            return {
                "tools": tools,
                "selected_tool": target.tool,
                "requires_tool_selection": False,
                "services": services,
                "read_only": True,
            }


async def preview_update(storage, connection, target, targets, *, executor):
    from .ssh_compose_ownership import verify_session

    async with open_ssh_session(storage, connection) as session:
        await verify_session(storage, executor, session, target)
        async with session.sftp() as sftp:
            plan, _ = await make_plan(session, sftp, target, targets)
            return plan.public_summary()


async def _check_inputs(session, sftp, plan):
    for path, content in plan.files.items():
        current = await read_project_file(session, sftp, path, optional=content is None)
        if fingerprint(content) != fingerprint(current):
            raise SSHOperationError("conflict", "project_files_changed")


async def _write_staged(session, sftp, path, content, before):
    # Keep staging next to the original so relative Compose paths retain meaning.
    parent = posixpath.dirname(path)
    async with asyncio.timeout(session.policy.read_timeout_seconds):
        if await sftp.realpath(parent) != parent:
            raise SSHOperationError("write", "symlinked_parent_not_supported")
        old = await sftp.stat(path, follow_symlinks=False) if before is not None else None
    if old and (old.permissions is None or not stat.S_ISREG(old.permissions)):
        raise SSHOperationError("write", "regular_file_required")
    temporary = posixpath.join(parent, f".releasetracker-{uuid.uuid4().hex}.tmp")
    try:
        async with asyncio.timeout(session.policy.write_timeout_seconds):
            async with sftp.open(
                temporary, "xb", attrs=asyncssh.SFTPAttrs(permissions=0o600)
            ) as handle:
                await handle.write(content.encode())
            if old:
                created = await sftp.stat(temporary, follow_symlinks=False)
                if (old.uid, old.gid) != (created.uid, created.gid):
                    await sftp.setstat(temporary, asyncssh.SFTPAttrs(uid=old.uid, gid=old.gid))
                # Reject setuid/setgid/sticky configuration files, never recreate those bits.
                if old.permissions & 0o7000:
                    raise SSHOperationError("write", "special_permissions_not_supported")
                await sftp.chmod(temporary, stat.S_IMODE(old.permissions))
        return temporary
    except BaseException:
        try:
            async with asyncio.timeout(session.policy.write_timeout_seconds):
                await sftp.remove(temporary)
        except (OSError, asyncssh.Error, TimeoutError):
            pass
        raise


async def _image_id(session, target, image):
    engine = TOOLS[target.tool][1]
    raw = await _command(session, [engine, "image", "inspect", image], target)
    try:
        obj = json.loads(raw)[0]
        result = obj.get("Id") or obj.get("ID")
        if not isinstance(result, str) or not result:
            raise ValueError()
        return result
    except (ValueError, KeyError, IndexError, TypeError):
        raise SSHOperationError("verify", "image_inspection_unavailable") from None


def replica_counts(rendered, targets):
    counts = {}
    for name in targets:
        service = rendered.get("services", {}).get(name, {})
        count = service.get("scale", (service.get("deploy") or {}).get("replicas", 1))
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 64:
            raise SSHOperationError("plan", "unsupported_replica_count")
        counts[name] = count
    return counts


async def verify_running(session, target, args, expected, counts=None, *, require_health=False):
    engine = TOOLS[target.tool][1]
    for service, image_id in expected.items():
        ids = []
        for project_label in (
            "com.docker.compose.project",
            "io.podman.compose.project",
        ):
            output = await _command(
                session,
                [
                    engine,
                    "ps",
                    "--all",
                    "--no-trunc",
                    "--filter",
                    f"label={project_label}={target.project}",
                    "--filter",
                    f"label=com.docker.compose.service={service}",
                    "--format",
                    "{{.ID}}",
                ],
                target,
            )
            ids.extend(cid for cid in output.split() if cid not in ids)
        if (
            not ids
            or len(ids) != (counts or {}).get(service, 1)
            or len(ids) > 64
            or any(
                not 12 <= len(cid) <= 64 or not all(c in "0123456789abcdef" for c in cid)
                for cid in ids
            )
        ):
            return False
        for cid in ids:
            try:
                obj = json.loads(await _command(session, [engine, "inspect", cid], target))[0]
                state = obj.get("State", {})
                health = (state.get("Health") or state.get("Healthcheck") or {}).get("Status")
                if (
                    obj.get("Image") != image_id
                    or not state.get("Running")
                    or health not in (None, "healthy")
                    or (require_health and health != "healthy")
                ):
                    return False
            except (ValueError, IndexError, TypeError, AttributeError):
                raise SSHOperationError("verify", "container_inspection_unavailable") from None
    return True


@asynccontextmanager
async def project_lock(session, sftp, target):
    async with asyncio.timeout(session.policy.read_timeout_seconds):
        directory = await sftp.realpath(target.working_dir)
    name = hashlib.sha256((directory + "\0" + target.project).encode()).hexdigest()[:20]
    path = posixpath.join(directory, f".releasetracker-{name}.lock")
    try:
        async with asyncio.timeout(session.policy.write_timeout_seconds):
            await sftp.mkdir(path, attrs=asyncssh.SFTPAttrs(permissions=0o700))
    except (asyncssh.Error, TimeoutError):
        raise SSHOperationError("lock", "project_busy_or_requires_manual_reconciliation") from None
    state = {"path": path, "uncertain": False, "token": uuid.uuid4().hex}
    try:
        async with asyncio.timeout(session.policy.write_timeout_seconds):
            async with sftp.open(
                posixpath.join(path, "owner"), "x", attrs=asyncssh.SFTPAttrs(permissions=0o600)
            ) as handle:
                await handle.write(state["token"])
        yield state
    finally:
        if not state["uncertain"]:
            try:
                async with asyncio.timeout(session.policy.write_timeout_seconds):
                    await sftp.remove(posixpath.join(path, "owner"))
                    await sftp.rmdir(path)
            except (OSError, asyncssh.Error, TimeoutError):
                pass


async def read_managed_markers(storage, connection, target, services=None):
    """Read per-service ownership labels from the remote rendered Compose model."""
    async with open_ssh_session(storage, connection) as session:
        async with session.sftp() as sftp:
            _, _, rendered, _, _ = await read_state(session, sftp, target)
    if services is not None:
        rendered = {"services": {s: rendered.get("services", {}).get(s, {}) for s in services}}
    return extract_compose_service_markers(rendered)


async def execute_update(
    storage,
    connection,
    target: SSHComposeTarget,
    targets,
    save_snapshot,
    *,
    executor,
    approved_plan_id=None,
    defer_verification=False,
):
    """save_snapshot must durably encrypt the supplied payload or raise before commit."""
    from .ssh_compose_ownership import verify_session
    from .task_effects import mark_deployment_mutation

    async with open_ssh_session(storage, connection) as session:
        await verify_session(storage, executor, session, target)
        async with session.sftp() as sftp:
            async with project_lock(session, sftp, target) as lock:
                temporary = None
                try:
                    plan, env_paths = await make_plan(session, sftp, target, targets)
                    summary = plan.public_summary()
                    if approved_plan_id is not None and summary["plan_id"] != approved_plan_id:
                        raise SSHOperationError("conflict", "preview_expired")
                    help_text = await _command(
                        session, [*TOOLS[target.tool][0], "up", "--help"], target
                    )
                    if any(
                        flag not in help_text
                        for flag in ("--no-deps", "--no-build", "--force-recreate")
                    ):
                        raise SSHOperationError("preflight", "compose_update_flags_not_supported")
                    replacements = {}
                    if plan.changes:
                        change = plan.changes[0]
                        temporary = await _write_staged(
                            session, sftp, change.path, change.after, change.before
                        )
                        replacements[change.path] = temporary
                    args = compose_args(
                        target, env_paths, override_exists=target.write_strategy == "override"
                    )
                    staged_args = compose_args(
                        target,
                        env_paths,
                        replacements=replacements,
                        override_exists=target.write_strategy == "override",
                    )
                    staged = await asyncio.to_thread(
                        load_yaml, await _command(session, [*staged_args, "config"], target)
                    )
                    plan.validate_rendered(staged)
                    engine = TOOLS[target.tool][1]
                    expected = {}
                    for image in dict.fromkeys(targets.values()):
                        await _command(session, [engine, "pull", image], target, write=True)
                    for service, image in targets.items():
                        expected[service] = await _image_id(session, target, image)
                    counts = replica_counts(staged, targets)
                    if not plan.changes and await verify_running(
                        session, target, args, expected, counts
                    ):
                        return {"status": "skipped", "plan": summary, "snapshot_id": None}
                    await _check_inputs(session, sftp, plan)
                    snapshot_id = await save_snapshot(
                        {
                            "target": target.model_dump(),
                            "connection_id": connection.id,
                            "host_key": connection.config["host_key"],
                            "connection_identity": {
                                k: connection.config.get(k)
                                for k in (
                                    "host",
                                    "port",
                                    "username",
                                    "host_key",
                                    "proxy_connection_id",
                                )
                            },
                            "lock_token": lock["token"],
                            "changes": [
                                {
                                    "path": c.path,
                                    "before": c.before,
                                    "after_sha256": fingerprint(c.after),
                                }
                                for c in plan.changes
                            ],
                            "inputs": {
                                path: fingerprint(value) for path, value in plan.files.items()
                            },
                            "previous_images": {
                                s: plan.rendered["services"][s]["image"] for s in targets
                            },
                            "lock_path": lock["path"],
                            "plan_id": summary["plan_id"],
                            "managed_markers": dict(MANAGED_MARKERS.get() or {}),
                        }
                    )
                    await _check_inputs(session, sftp, plan)
                    await mark_deployment_mutation()
                    lock["uncertain"] = True  # Includes loss of the rename acknowledgement.
                    if temporary:
                        async with asyncio.timeout(session.policy.write_timeout_seconds):
                            await sftp.posix_rename(temporary, plan.changes[0].path)
                        temporary = None
                    actual = await asyncio.to_thread(
                        load_yaml, await _command(session, [*args, "config"], target)
                    )
                    plan.validate_rendered(actual)
                    await _command(
                        session,
                        [
                            *args,
                            "up",
                            "-d",
                            "--no-deps",
                            "--no-build",
                            "--force-recreate",
                            *sorted(targets),
                        ],
                        target,
                        write=True,
                    )
                    # Acknowledged completion proves the command stopped, not readiness.
                    if defer_verification:
                        lock["uncertain"] = False
                        return {
                            "status": "success",
                            "plan": summary,
                            "snapshot_id": snapshot_id,
                            "readiness_context": {
                                "deferred": True,
                                "expected": expected,
                                "counts": counts,
                                "targets": dict(targets),
                                "target": target.model_dump(),
                            },
                        }
                    # Command completion is not success. Check every replica and native health.
                    async with asyncio.timeout(session.policy.write_timeout_seconds):
                        while not await verify_running(session, target, args, expected, counts):
                            await asyncio.sleep(1)
                    lock["uncertain"] = False
                    return {"status": "success", "plan": summary, "snapshot_id": snapshot_id}
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if lock["uncertain"]:
                        raise SSHOperationError(
                            "deploy", "manual_reconciliation_required_lock_retained"
                        ) from None
                    if isinstance(exc, SSHOperationError):
                        raise
                    raise SSHOperationError(
                        "deploy", "preflight_failed_no_project_mutation"
                    ) from None
                finally:
                    if temporary:
                        try:
                            await sftp.remove(temporary)
                        except (OSError, asyncssh.Error):
                            pass
