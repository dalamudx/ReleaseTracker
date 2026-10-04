import hashlib
from pathlib import Path
import sqlite3
import time

import pytest

from releasetracker.services.online_restore_files import RestoreFiles, atomic_copy
from test_online_restore import application, minimal_factory, open_controller, seed
from releasetracker import cli


@pytest.mark.parametrize("point", ["first_file", "whole_pair", "reloading", "rollback_interrupted"])
async def test_startup_recovers_exact_original_pair_after_switch_interruption(
    storage, system_key_manager, point
):
    await seed(storage, system_key_manager)
    files = RestoreFiles(storage.db_path)
    files.acquire()
    try:
        files.recover()
        files.save_original()
        original_db = files.db.read_bytes()
        original_keys = files.keys.read_bytes()
        receipt = {
            "id": "a" * 32,
            "token_hash": hashlib.sha256(b"recovery-result").hexdigest(),
            "issued_at": time.time(),
            "state": "running",
            "phase": "switching",
            "original_saved": True,
        }
        files.persist(receipt)
        files.db.write_bytes(b"partial replaced db")
        if point != "first_file":
            files.keys.write_bytes(b"partial new key")
        if point == "reloading":
            receipt["phase"] = "reloading"
            files.persist(receipt)
        if point == "rollback_interrupted":
            receipt["phase"] = "rolling_back"
            files.persist(receipt)
            atomic_copy(files.previous / "releases.db", files.db)
    finally:
        files.release()
    restarted = RestoreFiles(storage.db_path)
    restarted.acquire()
    try:
        recovered = restarted.recover()
        assert recovered["state"] == "failed" and recovered["rolled_back"]
        assert (
            restarted.db.read_bytes() == original_db
            and restarted.keys.read_bytes() == original_keys
        )
        assert restarted.recover()["state"] == "failed"
    finally:
        restarted.release()


async def test_corrupt_rollback_pair_fails_closed(storage, system_key_manager):
    await seed(storage, system_key_manager)
    files = RestoreFiles(storage.db_path)
    files.acquire()
    try:
        files.recover()
        files.save_original()
        files.persist({"state": "running", "phase": "switching", "original_saved": True})
        (files.previous / "system-secrets.json").write_bytes(b"corrupt key")
        with pytest.raises(ValueError, match="damaged"):
            files.recover()
        assert files.load_receipt()["state"] == "running"
    finally:
        files.release()


@pytest.mark.parametrize("alternate_name", [False, True])
async def test_single_owner_refuses_second_worker(storage, system_key_manager, alternate_name):
    first = RestoreFiles(storage.db_path)
    second = RestoreFiles(
        Path(storage.db_path).with_name("alternate.db") if alternate_name else storage.db_path
    )
    first.acquire()
    try:
        with pytest.raises(BlockingIOError):
            second.acquire()
    finally:
        first.release()
        second.release()
    second.acquire()
    second.release()


async def test_pre_migration_recovery_restores_pair_before_schema_access(
    storage, system_key_manager, monkeypatch
):
    await seed(storage, system_key_manager)
    files = RestoreFiles(storage.db_path)
    files.acquire()
    try:
        files.recover()
        files.save_original()
        oldkeys = files.keys.read_bytes()
        files.persist({"state": "running", "phase": "switching", "original_saved": True})
        files.db.write_bytes(b"partial database")
        files.keys.write_bytes(b"new keys")
    finally:
        files.release()
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", storage.db_path)
    assert cli.main(["recover-online-restore"]) == 0
    with sqlite3.connect(storage.db_path) as db:
        assert (
            db.execute("SELECT value FROM settings WHERE key='online-marker'").fetchone()[0]
            == "live"
        )
    assert files.keys.read_bytes() == oldkeys


async def test_restore_revokes_unexpanded_admission_and_webhook_parent_intents(
    storage, system_key_manager
):
    from helpers.executor_runtime import save_docker_tracker_config
    from releasetracker.services.restore_safety import quarantine_restored_intents

    await save_docker_tracker_config(
        storage, name="isolated-restore-intent", image="docker.io/library/nginx"
    )
    source = (await storage.get_aggregate_tracker("isolated-restore-intent")).sources[0].id
    db = await storage._get_connection()
    cursor = await db.execute(
        "INSERT INTO tasks(kind,resource_key,dedupe_key,target_label,payload,max_retries,due_at,created_at,updated_at) VALUES ('deploy','executor:1','isolated','nginx','{}',0,0,0,0)"
    )
    cursor = await db.execute(
        "INSERT INTO deployment_plans(task_id,executor_id,target_id,fingerprint,identity_key,evidence_hash,summary,state,reason,created_at,expires_at) VALUES (?,1,'target','f','i','e','{}','pending','unmanaged',0,1)",
        (cursor.lastrowid,),
    )
    cursor = await db.execute(
        "INSERT INTO deployment_admission_events(plan_id,event,payload,created_at,due_at) VALUES (?,'approval_required','{}',0,0)",
        (cursor.lastrowid,),
    )
    event_id = cursor.lastrowid
    for index, status in enumerate(["pending", "sending", "delivered", "failed"]):
        await db.execute(
            "INSERT INTO deployment_admission_notification_outbox(admission_event_id,notifier_id,event,payload,status,available_at,created_at) VALUES (?,?,'approval_required','{}',?,0,0)",
            (event_id, index, status),
        )
    await db.execute(
        "INSERT INTO repository_webhooks(id,tracker_source_id,provider,auth_mode,secret,config,source_identity,created_at,updated_at) VALUES ('isolated',?,'github','shared_secret','synthetic','{}','identity',0,0)",
        (source,),
    )
    cursor = await db.execute(
        "INSERT INTO webhook_deliveries(webhook_id,delivery_key,payload_hash,summary,state,received_at) VALUES ('isolated','event','hash','{}','pending',0)"
    )
    await db.execute(
        "INSERT INTO source_refresh_requests(delivery_id,tracker_source_id,webhook_generation,source_identity,due_at) VALUES (?,?,1,'identity',0)",
        (cursor.lastrowid, source),
    )
    await db.commit()
    await storage.close()
    with sqlite3.connect(storage.db_path) as connection:
        quarantine_restored_intents(connection)
        connection.commit()
        assert (
            connection.execute("SELECT expanded_at FROM deployment_admission_events").fetchone()[0]
            is not None
        )
        assert [
            row[0]
            for row in connection.execute(
                "SELECT status FROM deployment_admission_notification_outbox ORDER BY id"
            )
        ] == ["discarded", "discarded", "delivered", "failed"]
        assert connection.execute(
            "SELECT state,reason FROM source_refresh_requests"
        ).fetchone() == ("ignored", "restore_review_required")


async def test_plan_pin_and_cancel_lifecycle(storage, system_key_manager):
    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    try:
        await app.state.instance_backup.create()
        await controller.preview(archive.name, 1)
        with pytest.raises(ValueError, match="backup_in_use"):
            await app.state.instance_backup.delete(archive.name)
        await app.state.instance_backup.create(retain=1)
        assert archive.exists()
        controller.drop_plan()
        await app.state.instance_backup.delete(archive.name)
        assert not archive.exists()
    finally:
        await controller.close()
