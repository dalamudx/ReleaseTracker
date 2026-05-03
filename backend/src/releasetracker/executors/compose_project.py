from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_COMPOSE_FILE_NAMES = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)


class _ComposeProjectAdapterMixin:
    async def _render_compose_config(
        self,
        target_ref: dict[str, Any],
        *,
        service_target_images: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            raw_payload = await self._run_compose_command(
                target_ref,
                ["config", "--format", "json"],
                service_target_images=service_target_images,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "--format" not in message and "format" not in message:
                raise
            raw_payload = await self._run_compose_command(
                target_ref,
                ["config"],
                service_target_images=service_target_images,
            )

        parsed = self._parse_compose_render_payload(raw_payload)
        if not isinstance(parsed, dict):
            raise ValueError("rendered compose config must be an object")
        services = parsed.get("services")
        if not isinstance(services, dict):
            raise ValueError("rendered compose config is missing a services map")
        return parsed

    async def _apply_compose_service_update(
        self,
        target_ref: dict[str, Any],
        service_names: list[str],
        *,
        service_target_images: dict[str, str] | None = None,
    ) -> None:
        if not service_names:
            return
        await self._run_compose_command(
            target_ref,
            ["pull", *service_names],
            service_target_images=service_target_images,
        )
        await self._run_compose_command(
            target_ref,
            ["up", "-d", "--no-deps", "--force-recreate", *service_names],
            service_target_images=service_target_images,
        )

    async def _run_compose_command(
        self,
        target_ref: dict[str, Any],
        compose_args: list[str],
        *,
        service_target_images: dict[str, str] | None = None,
    ) -> str:
        project_raw = target_ref.get("project")
        if not isinstance(project_raw, str) or not project_raw.strip():
            raise ValueError("target_ref.project must be a non-empty string")
        project = project_raw.strip()
        working_dir, config_files = self._resolve_compose_project_paths(target_ref)
        override_payload = None
        if service_target_images:
            override_payload = self._build_compose_override(service_target_images)

        return await asyncio.to_thread(
            self._run_compose_command_sync,
            working_dir,
            config_files,
            project,
            compose_args,
            override_payload,
        )

    def _run_compose_command_sync(
        self,
        working_dir: Path,
        config_files: list[Path],
        project: str,
        compose_args: list[str],
        override_payload: dict[str, Any] | None,
    ) -> str:
        override_path: Path | None = None
        try:
            if override_payload is not None:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".releasetracker.compose.override.yml",
                    delete=False,
                ) as handle:
                    yaml.safe_dump(override_payload, handle, sort_keys=False)
                    override_path = Path(handle.name)

            command_prefix = self._build_compose_project_command(
                project=project,
                working_dir=working_dir,
                config_files=config_files,
                override_path=override_path,
            )
            env = os.environ.copy()
            env.update(self._compose_command_environment())

            last_error: RuntimeError | None = None
            for command in self._compose_command_candidates():
                full_command = [*command, *command_prefix, *compose_args]
                try:
                    completed = subprocess.run(
                        full_command,
                        cwd=str(working_dir),
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                except FileNotFoundError:
                    continue

                if completed.returncode == 0:
                    return completed.stdout

                error_message = (completed.stderr or completed.stdout or "").strip()
                if self._compose_command_is_unavailable(error_message):
                    continue

                last_error = RuntimeError(
                    f"{self._compose_runtime_label()} compose command failed: {error_message or completed.returncode}"
                )
                break

            if last_error is not None:
                raise last_error
            raise RuntimeError(
                f"{self._compose_runtime_label()} compose command is unavailable on the execution host"
            )
        finally:
            if override_path is not None:
                override_path.unlink(missing_ok=True)

    def _build_compose_project_command(
        self,
        *,
        project: str,
        working_dir: Path,
        config_files: list[Path],
        override_path: Path | None,
    ) -> list[str]:
        command = ["--project-directory", str(working_dir), "-p", project]
        for config_file in config_files:
            command.extend(["-f", str(config_file)])
        if override_path is not None:
            command.extend(["-f", str(override_path)])
        return command

    def _resolve_compose_project_paths(self, target_ref: dict[str, Any]) -> tuple[Path, list[Path]]:
        working_dir_raw = target_ref.get("working_dir")
        if not isinstance(working_dir_raw, str) or not working_dir_raw.strip():
            raise ValueError(
                "docker_compose target requires target_ref.working_dir for render/diff updates"
            )

        working_dir = Path(working_dir_raw).expanduser().resolve()
        if not working_dir.is_dir():
            raise ValueError(f"compose working_dir is not accessible: {working_dir}")

        configured_files = target_ref.get("config_files")
        config_files_raw = configured_files if isinstance(configured_files, list) else []
        resolved_files: list[Path] = []
        for item in config_files_raw:
            if not isinstance(item, str) or not item.strip():
                continue
            candidate = Path(item.strip())
            if not candidate.is_absolute():
                candidate = (working_dir / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if not candidate.is_file():
                raise ValueError(f"compose config file is not accessible: {candidate}")
            resolved_files.append(candidate)

        if not resolved_files:
            for name in _DEFAULT_COMPOSE_FILE_NAMES:
                candidate = (working_dir / name).resolve()
                if candidate.is_file():
                    resolved_files.append(candidate)
                    break

        if not resolved_files:
            raise ValueError(
                "docker_compose target requires target_ref.config_files or a default compose file in working_dir"
            )

        return working_dir, resolved_files

    @staticmethod
    def _parse_compose_render_payload(payload: str) -> Any:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return yaml.safe_load(payload)

    @staticmethod
    def _build_compose_override(service_target_images: dict[str, str]) -> dict[str, Any]:
        return {
            "services": {
                service: {"image": image}
                for service, image in sorted(service_target_images.items())
            }
        }

    @classmethod
    def _rendered_service_images(cls, rendered_config: dict[str, Any]) -> dict[str, str | None]:
        services = rendered_config.get("services")
        if not isinstance(services, dict):
            raise ValueError("rendered compose config is missing a services map")

        images: dict[str, str | None] = {}
        for service_name, service_config in services.items():
            if not isinstance(service_name, str) or not service_name.strip():
                continue
            image: str | None = None
            if isinstance(service_config, dict):
                raw_image = service_config.get("image")
                if isinstance(raw_image, str) and raw_image.strip():
                    image = raw_image.strip()
            images[service_name] = image
        return images

    @classmethod
    def _diff_rendered_compose_config(
        cls,
        current: Any,
        desired: Any,
        path: tuple[Any, ...] = (),
    ) -> list[tuple[Any, ...]]:
        if isinstance(current, dict) and isinstance(desired, dict):
            diffs: list[tuple[Any, ...]] = []
            for key in sorted(set(current) | set(desired), key=str):
                if key not in current or key not in desired:
                    diffs.append((*path, key))
                    continue
                diffs.extend(
                    cls._diff_rendered_compose_config(current[key], desired[key], (*path, key))
                )
            return diffs

        if isinstance(current, list) and isinstance(desired, list):
            if len(current) != len(desired):
                return [path]
            diffs: list[tuple[Any, ...]] = []
            for index, (left, right) in enumerate(zip(current, desired)):
                diffs.extend(cls._diff_rendered_compose_config(left, right, (*path, index)))
            return diffs

        if current != desired:
            return [path]
        return []

    @staticmethod
    def _compose_diff_is_allowed(
        diff_path: tuple[Any, ...],
        service_target_images: dict[str, str],
    ) -> bool:
        return (
            len(diff_path) == 3
            and diff_path[0] == "services"
            and isinstance(diff_path[1], str)
            and diff_path[1] in service_target_images
            and diff_path[2] == "image"
        )

    @staticmethod
    def _compose_command_is_unavailable(message: str) -> bool:
        normalized = message.lower()
        return any(
            token in normalized
            for token in (
                "unknown command",
                "is not a docker command",
                "is not a podman command",
                "command not found",
                "no such command",
            )
        )

    def _compose_command_candidates(self) -> list[list[str]]:
        raise NotImplementedError

    def _compose_command_environment(self) -> dict[str, str]:
        return {}

    def _compose_runtime_label(self) -> str:
        return "compose"
