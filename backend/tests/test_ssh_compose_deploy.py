import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from releasetracker.services import ssh_compose_deploy as deploy
from releasetracker.services.ssh_compose_plan import SSHComposeTarget
from releasetracker.services.ssh_transport import SSHCommandResult, SSHOperationError, SSHSession
from test_ssh_connections import credential, config, server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "deferred",
        "snapshot_failed",
        "conflict",
        "up_failed",
        "already_current",
        "non_image_change",
        "unsupported_flags",
    ],
)
async def test_controlled_update_uses_real_sftp_but_no_real_container_commands(
    storage, tmp_path, monkeypatch, scenario
):
    from releasetracker.services import ssh_compose_ownership

    # File/deploy tests isolate ownership; dedicated ownership tests use real storage and probes.
    monkeypatch.setattr(ssh_compose_ownership, "verify_session", AsyncMock())
    root = tmp_path / "app"
    root.mkdir()
    original = (
        "services:\n  web:\n    image: app:1\n    environment:\n      PASSWORD: do-not-leak\n"
    )
    (root / "compose.yml").write_text(original)
    (root / "compose.yml").chmod(0o640)
    calls, snapshots = [], []
    running = "sha256:new" if scenario == "already_current" else "sha256:old"

    async def run(self, argv, **kwargs):
        nonlocal running
        calls.append(argv)
        output = ""
        if argv[-1] == "--help":
            output = (
                "" if scenario == "unsupported_flags" else "--no-deps --no-build --force-recreate"
            )
        elif argv[-1] == "config":
            files = [argv[i + 1] for i, p in enumerate(argv) if p == "-f"]
            doc = yaml.safe_load((tmp_path / files[0].lstrip("/")).read_text())
            for f in files[1:]:
                override = yaml.safe_load((tmp_path / f.lstrip("/")).read_text())
                for service, spec in override["services"].items():
                    doc["services"][service].update(spec)
            if scenario == "non_image_change" and any(f.endswith(".tmp") for f in files):
                doc["services"]["web"]["environment"]["PASSWORD"] = "unexpected"
            output = yaml.safe_dump(doc)
        elif argv == ["env", "-0"]:
            output = "SECRET=not-returned\0"
        elif argv[:2] == ["docker", "pull"]:
            if scenario == "conflict":
                (root / "compose.yml").write_text(original + "# external update\n")
        elif argv[:3] == ["docker", "image", "inspect"]:
            output = json.dumps([{"Id": "sha256:new"}])
        elif "up" in argv:
            assert snapshots, "snapshot must precede deployment"
            assert "--no-deps" in argv and "--no-build" in argv
            if scenario == "up_failed":
                return SSHCommandResult(1, "", "secret must not leak")
            running = "sha256:new"
        elif "ps" in argv:
            assert argv[:2] == ["docker", "ps"]
            assert any(arg.endswith(".compose.project=app") for arg in argv)
            assert "label=com.docker.compose.service=web" in argv
            output = "abc123def456\n"
        elif argv[:2] == ["docker", "inspect"]:
            output = json.dumps(
                [{"Image": running, "State": {"Running": True, "Health": {"Status": "healthy"}}}]
            )
        else:
            pytest.fail(f"unexpected command: {argv}")
        return SSHCommandResult(0, output, "")

    async def save(payload):
        if scenario == "snapshot_failed":
            raise RuntimeError("disk full with sensitive info")
        snapshots.append(payload)
        assert (root / "compose.yml").read_text() == original
        return 42

    monkeypatch.setattr(SSHSession, "run", run)
    cid = await credential(storage)
    async with server(tmp_path) as (port, host_key):
        connection = config(cid, port=port, host_key=host_key)
        target = SSHComposeTarget(
            working_dir="/app", project="app", config_files=["compose.yml"], tool="docker_compose"
        )
        images = {"web": "app:1" if scenario == "already_current" else "app:2"}
        if scenario in {"success", "deferred", "already_current"}:
            result = await deploy.execute_update(
                storage,
                connection,
                target,
                images,
                save,
                executor=SimpleNamespace(id=1),
                defer_verification=scenario == "deferred",
            )
            assert result["status"] == ("skipped" if scenario == "already_current" else "success")
            if scenario == "deferred":
                assert result["readiness_context"]["expected"] == {"web": "sha256:new"}
                assert result["readiness_context"]["counts"] == {"web": 1}
                assert not any("ps" in call for call in calls)
            assert "do-not-leak" not in str(result)
            assert not list(root.glob("*.lock"))
            assert (root / "compose.yml").stat().st_mode & 0o777 == 0o640
        else:
            with pytest.raises(SSHOperationError) as exc:
                await deploy.execute_update(
                    storage, connection, target, images, save, executor=SimpleNamespace(id=1)
                )
            assert "sensitive" not in str(exc.value) and "secret must not leak" not in str(
                exc.value
            )
            if scenario == "up_failed":
                assert list(root.glob("*.lock"))
                assert "app:2" in (root / "compose.yml").read_text()
                from releasetracker.services import ssh_compose_recovery as recovery

                monkeypatch.setattr(recovery, "load_snapshot", AsyncMock(return_value=snapshots[0]))
                monkeypatch.setattr(
                    storage, "get_runtime_connection", AsyncMock(return_value=connection)
                )
                monkeypatch.setattr(storage, "set_executor_snapshot_locked", AsyncMock())
                executor = SimpleNamespace(
                    id=1, runtime_connection_id=connection.id, target_ref=target.model_dump()
                )
                (root / "compose.yml").write_text(original + "# outside edit")
                with pytest.raises(SSHOperationError, match="file_changed"):
                    await recovery.recover_project(storage, executor, 42, "restore_files")
                (root / "compose.yml").write_text(original.replace("app:1", '"app:2"'))
                restored = await recovery.recover_project(storage, executor, 42, "restore_files")
                assert restored["lock_retained"] and not restored["containers_restored"]
                assert (root / "compose.yml").read_text() == original
                with pytest.raises(SSHOperationError, match="containers_do_not_match"):
                    await recovery.recover_project(storage, executor, 42, "verify_and_unlock")
                assert list(root.glob("*.lock"))
                running = "sha256:new"  # Operator explicitly reconciled the remote runtime.
                verified = await recovery.recover_project(
                    storage, executor, 42, "verify_and_unlock"
                )
                assert not verified["lock_retained"]
                assert not list(root.glob("*.lock"))
                # A restart after remote unlock but before local commit can safely retry verification.
                verified_again = await recovery.recover_project(
                    storage, executor, 42, "verify_and_unlock"
                )
                assert not verified_again["lock_retained"]
            else:
                assert not list(root.glob("*.lock"))
                assert not any("up" in call and "--help" not in call for call in calls)
                if scenario != "conflict":
                    assert (root / "compose.yml").read_text() == original
        assert not list(root.glob("*.tmp"))


@pytest.mark.asyncio
async def test_encrypted_snapshot_roundtrip_rotation_and_redaction(storage, system_key_manager):
    from test_executor_snapshots_history import _create_executor
    from releasetracker.services.ssh_compose_snapshot import save_snapshot, load_snapshot
    from releasetracker.services.snapshot_service import SnapshotService
    from releasetracker.services.system_keys import rotate_encryption_key

    eid = await _create_executor(storage)
    payload = {"changes": [{"before": "PASSWORD=super-secret-value"}]}
    sid = await save_snapshot(storage, eid, None, payload)
    raw = await storage.get_executor_snapshot_by_id(eid, sid)
    assert "super-secret-value" not in str(raw.snapshot_data)
    assert await load_snapshot(storage, eid, sid) == payload
    detail = await SnapshotService(storage).get_snapshot(eid, sid, runtime_type="ssh")
    assert detail.snapshot_data["secret"] == "***REDACTED***"
    await rotate_encryption_key(storage, system_key_manager, generate=True)
    assert await load_snapshot(storage, eid, sid) == payload
    after = await storage.get_executor_snapshot_by_id(eid, sid)
    assert raw.snapshot_sha256 != after.snapshot_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replicas,health,running,expected",
    [
        (1, "healthy", True, True),
        (2, "healthy", True, False),
        (1, "starting", True, False),
        (1, "unhealthy", True, False),
        (1, None, False, False),
    ],
)
async def test_verification_requires_all_replicas_running_and_healthy(
    monkeypatch, replicas, health, running, expected
):
    calls = []

    async def command(session, argv, target, **kwargs):
        calls.append(argv)
        if "ps" in argv:
            assert argv[:2] == ["docker", "ps"]
            assert "--all" in argv and "--no-trunc" in argv
            assert any(arg.endswith(".compose.project=app") for arg in argv)
            assert "label=com.docker.compose.service=web" in argv
            return "abc123def456"
        return json.dumps(
            [
                {
                    "Image": "sha256:expected",
                    "State": {"Running": running, "Health": {"Status": health}},
                }
            ]
        )

    monkeypatch.setattr(deploy, "_command", command)
    target = SSHComposeTarget(
        working_dir="/app", project="app", config_files=["compose.yml"], tool="docker_compose"
    )
    assert (
        await deploy.verify_running(
            None, target, ["docker", "compose"], {"web": "sha256:expected"}, {"web": replicas}
        )
        is expected
    )


@pytest.mark.asyncio
async def test_verification_uses_engine_labels_with_legacy_podman_compose(monkeypatch):
    calls = []

    async def command(session, argv, target, **kwargs):
        calls.append(argv)
        if argv[:2] == ["podman", "ps"]:
            return "0123456789ab"
        assert argv[:2] == ["podman", "inspect"]
        return json.dumps(
            [
                {
                    "Image": "sha256:expected",
                    "State": {"Running": True, "Health": {"Status": "healthy"}},
                }
            ]
        )

    monkeypatch.setattr(deploy, "_command", command)
    target = SSHComposeTarget(
        working_dir="/app",
        project="app",
        config_files=["compose.yml"],
        tool="podman-compose",
    )

    assert await deploy.verify_running(
        None, target, ["podman-compose"], {"web": "sha256:expected"}, {"web": 1}
    )
    assert all(call[:2] != ["podman-compose", "ps"] for call in calls)
    assert len([call for call in calls if call[:2] == ["podman", "ps"]]) == 2
