"""FastAPI application entry point"""

from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import __version__
from .scheduler import ReleaseScheduler
from .scheduler_host import SchedulerHost
from .executor_scheduler import ExecutorScheduler
from .services.auth import AuthService
from .services.system_keys import SystemKeyManager
from .storage.sqlite import SQLiteStorage
from .logger import LogConfig
from .routers import auth, notifiers, settings, trackers, credentials, releases, system
from .routers import runtime_connections
from .routers import executors
from .routers import oidc as oidc_router
from .routers import oidc_admin as oidc_admin_router


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
    LogConfig.setup_logging(level=getattr(logging, await storage.get_system_log_level()))
    # Initialize configuration without AppConfig

    # Bind to app.state
    app.state.storage = storage
    app.state.system_key_manager = system_key_manager
    # app.state.config = app_config # REMOVED

    # Ensure an admin user exists
    auth_service = AuthService(storage, system_key_manager)
    await auth_service.ensure_admin_user()

    # Initialize schedulers
    scheduler_host = SchedulerHost()
    scheduler = ReleaseScheduler(storage, scheduler_host=scheduler_host)
    executor_scheduler = ExecutorScheduler(storage, scheduler_host=scheduler_host)

    # Bind schedulers to app.state
    app.state.scheduler_host = scheduler_host
    app.state.scheduler = scheduler
    app.state.executor_scheduler = executor_scheduler

    await scheduler.initialize()
    await executor_scheduler.initialize()
    await scheduler_host.start()
    await scheduler.start()
    await executor_scheduler.start()

    yield

    # Clean up on shutdown
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


# ==================== Route registration ====================

app.include_router(auth.router)
app.include_router(notifiers.router)
app.include_router(settings.router)
app.include_router(trackers.router)
app.include_router(credentials.router)
app.include_router(runtime_connections.router)
app.include_router(executors.router)
app.include_router(releases.router)
app.include_router(system.router)
app.include_router(oidc_router.router)
app.include_router(oidc_admin_router.router)


# ==================== Static file serving ====================

# Check whether the static files directory exists
static_dir = Path(__file__).resolve().parent.parent.parent / "static"

if static_dir.exists():
    from fastapi import Request
    from fastapi.responses import JSONResponse

    # Mount the assets directory for static resources such as JS and CSS
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    # Custom 404 handler for SPA fallback
    @app.exception_handler(404)
    async def custom_404_handler(request: Request, exc):
        """Handle 404 errors by returning the SPA index.html"""
        # Return index.html only for non-API and non-OIDC callback paths
        if request.url.path.startswith("/api") or request.url.path.startswith("/auth/oidc"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        # Try to return a static file
        file_path = static_dir / request.url.path.lstrip("/")
        if file_path.is_file():
            return FileResponse(file_path)

        # SPA fallback - return index.html
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)

        return JSONResponse(status_code=404, content={"detail": "Not found"})

else:

    @app.get("/")
    async def root():
        """Root path for development mode"""
        return {
            "message": app.title,
            "version": app.version,
            "note": "Frontend not available in development mode",
        }
