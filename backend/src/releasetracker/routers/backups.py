"""Administrator-only backup management. Restoration uses an owned maintenance coordinator."""

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from ..services.online_restore import RestoreError

from ..dependencies import get_current_admin_user
from ..models import User
from ..services.instance_backup import (
    BackupManagementError,
    archive_created_at,
    backup_options,
    retention_tiers,
)

status_router = APIRouter(prefix="/api/backups", tags=["backups"])

router = APIRouter(
    prefix="/api/backups", tags=["backups"], dependencies=[Depends(get_current_admin_user)]
)


class CreateBackup(BaseModel):
    include_secrets_confirmed: bool = False


class DeleteBackup(BaseModel):
    confirm_name: str


class DownloadBackupResponse(FileResponse):
    def __init__(self, *args, backup, archive_name, **kwargs):
        super().__init__(*args, **kwargs)
        self.backup = backup
        self.archive_name = archive_name

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.backup.release_download(self.archive_name)


def service(request: Request):
    backup = getattr(request.app.state, "instance_backup", None)
    if backup is None:
        raise HTTPException(503, "Backup service is unavailable")
    return backup


def entry(path: Path):
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "created_at": archive_created_at(path)}


@router.get("")
async def list_backups(request: Request):
    backup = service(request)
    hours, retain = await backup_options(backup.storage)
    status = await backup.status()
    latest = backup.latest_archive_time()
    days, weeks = retention_tiers()
    items = [entry(path) for path in backup.archives()]
    for item in items:
        item["in_use"] = (
            bool(backup._downloads.get(item["name"])) or item["name"] in backup._restore_pins
        )
    return {
        "directory": str(backup.directory),
        "interval_hours": hours,
        "retention": retain,
        "daily_retention": days,
        "weekly_retention": weeks,
        "total_size": sum(item["size"] for item in items),
        "minimum_local_archives": 1,
        "running": backup.lock.locked(),
        "restore_review_required": await backup.storage.get_setting("restore.review_required")
        is not None,
        "last_success_at": latest,
        "last_failure_at": status.get("last_failure_at"),
        "last_error_code": status.get("last_error_code"),
        "consecutive_failures": int(status.get("consecutive_failures") or 0),
        # Overdue when scheduled backups exist but the newest archive is older
        # than twice the interval (one missed run tolerated).
        "overdue": bool(hours) and (latest is None or time.time() - latest > hours * 7200),
        "items": items,
        "online_restore_available": getattr(request.app.state, "online_restore", None) is not None,
        "safety_backup": (
            getattr(request.app.state, "online_restore", None).safety_entry()
            if getattr(request.app.state, "online_restore", None)
            else None
        ),
    }


@router.post("", status_code=201)
async def create_backup(body: CreateBackup, request: Request):
    if not body.include_secrets_confirmed:
        raise HTTPException(400, "Confirm that the backup contains sensitive credentials")
    backup = service(request)
    try:
        _, retain = await backup_options(backup.storage)
        path = await backup.create(retain=retain)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Instance backup failed")
        raise HTTPException(503, "Backup failed; check server logs") from exc
    return entry(path)


@router.delete("/{name}")
async def delete_backup(name: str, body: DeleteBackup, request: Request):
    if body.confirm_name != name:
        raise HTTPException(400, "backup_confirmation_mismatch")
    try:
        await service(request).delete(name)
    except BackupManagementError as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    except OSError:
        raise HTTPException(503, "backup_delete_failed") from None
    return {"deleted": name}


class RestoreConfirmation(BaseModel):
    plan_id: str = Field(max_length=64)
    fingerprint: str = Field(max_length=64)
    confirm_name: str = Field(max_length=128)
    data_loss_confirmed: bool = False


class RestoreReview(BaseModel):
    reviewed: bool = False


def controller(request):
    value = getattr(request.app.state, "online_restore", None)
    if value is None:
        raise HTTPException(503, "online_restore_unavailable")
    return value


@router.post("/{name}/restore-plan")
async def restore_plan(name: str, request: Request, user: User = Depends(get_current_admin_user)):
    try:
        value = await controller(request).preview(name, user.id)
    except (RestoreError, BackupManagementError) as error:
        raise HTTPException(
            getattr(error, "status", getattr(error, "status_code", 409)), error.code
        ) from None
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.delete("/{name}/restore-plan")
async def cancel_restore_plan(
    name: str, request: Request, user: User = Depends(get_current_admin_user)
):
    value = controller(request)
    async with value.lock:
        if value.plan and value.plan["name"] == name and value.plan["actor"] == user.id:
            value.drop_plan()
    return {"cancelled": True}


@router.post("/{name}/restore", status_code=202)
async def restore_instance(
    name: str,
    body: RestoreConfirmation,
    request: Request,
    user: User = Depends(get_current_admin_user),
):
    if body.confirm_name != name or not body.data_loss_confirmed:
        raise HTTPException(400, "restore_confirmation_required")
    try:
        receipt = await controller(request).begin(name, body, user.id)
    except (RestoreError, BackupManagementError) as error:
        raise HTTPException(
            getattr(error, "status", getattr(error, "status_code", 409)), error.code
        ) from None
    return JSONResponse(receipt, status_code=202, headers={"Cache-Control": "no-store"})


@status_router.get("/restore-status/{identifier}")
async def restore_status(
    identifier: str,
    request: Request,
    token: str = Header(default="", alias="X-Restore-Token", max_length=128),
):
    # A one-hour read-only capability remains usable after old sessions are
    # invalidated. It is supplied in a header, never URL/access logs.
    try:
        value = controller(request).status(identifier, token)
    except RestoreError as error:
        raise HTTPException(error.status, error.code) from None
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.post("/restore-review")
async def review_restored_instance(body: RestoreReview, request: Request):
    if not body.reviewed:
        raise HTTPException(400, "restore_review_required")
    try:
        await controller(request).review()
    except RestoreError as error:
        raise HTTPException(error.status, error.code) from None
    return {"review_required": False}


@router.get("/restore-safety/current/download")
async def download_restore_safety(request: Request):
    value = controller(request)
    try:
        path = value.acquire_safety()
    except RestoreError as error:
        raise HTTPException(error.status, error.code) from None
    return DownloadBackupResponse(
        path,
        backup=value,
        archive_name="safety",
        media_type="application/zip",
        filename="before-online-restore.zip",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{name}/download")
async def download_backup(name: str, request: Request):
    backup = service(request)
    try:
        path = await backup.acquire_download(name)
    except BackupManagementError as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    return DownloadBackupResponse(
        path,
        backup=backup,
        archive_name=name,
        media_type="application/zip",
        filename=name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
