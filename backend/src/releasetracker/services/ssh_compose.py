"""Read-only remote Compose discovery with conservative image provenance.

Remote Compose is authoritative for interpolation. Source files and environment
are used only to explain provenance, never to invent an executable image.
"""

from __future__ import annotations

import asyncio
import hashlib
import posixpath
import re
import stat
from typing import Literal

import asyncssh
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ssh_transport import SSHOperationError, open_ssh_session

TOOLS = {
    "docker_compose": (["docker", "compose"], "docker"),
    "docker-compose": (["docker-compose"], "docker"),
    "podman_compose": (["podman", "compose"], "podman"),
    "podman-compose": (["podman-compose"], "podman"),
}
MAX_FILE_BYTES = 1024 * 1024
VARIABLE = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-)([^{}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))"
)
ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class SSHComposeProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    working_dir: str
    project: str
    config_files: list[str] = Field(min_length=1, max_length=8)
    env_files: list[str] = Field(default_factory=list, max_length=8)
    profiles: list[str] = Field(default_factory=list, max_length=16)
    tool: Literal[
        "auto", "docker_compose", "docker-compose", "podman_compose", "podman-compose"
    ] = "auto"

    @field_validator("working_dir")
    @classmethod
    def directory(cls, value):
        if not value.startswith("/") or any(ord(c) < 32 for c in value):
            raise ValueError("Compose working_dir must be an absolute POSIX path")
        return posixpath.normpath(value)

    @field_validator("project")
    @classmethod
    def project_name(cls, value):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value):
            raise ValueError("Invalid Compose project name")
        return value

    @field_validator("config_files", "env_files")
    @classmethod
    def file_paths(cls, value):
        if len(set(value)) != len(value) or any(
            not p or p.startswith("-") or any(ord(c) < 32 for c in p) for p in value
        ):
            raise ValueError(
                "Compose paths must be unique non-empty paths without control characters"
            )
        return value

    @field_validator("profiles")
    @classmethod
    def profile_names(cls, value):
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", p) for p in value):
            raise ValueError("Invalid Compose profile")
        return value

    def path(self, path):
        return posixpath.normpath(posixpath.join(self.working_dir, path))

    def argv(self, tool):
        args = [*TOOLS[tool][0], "--project-name", self.project]
        for path in self.config_files:
            args += ["-f", self.path(path)]
        for path in self.env_files:
            args += ["--env-file", self.path(path)]
        for profile in self.profiles:
            args += ["--profile", profile]
        return args


async def read_project_file(session, sftp, path, *, optional=False):
    try:
        async with asyncio.timeout(session.policy.read_timeout_seconds):
            attrs = await sftp.stat(path, follow_symlinks=False)
            if attrs.permissions is None or not stat.S_ISREG(attrs.permissions):
                raise SSHOperationError("compose", "regular_file_required")
            if attrs.size is None or attrs.size > MAX_FILE_BYTES:
                raise SSHOperationError("compose", "file_limit_exceeded")
            async with sftp.open(path, "rb") as handle:
                content = await handle.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise SSHOperationError("compose", "file_limit_exceeded")
            return content.decode("utf-8")
    except asyncssh.SFTPNoSuchFile:
        if optional:
            return None
        raise SSHOperationError("compose", "configured_file_missing") from None
    except UnicodeError:
        raise SSHOperationError("compose", "utf8_file_required") from None


def load_yaml(text):
    try:
        # Reject aliases before construction: avoids cycles and alias amplification,
        # and does not pretend that inherited nodes have safe edit locations.
        if any(
            isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
            for t in yaml.scan(text)
        ):
            raise SSHOperationError("compose", "yaml_anchors_require_explicit_override")

        class UniqueLoader(yaml.SafeLoader):
            def construct_mapping(self, node, deep=False):
                seen = set()
                for key_node, _ in node.value:
                    key = self.construct_object(key_node, deep=deep)
                    if not isinstance(key, str) or key in seen:
                        raise ValueError("Ambiguous mapping")
                    seen.add(key)
                return super().construct_mapping(node, deep=deep)

        data = yaml.load(text, Loader=UniqueLoader)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (yaml.YAMLError, ValueError, RecursionError):
        raise SSHOperationError("compose", "unsupported_yaml_structure") from None


def dotenv_entries(text):
    """Only simple, unambiguous assignments are eligible for in-place updates."""
    entries = {}
    for line in text.splitlines():
        match = ASSIGNMENT.fullmatch(line.strip())
        if not match:
            continue
        key, raw = match.groups()
        value = raw.strip()
        simple = "$" not in value and "\\" not in value and "#" not in value
        if value.startswith(("'", '"')):
            simple &= len(value) >= 2 and value[-1] == value[0] and value.count(value[0]) == 2
            value = value[1:-1]
        elif "'" in value or '"' in value or any(c.isspace() for c in value):
            simple = False
        entries[key] = {"value": value, "simple": simple and key not in entries}
    return entries


def image_provenance(files, rendered, env_files, process_env):
    """Return a redacted source map, not the full Compose document or environment."""
    raw_services = {}
    for path, text in files.items():
        document = load_yaml(text)
        if any(key in document for key in ("include",)):
            raise SSHOperationError("compose", "includes_require_explicit_configuration")
        services = document.get("services", {})
        if not isinstance(services, dict):
            raise SSHOperationError("compose", "invalid_services")
        if any(isinstance(spec, dict) and "extends" in spec for spec in services.values()):
            raise SSHOperationError("compose", "extends_requires_explicit_configuration")
        for service, spec in services.items():
            if isinstance(spec, dict) and "image" in spec:
                raw_services[service] = (path, spec["image"])
    environments = {path: dotenv_entries(text) for path, text in env_files.items()}
    rendered_services = rendered.get("services", {})
    if not isinstance(rendered_services, dict):
        raise SSHOperationError("compose", "invalid_rendered_services")
    result = []
    for service, spec in rendered_services.items():
        if not isinstance(spec, dict):
            continue
        image = spec.get("image")
        if image is not None and (
            not isinstance(image, str) or len(image) > 2048 or any(c.isspace() for c in image)
        ):
            raise SSHOperationError("compose", "invalid_rendered_image")
        path, expression = raw_services.get(service, (None, None))
        row = {
            "service": service,
            "image": image,
            "expression": expression,
            "compose_file": path,
            "source": "unresolved",
            "variable": None,
            "write_file": None,
            "safe_to_edit": False,
            "warnings": [],
        }
        if not image or not isinstance(expression, str):
            row["warnings"].append("image_not_explicit_or_build_only")
        elif "$" not in expression:
            row.update(source="compose", write_file=path, safe_to_edit=expression == image)
        else:
            matches = list(VARIABLE.finditer(expression))
            if len(matches) != 1 or "$" in VARIABLE.sub("", expression):
                row["warnings"].append("complex_image_expression")
            else:
                match = matches[0]
                variable = match[1] or match[4]
                row["variable"] = variable
                value = None
                origin = None
                simple = False
                for env_path, entries in environments.items():
                    if variable in entries:
                        origin = env_path
                        value = entries[variable]["value"]
                        simple = entries[variable]["simple"]
                if variable in process_env:
                    value = process_env[variable]
                    row["source"] = "process_environment"
                    row["warnings"].append("external_environment_requires_managed_strategy")
                elif origin:
                    row.update(source="env_file", write_file=origin)
                else:
                    row["source"] = "default_or_unresolved"
                    row["warnings"].append("no_persistent_variable_source")
                if match[2] == ":-" and not value or match[2] == "-" and value is None:
                    value = match[3]
                    simple = False
                    row["warnings"].append("default_value_in_use")
                resolved = expression[: match.start()] + (value or "") + expression[match.end() :]
                # Shared use anywhere (including container environment and paths)
                # requires an explicit override instead of mutating the variable.
                uses = sum(
                    sum(1 for m in VARIABLE.finditer(text) if (m[1] or m[4]) == variable)
                    for text in files.values()
                )
                if uses != 1:
                    row["warnings"].append("shared_variable")
                row["safe_to_edit"] = (
                    row["source"] == "env_file" and simple and uses == 1 and resolved == image
                )
                if resolved != image:
                    row["warnings"].append("provider_resolution_differs")
        result.append(row)
    return result


def compose_tool_choices(tools, engine=None):
    """Keep explicit entrypoints usable, but count verified wrappers only once."""
    available = {item["tool"]: item for item in tools if item["available"]}
    return [
        name
        for name, item in available.items()
        if (engine is None or item["engine"] == engine)
        and not (
            item.get("alias_of") in available
            and available[item["alias_of"]]["engine"] == item["engine"]
        )
    ]


async def _identify_podman_wrappers(session, tools, versions):
    """Verify the delegated executable, not merely matching version numbers.

    Podman compose is a wrapper, including when reached through podman-docker.
    A different/custom provider or unverified path remains a separate choice.
    Raw probe output and executable paths never enter the public response.
    """
    available = {item["tool"]: item for item in tools if item["available"]}
    if "podman-compose" not in available:
        return
    resolved = None
    for name in ("podman_compose", "docker_compose"):
        if name not in available:
            continue
        try:
            version = versions[name]
            pattern = r"Executing external compose provider\s+[\"']([^\"'\r\n]+)[\"']"
            match = re.search(pattern, version.stderr)
            if not match and re.search(r"(?im)^\s*podman-compose\s+version\s+\S+", version.stdout):
                # This environment flag enables the wrapper's provider diagnostic only
                # for this read-only probe, even when warnings are normally disabled.
                version = await session.run(
                    ["env", "PODMAN_COMPOSE_WARNING_LOGS=true", *TOOLS[name][0], "version"]
                )
                if version.exit_status != 0:
                    continue
                match = re.search(pattern, version.stderr)
            if not match or not match[1].startswith("/"):
                continue
            if resolved is None:
                path = await session.run(["sh", "-c", "command -v podman-compose"])
                resolved = path.stdout.strip() if path.exit_status == 0 else ""
            if not resolved.startswith("/") or "\n" in resolved:
                continue
            same = resolved == match[1]
            if not same:
                paths = await session.run(["readlink", "-f", "--", resolved, match[1]])
                canonical = paths.stdout.splitlines()
                same = (
                    paths.exit_status == 0
                    and len(canonical) == 2
                    and canonical[0].startswith("/")
                    and canonical[0] == canonical[1]
                )
            if same:
                available[name]["alias_of"] = "podman-compose"
                available[name]["engine"] = "podman"
        except SSHOperationError:
            # Failed identity verification must never collapse independent providers.
            continue


async def probe_compose_tools(session):
    results = []
    engines = {}
    versions = {}
    for name, (command, engine) in TOOLS.items():
        try:
            version = await session.run([*command, "version"])
            versions[name] = version
            if version.exit_status == 0 and engine not in engines:
                engines[engine] = (await session.run([engine, "info"])).exit_status == 0
            available = version.exit_status == 0 and engines.get(engine, False)
            results.append(
                {
                    "tool": name,
                    "available": available,
                    "engine": engine,
                    "reason": None if available else "tool_or_engine_unavailable",
                }
            )
        except SSHOperationError as exc:
            results.append(
                {"tool": name, "available": False, "engine": engine, "reason": exc.reason}
            )
    await _identify_podman_wrappers(session, results, versions)
    return results


async def analyze_compose(storage, connection, project: SSHComposeProject):
    async with open_ssh_session(storage, connection) as session:
        tools = await probe_compose_tools(session)
        available = [item["tool"] for item in tools if item["available"]]
        tool = project.tool
        if tool == "auto":
            choices = compose_tool_choices(tools)
            if len(choices) != 1:
                return {
                    "tools": tools,
                    "selected_tool": None,
                    "requires_tool_selection": True,
                    "services": [],
                    "read_only": True,
                }
            tool = choices[0]
        elif tool not in available:
            raise SSHOperationError("compose", "selected_tool_or_engine_unavailable")
        files, env_files = {}, {}
        async with session.sftp() as sftp:
            for path in project.config_files:
                absolute = project.path(path)
                files[absolute] = await read_project_file(session, sftp, absolute)
            for path in project.env_files:
                absolute = project.path(path)
                env_files[absolute] = await read_project_file(session, sftp, absolute)
            if not project.env_files:
                # Compose may consider both PWD and project-directory .env files.
                # Multiple candidates are ambiguous: mark them, never guess precedence.
                candidates = list(
                    dict.fromkeys(
                        [
                            project.path(".env"),
                            posixpath.join(
                                posixpath.dirname(project.path(project.config_files[0])), ".env"
                            ),
                        ]
                    )
                )
                for path in candidates:
                    text = await read_project_file(session, sftp, path, optional=True)
                    if text is not None:
                        env_files[path] = text
        # Detect unsupported source constructs before asking Compose to follow them.
        for content in files.values():
            data = await asyncio.to_thread(load_yaml, content)
            services = data.get("services", {})
            if not isinstance(services, dict):
                raise SSHOperationError("compose", "invalid_services")
            if "include" in data or any(
                isinstance(s, dict) and "extends" in s for s in services.values()
            ):
                raise SSHOperationError("compose", "includes_require_explicit_configuration")
        rendered_result = await session.run(
            [*project.argv(tool), "config"], cwd=project.working_dir
        )
        if rendered_result.exit_status != 0:
            raise SSHOperationError("compose", "config_failed_check_remote_project")
        rendered = await asyncio.to_thread(load_yaml, rendered_result.stdout)
        env = await session.run(["env", "-0"], cwd=project.working_dir)
        if env.exit_status != 0:
            raise SSHOperationError("compose", "environment_detection_unavailable")
        process_env = dict(
            entry.split("=", 1) for entry in env.stdout.split("\x00") if "=" in entry
        )
        services = await asyncio.to_thread(
            image_provenance, files, rendered, env_files, process_env
        )
        if (not project.env_files and len(env_files) > 1) or any(
            k in process_env for k in ("COMPOSE_ENV_FILES", "COMPOSE_DISABLE_ENV_FILE")
        ):
            for row in services:
                row["safe_to_edit"] = False
                row["warnings"].append("implicit_environment_requires_explicit_configuration")
        return {
            "tools": tools,
            "selected_tool": tool,
            "requires_tool_selection": False,
            "services": services,
            "files": [
                {"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                for path, content in {**files, **env_files}.items()
            ],
            "read_only": True,
        }
