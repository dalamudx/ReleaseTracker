"""OIDC 提供商管理路由（仅管理员）"""

import logging
from typing import Annotated
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel

from ..storage.sqlite import SQLiteStorage
from ..oidc_models import OIDCProvider
from ..models import User
from ..dependencies import get_storage, get_current_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oidc-providers", tags=["OIDC Management"])


class CreateOIDCProviderRequest(BaseModel):
    """创建 OIDC 提供商请求"""

    name: str
    slug: str
    issuer_url: str | None = None
    discovery_enabled: bool = True
    client_id: str
    client_secret: str
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    jwks_uri: str | None = None
    scopes: str = "openid email profile"
    enabled: bool = True
    icon_url: str | None = None
    description: str | None = None


class UpdateOIDCProviderRequest(BaseModel):
    """更新 OIDC 提供商请求（所有字段可选）"""

    name: str | None = None
    issuer_url: str | None = None
    discovery_enabled: bool | None = None
    client_id: str | None = None
    client_secret: str | None = None  # 不传则不更新密钥
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    jwks_uri: str | None = None
    scopes: str | None = None
    enabled: bool | None = None
    icon_url: str | None = None
    description: str | None = None


def _provider_to_response(p: OIDCProvider) -> dict:
    """将 OIDCProvider 转换为响应字典（不返回 client_secret）"""
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "issuer_url": p.issuer_url,
        "discovery_enabled": p.discovery_enabled,
        "client_id": p.client_id,
        # client_secret 永不返回给前端
        "authorization_url": p.authorization_url,
        "token_url": p.token_url,
        "userinfo_url": p.userinfo_url,
        "jwks_uri": p.jwks_uri,
        "scopes": p.scopes,
        "enabled": p.enabled,
        "icon_url": p.icon_url,
        "description": p.description,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_oidc_provider(
    req: CreateOIDCProviderRequest,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    _: Annotated[User, Depends(get_current_admin_user)],
):
    """创建 OIDC 提供商配置（仅管理员）"""
    # 检查 slug 唯一性
    existing = await storage.get_oauth_provider(req.slug)
    if existing:
        raise HTTPException(status_code=400, detail="Slug 已存在")

    provider = OIDCProvider(
        name=req.name,
        slug=req.slug,
        issuer_url=req.issuer_url,
        discovery_enabled=req.discovery_enabled,
        client_id=req.client_id,
        client_secret=req.client_secret,
        authorization_url=req.authorization_url,
        token_url=req.token_url,
        userinfo_url=req.userinfo_url,
        jwks_uri=req.jwks_uri,
        scopes=req.scopes,
        enabled=req.enabled,
        icon_url=req.icon_url,
        description=req.description,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    saved = await storage.save_oauth_provider(provider)
    return {"message": "OIDC 提供商已创建", "id": saved.id}


@router.get("")
async def list_oidc_providers_admin(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    _: Annotated[User, Depends(get_current_admin_user)],
):
    """列出所有 OIDC 提供商配置（仅管理员）"""
    providers = await storage.list_oauth_providers(enabled_only=False)
    return [_provider_to_response(p) for p in providers]


@router.get("/{provider_id}")
async def get_oidc_provider(
    provider_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    _: Annotated[User, Depends(get_current_admin_user)],
):
    """获取 OIDC 提供商详情（仅管理员）"""
    provider = await storage.get_oauth_provider_by_id(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="OIDC 提供商不存在")
    return _provider_to_response(provider)


@router.put("/{provider_id}")
async def update_oidc_provider(
    provider_id: int,
    req: UpdateOIDCProviderRequest,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    _: Annotated[User, Depends(get_current_admin_user)],
):
    """更新 OIDC 提供商配置（仅管理员）"""
    existing = await storage.get_oauth_provider_by_id(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="OIDC 提供商不存在")

    # 合并更新字段
    updated = OIDCProvider(
        id=provider_id,
        name=req.name if req.name is not None else existing.name,
        slug=existing.slug,  # slug 不允许修改
        issuer_url=req.issuer_url if req.issuer_url is not None else existing.issuer_url,
        discovery_enabled=(
            req.discovery_enabled
            if req.discovery_enabled is not None
            else existing.discovery_enabled
        ),
        client_id=req.client_id if req.client_id is not None else existing.client_id,
        client_secret=req.client_secret,  # None 表示不更新
        authorization_url=(
            req.authorization_url
            if req.authorization_url is not None
            else existing.authorization_url
        ),
        token_url=req.token_url if req.token_url is not None else existing.token_url,
        userinfo_url=req.userinfo_url if req.userinfo_url is not None else existing.userinfo_url,
        jwks_uri=req.jwks_uri if req.jwks_uri is not None else existing.jwks_uri,
        scopes=req.scopes if req.scopes is not None else existing.scopes,
        enabled=req.enabled if req.enabled is not None else existing.enabled,
        icon_url=req.icon_url if req.icon_url is not None else existing.icon_url,
        description=req.description if req.description is not None else existing.description,
        created_at=existing.created_at,
        updated_at=datetime.now(),
    )
    await storage.update_oauth_provider(provider_id, updated)
    return {"message": "OIDC 提供商已更新"}


@router.delete("/{provider_id}")
async def delete_oidc_provider(
    provider_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    _: Annotated[User, Depends(get_current_admin_user)],
):
    """删除 OIDC 提供商配置（仅管理员）"""
    existing = await storage.get_oauth_provider_by_id(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="OIDC 提供商不存在")
    await storage.delete_oauth_provider(provider_id)
    return {"message": "OIDC 提供商已删除"}
