from contextlib import asynccontextmanager
import json
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import ssh_compose_discovery as discovery
from releasetracker.services.ssh_compose import compose_tool_choices, probe_compose_tools
from releasetracker.services.ssh_transport import SSHCommandResult, SSHOperationError


class Session:
    def __init__(
        self,
        *,
        provider="/usr/bin/podman-compose",
        wrapper=True,
        warnings=True,
        shim=False,
        symlink=False,
        identity_failure=False,
    ):
        self.provider = provider
        self.wrapper = wrapper
        self.warnings = warnings
        self.shim = shim
        self.symlink = symlink
        self.identity_failure = identity_failure
        self.calls = []

    async def run(self, argv):
        self.calls.append(argv)
        diagnostic = f'>>>> Executing external compose provider "{self.provider}". Please see podman-compose(1). <<<<\n'
        stdout = "podman-compose version 1.5.0\npodman version 5.4.0\n"
        forced = argv[:2] == ["env", "PODMAN_COMPOSE_WARNING_LOGS=true"]
        command = argv[2:] if forced else argv
        if command[-1] == "version":
            direct = command[0] == "podman-compose"
            wrapper = self.wrapper and (
                command[:2] == ["podman", "compose"]
                or self.shim
                and command[:2] == ["docker", "compose"]
            )
            return SSHCommandResult(
                0 if direct or wrapper else 127,
                stdout if direct or wrapper else "",
                diagnostic if wrapper and (forced or self.warnings) else "",
            )
        if command[-1] == "info":
            return SSHCommandResult(0, "", "")
        if command == ["sh", "-c", "command -v podman-compose"]:
            if self.identity_failure:
                raise SSHOperationError("command", "timeout")
            return SSHCommandResult(0, "/usr/bin/podman-compose\n", "")
        if command[:2] == ["readlink", "-f"]:
            paths = ["/usr/lib/podman-compose.py"] * 2 if self.symlink else command[3:]
            return SSHCommandResult(0, "\n".join(paths), "")
        if command[1] == "ps":
            assert (
                command[0] == "podman"
            ), "docker compatibility wrapper must not duplicate projects"
            return SSHCommandResult(0, "a" * 64, "")
        if command[1] == "inspect":
            return SSHCommandResult(
                0,
                json.dumps(
                    {
                        "com.docker.compose.project": "app",
                        "com.docker.compose.service": "web",
                        "com.docker.compose.project.working_dir": "/app",
                        "com.docker.compose.project.config_files": "/app/compose.yml",
                    }
                ),
                "",
            )
        raise AssertionError(argv)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "warnings,shim,symlink",
    [(True, False, False), (False, False, False), (True, True, False), (False, True, True)],
)
async def test_verified_podman_wrappers_are_one_choice(monkeypatch, warnings, shim, symlink):
    from releasetracker.services import ssh_compose_ownership
    from releasetracker.storage import sqlite_compose_ownership

    monkeypatch.setattr(
        ssh_compose_ownership, "runtime_identity", AsyncMock(return_value="test-runtime")
    )
    monkeypatch.setattr(sqlite_compose_ownership, "occupants", AsyncMock(return_value=[]))
    session = Session(
        warnings=warnings,
        shim=shim,
        symlink=symlink,
        provider="/opt/provider" if symlink else "/usr/bin/podman-compose",
    )

    @asynccontextmanager
    async def opened(*_):
        yield session

    monkeypatch.setattr(discovery, "open_ssh_session", opened)
    result = await discovery.discover_compose_projects(None, None)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["tool"] == "podman-compose" and item["tool_choices"] == ["podman-compose"]
    assert not item["warnings"]
    wrapper = next(t for t in result["tools"] if t["tool"] == "podman_compose")
    assert wrapper["available"] and wrapper["alias_of"] == "podman-compose"
    assert "/usr/bin/podman-compose" not in str(result)
    assert not any("up" in c or "pull" in c for c in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,identity_failure",
    [
        ("/opt/custom/podman-compose", False),
        ("/usr/bin/docker-compose", False),
        ("/usr/bin/podman-compose", True),
    ],
)
async def test_different_or_unverified_providers_are_not_collapsed(provider, identity_failure):
    # All version banners are deliberately identical. Only executable identity counts.
    tools = await probe_compose_tools(Session(provider=provider, identity_failure=identity_failure))
    assert compose_tool_choices(tools, "podman") == ["podman_compose", "podman-compose"]


@pytest.mark.asyncio
async def test_standalone_podman_compose_with_missing_metadata_keeps_tool():
    session = Session(wrapper=False)
    tools = await probe_compose_tools(session)
    item = discovery._candidate("podman", "app", [{"com.docker.compose.project": "app"}], tools)
    assert item["tool"] == "podman-compose"
    assert item["warnings"] == ["incomplete_project_metadata"]
    assert all(c[0] != "sh" for c in session.calls)


def test_unavailable_alias_target_does_not_remove_a_working_wrapper():
    tools = [
        {
            "tool": "podman_compose",
            "engine": "podman",
            "available": True,
            "alias_of": "podman-compose",
        },
        {"tool": "podman-compose", "engine": "podman", "available": False},
    ]
    assert compose_tool_choices(tools, "podman") == ["podman_compose"]
