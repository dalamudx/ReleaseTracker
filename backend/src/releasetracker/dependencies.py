"""FastAPI 依赖项"""

from typing import Annotated
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from .services.auth import AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")

def get_auth_service(request: Request):
    """获取 AuthService 实例"""
    storage = getattr(request.app.state, "storage", None)
    app_config = getattr(request.app.state, "config", None)
    
    if not storage or not app_config:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return AuthService(storage, app_config)

async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
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
