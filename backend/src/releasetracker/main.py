"""FastAPI application entry point"""

import asyncio
from contextlib import asynccontextmanager
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
import logging
import os
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__
from .scheduler import ReleaseScheduler
from .scheduler_host import SchedulerHost
from .webhook_scheduler import RepositoryWebhookScheduler
from .executor_scheduler import ExecutorScheduler
from .services.auth import AuthService
from .services.system_keys import (
    SystemKeyManager,
    recover_pending_encryption_key_rotation,
)
from .services.ssh_compose_snapshot import migrate_legacy_snapshots
from .storage.sqlite import SQLiteStorage
from .storage.sqlite_retention import prune_fetch_runs
from .logger import LogConfig
from .paths import database_path, system_secrets_path
from .services.http_security import configure_http_security
from .services.instance_backup import InstanceBackup, retention_tiers
from .routers import backups, metrics, browser_auth
from .routers import (
    auth,
    notifiers,
    settings,
    trackers,
    credentials,
    releases,
    system,
    webhooks,
    notification_templates,
)
from .routers import runtime_connections, ssh_connections, ssh_compose
from .routers import executors, tasks
from .services.task_queue import TaskQueue
from .services.shutdown import shutdown_services
from .services.online_restore import RestoreMaintenanceMiddleware
from .services.fetch_tasks import FetchTasks
from .services.deploy_tasks import DeployTasks
from .services.recovery_tasks import RecoveryTasks
from .routers import oidc as oidc_router
from .routers import oidc_admin as oidc_admin_router


class StorageConnectionCleanupMiddleware:
    """Close request-scoped SQLite connections while the ASGI loop is alive."""

    def __init__(self, application) -> None:
        self.application = application

    async def __call__(self, scope, receive, send) -> None:
        try:
            await self.application(scope, receive, send)
        finally:
            if scope["type"] == "http":
                application = scope.get("app")
                storage = (
                    getattr(application.state, "storage", None) if application is not None else None
                )
                if storage is not None:
                    await storage.close_current_task_connection()


@asynccontextmanager
async def runtime_lifespan(app: FastAPI):
    """Application lifecycle management"""

    # Initialize storage
    db_path = str(database_path())

    system_key_manager = SystemKeyManager(system_secrets_path())
    await system_key_manager.initialize()

    storage = SQLiteStorage(db_path, system_key_manager=system_key_manager)
    # Register ownership before initialize/start: either may partially allocate
    # connections or workers before failing. Only constructed resources are closed.
    owned_closers = {"storage": storage.close}
    app.state.runtime_services = []
    body_error: BaseException | None = None
    try:
        await storage.initialize()
        await recover_pending_encryption_key_rotation(storage, system_key_manager)
        migrated_snapshots = await migrate_legacy_snapshots(storage)
        if migrated_snapshots:
            logging.getLogger(__name__).info(
                "encrypted %s legacy executor snapshots", migrated_snapshots
            )
        LogConfig.setup_logging(level=getattr(logging, await storage.get_system_log_level()))
        # Initialize configuration without AppConfig

        # Bind to app.state
        app.state.storage = storage
        app.state.system_key_manager = system_key_manager
        # app.state.config = app_config # REMOVED

        # Ensure an admin user exists
        auth_service = AuthService(storage, system_key_manager)
        await auth_service.ensure_admin_user()
        interrupted_source_runs = await storage.reconcile_interrupted_source_fetch_runs()
        if interrupted_source_runs:
            logging.getLogger(__name__).warning(
                "Reconciled %s interrupted source fetch runs", interrupted_source_runs
            )
        reconciled_claims = await storage.reconcile_stale_executor_snapshot_claims(
            stale_before=datetime.now() - timedelta(minutes=30)
        )
        if reconciled_claims:
            logging.getLogger(__name__).warning(
                "Reconciled %s stale executor snapshot rollback claims", reconciled_claims
            )
        reconcile_tasks = getattr(storage, "reconcile_orphaned_executor_tasks", None)
        if reconcile_tasks is not None:
            reconciled_tasks = await reconcile_tasks()
            if reconciled_tasks:
                logging.getLogger(__name__).warning(
                    "Reconciled %s orphaned tasks for deleted executors", reconciled_tasks
                )

        # Initialize schedulers
        scheduler_host = SchedulerHost()
        owned_closers["scheduler_host"] = scheduler_host.shutdown
        retention_tiers()  # Reject invalid retention before starting background work.
        instance_backup = InstanceBackup(storage, system_key_manager)
        await instance_backup.load_configuration()
        app.state.instance_backup = instance_backup
        instance_backup.scheduler_host = scheduler_host
        await instance_backup.reschedule()

        async def verify_stored_backup():
            try:
                await instance_backup.verify_latest()
            except Exception:
                logging.getLogger(__name__).error("Stored backup verification failed")
            finally:
                await storage.close_current_task_connection()

        # Also check manually-created archives when automatic creation is disabled.
        verification_status = await instance_backup.status()
        now = time.time()
        last_verified = float(verification_status.get("last_verified_at") or 0)
        scheduler_host.add_interval_job(
            "maintenance",
            "instance_backup_verification",
            verify_stored_backup,
            seconds=86400,
            next_run_time=datetime.fromtimestamp(max(now + 300, last_verified + 86400)),
        )

        async def prune_old_fetch_runs():
            try:
                removed = await prune_fetch_runs(storage)
                if removed:
                    logging.getLogger(__name__).info("Pruned %s old fetch runs", removed)
            finally:
                await storage.close_current_task_connection()

        scheduler_host.add_interval_job(
            "maintenance", "fetch_retention", prune_old_fetch_runs, seconds=86400
        )
        scheduler = ReleaseScheduler(storage, scheduler_host=scheduler_host)
        executor_scheduler = ExecutorScheduler(storage, scheduler_host=scheduler_host)
        owned_closers["executor_scheduler"] = executor_scheduler.shutdown
        repository_webhook_scheduler = RepositoryWebhookScheduler(
            storage, scheduler, scheduler_host
        )
        owned_closers["repository_webhook"] = repository_webhook_scheduler.shutdown

        from .services.deployment_readiness import DeploymentReadiness
        from .services.executor_notification_outbox import ExecutorNotificationOutbox
        from .services.deployment_admission_notifications import (
            DeploymentAdmissionNotificationOutbox,
        )

        from .services.release_notification_outbox import ReleaseNotificationOutbox

        notification_outbox = ExecutorNotificationOutbox(storage, scheduler_host)
        owned_closers["executor_outbox"] = notification_outbox.shutdown
        release_notification_outbox = ReleaseNotificationOutbox(storage, scheduler_host)
        owned_closers["release_outbox"] = release_notification_outbox.shutdown
        admission_notification_outbox = DeploymentAdmissionNotificationOutbox(
            storage, scheduler_host
        )
        owned_closers["admission_outbox"] = admission_notification_outbox.shutdown
        app.state.runtime_services = [
            notification_outbox,
            release_notification_outbox,
            admission_notification_outbox,
            repository_webhook_scheduler,
        ]
        executor_scheduler.notification_outbox = notification_outbox

        readiness = DeploymentReadiness(storage, executor_scheduler, scheduler_host)
        owned_closers["readiness"] = readiness.shutdown
        executor_scheduler.readiness = readiness
        from .services.runtime_health_watch import RuntimeHealthWatch

        runtime_health_watch = RuntimeHealthWatch(
            storage, executor_scheduler, readiness, scheduler_host
        )
        owned_closers["runtime_health"] = runtime_health_watch.shutdown
        task_queue = TaskQueue(storage.tasks, scheduler_host)
        owned_closers["task_queue"] = task_queue.shutdown
        fetch_tasks = FetchTasks(storage, scheduler)
        deploy_tasks = DeployTasks(storage, executor_scheduler)
        recovery_tasks = RecoveryTasks(storage, executor_scheduler)
        task_queue.register("fetch", fetch_tasks)
        task_queue.register("deploy", deploy_tasks)
        task_queue.register("recover", recovery_tasks)
        scheduler.fetch_tasks = fetch_tasks
        repository_webhook_scheduler.fetch_tasks = fetch_tasks
        executor_scheduler.deploy_tasks = deploy_tasks
        executor_scheduler.recovery_tasks = recovery_tasks
        app.state.task_queue = task_queue
        app.state.fetch_tasks = fetch_tasks

        # Bind schedulers to app.state
        app.state.scheduler_host = scheduler_host
        app.state.scheduler = scheduler
        app.state.executor_scheduler = executor_scheduler
        app.state.repository_webhook_scheduler = repository_webhook_scheduler

        await task_queue.initialize()
        await readiness.initialize()
        await runtime_health_watch.initialize()
        await notification_outbox.initialize()
        await release_notification_outbox.initialize()
        await admission_notification_outbox.initialize()
        await scheduler.initialize()
        await executor_scheduler.initialize()
        await repository_webhook_scheduler.initialize()
        if await storage.get_setting("restore.review_required") is not None or getattr(
            getattr(app.state, "online_restore", None), "maintenance", False
        ):
            scheduler_host.pause()
        await scheduler_host.start()
        await scheduler.start()
        await executor_scheduler.start()
        yield
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        # Preserve the established shutdown order even for a partial startup.
        steps = [
            (name, owned_closers[name])
            for name in (
                "repository_webhook",
                "runtime_health",
                "task_queue",
                "readiness",
                "executor_outbox",
                "release_outbox",
                "admission_outbox",
                "executor_scheduler",
                "scheduler_host",
                "storage",
            )
            if name in owned_closers
        ]
        try:
            await shutdown_services(steps)
        except Exception:
            if isinstance(body_error, asyncio.CancelledError):
                raise body_error
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Small embedded/test applications retain the original lifecycle. The
    # shipped app explicitly owns its data directory and enables reloading.
    if not getattr(app.state, "enable_online_restore", False):
        async with runtime_lifespan(app):
            yield
        return
    from .services.online_restore import OnlineRestore

    controller = OnlineRestore(app, runtime_lifespan, database_path())
    app.state.online_restore = controller
    body_error = None
    try:
        await controller.open()
        yield
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            await shutdown_services([("online_restore", controller.close)])
        except Exception:
            if isinstance(body_error, asyncio.CancelledError):
                raise body_error
            raise
        finally:
            app.state.online_restore = None


# Create the FastAPI application
app = FastAPI(
    title="ReleaseTracker API",
    description="A lightweight, configurable release tracking and update orchestration API",
    version=__version__,
    lifespan=lifespan,
)

online_restore_option = os.environ.get(
    "RELEASETRACKER_ONLINE_RESTORE", "1" if os.name == "posix" else "0"
).strip()
if online_restore_option not in {"0", "1"}:
    raise ValueError("RELEASETRACKER_ONLINE_RESTORE must be 0 or 1")
app.state.enable_online_restore = online_restore_option == "1"
app.add_middleware(StorageConnectionCleanupMiddleware)
app.add_middleware(RestoreMaintenanceMiddleware)
configure_http_security(app)


# ==================== Route registration ====================

app.include_router(auth.router)
app.include_router(browser_auth.router)
app.include_router(notifiers.router)
app.include_router(notification_templates.router)
app.include_router(webhooks.router)
app.include_router(settings.router)
app.include_router(backups.router)
app.include_router(backups.status_router)
app.include_router(metrics.router)
app.include_router(trackers.router)
app.include_router(credentials.router)
app.include_router(runtime_connections.router)
app.include_router(ssh_connections.router)
app.include_router(ssh_compose.router)
app.include_router(executors.router)
app.include_router(tasks.router)
app.include_router(releases.router)
app.include_router(system.router)
app.include_router(oidc_router.router)
app.include_router(oidc_admin_router.router)


# ==================== Static file serving ====================

# Check whether the static files directory exists
static_dir = Path(__file__).resolve().parent.parent.parent / "static"


def _resolve_static_file(static_root: Path, request_path: str) -> Path | None:
    """Resolve a requested static file without allowing directory escapes."""
    try:
        resolved_root = static_root.resolve(strict=True)
        resolved_file = (resolved_root / request_path.lstrip("/")).resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    if resolved_root not in resolved_file.parents or not resolved_file.is_file():
        return None
    return resolved_file


def _frontend_base_path(base_url: str | None, root_path: str) -> str:
    """Return the normalized app path for the document's runtime ``<base>``."""
    configured_path = urlsplit(base_url).path if base_url else root_path
    normalized_path = configured_path.rstrip("/")
    return f"{normalized_path}/" if normalized_path else "/"


async def _render_static_index(request: Request, index_template: str) -> HTMLResponse:
    storage = getattr(request.app.state, "storage", None)
    base_url = await storage.get_system_base_url() if storage is not None else None
    base_path = _frontend_base_path(base_url, request.scope.get("root_path", ""))
    base_tag = f'<base href="{escape(base_path, quote=True)}">'
    nonce = secrets.token_urlsafe(24)
    html = index_template.replace("<!-- APP_BASE_HREF -->", base_tag)
    html = re.sub(r"<script\b", f'<script nonce="{nonce}"', html, flags=re.IGNORECASE)
    # Allow the trusted theme bootstrap without allowing arbitrary inline JS.
    policy = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: https: http:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "object-src 'none'; form-action 'self'"
    )
    return HTMLResponse(
        html, headers={"Content-Security-Policy": policy, "Cache-Control": "no-store"}
    )


def configure_static_frontend(application: FastAPI, static_root: Path) -> None:
    """Serve production assets and fall back to the SPA only for client routes."""
    application.mount("/assets", StaticFiles(directory=static_root / "assets"), name="assets")
    index_path = static_root / "index.html"
    index_template = index_path.read_text(encoding="utf-8") if index_path.is_file() else None
    resolved_index_path = index_path.resolve() if index_template is not None else None

    @application.exception_handler(404)
    async def static_aware_404_handler(request: Request, exc):
        del exc
        request_path = request.url.path
        if request_path.startswith("/api") or request_path.startswith("/auth/oidc"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        static_file = _resolve_static_file(static_root, request_path)
        if static_file is not None and static_file != resolved_index_path:
            return FileResponse(static_file)

        if index_template is not None:
            return await _render_static_index(request, index_template)

        return JSONResponse(status_code=404, content={"detail": "Not found"})


if static_dir.exists():
    configure_static_frontend(app, static_dir)

else:

    @app.get("/")
    async def root():
        """Root path for development mode"""
        return {
            "message": app.title,
            "version": app.version,
            "note": "Frontend not available in development mode",
        }
