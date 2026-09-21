import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import ssh_compose_discovery as module
from releasetracker.services.ssh_transport import SSHCommandResult

P = module.PREFIX


def labels(project="app", directory="/app", files="/app/compose.yml,/app/prod.yml", **extra):
    return {
        P + "project": project,
        P + "service": "web",
        P + "project.working_dir": directory,
        P + "project.config_files": files,
        P + "project.environment_file": "/app/base.env,/app/prod.env",
        "SECRET": "never-return",
        **extra,
    }


async def discover(monkeypatch, rows, tools=None, fail=False):
    from releasetracker.services import ssh_compose_ownership
    from releasetracker.storage import sqlite_compose_ownership

    monkeypatch.setattr(
        ssh_compose_ownership, "runtime_identity", AsyncMock(return_value="test-runtime")
    )
    monkeypatch.setattr(sqlite_compose_ownership, "occupants", AsyncMock(return_value=[]))
    calls = []
    tools = tools or [
        {"tool": "docker_compose", "available": True, "engine": "docker", "reason": None}
    ]
    monkeypatch.setattr(module, "probe_compose_tools", AsyncMock(return_value=tools))

    class Session:
        async def run(self, argv):
            calls.append(argv)
            if "ps" in argv:
                assert "--all" in argv
                return SSHCommandResult(0, "\n".join(f"{i:012x}" for i in range(len(rows))), "")
            assert "inspect" in argv and argv[3] == "{{json .Config.Labels}}"
            return SSHCommandResult(
                1 if fail else 0,
                "\n".join(json.dumps(rows[int(i, 16)]) for i in argv[4:]),
                "secret-stderr",
            )

    @asynccontextmanager
    async def opened(*_):
        yield Session()

    monkeypatch.setattr(module, "open_ssh_session", opened)
    return await module.discover_compose_projects(None, None), calls


@pytest.mark.asyncio
async def test_discovery_is_read_only_deduplicates_and_preserves_order(monkeypatch):
    result, calls = await discover(monkeypatch, [labels(), labels(**{P + "service": "worker"})])
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["tool"] == "docker_compose"
    assert item["config_files"] == ["/app/compose.yml", "/app/prod.yml"]
    assert item["env_files"] == ["/app/base.env", "/app/prod.env"]
    assert item["services"] == ["web", "worker"]
    assert "never-return" not in str(result)
    assert sum("inspect" in c for c in calls) == 1
    assert all(c[1] in {"ps", "inspect"} for c in calls)


@pytest.mark.asyncio
async def test_engine_isolation_and_multiple_tools_require_selection(monkeypatch):
    tools = [
        {"tool": name, "engine": engine, "available": True}
        for name, engine in [
            ("docker_compose", "docker"),
            ("docker-compose", "docker"),
            ("podman-compose", "podman"),
        ]
    ]
    result, _ = await discover(monkeypatch, [labels()], tools)
    assert len(result["items"]) == 2
    docker, podman = result["items"]
    assert docker["tool"] is None and "choose_compose_tool" in docker["warnings"]
    assert podman["tool"] == "podman-compose"
    assert docker["id"] != podman["id"]


@pytest.mark.asyncio
async def test_missing_and_conflicting_labels_are_not_guessed(monkeypatch):
    result, _ = await discover(
        monkeypatch,
        [{"io.podman.compose.project": "missing"}, labels(), labels(directory="/other")],
    )
    assert all("incomplete_project_metadata" in item["warnings"] for item in result["items"])
    assert result["items"][0]["working_dir"] == ""


@pytest.mark.asyncio
async def test_managed_override_is_not_readded_as_input(monkeypatch):
    result, _ = await discover(
        monkeypatch, [labels(files="/app/compose.yml,/app/.releasetracker-app.override.yml")]
    )
    assert result["items"][0]["write_strategy"] == "override"
    assert result["items"][0]["config_files"] == ["/app/compose.yml"]


@pytest.mark.asyncio
async def test_partial_service_update_accepts_equivalent_relative_labels(monkeypatch):
    result, _ = await discover(
        monkeypatch,
        [
            labels(
                files="compose.yml,./prod.yml",
                **{P + "project.environment_file": "base.env,prod.env"},
            ),
            labels(**{P + "service": "worker"}),
        ],
    )
    item = result["items"][0]
    assert item["warnings"] == []
    assert item["config_files"] == ["/app/compose.yml", "/app/prod.yml"]
    assert item["env_files"] == ["/app/base.env", "/app/prod.env"]
    assert item["services"] == ["web", "worker"]


@pytest.mark.asyncio
async def test_equivalent_path_normalization_does_not_hide_file_order_conflict(monkeypatch):
    result, _ = await discover(monkeypatch, [labels(files="prod.yml,compose.yml"), labels()])
    assert "conflicting_project_metadata" in result["items"][0]["warnings"]


@pytest.mark.asyncio
async def test_invalid_relative_path_is_not_sanitized_into_a_valid_label(monkeypatch):
    result, _ = await discover(monkeypatch, [labels(files="-compose.yml")])
    assert "incomplete_project_metadata" in result["items"][0]["warnings"]


@pytest.mark.asyncio
async def test_discovery_budget_and_partial_failure(monkeypatch):
    result, calls = await discover(monkeypatch, [labels()] * (module.MAX_CONTAINERS + 1))
    assert result["truncated"]
    assert sum(len(c[4:]) for c in calls if "inspect" in c) == module.MAX_CONTAINERS
    result, _ = await discover(monkeypatch, [labels()], fail=True)
    assert not result["items"] and result["warnings"]
    assert "secret-stderr" not in str(result)


@pytest.mark.asyncio
async def test_oneoff_and_unlabelled_containers_are_not_projects(monkeypatch):
    result, _ = await discover(monkeypatch, [{}, labels(**{P + "oneoff": "True"})])
    assert result["items"] == []


@pytest.mark.asyncio
async def test_conflicting_env_files_never_become_implicit_dotenv(monkeypatch):
    result, _ = await discover(
        monkeypatch, [labels(), labels(**{P + "project.environment_file": "/app/other.env"})]
    )
    item = result["items"][0]
    assert item["tool"] == "docker_compose" and not item["working_dir"] and not item["config_files"]
    assert "conflicting_project_metadata" in item["warnings"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {P + "project.environment_file": "invalid\npath"},
        {"io.podman.compose.project": "another-project"},
    ],
)
async def test_invalid_labels_do_not_silently_discard_environment_metadata(monkeypatch, bad):
    result, _ = await discover(monkeypatch, [labels(**bad)])
    assert not result["items"] and result["warnings"]


@pytest.mark.asyncio
async def test_no_available_tool_never_runs_container_commands(monkeypatch):
    result, calls = await discover(
        monkeypatch, [], [{"tool": "docker_compose", "engine": "docker", "available": False}]
    )
    assert result["items"] == [] and calls == []
