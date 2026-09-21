import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import ssh_compose_ownership as service
from releasetracker.services.ssh_compose_plan import SSHComposeTarget
from releasetracker.services.ssh_transport import SSHCommandResult, SSHOperationError
from releasetracker.storage import sqlite_compose_ownership as claims
from test_ssh_executor import setup


def proof(config, key="runtime"):
    return {
        "runtime_key": key,
        "project": config.target_ref["project"],
        "working_dir": config.target_ref["working_dir"],
    }


def opened_identity(monkeypatch, identify):
    @asynccontextmanager
    async def opened(*args):
        yield SimpleNamespace()

    monkeypatch.setattr(service, "open_ssh_session", opened)
    monkeypatch.setattr(service, "identify", identify)


@pytest.mark.asyncio
async def test_unique_claim_includes_disabled_and_ignores_directory_and_entrypoint(storage):
    config = await setup(storage)
    config.enabled = False
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    duplicate = config.model_copy(deep=True, update={"id": None, "name": "duplicate"})
    duplicate.target_ref["working_dir"] = "/another-directory"
    duplicate.target_ref["tool"] = "docker-compose"
    duplicate._ssh_ownership = proof(duplicate)
    with pytest.raises(claims.ComposeOwnershipConflict, match="already managed"):
        await storage.create_executor_config(duplicate)
    assert await storage.get_executor_config_by_name("duplicate") is None
    assert len(await storage.get_all_executor_configs()) == 1
    assert await storage.delete_executor_config(config.id)
    duplicate.id = await storage.create_executor_config(duplicate)
    assert (await claims.get(storage, duplicate.id))["runtime_key"] == "runtime"


@pytest.mark.asyncio
async def test_concurrent_creation_has_one_owner_and_rolls_back_loser(storage):
    original = await setup(storage)

    async def create(name):
        config = original.model_copy(deep=True, update={"name": name})
        config._ssh_ownership = proof(config)
        try:
            return await storage.create_executor_config(config)
        except claims.ComposeOwnershipConflict:
            return None

    results = await asyncio.gather(create("one"), create("two"))
    assert sum(value is not None for value in results) == 1
    assert len(await storage.get_all_executor_configs()) == 1


@pytest.mark.asyncio
async def test_same_name_in_different_runtimes_and_different_projects_are_allowed(storage):
    config = await setup(storage)
    config._ssh_ownership = proof(config)
    await storage.create_executor_config(config)
    other = config.model_copy(deep=True, update={"name": "other-runtime"})
    other._ssh_ownership = proof(other, "rootless-another-user")
    await storage.create_executor_config(other)
    other = config.model_copy(deep=True, update={"name": "other-project"})
    other.target_ref["project"] = "second"
    other._ssh_ownership = proof(other)
    await storage.create_executor_config(other)
    assert len(await storage.get_all_executor_configs()) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "project",
        "working_dir",
        "config_files",
        "env_files",
        "profiles",
        "tool",
        "discovery_id",
        "runtime_connection_id",
        "runtime_type",
    ],
)
async def test_update_cannot_retarget_or_release_ownership(storage, change):
    config = await setup(storage)
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    edited = config.model_copy(deep=True)
    target_changes = {
        "project": "other",
        "working_dir": "/other",
        "config_files": ["other.yml"],
        "env_files": ["other.env"],
        "profiles": ["other"],
        "tool": "docker-compose",
        "discovery_id": "0123456789abcdefabcd",
    }
    if change in target_changes:
        edited.target_ref[change] = target_changes[change]
    else:
        setattr(edited, change, 900 if change == "runtime_connection_id" else "docker")
    with pytest.raises(claims.ComposeOwnershipConflict, match="fixed"):
        await storage.update_executor_config(config.id, edited)
    assert (await storage.get_executor_config(config.id)).target_ref == config.target_ref
    assert (await claims.get(storage, config.id))["runtime_key"] == "runtime"


@pytest.mark.asyncio
async def test_unverified_configuration_cannot_execute(storage):
    config = await setup(storage)
    config.id = await storage.create_executor_config(config)
    with pytest.raises(claims.ComposeOwnershipConflict, match="requires verification"):
        await claims.assert_owner(storage, config, proof(config))
    config._ssh_ownership = proof(config)
    await storage.update_executor_config(config.id, config)
    await claims.assert_owner(storage, config, proof(config))
    with pytest.raises(claims.ComposeOwnershipConflict, match="identity changed"):
        await claims.assert_owner(storage, config, proof(config, "another-runtime"))
    altered = config.model_copy(deep=True)
    altered.target_ref["config_files"] = ["other.yml"]
    with pytest.raises(claims.ComposeOwnershipConflict, match="configuration changed"):
        await claims.assert_owner(storage, altered, proof(config))


@pytest.mark.asyncio
async def test_legacy_conflict_is_not_assigned_to_first_editor(storage, monkeypatch):
    config = await setup(storage)
    config.id = await storage.create_executor_config(config)
    other = config.model_copy(deep=True, update={"id": None, "name": "legacy-duplicate"})
    other.id = await storage.create_executor_config(other)
    identify = AsyncMock(return_value=proof(config))
    opened_identity(monkeypatch, identify)
    connection = await storage.get_runtime_connection(config.runtime_connection_id)
    with pytest.raises(claims.ComposeOwnershipConflict, match="Legacy Compose project conflict"):
        await service.prepare(storage, connection, config, config)
    assert (await claims.get(storage, config.id))["runtime_key"] is None
    assert (await claims.get(storage, other.id))["runtime_key"] is None
    # Different engines with the same project name can each be explicitly confirmed.
    identify.side_effect = [proof(config), proof(other, "different-runtime")]
    await service.prepare(storage, connection, config, config)
    await storage.update_executor_config(config.id, config)
    assert (await claims.get(storage, config.id))["runtime_key"] == "runtime"


@pytest.mark.asyncio
async def test_api_returns_409_and_does_not_trust_client_identity(
    storage, authed_client, monkeypatch
):
    from releasetracker.services import ssh_compose

    config = await setup(storage)
    monkeypatch.setattr(
        ssh_compose,
        "analyze_compose",
        AsyncMock(return_value={"services": [{"service": "web", "safe_to_edit": True}]}),
    )
    opened_identity(monkeypatch, AsyncMock(return_value=proof(config)))
    payload = config.model_dump(mode="json")
    payload["_ssh_ownership"] = {"runtime_key": "forged"}
    response = authed_client.post("/api/executors", json=payload)
    assert response.status_code == 200, response.text
    eid = response.json()["id"]
    assert (await claims.get(storage, eid))["runtime_key"] == "runtime"
    payload["name"] = "duplicate"
    response = authed_client.post("/api/executors", json=payload)
    assert response.status_code == 409, response.text
    payload["name"] = config.name
    payload["target_ref"]["project"] = "different"
    assert authed_client.put(f"/api/executors/{eid}", json=payload).status_code == 409


@pytest.mark.asyncio
async def test_discovery_shows_owner_even_when_disabled(storage):
    config = await setup(storage)
    config.enabled = False
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    result = {
        "items": [{"engine": "docker", "project": "app"}, {"engine": "podman", "project": "app"}]
    }
    result = await service.annotate(storage, result, {"docker": "runtime"})
    assert result["items"][0]["owner"] == {"executor_id": config.id, "name": config.name}
    assert result["items"][1]["owner"] is None
    assert not result["items"][1]["ownership_verified"]


@pytest.mark.asyncio
async def test_migration_preserves_existing_duplicates_as_unverified(storage):
    from pathlib import Path
    from releasetracker.storage.sqlite_compose_status import public_status

    config = await setup(storage)
    config.id = await storage.create_executor_config(config)
    duplicate = config.model_copy(deep=True, update={"id": None, "name": "old-duplicate"})
    duplicate.id = await storage.create_executor_config(duplicate)
    db = await storage._get_connection()
    await db.execute("DROP TABLE ssh_compose_ownership")
    migration = Path("dbmate/migrations/20260918000001_ssh_compose_ownership.sql").read_text()
    await db.executescript(migration.split("-- migrate:down")[0].split("-- migrate:up")[1])
    assert len(await storage.get_all_executor_configs()) == 2
    assert (await claims.get(storage, config.id))["runtime_key"] is None
    assert (await claims.get(storage, duplicate.id))["runtime_key"] is None
    assert await public_status(storage, config) == "conflict"


@pytest.mark.asyncio
async def test_multiple_files_and_services_stay_in_one_project(storage):
    from releasetracker.config import ExecutorServiceBinding

    config = await setup(storage)
    config.target_ref["config_files"] = ["compose.yml", "prod.yml"]
    config.service_bindings.append(
        ExecutorServiceBinding(
            service="worker", tracker_source_id=config.tracker_source_id, channel_name="stable"
        )
    )
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    saved = await storage.get_executor_config(config.id)
    assert len(saved.service_bindings) == 2 and len(saved.target_ref["config_files"]) == 2
    assert len(await claims.occupants(storage, "runtime", "app")) == 1


@pytest.mark.asyncio
async def test_locked_snapshot_blocks_delete_update_and_generic_unlock(storage, authed_client):
    from releasetracker.services.ssh_compose_snapshot import save_snapshot
    from releasetracker.storage.sqlite_compose_status import public_status

    config = await setup(storage)
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    sid = await save_snapshot(storage, config.id, None, {"changes": []})
    assert not await storage.delete_executor_config(config.id)
    assert await public_status(storage, config) == "recovery_required"
    assert (
        authed_client.post(f"/api/executors/{config.id}/snapshots/{sid}/unlock").status_code == 409
    )
    with pytest.raises(claims.ComposeOwnershipConflict, match="requires recovery"):
        await claims.assert_owner(storage, config, proof(config))
    await claims.assert_owner(storage, config, proof(config), recovery=True)
    with pytest.raises(claims.ComposeOwnershipConflict, match="requires recovery"):
        await storage.update_executor_config(config.id, config)


@pytest.mark.asyncio
async def test_legacy_snapshot_allows_identity_confirmation_but_not_config_changes(storage):
    from releasetracker.services.ssh_compose_snapshot import save_snapshot

    config = await setup(storage)
    config.id = await storage.create_executor_config(config)
    await save_snapshot(storage, config.id, None, {"changes": []})
    config._ssh_ownership = proof(config)
    edited = config.model_copy(deep=True)
    edited.target_ref["config_files"] = ["other.yml"]
    with pytest.raises(claims.ComposeOwnershipConflict, match="fixed"):
        await storage.update_executor_config(config.id, edited)
    await storage.update_executor_config(config.id, config)
    assert (await claims.get(storage, config.id))["runtime_key"] == "runtime"


@pytest.mark.asyncio
async def test_same_runtime_over_another_ssh_connection_cannot_claim(storage, monkeypatch):
    from test_ssh_connections import config as connection_config

    owner = await setup(storage)
    owner._ssh_ownership = proof(owner)
    owner.id = await storage.create_executor_config(owner)
    cid = (await storage.get_runtime_connection(owner.runtime_connection_id)).credential_id
    connection = connection_config(cid).model_copy(update={"name": "another-route"})
    connection.id = await storage.create_runtime_connection(connection)
    other = owner.model_copy(
        deep=True, update={"id": None, "name": "other", "runtime_connection_id": connection.id}
    )
    opened_identity(monkeypatch, AsyncMock(return_value=proof(other)))
    with pytest.raises(claims.ComposeOwnershipConflict, match="already managed"):
        await service.prepare(storage, connection, other)


@pytest.mark.asyncio
async def test_discovery_reserves_unverified_legacy_project(storage):
    config = await setup(storage)
    config.id = await storage.create_executor_config(config)
    result = await service.annotate(
        storage,
        {"items": [{"engine": "docker", "project": "app"}]},
        {"docker": "runtime"},
        config.runtime_connection_id,
    )
    assert result["items"][0]["owner"]["executor_id"] == config.id


@pytest.mark.asyncio
async def test_execute_refuses_changed_runtime_before_remote_mutation(storage, monkeypatch):
    from releasetracker.services import ssh_compose_deploy

    config = await setup(storage)
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    connection = await storage.get_runtime_connection(config.runtime_connection_id)
    target = SSHComposeTarget.model_validate(config.target_ref)
    session = SimpleNamespace(run=AsyncMock(), sftp=AsyncMock())

    @asynccontextmanager
    async def opened(*args):
        yield session

    monkeypatch.setattr(ssh_compose_deploy, "open_ssh_session", opened)
    monkeypatch.setattr(service, "identify", AsyncMock(return_value=proof(config, "other-daemon")))
    save = AsyncMock()
    with pytest.raises(claims.ComposeOwnershipConflict, match="identity changed"):
        await ssh_compose_deploy.execute_update(
            storage, connection, target, {"web": "app:2"}, save, executor=config
        )
    save.assert_not_awaited()
    session.run.assert_not_awaited()
    session.sftp.assert_not_called()


def session_for(info, machine="a" * 32):
    return SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                SSHCommandResult(0, json.dumps(info), ""),
                SSHCommandResult(0, machine, ""),
            ]
        )
    )


@pytest.mark.asyncio
async def test_runtime_identity_deduplicates_routes_and_podman_wrapper():
    first = await service.runtime_identity(session_for({"ID": "daemon-unique"}), "docker")
    second = await service.runtime_identity(session_for({"ID": "daemon-unique"}), "docker")
    assert first == second
    info = {
        "host": {"serviceIsRemote": False},
        "store": {"graphRoot": "/home/alice/.local/share/containers/storage"},
    }
    podman = await service.runtime_identity(session_for(info), "podman")
    shim = await service.runtime_identity(session_for(info), "docker")
    assert podman == shim and podman != first
    info["store"]["graphRoot"] = "/home/bob/.local/share/containers/storage"
    assert await service.runtime_identity(session_for(info), "podman") != podman


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info",
    [
        {},
        {"ID": ""},
        {"host": {"serviceIsRemote": True}, "store": {"graphRoot": "/store"}},
        {"host": {}, "store": {"graphRoot": "/store"}},
    ],
)
async def test_ambiguous_runtime_identity_fails_closed(info):
    with pytest.raises(SSHOperationError, match="runtime_identity_unavailable"):
        await service.runtime_identity(session_for(info), "docker")


@pytest.mark.asyncio
async def test_verified_directory_and_identity_checked_in_execution_session(storage, monkeypatch):
    config = await setup(storage)
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    target = SSHComposeTarget.model_validate(config.target_ref)
    monkeypatch.setattr(
        service,
        "identify",
        AsyncMock(return_value={**proof(config), "working_dir": "/different-realpath"}),
    )
    with pytest.raises(claims.ComposeOwnershipConflict, match="identity changed"):
        await service.verify_session(storage, config, SimpleNamespace(), target)


@pytest.mark.asyncio
async def test_delete_and_edit_blocked_for_queued_ssh_project(storage):
    from releasetracker.models import ExecutorRunHistory
    from datetime import datetime

    config = await setup(storage)
    config._ssh_ownership = proof(config)
    config.id = await storage.create_executor_config(config)
    await storage.create_executor_run(
        ExecutorRunHistory(executor_id=config.id, status="queued", started_at=datetime.now())
    )
    assert not await storage.delete_executor_config(config.id)
    with pytest.raises(claims.ComposeOwnershipConflict, match="running or requires recovery"):
        await storage.update_executor_config(config.id, config)
