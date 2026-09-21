"""Bounded, read-only Compose project discovery from container labels, never a disk scan."""

import asyncio
import hashlib
import json
import posixpath
import re

from .ssh_compose import TOOLS, SSHComposeProject, compose_tool_choices, probe_compose_tools
from .ssh_transport import SSHOperationError, open_ssh_session

MAX_CONTAINERS = 200
BATCH_SIZE = 20
PREFIX = "com.docker.compose."


def _paths(value):
    return [p.strip() for p in value.split(",") if p.strip()]


def _configuration(project):
    """Compare the same paths used by Compose, without changing saved targets."""
    return {
        "project": project.project,
        "working_dir": project.working_dir,
        "config_files": [project.path(path) for path in project.config_files],
        "env_files": [project.path(path) for path in project.env_files],
        "profiles": project.profiles,
    }


def _candidate(engine, name, rows, tools, truncated=False):
    # Recreated containers may report absolute -f paths while untouched services
    # retain relative labels. Normalize each observation before conflict detection.
    normalized = []
    for row in rows:
        row = dict(row)
        directory = row.get(PREFIX + "project.working_dir", "")
        try:
            SSHComposeProject(
                working_dir=directory,
                project=name,
                config_files=_paths(row.get(PREFIX + "project.config_files", "")),
                env_files=_paths(row.get(PREFIX + "project.environment_file", "")),
            )
        except ValueError:
            normalized.append(row)
            continue
        if directory.startswith("/"):
            directory = posixpath.normpath(directory)
            row[PREFIX + "project.working_dir"] = directory
            for key in ("project.config_files", "project.environment_file"):
                row[PREFIX + key] = ",".join(
                    posixpath.normpath(posixpath.join(directory, path))
                    for path in _paths(row.get(PREFIX + key, ""))
                )
        normalized.append(row)
    rows = normalized

    def unique(key):
        values = {row.get(PREFIX + key, "") for row in rows}
        return next(iter(values)) if len(values) == 1 else ""

    warnings = []
    for key in ("project.working_dir", "project.config_files", "project.environment_file"):
        if len({row.get(PREFIX + key, "") for row in rows}) > 1:
            warnings.append("conflicting_project_metadata")
    directory = unique("project.working_dir")
    files = _paths(unique("project.config_files"))
    env_files = _paths(unique("project.environment_file"))
    if warnings:
        # Do not turn conflicting env-file observations into an implicit .env fallback.
        directory, files, env_files = "", [], []
    if truncated:
        warnings.append("incomplete_discovery")
    strategy = "source"
    managed = posixpath.join(directory, f".releasetracker-{name}.override.yml")
    if directory and files and posixpath.normpath(posixpath.join(directory, files[-1])) == managed:
        files = files[:-1]
        strategy = "override"
    try:
        SSHComposeProject(
            working_dir=directory, project=name, config_files=files, env_files=env_files
        )
    except ValueError:
        warnings.append("incomplete_project_metadata")
    choices = compose_tool_choices(tools, engine)
    if len(choices) != 1:
        warnings.append("choose_compose_tool")
    return {
        "id": hashlib.sha256(f"{engine}\0{name}".encode()).hexdigest()[:20],
        "engine": engine,
        "project": name,
        "working_dir": directory,
        "config_files": files,
        "env_files": env_files,
        "profiles": [],
        "tool": choices[0] if len(choices) == 1 and not truncated else None,
        "tool_choices": choices,
        "write_strategy": strategy,
        "services": sorted(
            {row[PREFIX + "service"] for row in rows if row.get(PREFIX + "service")}
        ),
        "warnings": sorted(set(warnings)),
    }


async def verify_discovered_project(storage, connection, target):
    """Ensure a client cannot alter configuration reported by discovery."""
    if target.discovery_id is None:
        return
    result = await discover_compose_projects(storage, connection)
    item = next((row for row in result["items"] if row["id"] == target.discovery_id), None)
    expected = _configuration(target)
    observed = {}
    if item is not None:
        try:
            observed = _configuration(SSHComposeProject(**{key: item[key] for key in expected}))
        except ValueError:
            item = None
    if (
        item is None
        or item["engine"] != TOOLS[target.tool][1]
        or any(observed[key] != value for key, value in expected.items())
        or target.tool not in item["tool_choices"]
        or any(
            warning in item["warnings"]
            for warning in (
                "conflicting_project_metadata",
                "incomplete_project_metadata",
                "incomplete_discovery",
            )
        )
    ):
        raise ValueError(
            "Discovered Compose project configuration changed; refresh discovery and check the project directory, file order and environment files"
        )


async def discover_compose_projects(storage, connection):
    try:
        async with asyncio.timeout(60):
            return await _discover_compose_projects(storage, connection)
    except TimeoutError:
        raise SSHOperationError("discovery", "discovery_timeout") from None


async def _discover_compose_projects(storage, connection):
    async with open_ssh_session(storage, connection) as session:
        tools = await probe_compose_tools(session)
        from .ssh_compose_ownership import runtime_identity, annotate

        identities = {}
        groups = {}
        warnings = []
        truncated = set()
        remaining = MAX_CONTAINERS
        # Query each engine only once even when both Compose entrypoints are installed.
        for engine in ("docker", "podman"):
            if not any(t["engine"] == engine and t["available"] for t in tools):
                continue
            try:
                identities[engine] = await runtime_identity(session, engine)
            except SSHOperationError:
                warnings.append(f"{engine}: runtime_identity_unavailable")
            ids = []
            seen = set()
            try:
                for label in ("com.docker.compose.project", "io.podman.compose.project"):
                    result = await session.run(
                        [
                            engine,
                            "ps",
                            "--all",
                            "--no-trunc",
                            "--filter",
                            f"label={label}",
                            "--format",
                            "{{.ID}}",
                        ]
                    )
                    if result.exit_status:
                        raise SSHOperationError("discovery", "container_list_failed")
                    for value in result.stdout.splitlines():
                        value = value.strip()
                        if not re.fullmatch(r"[a-f0-9]{12,64}", value):
                            raise SSHOperationError("discovery", "invalid_container_id")
                        if value not in seen and len(ids) <= MAX_CONTAINERS:
                            seen.add(value)
                            ids.append(value)
                if len(ids) > remaining:
                    truncated.add(engine)
                ids = ids[:remaining]
                remaining -= len(ids)
                for start in range(0, len(ids), BATCH_SIZE):
                    result = await session.run(
                        [
                            engine,
                            "inspect",
                            "--format",
                            "{{json .Config.Labels}}",
                            *ids[start : start + BATCH_SIZE],
                        ]
                    )
                    if result.exit_status:
                        raise SSHOperationError("discovery", "container_labels_unavailable")
                    label_lines = result.stdout.splitlines()
                    if len(label_lines) != len(ids[start : start + BATCH_SIZE]):
                        raise SSHOperationError("discovery", "incomplete_container_labels")
                    for line in label_lines:
                        labels = json.loads(line)
                        if labels is None:
                            continue
                        if not isinstance(labels, dict):
                            raise ValueError()
                        # Do not return arbitrary labels, inspect output or container environment.
                        row = {
                            k: v
                            for k, v in labels.items()
                            if k
                            in {
                                PREFIX + "project",
                                PREFIX + "service",
                                PREFIX + "oneoff",
                                PREFIX + "project.working_dir",
                                PREFIX + "project.config_files",
                                PREFIX + "project.environment_file",
                                "io.podman.compose.project",
                            }
                        }
                        if any(
                            not isinstance(v, str) or len(v) > 4096 or any(ord(c) < 32 for c in v)
                            for v in row.values()
                        ):
                            raise SSHOperationError("discovery", "invalid_container_labels")
                        if (
                            row.get(PREFIX + "project")
                            and row.get("io.podman.compose.project")
                            and row[PREFIX + "project"] != row["io.podman.compose.project"]
                        ):
                            raise SSHOperationError("discovery", "conflicting_project_labels")
                        name = row.get(PREFIX + "project") or row.get("io.podman.compose.project")
                        if not name or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", name):
                            continue
                        if row.get(PREFIX + "oneoff", "").lower() == "true":
                            continue
                        groups.setdefault((engine, name), []).append(row)
            except (SSHOperationError, ValueError) as exc:
                reason = (
                    exc.reason if isinstance(exc, SSHOperationError) else "invalid_container_labels"
                )
                warnings.append(f"{engine}: {reason}")
                # Never auto-fill from a partially inspected engine.
                groups = {key: rows for key, rows in groups.items() if key[0] != engine}
        result = {
            "items": [
                _candidate(engine, name, rows, tools, engine in truncated)
                for (engine, name), rows in sorted(groups.items())
            ],
            "tools": tools,
            "warnings": warnings,
            "truncated": bool(truncated),
            "read_only": True,
        }
        return await annotate(storage, result, identities, getattr(connection, "id", None))
