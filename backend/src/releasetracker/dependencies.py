"""FastAPI dependencies"""

from typing import Annotated
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from .services.auth import AuthService
from .services.system_keys import SystemKeyManager

# Use TYPE_CHECKING to avoid circular imports if necessary,
# but here imports should be fine if main.py is not importing dependencies at top level eagerly
# (it does, but get_storage/etc rely on app state populated at runtime)
from .storage.sqlite import SQLiteStorage
from .scheduler import ReleaseScheduler
from .scheduler_host import SchedulerHost
from .executor_scheduler import ExecutorScheduler

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")


def get_storage(request: Request) -> SQLiteStorage:
    """Get the Storage instance from app.state"""
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="Storage service is not initialized")
    return storage


def get_scheduler(request: Request) -> ReleaseScheduler:
    """Get the Scheduler instance from app.state"""
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler:
        raise HTTPException(status_code=503, detail="Scheduler service is not initialized")
    return scheduler


def get_scheduler_host(request: Request) -> SchedulerHost:
    """Get the shared SchedulerHost instance from app.state"""
    scheduler_host = getattr(request.app.state, "scheduler_host", None)
    if not scheduler_host:
        raise HTTPException(status_code=503, detail="Scheduler host service is not initialized")
    return scheduler_host


def get_executor_scheduler(request: Request) -> ExecutorScheduler:
    scheduler = getattr(request.app.state, "executor_scheduler", None)
    if not scheduler:
        raise HTTPException(status_code=503, detail="Executor scheduler service is not initialized")
    return scheduler


def get_system_key_manager(request: Request) -> SystemKeyManager:
    key_manager = getattr(request.app.state, "system_key_manager", None)
    if not key_manager:
        raise HTTPException(status_code=503, detail="System key service is not initialized")
    return key_manager


def get_auth_service(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    system_key_manager: Annotated[SystemKeyManager, Depends(get_system_key_manager)],
) -> AuthService:
    """Get the AuthService instance"""
    return AuthService(storage, system_key_manager)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """Get the current authenticated user"""
    try:
        user = await auth_service.get_current_user(token)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_admin_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """Get the current admin user; only admin users may access this"""
    try:
        user = await auth_service.get_current_user(token)
        if user.username != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
