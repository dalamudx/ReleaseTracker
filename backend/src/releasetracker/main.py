"""FastAPI application entry point"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
from .logger import LogConfig
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
async def lifespan(app: FastAPI):
    """Application lifecycle management"""

    # Initialize storage
    # data/releases.db relative to backend root
    base_dir = Path(__file__).resolve().parent.parent.parent
    db_path = str(base_dir / "data" / "releases.db")

    system_key_manager = SystemKeyManager(base_dir / "data" / "system-secrets.json")
    await system_key_manager.initialize()

    storage = SQLiteStorage(db_path, system_key_manager=system_key_manager)
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

    # Initialize schedulers
    scheduler_host = SchedulerHost()
    scheduler = ReleaseScheduler(storage, scheduler_host=scheduler_host)
    executor_scheduler = ExecutorScheduler(storage, scheduler_host=scheduler_host)
    repository_webhook_scheduler = RepositoryWebhookScheduler(storage, scheduler, scheduler_host)

    from .services.deployment_readiness import DeploymentReadiness
    from .services.executor_notification_outbox import ExecutorNotificationOutbox
    from .services.deployment_admission_notifications import DeploymentAdmissionNotificationOutbox

    notification_outbox = ExecutorNotificationOutbox(storage, scheduler_host)
    admission_notification_outbox = DeploymentAdmissionNotificationOutbox(storage, scheduler_host)
    executor_scheduler.notification_outbox = notification_outbox

    readiness = DeploymentReadiness(storage, executor_scheduler, scheduler_host)
    executor_scheduler.readiness = readiness
    task_queue = TaskQueue(storage.tasks, scheduler_host)
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
    await notification_outbox.initialize()
    await admission_notification_outbox.initialize()
    await scheduler.initialize()
    await executor_scheduler.initialize()
    await repository_webhook_scheduler.initialize()
    await scheduler_host.start()
    await scheduler.start()
    await executor_scheduler.start()

    yield

    # Clean up on shutdown
    if repository_webhook_scheduler:
        await repository_webhook_scheduler.shutdown()
    await task_queue.shutdown()
    await readiness.shutdown()
    await notification_outbox.shutdown()
    await admission_notification_outbox.shutdown()
    if executor_scheduler:
        await executor_scheduler.shutdown()
    if scheduler_host:
        await scheduler_host.shutdown()
    # Close the persistent database connection
    await storage.close()


# Create the FastAPI application
app = FastAPI(
    title="ReleaseTracker API",
    description="A lightweight, configurable release tracking and update orchestration API",
    version=__version__,
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins in development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(StorageConnectionCleanupMiddleware)


# ==================== Route registration ====================

app.include_router(auth.router)
app.include_router(notifiers.router)
app.include_router(notification_templates.router)
app.include_router(webhooks.router)
app.include_router(settings.router)
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
    return HTMLResponse(index_template.replace("<!-- APP_BASE_HREF -->", base_tag))


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
