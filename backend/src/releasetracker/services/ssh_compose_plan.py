"""Conservative, format-preserving SSH Compose image update plans.

Plans contain secrets internally. Only public_summary() may be returned to clients.
No shell, file writes, or network operations occur in this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Literal

import yaml
from pydantic import ConfigDict, field_validator, model_validator

from .ssh_compose import (
    ASSIGNMENT,
    VARIABLE,
    MAX_FILE_BYTES,
    SSHComposeProject,
    image_provenance,
    load_yaml,
    inject_managed_markers,
    extract_compose_service_markers,
)
from .deployment_plan import MARKER_KEYS
from .ssh_transport import SSHOperationError

SERVICE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,2047}\Z")


class SSHComposeTarget(SSHComposeProject):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    mode: Literal["ssh_compose"] = "ssh_compose"
    discovery_id: str | None = None
    write_strategy: Literal["source", "override"] = "source"

    @field_validator("discovery_id")
    @classmethod
    def discovered_project_id(cls, value):
        if value is not None and not re.fullmatch(r"[0-9a-f]{20}", value):
            raise ValueError("Invalid discovered Compose project ID")
        return value

    @field_validator("tool")
    @classmethod
    def fixed_tool(cls, value):
        if value == "auto":
            raise ValueError("Choose and confirm a Compose tool before enabling updates")
        return value

    @model_validator(mode="after")
    def update_paths(self):
        paths = [self.path(p) for p in [*self.config_files, *self.env_files]]
        if len(set(paths)) != len(paths) or self.override_file in paths:
            raise ValueError("Duplicate or reserved Compose input path")
        if any(posixpath.commonpath([self.working_dir, p]) != self.working_dir for p in paths):
            raise ValueError("SSH Compose update inputs must be inside the project directory")
        return self

    @property
    def override_file(self):
        return self.path(f".releasetracker-{self.project}.override.yml")


def fingerprint(content: str | None) -> str | None:
    return hashlib.sha256(content.encode()).hexdigest() if content is not None else None


@dataclass
class FileChange:
    path: str
    before: str | None = field(repr=False)
    after: str = field(repr=False)


@dataclass
class ComposePlan:
    target: SSHComposeTarget
    files: dict[str, str | None] = field(repr=False)
    rendered: dict = field(repr=False)
    targets: dict[str, str]
    changes: list[FileChange] = field(repr=False)
    managed_markers: dict[str, str] = field(default_factory=dict)

    def public_summary(self):
        payload = {
            "tool": self.target.tool,
            "write_strategy": self.target.write_strategy,
            "services": [
                {
                    "service": name,
                    "current_image": self.rendered["services"][name]["image"],
                    "target_image": image,
                }
                for name, image in sorted(self.targets.items())
            ],
            "files": [
                {
                    "path": change.path,
                    "before_sha256": fingerprint(change.before),
                    "after_sha256": fingerprint(change.after),
                }
                for change in self.changes
            ],
        }
        # Include every input hash, even files which aren't edited, in approval identity.
        identity = {
            **payload,
            "target": self.target.model_dump(),
            "inputs": {p: fingerprint(v) for p, v in self.files.items()},
        }
        payload["plan_id"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()
        payload["requires_recreate"] = True  # A floating tag can retain its text.
        return payload

    def validate_rendered(self, rendered: dict):
        expected = copy.deepcopy(self.rendered)
        for service, image in self.targets.items():
            expected["services"][service]["image"] = image
        if self.managed_markers:
            expected = load_yaml(
                inject_managed_markers(
                    yaml.safe_dump(expected), self.managed_markers, service_names=self.targets
                )
            )
        if expected != rendered:
            raise SSHOperationError("plan", "non_image_configuration_change")


def _replace_image(text: str, service: str, image: str) -> str:
    # load_yaml rejects duplicate keys, aliases and unsafe constructors first.
    load_yaml(text)
    node = yaml.compose(text)
    for key in ("services", service, "image"):
        if not isinstance(node, yaml.MappingNode):
            raise SSHOperationError("plan", "image_location_not_scalar")
        node = next((v for k, v in node.value if k.value == key), None)
    if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
        raise SSHOperationError("plan", "image_location_not_scalar")
    return text[: node.start_mark.index] + json.dumps(image) + text[node.end_mark.index :]


def _variable_value(expression: str, target: str) -> str:
    matches = list(VARIABLE.finditer(expression))
    if len(matches) != 1:
        raise SSHOperationError("plan", "ambiguous_variable")
    match = matches[0]
    prefix, suffix = expression[: match.start()], expression[match.end() :]
    if not target.startswith(prefix) or (suffix and not target.endswith(suffix)):
        raise SSHOperationError("plan", "image_requires_override_strategy")
    value = target[len(prefix) : len(target) - len(suffix) if suffix else None]
    # Inserting @sha256 into a tag variable is invalid even if it fits the prefix.
    if prefix.endswith(":") and not re.fullmatch(r"[\w][\w.-]{0,127}", value, re.ASCII):
        raise SSHOperationError("plan", "digest_requires_full_image_or_override")
    if not value or "$" in value or not IMAGE.fullmatch(value):
        raise SSHOperationError("plan", "unsafe_variable_value")
    return value


def _replace_variable(text: str, variable: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    matches = [
        i
        for i, line in enumerate(lines)
        if (m := ASSIGNMENT.fullmatch(line.strip())) and m[1] == variable
    ]
    if len(matches) != 1:
        raise SSHOperationError("plan", "ambiguous_environment_assignment")
    i = matches[0]
    original = lines[i]
    prefix = original[: original.index("=") + 1]
    newline = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
    lines[i] = prefix + value + newline
    return "".join(lines)


def build_plan(
    target: SSHComposeTarget,
    files: dict[str, str],
    rendered: dict,
    env_files: dict[str, str],
    process_env: dict[str, str],
    targets: dict[str, str],
    *,
    override: str | None = None,
    managed_markers: dict[str, str] | None = None,
) -> ComposePlan:
    if not targets or len(targets) > 64:
        raise SSHOperationError("plan", "one_to_64_services_required")
    if any(
        not SERVICE.fullmatch(s) or not isinstance(i, str) or not IMAGE.fullmatch(i)
        for s, i in targets.items()
    ):
        raise SSHOperationError("plan", "invalid_service_or_image")
    rows = {
        row["service"]: row for row in image_provenance(files, rendered, env_files, process_env)
    }
    if any(s not in rows or not rows[s]["image"] for s in targets):
        raise SSHOperationError("plan", "bound_service_image_missing")
    markers = {k: v for k, v in (managed_markers or {}).items() if k in MARKER_KEYS}
    all_files = {**files, **env_files}
    after = dict(all_files)
    if target.write_strategy == "override":
        document = load_yaml(override) if override is not None else {"services": {}}
        if (
            set(document) != {"services"}
            or not isinstance(document["services"], dict)
            or any(
                not isinstance(v, dict)
                or "image" not in v
                or set(v) - {"image", "labels"}
                or not isinstance(v.get("labels", {}), dict)
                or set(v.get("labels", {})) - set(MARKER_KEYS)
                for v in document["services"].values()
            )
        ):
            raise SSHOperationError("plan", "override_not_owned_image_only_file")
        for service, image in targets.items():
            document["services"].setdefault(service, {})["image"] = image
        all_files[target.override_file] = override
        after[target.override_file] = (
            "# Managed by ReleaseTracker; include this file last when running Compose manually.\n"
            + yaml.safe_dump(document, sort_keys=True)
        )
    else:
        if (not target.env_files and len(env_files) > 1) or any(
            k in process_env for k in ("COMPOSE_ENV_FILES", "COMPOSE_DISABLE_ENV_FILE")
        ):
            raise SSHOperationError("plan", "explicit_environment_files_required")
        for service, image in targets.items():
            row = rows[service]
            if not row["safe_to_edit"]:
                raise SSHOperationError("plan", "ambiguous_image_source_choose_override")
            if row["image"] == image:
                continue
            path = row["write_file"]
            if row["source"] == "compose":
                after[path] = _replace_image(after[path], service, image)
            else:
                after[path] = _replace_variable(
                    after[path], row["variable"], _variable_value(row["expression"], image)
                )
    if markers:
        selected = {"services": {s: rendered["services"][s] for s in targets}}
        existing = extract_compose_service_markers(selected)
        needs_markers = any(any(item.get(k) != v for k, v in markers.items()) for item in existing)
        if needs_markers or target.write_strategy == "override":
            # Preserve the single-file transaction boundary. Environment-only updates
            # may proceed once enrolled; first enrollment uses an explicit override.
            path = (
                target.override_file
                if target.write_strategy == "override"
                else target.path(target.config_files[-1])
            )
            if any(p != path and after[p] != all_files.get(p) for p in after):
                raise SSHOperationError("plan", "managed_markers_require_override_strategy")
            after[path] = inject_managed_markers(after[path], markers, service_names=targets)
    changes = [
        FileChange(path, all_files.get(path), text)
        for path, text in after.items()
        if all_files.get(path) != text
    ]
    # One atomic replacement only. No pretence that multi-file replacement is a transaction.
    if len(changes) > 1:
        raise SSHOperationError("plan", "multiple_write_files_choose_override")
    for change in changes:
        if len(change.after.encode()) > MAX_FILE_BYTES:
            raise SSHOperationError("plan", "updated_file_limit_exceeded")
        if posixpath.commonpath([target.working_dir, change.path]) != target.working_dir:
            raise SSHOperationError("plan", "write_outside_project")
    return ComposePlan(target, all_files, rendered, dict(targets), changes, markers)
