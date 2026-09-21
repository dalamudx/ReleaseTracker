"""SSH Compose integration with normal executor policy, history and notifications."""

from .services.ssh_compose_deploy import execute_update, preview_update, read_state
from .services.ssh_compose_discovery import verify_discovered_project
from .services.ssh_compose_plan import SSHComposeTarget, SERVICE
from .services.ssh_compose_snapshot import save_snapshot
from .services.ssh_transport import SSHOperationError, open_ssh_session
from .services.ssh_compose_ownership import verify_session
from .storage.sqlite_compose_ownership import ComposeOwnershipConflict


class ExecutorSchedulerSSH:
    async def _resolve_ssh_update(self, executor):
        connection = await self.storage.get_runtime_connection(executor.runtime_connection_id)
        if connection is None or connection.type != "ssh" or not connection.enabled:
            raise SSHOperationError("configuration", "enabled_ssh_connection_required")
        target = SSHComposeTarget.model_validate(executor.target_ref)
        await verify_discovered_project(self.storage, connection, target)
        async with open_ssh_session(self.storage, connection) as session:
            await verify_session(self.storage, executor, session, target)
            async with session.sftp() as sftp:
                _, _, rendered, _, _ = await read_state(session, sftp, target)
        targets = {}
        for binding in executor.service_bindings:
            if not SERVICE.fullmatch(binding.service):
                raise SSHOperationError("binding", "invalid_service_name")
            resolved = await self._resolve_tracker_binding_by_source_id(binding.tracker_source_id)
            if resolved is None:
                raise SSHOperationError("binding", "tracker_source_missing")
            name, source = resolved
            tracker = await self.storage.get_tracker_config(name)
            if (
                tracker is None
                or not tracker.enabled
                or not source.enabled
                or source.source_type != "container"
            ):
                raise SSHOperationError("binding", "enabled_container_source_required")
            selected = await self._resolve_tracker_latest_target(
                name,
                binding.channel_name,
                tracker_source_id=source.id,
                tracker_source_type=source.source_type,
            )
            if selected is None:
                raise SSHOperationError("binding", "source_channel_target_unavailable")
            current_image = rendered.get("services", {}).get(binding.service, {}).get("image")
            if not current_image:
                raise SSHOperationError("binding", "service_image_missing")
            targets[binding.service] = self._build_target_image(
                current_image=current_image,
                target_version=selected[0],
                target_digest=selected[1],
                executor_config=executor,
                tracker_source=source,
                tracker_source_type=source.source_type,
            )
        return connection, target, targets

    async def preview_ssh_executor(self, executor):
        connection, target, targets = await self._resolve_ssh_update(executor)
        return await preview_update(self.storage, connection, target, targets, executor=executor)

    async def _execute_ssh_compose_executor(self, executor_config, *, manual, _run_id=None):
        if not executor_config.enabled:
            return await self._record_skipped(
                executor_config, message="executor disabled", run_id=_run_id
            )
        if not manual and (
            executor_config.update_mode == "manual"
            or (
                executor_config.update_mode == "maintenance_window"
                and not self._within_maintenance_window(executor_config.maintenance_window)
            )
        ):
            return await self._record_skipped(
                executor_config, message="outside update policy", run_id=_run_id
            )
        run_id = _run_id or await self._create_run_record(
            executor_config, from_version=None, to_version=None
        )
        diagnostics = {"kind": "ssh_compose", "automatic_rollback": False}
        from_version = to_version = None
        previous_images: dict[str, str] = {}
        targets: dict[str, str] = {}
        try:
            connection, target, targets = await self._resolve_ssh_update(executor_config)
            to_version = "; ".join(f"{name}: {image}" for name, image in sorted(targets.items()))

            async def persist(payload):
                nonlocal from_version, previous_images
                previous_images = dict(payload.get("previous_images", {}))
                from_version = "; ".join(
                    f"{name}: {image}" for name, image in sorted(previous_images.items())
                )
                snapshot_id = await save_snapshot(self.storage, executor_config.id, run_id, payload)
                diagnostics["snapshot_id"] = snapshot_id
                diagnostics["remote_lock"] = payload["lock_path"]
                return snapshot_id

            from .services.deployment_readiness_context import DEFER_READINESS

            defer_verification = bool(DEFER_READINESS.get())
            result = await execute_update(
                self.storage,
                connection,
                target,
                targets,
                persist,
                executor=executor_config,
                defer_verification=defer_verification,
            )
            status = result["status"]
            diagnostics.update(result)
            if status == "skipped" and not from_version:
                from_version = to_version
                previous_images = dict(targets)
            diagnostics["services"] = [
                {
                    "service": name,
                    "status": "skipped" if status == "skipped" else "success",
                    "from_version": (
                        previous_images.get(name)
                        if previous_images
                        else (targets.get(name) if status == "skipped" else None)
                    ),
                    "to_version": targets.get(name),
                    "message": (
                        "runtime already at target image" if status == "skipped" else "updated"
                    ),
                }
                for name in sorted(targets.keys())
            ]
            message = (
                (
                    "Remote Compose update applied; readiness verification pending"
                    if defer_verification
                    else "Remote Compose images and native health verified"
                )
                if status == "success"
                else "Runtime already at target images"
            )
            if result.get("snapshot_id") and not defer_verification:
                await self.storage.set_executor_snapshot_locked(
                    executor_config.id, result["snapshot_id"], locked=False
                )
            if not defer_verification:
                await self._prune_snapshot_history(executor_config.id)
        except Exception as exc:
            status = "failed"
            if targets:
                diagnostics["services"] = [
                    {
                        "service": name,
                        "status": "failed",
                        "from_version": previous_images.get(name),
                        "to_version": targets.get(name),
                        "message": str(exc),
                    }
                    for name in sorted(targets.keys())
                ]
            message = (
                str(exc)
                if isinstance(exc, (SSHOperationError, ComposeOwnershipConflict))
                else "SSH Compose operation failed; inspect remote project"
            )
            uncertain = (
                isinstance(exc, SSHOperationError)
                and exc.reason == "manual_reconciliation_required_lock_retained"
            )
            diagnostics["manual_reconciliation_required"] = uncertain
            if diagnostics.get("snapshot_id") and not uncertain:
                await self.storage.set_executor_snapshot_locked(
                    executor_config.id, diagnostics["snapshot_id"], locked=False
                )
        return await self._finalize_run(
            executor_config,
            run_id,
            status=status,
            from_version=from_version,
            to_version=to_version,
            message=message,
            last_error=message if status == "failed" else None,
            diagnostics=diagnostics,
        )
