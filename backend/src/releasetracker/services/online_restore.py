"""One-owner, page-initiated maintenance restore; no process killing or host socket."""

from __future__ import annotations
import asyncio
import hashlib
import hmac
import logging
from pathlib import Path
import secrets
import shutil
import tempfile
import time

from starlette.responses import JSONResponse

from ..executors.adapter_lifetime import wait_for_runtime_worker
from .instance_backup import _finish_thread
from .online_restore_files import RestoreFiles, atomic_copy, digest, prepare_archive

logger = logging.getLogger(__name__)


class RestoreError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


class RestoreMaintenanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        application = scope.get("app")
        controller = getattr(application.state, "online_restore", None) if application else None
        if scope["type"] != "http" or controller is None:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        status_request = scope["method"] == "GET" and path.startswith(
            "/api/backups/restore-status/"
        )
        if status_request:
            return await self.app(scope, receive, send)
        if controller.maintenance or controller.closing:
            return await JSONResponse(
                {"detail": "restore_maintenance"},
                status_code=503,
                headers={"Retry-After": "3", "Cache-Control": "no-store"},
            )(scope, receive, send)
        controller.requests += 1
        controller.drained.clear()
        try:
            await self.app(scope, receive, send)
        finally:
            controller.requests -= 1
            if not controller.requests:
                controller.drained.set()


class OnlineRestore:
    def __init__(self, app, factory, db_path):
        self.app, self.factory = app, factory
        self.files = RestoreFiles(db_path)
        self.runtime = None
        self.maintenance = False
        self.requests = 0
        self.drained = asyncio.Event()
        self.drained.set()
        self.lock = asyncio.Lock()
        self.plan = None
        self.expiry = None
        self.task = None
        self.receipt = None
        self.cleaners = set()
        self._downloads = {}
        self.closing = False

    async def open(self):
        await _finish_thread(self.files.acquire)
        try:
            self.receipt = await _finish_thread(self.files.recover)
            await self.start_runtime()
        except BaseException:
            self.files.release()
            raise

    async def start_runtime(self):
        context = self.factory(self.app)
        await context.__aenter__()
        self.runtime = context

    async def stop_runtime(self):
        context, self.runtime = self.runtime, None
        if context is not None:
            await context.__aexit__(None, None, None)

    async def close(self):
        self.closing = True
        await self.drained.wait()
        if self.task is not None:
            await wait_for_runtime_worker(self.task)
        self.drop_plan()
        if self.cleaners:
            await asyncio.gather(*self.cleaners)
        try:
            await self.stop_runtime()
        finally:
            self.files.release()

    def clean(self, directory):
        work = asyncio.create_task(_finish_thread(shutil.rmtree, directory, True))
        self.cleaners.add(work)
        work.add_done_callback(self.cleaners.discard)

    def expire_plan(self):
        if self.lock.locked():
            self.expiry = asyncio.get_running_loop().call_later(1, self.expire_plan)
        else:
            self.drop_plan()

    def drop_plan(self):
        if self.expiry:
            self.expiry.cancel()
            self.expiry = None
        if self.plan:
            plan, self.plan = self.plan, None
            plan["backup"]._restore_pins.discard(plan["name"])
            self.clean(plan["directory"])

    def ensure_available(self):
        if (
            self.closing
            or self.maintenance
            or self.lock.locked()
            or (self.task and not self.task.done())
        ):
            raise RestoreError("restore_busy")

    async def preview(self, name, actor):
        self.ensure_available()
        async with self.lock:
            return await wait_for_runtime_worker(asyncio.create_task(self._preview(name, actor)))

    async def _preview(self, name, actor):
        self.drop_plan()
        backup = self.app.state.instance_backup
        if backup.lock.locked():
            raise RestoreError("restore_busy")
        async with backup.lock:
            path = backup.archive(name)
            directory = Path(tempfile.mkdtemp(prefix="plan-", dir=self.files.root / "plans"))
            backup._restore_pins.add(name)
            try:
                fingerprint, manifest = await _finish_thread(prepare_archive, path, directory)
            except BaseException:
                backup._restore_pins.discard(name)
                self.clean(directory)
                raise RestoreError("restore_validation_failed", 422) from None
        plan = dict(
            id=secrets.token_hex(16),
            name=name,
            actor=actor,
            fingerprint=fingerprint,
            directory=directory,
            backup=backup,
            expires_at=time.time() + 600,
        )
        self.plan = plan
        self.expiry = asyncio.get_running_loop().call_later(600, self.expire_plan)
        return {key: plan[key] for key in ("id", "name", "fingerprint", "expires_at")} | {
            "created_at": manifest.get("created_at"),
            "app_version": manifest.get("version"),
            "mutation_performed": False,
        }

    async def assert_idle(self, *, allow_fetch=False):
        backup = self.app.state.instance_backup
        queue = self.app.state.task_queue
        executor = self.app.state.executor_scheduler
        if (
            backup.lock.locked()
            or backup._downloads
            or self._downloads
            or any(kind != "fetch" or not allow_fetch for kind, _ in queue.workers.values())
            or executor._running_executor_ids
            or any(not task.done() for task in executor._background_tasks)
            or any(lifetime.borrowers for lifetime in executor._adapter_lifetimes.values())
            or executor.readiness.workers
        ):
            raise RestoreError("restore_active_work")
        db = await self.app.state.storage._get_connection()
        row = await (
            await db.execute(
                "SELECT 1 FROM tasks WHERE kind IN ('deploy','recover') AND state IN ('running','needs_attention') LIMIT 1"
            )
        ).fetchone()
        if row:
            raise RestoreError("restore_unresolved_deployment")

    async def begin(self, name, body, actor):
        self.ensure_available()
        async with self.lock:
            return await wait_for_runtime_worker(
                asyncio.create_task(self._begin(name, body, actor))
            )

    async def _begin(self, name, body, actor):
        plan = self.plan
        if (
            not plan
            or plan["id"] != body.plan_id
            or plan["name"] != name
            or plan["actor"] != actor
            or plan["fingerprint"] != body.fingerprint
            or plan["expires_at"] <= time.time()
        ):
            raise RestoreError("restore_plan_expired")
        await self.assert_idle(allow_fetch=True)
        if await _finish_thread(digest, plan["backup"].archive(name)) != plan["fingerprint"]:
            self.drop_plan()
            raise RestoreError("restore_archive_changed")
        if self.plan is not plan or plan["expires_at"] <= time.time():
            self.drop_plan()
            raise RestoreError("restore_plan_expired")
        token = secrets.token_urlsafe(32)
        receipt = dict(
            id=secrets.token_hex(16),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            issued_at=time.time(),
            state="running",
            phase="draining",
            error_code=None,
            rolled_back=False,
            original_saved=False,
        )
        await _finish_thread(self.files.persist, receipt)
        self.receipt = receipt
        self.expiry.cancel()
        self.expiry = None
        self.plan = None
        self.maintenance = True
        self.app.state.scheduler_host.pause()
        self.task = asyncio.create_task(self.run(plan))
        return {"id": receipt["id"], "token": token}

    async def phase(self, phase, **changes):
        value = self.receipt | {"phase": phase} | changes
        await _finish_thread(self.files.persist, value)
        self.receipt = value

    async def run(self, plan):
        stopped = switched = stop_failed = False
        try:
            await asyncio.wait_for(self.drained.wait(), 30)
            await self.app.state.scheduler_host.drain(30)
            spawned = set()
            for service in getattr(self.app.state, "runtime_services", []):
                for name in ("_worker", "_worker_task"):
                    worker = getattr(service, name, None)
                    if worker is not None and not worker.done():
                        spawned.add(worker)
            if spawned:
                _, unfinished = await asyncio.wait(spawned, timeout=30)
                if unfinished:
                    raise RestoreError("restore_active_work")
            fetches = {
                worker
                for kind, worker in self.app.state.task_queue.workers.values()
                if kind == "fetch" and not worker.done()
            }
            if fetches:
                _, unfinished = await asyncio.wait(fetches, timeout=30)
                if unfinished:
                    raise RestoreError("restore_active_work")
            await self.assert_idle()
            await self.phase("safety_backup")
            # All HTTP/background writers are now quiescent. Creation is verified
            # before any resource shutdown or paired filesystem replacement.
            archive = await self.app.state.instance_backup.create()
            await _finish_thread(atomic_copy, archive, self.files.safety)
            await self.phase("closing", safety_created_at=time.time())
            stop_failed = True
            await self.stop_runtime()
            stop_failed = False
            stopped = True
            await _finish_thread(self.files.save_original)
            await self.phase("switching", original_saved=True)
            switched = True
            await _finish_thread(self.files.install, plan["directory"] / "restored")
            await self.phase("reloading")
            await self.start_runtime()
            await self.phase("finished", state="succeeded")
            self.maintenance = False
        except BaseException as error:
            logger.error("online_restore_failed error_type=%s", type(error).__name__)
            code = error.code if isinstance(error, RestoreError) else "restore_failed"
            try:
                if stop_failed:
                    # A close failure cannot prove every old connection is gone.
                    await self.phase(
                        "cleanup_failed", state="blocked", error_code="restore_cleanup_failed"
                    )
                    return
                if switched:
                    await self.stop_runtime()
                    await self.phase("rolling_back")
                    await _finish_thread(self.files.rollback)
                if stopped:
                    await self.start_runtime()
                await self.phase("finished", state="failed", error_code=code, rolled_back=switched)
                self.maintenance = False
                if not await self.app.state.storage.get_setting("restore.review_required"):
                    self.app.state.scheduler_host.resume()
            except BaseException as rollback_error:
                logger.error(
                    "online_restore_rollback_failed error_type=%s", type(rollback_error).__name__
                )
                self.receipt.update(
                    state="blocked", phase="failed_closed", error_code="restore_rollback_failed"
                )
                try:
                    await _finish_thread(self.files.persist, self.receipt)
                except OSError:
                    pass
                # No new writes after an unjournalled/failed rollback.
                self.maintenance = True
        finally:
            plan["backup"]._restore_pins.discard(plan["name"])
            self.clean(plan["directory"])

    def status(self, identifier, token):
        value = self.receipt
        if (
            not value
            or value["id"] != identifier
            or time.time() > value["issued_at"] + 3600
            or not hmac.compare_digest(
                value["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            )
        ):
            raise RestoreError("restore_receipt_not_found", 404)
        return {
            key: value.get(key) for key in ("id", "state", "phase", "error_code", "rolled_back")
        } | {"review_required": value["state"] == "succeeded"}

    def safety_entry(self):
        if self.files.safety.is_file() and not self.files.safety.is_symlink():
            return {
                "name": "before-online-restore.zip",
                "size": self.files.safety.stat().st_size,
                "created_at": (self.receipt or {}).get(
                    "safety_created_at", self.files.safety.stat().st_mtime
                ),
            }
        return None

    def acquire_safety(self):
        if self.maintenance or not self.safety_entry():
            raise RestoreError("restore_busy")
        self._downloads["safety"] = self._downloads.get("safety", 0) + 1
        return self.files.safety

    def release_download(self, name):
        count = self._downloads.get(name, 0)
        if count <= 1:
            self._downloads.pop(name, None)
        else:
            self._downloads[name] = count - 1

    async def review(self):
        self.ensure_available()
        async with self.lock:
            db = await self.app.state.storage._get_connection()
            await db.execute("DELETE FROM settings WHERE key='restore.review_required'")
            await db.commit()
            self.app.state.scheduler_host.resume()
