"""FastAPI 依赖项"""

from typing import Annotated
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from .services.auth import AuthService

# Use TYPE_CHECKING to avoid circular imports if necessary,
# but here imports should be fine if main.py is not importing dependencies at top level eagerly
# (it does, but get_storage/etc rely on app state populated at runtime)
from .storage.sqlite import SQLiteStorage
from .scheduler import ReleaseScheduler

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")


def get_storage(request: Request) -> SQLiteStorage:
    """从 app.state 获取 Storage 实例"""
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="Storage service is not initialized")
    return storage


def get_scheduler(request: Request) -> ReleaseScheduler:
    """从 app.state 获取 Scheduler 实例"""
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler:
        raise HTTPException(status_code=503, detail="Scheduler service is not initialized")
    return scheduler


def get_auth_service(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
) -> AuthService:
    """获取 AuthService 实例"""
    return AuthService(storage)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """获取当前登录用户"""
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
    """获取当前管理员用户（仅 admin 用户可访问）"""
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
