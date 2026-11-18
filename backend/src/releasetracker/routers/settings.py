"""Settings routes"""

import logging
from typing import Literal, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from typing import Annotated
from zoneinfo import ZoneInfo

from ..services.backup_configuration import BACKUP_DEFAULTS, normalize_backup_setting
from ..services.instance_backup import BackupManagementError
from ..models import User
from ..config import READINESS_DEFAULTS, READINESS_BOUNDS
from ..storage.sqlite_tasks import FETCH_RETRY_SETTING, DEFAULT_FETCH_RETRIES
from ..storage.sqlite import (
    MAX_EXECUTOR_SNAPSHOT_RETENTION_COUNT,
    MAX_RELEASE_HISTORY_RETENTION_COUNT,
    MIN_EXECUTOR_SNAPSHOT_RETENTION_COUNT,
    MIN_RELEASE_HISTORY_RETENTION_COUNT,
    SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY,
    SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY,
    SYSTEM_TIMEZONE_SETTING_KEY,
    SYSTEM_LOG_LEVEL_SETTING_KEY,
    SYSTEM_BASE_URL_SETTING_KEY,
    SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY,
    CANONICAL_BOOLEAN_FALSE,
    CANONICAL_BOOLEAN_TRUE,
    ALLOWED_SYSTEM_LOG_LEVELS,
    RESERVED_AUTH_SETTING_KEYS,
    SQLiteStorage,
)
from ..dependencies import get_current_admin_user, get_system_key_manager
from ..services.secure_urls import require_canonical_https_base_url
from ..services.instance_policy import IMPORT_MARKER, POLICY_DEFAULTS, normalize_policy
from ..services.inbound_policy import load_policy
from ..services.metrics_credential import METRICS_TOKEN_SETTING_KEY, read_token, save_token
from ..services.outbound_http import WEBHOOK_FAKE_IP_SETTING, parse_fake_ip_cidrs
from ..services.system_keys import SystemKeyManager, rotate_encryption_key, rotate_jwt_secret

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Credentials never travel through the generic key/value routes.
RESERVED_SETTING_KEYS = RESERVED_AUTH_SETTING_KEYS | {IMPORT_MARKER, METRICS_TOKEN_SETTING_KEY}
REMOVED_BACKUP_SETTINGS = {"system.backup_daily_retention", "system.backup_weekly_retention"}


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


class ReleaseHistoryCleanupResponse(BaseModel):
    action: Literal["release_history_cleanup"]
    retention_count: int
    trackers_scanned: int
    tracker_release_history_deleted: int
    tracker_release_history_sources_deleted: int
    source_release_history_deleted: int
    source_release_run_observations_deleted: int
    sqlite_optimize_performed: bool
    wal_checkpoint_performed: bool
    vacuum_performed: bool


class SnapshotHistoryCleanupResponse(BaseModel):
    action: Literal["snapshot_history_cleanup"]
    retention_count: int
    executors_scanned: int
    executors_pruned: int
    snapshots_deleted: int


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


class MetricsTokenState(BaseModel):
    configured: bool
    token: str | None = None


def get_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="Storage service is not initialized")
    return storage


async def _update_backup_setting(request, key, value):
    backup = getattr(request.app.state, "instance_backup", None)
    if backup is None or backup.storage is not get_storage(request):
        raise HTTPException(503, "Backup service is unavailable")
    controller = getattr(request.app.state, "online_restore", None)
    if controller and (
        controller.maintenance
        or controller.lock.locked()
        or controller.plan
        or (controller.task and not controller.task.done())
    ):
        raise HTTPException(409, "backup_busy")
    try:
        return await backup.update_setting(key, value)
    except BackupManagementError as error:
        raise HTTPException(error.status_code, error.code) from None


async def _refresh_cached_container_redirect_settings(request: Request) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    refresh_redirect_settings = getattr(
        scheduler,
        "refresh_container_tracker_redirect_settings",
        None,
    )
    if refresh_redirect_settings is not None:
        await refresh_redirect_settings()


async def _refresh_instance_policy(request: Request, key: str) -> None:
    storage = get_storage(request)
    if key in {"system.cors_origins", "system.forwarded_allow_ips"}:
        request.app.state.inbound_policy = await load_policy(storage)
        return
    service_name = {
        "system.worker_poll_seconds": "scheduler_host",
        "system.mutation_concurrency": "task_queue",
        "system.runtime_health_interval_seconds": "runtime_health_watch",
    }.get(key)
    service = getattr(request.app.state, service_name, None) if service_name else None
    if service is not None:
        await service.apply_policy(storage)


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
    if key in POLICY_DEFAULTS:
        try:
            return normalize_policy(key, value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    normalized_value = str(value).strip()

    if key == WEBHOOK_FAKE_IP_SETTING:
        try:
            return "\n".join(map(str, parse_fake_ip_cidrs(value)))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    if key in BACKUP_DEFAULTS:
        try:
            return normalize_backup_setting(key, value)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    field = key.removeprefix("system.")
    if key.startswith("system.") and field in READINESS_BOUNDS:
        lower, upper = READINESS_BOUNDS[field]
        if (
            not normalized_value.isascii()
            or not normalized_value.isdecimal()
            or not lower <= int(normalized_value) <= upper
        ):
            raise HTTPException(
                status_code=400, detail=f"{field} must be an integer from {lower} to {upper}"
            )
        return str(int(normalized_value))
    if key == FETCH_RETRY_SETTING:
        if not normalized_value.isascii() or not normalized_value.isdecimal():
            raise HTTPException(
                status_code=400, detail="Fetch retry count must be an integer from 0 to 10"
            )
        count = int(normalized_value)
        if not 0 <= count <= 10:
            raise HTTPException(
                status_code=400, detail="Fetch retry count must be between 0 and 10"
            )
        return str(count)

    if key == SYSTEM_TIMEZONE_SETTING_KEY:
        timezone_value = normalized_value or "UTC"
        try:
            ZoneInfo(timezone_value)
        except Exception:
            raise HTTPException(
                status_code=400, detail="System timezone must be a valid IANA timezone"
            )
        return timezone_value

    if key == SYSTEM_LOG_LEVEL_SETTING_KEY:
        log_level = normalized_value.upper()
        if log_level not in ALLOWED_SYSTEM_LOG_LEVELS:
            raise HTTPException(
                status_code=400, detail="Log level must be DEBUG, INFO, WARNING, or ERROR"
            )
        return log_level

    if key == SYSTEM_BASE_URL_SETTING_KEY:
        if not normalized_value:
            return ""
        try:
            return require_canonical_https_base_url(normalized_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if key == SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY:
        if str(value) != normalized_value or normalized_value not in {
            CANONICAL_BOOLEAN_TRUE,
            CANONICAL_BOOLEAN_FALSE,
        }:
            raise HTTPException(
                status_code=400,
                detail="OCI registry redirects setting must be true or false",
            )
        return normalized_value

    if key != SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY:
        if key == SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY:
            try:
                snapshot_retention = int(normalized_value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="executor snapshot retention count must be an integer",
                )

            if not (
                MIN_EXECUTOR_SNAPSHOT_RETENTION_COUNT
                <= snapshot_retention
                <= MAX_EXECUTOR_SNAPSHOT_RETENTION_COUNT
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "executor snapshot retention count must be between "
                        f"{MIN_EXECUTOR_SNAPSHOT_RETENTION_COUNT} and "
                        f"{MAX_EXECUTOR_SNAPSHOT_RETENTION_COUNT}"
                    ),
                )

            return str(snapshot_retention)

        return value

    try:
        retention_count = int(normalized_value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail="Release history retention count must be an integer"
        )

    if not (
        MIN_RELEASE_HISTORY_RETENTION_COUNT
        <= retention_count
        <= MAX_RELEASE_HISTORY_RETENTION_COUNT
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Release history retention count must be between {MIN_RELEASE_HISTORY_RETENTION_COUNT} "
                f"and {MAX_RELEASE_HISTORY_RETENTION_COUNT}"
            ),
        )

    return str(retention_count)


@router.get(
    "/metrics-token",
    response_model=MetricsTokenState,
    response_model_exclude_none=True,
    dependencies=[Depends(get_current_admin_user)],
)
async def get_metrics_token_state(
    request: Request,
    response: Response,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    response.headers["Cache-Control"] = "no-store"
    configured, token = await read_token(get_storage(request))
    return MetricsTokenState(configured=configured, token=token)


@router.put(
    "/metrics-token",
    response_model=MetricsTokenState,
    response_model_exclude_none=True,
    dependencies=[Depends(get_current_admin_user)],
)
async def update_metrics_token(
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Set or rotate the scrape credential; an empty token revokes it.

    The body is read by hand because FastAPI's request validation echoes rejected
    input in its error details, which would disclose a submitted credential.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = None
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str):
        raise HTTPException(status_code=400, detail="Metrics token must be a string")
    try:
        configured = await save_token(get_storage(request), token)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return MetricsTokenState(configured=configured)


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


@router.post(
    "/actions/cleanup-release-history",
    response_model=ReleaseHistoryCleanupResponse,
    dependencies=[Depends(get_current_admin_user)],
)
async def cleanup_release_history_endpoint(
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    storage: SQLiteStorage = get_storage(request)
    result = await storage.cleanup_release_history()
    return ReleaseHistoryCleanupResponse(
        action="release_history_cleanup",
        **result,
    )


@router.post(
    "/actions/cleanup-snapshot-history",
    response_model=SnapshotHistoryCleanupResponse,
    dependencies=[Depends(get_current_admin_user)],
)
async def cleanup_snapshot_history_endpoint(
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    storage: SQLiteStorage = get_storage(request)
    executor_scheduler = getattr(request.app.state, "executor_scheduler", None)
    snapshot_service = getattr(executor_scheduler, "snapshot_service", None)
    if snapshot_service is None:
        raise HTTPException(
            status_code=503,
            detail="Snapshot cleanup service is not initialized",
        )

    retention = await storage.get_executor_snapshot_retention_count()
    executors = await storage.get_all_executor_configs()
    executors_scanned = 0
    executors_pruned = 0
    snapshots_deleted = 0
    for executor in executors:
        if executor.id is None:
            continue
        executors_scanned += 1
        deleted = await snapshot_service.prune_after_insert(executor.id, retention)
        if deleted:
            executors_pruned += 1
            snapshots_deleted += len(deleted)

    return SnapshotHistoryCleanupResponse(
        action="snapshot_history_cleanup",
        retention_count=retention,
        executors_scanned=executors_scanned,
        executors_pruned=executors_pruned,
        snapshots_deleted=snapshots_deleted,
    )


@router.get("", response_model=List[SettingItem], dependencies=[Depends(get_current_admin_user)])
async def get_settings(
    request: Request, current_user: Annotated[User, Depends(get_current_admin_user)]
):
    """Get all system settings"""
    storage: SQLiteStorage = get_storage(request)
    settings: dict[str, tuple[str, str | None]] = {
        key: (value, updated_at)
        for key, (value, updated_at) in (await storage.get_all_settings_with_updated_at()).items()
    }
    for key, default in POLICY_DEFAULTS.items():
        settings.setdefault(key, (default, None))
    for key, default in BACKUP_DEFAULTS.items():
        settings.setdefault(key, (default, None))
    settings.setdefault(FETCH_RETRY_SETTING, (str(DEFAULT_FETCH_RETRIES), None))
    settings.setdefault(WEBHOOK_FAKE_IP_SETTING, ("", None))
    for field, default in READINESS_DEFAULTS.items():
        settings.setdefault(f"system.{field}", (str(default), None))
    oci_redirects_setting = settings.get(SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY)
    if oci_redirects_setting is None or oci_redirects_setting[0] not in {
        CANONICAL_BOOLEAN_TRUE,
        CANONICAL_BOOLEAN_FALSE,
    }:
        settings[SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY] = (
            CANONICAL_BOOLEAN_FALSE,
            None,
        )
    return [
        SettingItem(key=key, value=value, updated_at=updated_at)
        for key, (value, updated_at) in settings.items()
        if key not in RESERVED_SETTING_KEYS and key not in REMOVED_BACKUP_SETTINGS
    ]


@router.post("", response_model=SettingItem, dependencies=[Depends(get_current_admin_user)])
async def update_setting(
    setting: SettingItem,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Update system settings"""
    storage: SQLiteStorage = get_storage(request)
    if setting.key in REMOVED_BACKUP_SETTINGS:
        raise HTTPException(status_code=400, detail="This backup setting has been removed")
    if setting.key in RESERVED_SETTING_KEYS:
        raise HTTPException(status_code=403, detail="Reserved authentication setting")

    setting.value = _normalize_setting_value(setting.key, setting.value)
    if setting.key in BACKUP_DEFAULTS:
        setting.updated_at = await _update_backup_setting(request, setting.key, setting.value)
    else:
        setting.updated_at = await storage.set_setting(setting.key, setting.value)
    if setting.key == SYSTEM_LOG_LEVEL_SETTING_KEY:
        logging.getLogger().setLevel(getattr(logging, setting.value))
    if setting.key == SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY:
        await _refresh_cached_container_redirect_settings(request)
    if setting.key in POLICY_DEFAULTS:
        await _refresh_instance_policy(request, setting.key)
    return setting


@router.delete("/{key}", dependencies=[Depends(get_current_admin_user)])
async def delete_setting(
    key: str, request: Request, current_user: Annotated[User, Depends(get_current_admin_user)]
):
    """Delete a system setting"""
    storage: SQLiteStorage = get_storage(request)
    if key in RESERVED_SETTING_KEYS:
        raise HTTPException(status_code=403, detail="Reserved authentication setting")
    if key in BACKUP_DEFAULTS:
        await _update_backup_setting(request, key, None)
    else:
        await storage.delete_setting(key)
    if key == SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY:
        await _refresh_cached_container_redirect_settings(request)
    if key in POLICY_DEFAULTS:
        await _refresh_instance_policy(request, key)
    return {"message": "Setting deleted"}
