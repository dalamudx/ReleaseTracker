"""Settings routes"""

import logging
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Annotated
from zoneinfo import ZoneInfo

from ..models import User
from ..storage.sqlite import (
    MAX_RELEASE_HISTORY_RETENTION_COUNT,
    MIN_RELEASE_HISTORY_RETENTION_COUNT,
    SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY,
    SYSTEM_TIMEZONE_SETTING_KEY,
    SYSTEM_LOG_LEVEL_SETTING_KEY,
    SYSTEM_BASE_URL_SETTING_KEY,
    ALLOWED_SYSTEM_LOG_LEVELS,
    SQLiteStorage,
)
from ..dependencies import get_current_admin_user, get_current_user, get_system_key_manager
from ..services.system_keys import SystemKeyManager, rotate_encryption_key, rotate_jwt_secret

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingItem(BaseModel):
    key: str
    value: str
    updated_at: Optional[str] = None


class SecurityKeyState(BaseModel):
    configured: bool
    fingerprint: str


class JwtSecretState(SecurityKeyState):
    active_sessions: int


class EncryptionKeyState(SecurityKeyState):
    inventory: dict[str, int]
    undecryptable_count: int


class SecurityKeysStatus(BaseModel):
    jwt_secret: JwtSecretState
    encryption_key: EncryptionKeyState


class SecurityKeyRotationRequest(BaseModel):
    value: str | None = None
    generate: bool = False


class JwtSecretRotationResponse(BaseModel):
    fingerprint: str
    invalidated_sessions: int
    requires_reauth: bool


class EncryptionKeyRotationResponse(BaseModel):
    fingerprint: str
    inventory: dict[str, int]
    rotated: dict[str, int]
    plaintext_reencrypted: int
    undecryptable_count: int


def get_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    return storage


async def _build_security_keys_status(
    storage: SQLiteStorage,
    key_manager: SystemKeyManager,
) -> SecurityKeysStatus:
    encryption_status = await storage.get_encryption_key_inventory()
    return SecurityKeysStatus(
        jwt_secret=JwtSecretState(
            configured=True,
            fingerprint=key_manager.fingerprint(key_manager.jwt_secret),
            active_sessions=await storage.count_active_sessions(),
        ),
        encryption_key=EncryptionKeyState(
            configured=True,
            fingerprint=key_manager.fingerprint(key_manager.encryption_key),
            inventory=encryption_status["inventory"],
            undecryptable_count=encryption_status["undecryptable_count"],
        ),
    )


def _normalize_setting_value(key: str, value: str) -> str:
    normalized_value = str(value).strip()

    if key == SYSTEM_TIMEZONE_SETTING_KEY:
        timezone_value = normalized_value or "UTC"
        try:
            ZoneInfo(timezone_value)
        except Exception:
            raise HTTPException(status_code=400, detail="系统时区必须是有效的 IANA 时区")
        return timezone_value

    if key == SYSTEM_LOG_LEVEL_SETTING_KEY:
        log_level = normalized_value.upper()
        if log_level not in ALLOWED_SYSTEM_LOG_LEVELS:
            raise HTTPException(status_code=400, detail="日志级别必须是 DEBUG、INFO、WARNING 或 ERROR")
        return log_level

    if key == SYSTEM_BASE_URL_SETTING_KEY:
        base_url = normalized_value.rstrip("/")
        if not base_url:
            return ""
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(status_code=400, detail="BASE URL 必须是有效的 http(s) 绝对地址")
        return base_url

    if key != SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY:
        return value

    try:
        retention_count = int(normalized_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="版本历史保留数量必须是整数")

    if not (
        MIN_RELEASE_HISTORY_RETENTION_COUNT
        <= retention_count
        <= MAX_RELEASE_HISTORY_RETENTION_COUNT
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"版本历史保留数量必须在 {MIN_RELEASE_HISTORY_RETENTION_COUNT} 到 "
                f"{MAX_RELEASE_HISTORY_RETENTION_COUNT} 之间"
            ),
        )

    return str(retention_count)


@router.get(
    "/security-keys",
    response_model=SecurityKeysStatus,
    dependencies=[Depends(get_current_admin_user)],
)
async def get_security_keys_status(
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
    key_manager: Annotated[SystemKeyManager, Depends(get_system_key_manager)],
):
    storage: SQLiteStorage = get_storage(request)
    return await _build_security_keys_status(storage, key_manager)


@router.post(
    "/security-keys/jwt-secret",
    response_model=JwtSecretRotationResponse,
    dependencies=[Depends(get_current_admin_user)],
)
async def rotate_jwt_secret_endpoint(
    req: SecurityKeyRotationRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
    key_manager: Annotated[SystemKeyManager, Depends(get_system_key_manager)],
):
    storage: SQLiteStorage = get_storage(request)
    try:
        return await rotate_jwt_secret(
            storage,
            key_manager,
            value=req.value,
            generate=req.generate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/security-keys/encryption-key",
    response_model=EncryptionKeyRotationResponse,
    dependencies=[Depends(get_current_admin_user)],
)
async def rotate_encryption_key_endpoint(
    req: SecurityKeyRotationRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
    key_manager: Annotated[SystemKeyManager, Depends(get_system_key_manager)],
):
    storage: SQLiteStorage = get_storage(request)
    try:
        return await rotate_encryption_key(
            storage,
            key_manager,
            value=req.value,
            generate=req.generate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=List[SettingItem], dependencies=[Depends(get_current_user)])
async def get_settings(request: Request, current_user: Annotated[User, Depends(get_current_user)]):
    """Get all system settings"""
    storage: SQLiteStorage = get_storage(request)
    settings_dict = await storage.get_all_settings()
    return [
        SettingItem(
            key=k, value=v, updated_at=datetime.now().isoformat()
        )  # TODO: Fetch real updated_at from DB
        for k, v in settings_dict.items()
    ]


@router.post("", response_model=SettingItem, dependencies=[Depends(get_current_user)])
async def update_setting(
    setting: SettingItem, request: Request, current_user: Annotated[User, Depends(get_current_user)]
):
    """Update system settings"""
    storage: SQLiteStorage = get_storage(request)

    setting.value = _normalize_setting_value(setting.key, setting.value)
    await storage.set_setting(setting.key, setting.value)
    if setting.key == SYSTEM_LOG_LEVEL_SETTING_KEY:
        logging.getLogger().setLevel(getattr(logging, setting.value))
    return setting


@router.delete("/{key}", dependencies=[Depends(get_current_user)])
async def delete_setting(
    key: str, request: Request, current_user: Annotated[User, Depends(get_current_user)]
):
    """Delete a system setting"""
    storage: SQLiteStorage = get_storage(request)
    await storage.delete_setting(key)
    return {"message": "Setting deleted"}
