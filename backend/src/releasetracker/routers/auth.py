"""认证路由"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated

from ..models import LoginRequest, RegisterRequest, User, TokenPair, Session, ChangePasswordRequest
from ..services.auth import AuthService
from ..storage.sqlite import SQLiteStorage

router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# 依赖项：获取 AuthService
# 依赖项：获取 AuthService
def get_auth_service(request: Request):
    storage = getattr(request.app.state, "storage", None)
    app_config = getattr(request.app.state, "config", None)
    
    if not storage or not app_config:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return AuthService(storage, app_config)

# 依赖项：获取当前用户
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    try:
        user = await auth_service.get_current_user(token)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )

@router.post("/register", response_model=User, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    try:
        user = await auth_service.register(req)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/login", response_model=dict)
async def login(
    req: LoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    try:
        # TODO: 获取真实 IP 和 UA
        user, token_pair = await auth_service.login(req, user_agent="unknown", ip_address="127.0.0.1")
        return {
            "user": user,
            "token": token_pair
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )

@router.post("/logout")
async def logout(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    await auth_service.logout(token)
    return {"message": "Logged out successfully"}

@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    try:
        await auth_service.change_password(token, req)
        return {"message": "Password changed successfully"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/me", response_model=User)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)]
):
    return current_user

@router.post("/refresh", response_model=TokenPair)
async def refresh_token(
    refresh_token: str,
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    try:
        return await auth_service.refresh_token(refresh_token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
