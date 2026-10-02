"""Administrator-only backup management. Restoration is intentionally offline."""

import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..dependencies import get_current_admin_user
from ..services.instance_backup import archive_created_at, backup_options

router = APIRouter(
    prefix="/api/backups", tags=["backups"], dependencies=[Depends(get_current_admin_user)]
)


class CreateBackup(BaseModel):
    include_secrets_confirmed: bool = False


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
    hours, retain = backup_options()
    status = await backup.status()
    latest = backup.latest_archive_time()
    return {
        "interval_hours": hours,
        "retention": retain,
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
        "items": [
            entry(p)
            for p in sorted(
                (
                    p
                    for p in backup.directory.glob("releasetracker-*.zip")
                    if p.is_file() and not p.is_symlink()
                ),
                key=archive_created_at,
                reverse=True,
            )
            if p.is_file() and not p.is_symlink()
        ],
    }


@router.post("", status_code=201)
async def create_backup(body: CreateBackup, request: Request):
    if not body.include_secrets_confirmed:
        raise HTTPException(400, "Confirm that the backup contains sensitive credentials")
    backup = service(request)
    try:
        _, retain = backup_options()
        path = await backup.create(retain=retain)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Instance backup failed")
        raise HTTPException(503, "Backup failed; check server logs") from exc
    return entry(path)


@router.get("/{name}/download")
async def download_backup(name: str, request: Request):
    backup = service(request)
    if not re.fullmatch(r"releasetracker-[0-9]+-[0-9a-f]{8}\.zip", name):
        raise HTTPException(404, "Backup not found")
    path = backup.directory / name
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "Backup not found")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
