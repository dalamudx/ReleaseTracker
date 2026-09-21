from unittest.mock import AsyncMock

import pytest

from releasetracker.services import ssh_compose_discovery as module
from releasetracker.services.ssh_compose_plan import SSHComposeTarget

PROJECT_ID = "0123456789abcdefabcd"
ITEM = {
    "id": PROJECT_ID,
    "engine": "docker",
    "project": "app",
    "working_dir": "/app",
    "config_files": ["compose.yml", "prod.yml"],
    "env_files": ["prod.env"],
    "profiles": ["release"],
    "tool": "docker_compose",
    "tool_choices": ["docker_compose", "docker-compose"],
    "write_strategy": "source",
    "services": ["web"],
    "warnings": [],
}


def target(**changes):
    values = {
        "mode": "ssh_compose",
        "discovery_id": PROJECT_ID,
        "tool": "docker_compose",
        "project": "app",
        "working_dir": "/app",
        "config_files": ["compose.yml", "prod.yml"],
        "env_files": ["prod.env"],
        "profiles": ["release"],
        "write_strategy": "source",
    }
    values.update(changes)
    return SSHComposeTarget(**values)


@pytest.mark.asyncio
async def test_discovered_configuration_is_verified_against_remote_labels(monkeypatch):
    discover = AsyncMock(return_value={"items": [ITEM]})
    monkeypatch.setattr(module, "discover_compose_projects", discover)
    await module.verify_discovered_project(object(), object(), target())
    await module.verify_discovered_project(object(), object(), target(tool="docker-compose"))
    assert discover.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"discovery_id": "fedcba9876543210abcd"},
        {"project": "other"},
        {"working_dir": "/other"},
        {"config_files": ["prod.yml", "compose.yml"]},
        {"env_files": []},
        {"profiles": []},
        {"tool": "podman-compose"},
    ],
)
async def test_discovered_configuration_cannot_be_client_modified(monkeypatch, changes):
    monkeypatch.setattr(
        module, "discover_compose_projects", AsyncMock(return_value={"items": [ITEM]})
    )
    with pytest.raises(ValueError, match="configuration changed"):
        await module.verify_discovered_project(object(), object(), target(**changes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "warning",
    [
        "conflicting_project_metadata",
        "incomplete_project_metadata",
        "incomplete_discovery",
    ],
)
async def test_incomplete_discovery_requires_explicit_manual_mode(monkeypatch, warning):
    monkeypatch.setattr(
        module,
        "discover_compose_projects",
        AsyncMock(return_value={"items": [{**ITEM, "warnings": [warning]}]}),
    )
    with pytest.raises(ValueError, match="configuration changed"):
        await module.verify_discovered_project(object(), object(), target())


@pytest.mark.asyncio
async def test_explicit_manual_configuration_does_not_claim_discovery_identity(monkeypatch):
    discover = AsyncMock()
    monkeypatch.setattr(module, "discover_compose_projects", discover)
    await module.verify_discovered_project(object(), object(), target(discovery_id=None))
    discover.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("absolute_saved", [False, True])
async def test_recreated_containers_keep_the_same_file_identity(monkeypatch, absolute_saved):
    absolute = {
        "config_files": ["/app/compose.yml", "/app/prod.yml"],
        "env_files": ["/app/prod.env"],
    }
    saved = target(**absolute) if absolute_saved else target()
    item = ITEM if absolute_saved else {**ITEM, **absolute}
    monkeypatch.setattr(
        module, "discover_compose_projects", AsyncMock(return_value={"items": [item]})
    )
    before = saved.model_dump()
    await module.verify_discovered_project(object(), object(), saved)
    assert saved.model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"config_files": ["/app/prod.yml", "/app/compose.yml"]},
        {"config_files": ["/other/compose.yml", "/app/prod.yml"]},
        {"env_files": ["/app/other.env"]},
        {"env_files": []},
        {"working_dir": "/other"},
    ],
)
async def test_canonical_comparison_still_blocks_real_drift(monkeypatch, changes):
    item = {
        **ITEM,
        "config_files": ["/app/compose.yml", "/app/prod.yml"],
        "env_files": ["/app/prod.env"],
        **changes,
    }
    monkeypatch.setattr(
        module, "discover_compose_projects", AsyncMock(return_value={"items": [item]})
    )
    with pytest.raises(ValueError, match="configuration changed"):
        await module.verify_discovered_project(object(), object(), target())


def test_discovery_id_must_be_backend_issued_shape():
    with pytest.raises(ValueError, match="discovered Compose project ID"):
        target(discovery_id="client-name")


@pytest.mark.asyncio
async def test_executor_create_reverifies_discovered_configuration(
    authed_client, storage, monkeypatch
):
    from releasetracker.services import ssh_compose, ssh_compose_ownership
    from test_ssh_executor import setup

    config = await setup(storage)
    config.target_ref["discovery_id"] = PROJECT_ID
    verify = AsyncMock()
    monkeypatch.setattr(module, "verify_discovered_project", verify)
    monkeypatch.setattr(ssh_compose_ownership, "prepare", AsyncMock())
    monkeypatch.setattr(
        ssh_compose,
        "analyze_compose",
        AsyncMock(return_value={"services": [{"service": "web", "safe_to_edit": True}]}),
    )
    response = authed_client.post("/api/executors", json=config.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert verify.await_count == 1
    verified = verify.await_args.args[2]
    assert verified.discovery_id == PROJECT_ID
    assert verified.config_files == ["compose.yml"]
