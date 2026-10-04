import asyncio
from contextlib import asynccontextmanager
import hashlib
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from releasetracker.main import runtime_lifespan, StorageConnectionCleanupMiddleware
from releasetracker.routers import auth, backups
from releasetracker.scheduler_host import SchedulerHost
from releasetracker.services.auth import pwd_context
from releasetracker.services.instance_backup import InstanceBackup
from releasetracker.services.online_restore import (
    OnlineRestore,
    RestoreError,
    RestoreMaintenanceMiddleware,
)
from releasetracker.services.system_keys import SystemKeyManager, rotate_jwt_secret
from releasetracker.storage.sqlite import SQLiteStorage


async def seed(storage, manager):
    admin = await storage.get_user_by_username("admin")
    await storage.update_user_password(admin.id, pwd_context.hash("restore-only-password"))
    await storage.set_setting("online-marker", "archived")
    archive = await InstanceBackup(storage, manager).create()
    archived_secret = manager.jwt_secret
    await storage.set_setting("online-marker", "live")
    await rotate_jwt_secret(storage, manager, generate=True)
    await storage.close()
    return archive, archived_secret, manager.jwt_secret


def application():
    app = FastAPI()
    app.include_router(backups.router)
    app.include_router(backups.status_router)
    app.include_router(auth.router)
    app.add_middleware(StorageConnectionCleanupMiddleware)
    app.add_middleware(RestoreMaintenanceMiddleware)
    return app


async def open_controller(app, factory, path):
    controller = OnlineRestore(app, factory, path)
    app.state.online_restore = controller
    await controller.open()
    return controller


async def login(client):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "restore-only-password"}
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token


async def test_real_runtime_restore_reloads_keys_sessions_and_requires_review(
    storage, system_key_manager, monkeypatch
):
    archive, archived_key, live_key = await seed(storage, system_key_manager)
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", storage.db_path)
    app = application()  # Default persisted interval is zero; seed has closed fixture storage.
    controller = await open_controller(app, runtime_lifespan, storage.db_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://isolated"
        ) as client:
            old_storage = app.state.storage
            await login(client)
            response = await client.post(f"/api/backups/{archive.name}/restore-plan")
            assert response.status_code == 200, response.text
            plan = response.json()
            assert not plan["mutation_performed"]
            assert await old_storage.get_setting("online-marker") == "live"
            response = await client.post(
                f"/api/backups/{archive.name}/restore",
                json={
                    "plan_id": plan["id"],
                    "fingerprint": plan["fingerprint"],
                    "confirm_name": archive.name,
                    "data_loss_confirmed": True,
                },
            )
            assert response.status_code == 202, response.text
            receipt = response.json()
            await asyncio.wait_for(asyncio.shield(controller.task), 15)
            status = await client.get(
                f"/api/backups/restore-status/{receipt['id']}",
                headers={"X-Restore-Token": receipt["token"]},
            )
            assert status.json()["state"] == "succeeded", status.text
            assert status.json()["review_required"]
            assert app.state.storage is not old_storage
            assert app.state.system_key_manager.jwt_secret == archived_key != live_key
            assert await app.state.storage.get_setting("online-marker") == "archived"
            assert await app.state.storage.get_setting("restore.review_required")
            assert not app.state.scheduler_host._accepting
            assert (await client.get("/api/backups")).status_code == 401
            assert (
                await client.get(f"/api/backups/restore-status/{receipt['id']}")
            ).status_code == 404
            assert (
                await client.post(
                    "/api/backups/restore-review", headers={"X-Restore-Token": receipt["token"]}
                )
            ).status_code == 401
            await login(client)
            assert (await client.post("/api/backups/restore-review", json={})).status_code == 400
            assert (
                await client.post("/api/backups/restore-review", json={"reviewed": True})
            ).status_code == 200
            assert app.state.scheduler_host._accepting
            assert await app.state.storage.get_setting("restore.review_required") is None
            response = await client.get("/api/backups/restore-safety/current/download")
            assert response.status_code == 200 and response.content[:2] == b"PK"
            assert not controller._downloads
    finally:
        await controller.close()


def minimal_factory(path, fail_reload=False):
    enters = 0

    @asynccontextmanager
    async def factory(app):
        nonlocal enters
        enters += 1
        if fail_reload and enters == 2:
            raise RuntimeError("private failure text must not be exposed")
        manager = SystemKeyManager(path.parent / "system-secrets.json")
        await manager.initialize()
        storage = SQLiteStorage(str(path), system_key_manager=manager)
        await storage.initialize()
        host = SchedulerHost()
        await host.start()
        if getattr(app.state.online_restore, "maintenance", False) or await storage.get_setting(
            "restore.review_required"
        ):
            host.pause()
        app.state.storage = storage
        app.state.system_key_manager = manager
        app.state.scheduler_host = host
        app.state.instance_backup = InstanceBackup(storage, manager)
        app.state.task_queue = SimpleNamespace(workers={})
        app.state.executor_scheduler = SimpleNamespace(
            _running_executor_ids=set(),
            _background_tasks=set(),
            _adapter_lifetimes={},
            readiness=SimpleNamespace(workers={}),
        )
        try:
            yield
        finally:
            await host.shutdown()
            await storage.close()

    return factory


@pytest.mark.parametrize("failure", ["safety", "original_copy", "background_drain"])
async def test_failures_before_switch_keep_live_data_and_resume(
    storage, system_key_manager, monkeypatch, failure
):
    from pathlib import Path

    archive, _, live_key = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    try:
        plan = await controller.preview(archive.name, 1)
        if failure == "safety":

            async def fail(**kwargs):
                raise OSError("private safety failure")

            monkeypatch.setattr(app.state.instance_backup, "create", fail)
        elif failure == "background_drain":

            async def fail(timeout):
                raise TimeoutError("private drain failure")

            monkeypatch.setattr(app.state.scheduler_host, "drain", fail)
        else:

            def fail():
                raise OSError("private copy failure")

            monkeypatch.setattr(controller.files, "save_original", fail)
        receipt = await controller.begin(
            archive.name, SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]), 1
        )
        await controller.task
        status = controller.status(receipt["id"], receipt["token"])
        assert status["state"] == "failed" and not status["rolled_back"]
        assert not controller.maintenance and app.state.scheduler_host._accepting
        assert await app.state.storage.get_setting("online-marker") == "live"
        assert app.state.system_key_manager.jwt_secret == live_key
    finally:
        await controller.close()


async def test_confirmation_repeated_cancellation_still_finishes_owned_operation(
    storage, system_key_manager, monkeypatch
):
    from pathlib import Path
    import threading

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    gate, finish = threading.Event(), threading.Event()
    original = controller.files.persist
    first = True

    def persist(value):
        nonlocal first
        original(value)
        if first:
            first = False
            gate.set()
            assert finish.wait(5)

    try:
        plan = await controller.preview(archive.name, 1)
        monkeypatch.setattr(controller.files, "persist", persist)
        task = asyncio.create_task(
            controller.begin(
                archive.name,
                SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]),
                1,
            )
        )
        try:
            assert await asyncio.to_thread(gate.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert controller.lock.locked()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await controller.task
        assert controller.receipt["state"] == "succeeded"
        assert await app.state.storage.get_setting("online-marker") == "archived"
        assert not controller.maintenance
    finally:
        await controller.close()


async def test_readonly_fetch_finishes_before_switch_instead_of_forced_cancellation(
    storage, system_key_manager
):
    from pathlib import Path

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    finish = asyncio.Event()

    async def readonly():
        await finish.wait()

    worker = asyncio.create_task(readonly())
    try:
        plan = await controller.preview(archive.name, 1)
        app.state.task_queue.workers[1] = ("fetch", worker)
        worker.add_done_callback(lambda _: app.state.task_queue.workers.pop(1, None))
        await controller.begin(
            archive.name, SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]), 1
        )
        assert controller.maintenance
        assert await app.state.storage.get_setting("online-marker") == "live"
        assert not worker.done()
        finish.set()
        await worker
        await controller.task
        assert controller.receipt["state"] == "succeeded"
        assert await app.state.storage.get_setting("online-marker") == "archived"
    finally:
        finish.set()
        await worker
        await controller.close()


async def test_expiring_plan_during_hash_does_not_delete_stage_until_confirmation_returns(
    storage, system_key_manager, monkeypatch
):
    from pathlib import Path
    import threading
    from releasetracker.services import online_restore as module

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    entered, finish = threading.Event(), threading.Event()
    original = module.digest

    def hash(path):
        entered.set()
        assert finish.wait(5)
        return original(path)

    try:
        plan = await controller.preview(archive.name, 1)
        directory = controller.plan["directory"]
        monkeypatch.setattr(module, "digest", hash)
        task = asyncio.create_task(
            controller.begin(
                archive.name,
                SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]),
                1,
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            controller.plan["expires_at"] = 0
            controller.expiry.cancel()
            controller.expire_plan()
            assert controller.plan and directory.exists()
            assert archive.name in app.state.instance_backup._restore_pins
        finally:
            finish.set()
        with pytest.raises(RestoreError, match="restore_plan_expired"):
            await task
        assert controller.plan is None and controller.task is None and not controller.maintenance
    finally:
        await controller.close()


async def test_shutdown_keeps_directory_owner_until_admitted_requests_end(
    storage, system_key_manager
):
    from pathlib import Path
    from releasetracker.services.online_restore_files import RestoreFiles

    await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    controller.requests = 1
    controller.drained.clear()
    close = asyncio.create_task(controller.close())
    await asyncio.sleep(0)
    contender = RestoreFiles(storage.db_path)
    try:
        with pytest.raises(BlockingIOError):
            contender.acquire()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://isolated"
        ) as client:
            assert (await client.get("/api/backups")).status_code == 503
    finally:
        controller.requests = 0
        controller.drained.set()
        await close
    contender.acquire()
    contender.release()


async def test_safety_copy_follows_spawned_notification_worker_commit(
    storage, system_key_manager, tmp_path
):
    from pathlib import Path
    import sqlite3
    from releasetracker.services.instance_backup import validate_archive

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    finish = asyncio.Event()
    source = app.state.storage

    async def notification():
        await finish.wait()
        await source.set_setting("online-marker", "after-notification-drain")

    worker = asyncio.create_task(notification())
    app.state.runtime_services = [SimpleNamespace(_worker=worker)]
    try:
        plan = await controller.preview(archive.name, 1)
        await controller.begin(
            archive.name, SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]), 1
        )
        assert not controller.files.safety.exists()
        finish.set()
        await worker
        await controller.task
        assert controller.receipt["state"] == "succeeded"
        destination = tmp_path / "safety-outbox"
        destination.mkdir()
        await asyncio.to_thread(validate_archive, controller.files.safety, destination)
        with sqlite3.connect(destination / "releases.db") as db:
            assert (
                db.execute("SELECT value FROM settings WHERE key='online-marker'").fetchone()[0]
                == "after-notification-drain"
            )
        assert await app.state.storage.get_setting("online-marker") == "archived"
    finally:
        finish.set()
        await worker
        await controller.close()


async def test_online_safety_creation_honors_configured_retention_and_keeps_reviewed_source(
    storage, system_key_manager, monkeypatch
):
    from pathlib import Path

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    try:
        older = [await app.state.instance_backup.create(retain=10) for _ in range(2)]
        plan = await controller.preview(archive.name, 1)
        await app.state.storage.set_setting("system.backup_retention", "1")
        await controller.begin(
            archive.name, SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]), 1
        )
        await controller.task
        assert controller.receipt["state"] == "succeeded"
        assert all(not point.exists() for point in older)
        assert archive.exists() and controller.files.safety.exists()
    finally:
        await controller.close()


async def test_reload_failure_restores_original_pair_and_resumes(storage, system_key_manager):
    from pathlib import Path

    archive, _, live_key = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(
        app, minimal_factory(Path(storage.db_path), True), storage.db_path
    )
    try:
        plan = await controller.preview(archive.name, 1)
        body = SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"])
        receipt = await controller.begin(archive.name, body, 1)
        await controller.task
        status = controller.status(receipt["id"], receipt["token"])
        assert status["state"] == "failed" and status["rolled_back"]
        assert "private failure" not in str(status)
        assert not controller.maintenance and app.state.scheduler_host._accepting
        assert await app.state.storage.get_setting("online-marker") == "live"
        assert app.state.system_key_manager.jwt_secret == live_key
        assert controller.files.safety.exists()
    finally:
        await controller.close()


@pytest.mark.parametrize("busy", ["queue", "native_borrow", "downloads", "unresolved"])
async def test_active_work_refuses_before_maintenance_or_data_change(
    storage, system_key_manager, busy
):
    from pathlib import Path

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    try:
        plan = await controller.preview(archive.name, 1)
        if busy == "queue":
            app.state.task_queue.workers[1] = ("deploy", None)
        if busy == "native_borrow":
            app.state.executor_scheduler._adapter_lifetimes[1] = SimpleNamespace(borrowers=1)
        if busy == "downloads":
            app.state.instance_backup._downloads[archive.name] = 1
        if busy == "unresolved":
            db = await app.state.storage._get_connection()
            await db.execute(
                "INSERT INTO tasks(resource_key,dedupe_key,target_label,max_retries,kind,state,payload,created_at,updated_at,due_at) VALUES ('executor:1','unsafe','isolated',0,'deploy','needs_attention','{}',0,0,0)"
            )
            await db.commit()
        with pytest.raises(RestoreError):
            await controller.begin(
                archive.name,
                SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"]),
                1,
            )
        assert not controller.maintenance and controller.task is None
        assert await app.state.storage.get_setting("online-marker") == "live"
    finally:
        await controller.close()


async def test_plan_actor_expiry_source_change_and_cap_permissions(storage, system_key_manager):
    from pathlib import Path

    archive, _, _ = await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    try:
        plan = await controller.preview(archive.name, 1)
        body = SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"])
        with pytest.raises(RestoreError, match="restore_plan_expired"):
            await controller.begin(archive.name, body, 2)
        controller.plan["expires_at"] = 0
        with pytest.raises(RestoreError, match="restore_plan_expired"):
            await controller.begin(archive.name, body, 1)
        plan = await controller.preview(archive.name, 1)
        body = SimpleNamespace(plan_id=plan["id"], fingerprint=plan["fingerprint"])
        archive.write_bytes(b"changed archive")
        with pytest.raises(RestoreError, match="restore_archive_changed"):
            await controller.begin(archive.name, body, 1)
        assert controller.plan is None and not controller.maintenance
        assert not app.state.instance_backup._restore_pins
    finally:
        await controller.close()


async def test_maintenance_gate_blocks_all_business_requests_but_not_private_status(
    storage, system_key_manager
):
    from pathlib import Path

    await seed(storage, system_key_manager)
    app = application()
    controller = await open_controller(app, minimal_factory(Path(storage.db_path)), storage.db_path)
    try:
        controller.maintenance = True
        controller.receipt = {
            "id": "a" * 32,
            "token_hash": hashlib.sha256(b"fixture-status").hexdigest(),
            "issued_at": __import__("time").time(),
            "state": "running",
            "phase": "draining",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://isolated"
        ) as client:
            for method, path in [
                ("GET", "/api/backups"),
                ("POST", "/api/auth/login"),
                ("POST", "/api/webhooks"),
                ("GET", "/settings"),
            ]:
                response = await client.request(method, path)
                assert response.status_code == 503
                assert response.headers["Retry-After"] == "3"
            response = await client.get(
                "/api/backups/restore-status/" + "a" * 32,
                headers={"X-Restore-Token": "fixture-status"},
            )
            assert response.status_code == 200
            assert "token_hash" not in response.text
    finally:
        await controller.close()
