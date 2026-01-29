"""认证路由"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import Annotated

from ..models import LoginRequest, RegisterRequest, User, TokenPair, Session, ChangePasswordRequest
from ..services.auth import AuthService
from ..storage.sqlite import SQLiteStorage

from ..dependencies import get_auth_service, get_current_user, oauth2_scheme

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/register", response_model=User, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    current_user: Annotated[User, Depends(get_current_user)]  # 需要认证
):
    """
    创建新用户（仅管理员可用）
    
    注意：此接口需要管理员权限。普通用户使用内置管理员账户登录。
    """
    # 可选：检查是否为管理员（如果有角色系统）

    #     raise HTTPException(status_code=403, detail="仅管理员可创建用户")
    
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

@router.post("/token", response_model=TokenPair)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    """OAuth2 兼容的登录接口 (供 Swagger UI 使用)"""
    req = LoginRequest(username=form_data.username, password=form_data.password)
    try:
        user, token_pair = await auth_service.login(req, user_agent="Swagger UI", ip_address="127.0.0.1")
        return token_pair
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
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
