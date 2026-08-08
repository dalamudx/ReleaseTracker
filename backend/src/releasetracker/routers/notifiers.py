"""Notifier routes"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Annotated
from datetime import datetime

from ..models import Notifier, User
from ..notifiers.webhook import WebhookNotifier

# ...

from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_admin_user

router = APIRouter(prefix="/api/notifiers", tags=["notifiers"])


def get_storage(request):
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="Storage service is not initialized")
    return storage


@router.get("", dependencies=[Depends(get_current_admin_user)])
async def get_notifiers(
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
    skip: int = 0,
    limit: int = 20,
):
    """Get all notifiers with pagination"""
    storage: SQLiteStorage = get_storage(request)

    total = await storage.get_total_notifiers_count()
    notifiers = await storage.get_notifiers_paginated(skip, limit)

    return {"items": notifiers, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/{notifier_id}", response_model=Notifier, dependencies=[Depends(get_current_admin_user)]
)
async def get_notifier(
    notifier_id: int,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Get a single notifier"""
    storage: SQLiteStorage = get_storage(request)
    notifier = await storage.get_notifier(notifier_id)
    if not notifier:
        raise HTTPException(status_code=404, detail="Notifier not found")
    return notifier


@router.post(
    "",
    response_model=Notifier,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin_user)],
)
async def create_notifier(
    notifier_data: dict,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Create a notifier"""
    storage: SQLiteStorage = get_storage(request)

    # Basic validation
    if "name" not in notifier_data or not notifier_data["name"]:
        raise HTTPException(status_code=400, detail="Name is required")
    if "url" not in notifier_data or not notifier_data["url"]:
        raise HTTPException(status_code=400, detail="URL is required")

    try:
        return await storage.create_notifier(notifier_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put(
    "/{notifier_id}", response_model=Notifier, dependencies=[Depends(get_current_admin_user)]
)
async def update_notifier(
    notifier_id: int,
    notifier_data: dict,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Update a notifier"""
    storage: SQLiteStorage = get_storage(request)
    try:
        return await storage.update_notifier(notifier_id, notifier_data)
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{notifier_id}", dependencies=[Depends(get_current_admin_user)])
async def delete_notifier(
    notifier_id: int,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Delete a notifier"""
    storage: SQLiteStorage = get_storage(request)
    try:
        await storage.delete_notifier(notifier_id)
        return {"message": "Notifier deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{notifier_id}/test", dependencies=[Depends(get_current_admin_user)])
async def test_notifier(
    notifier_id: int,
    request: Request,
    current_user: Annotated[User, Depends(get_current_admin_user)],
):
    """Test a notifier"""
    storage: SQLiteStorage = get_storage(request)
    notifier = await storage.get_notifier(notifier_id)
    if not notifier:
        raise HTTPException(status_code=404, detail="Notifier not found")

    message = (
        "这是一条来自 ReleaseTracker 的测试通知"
        if notifier.language == "zh"
        else "This is a test notification from ReleaseTracker"
    )
    payload = {
        "event": "test",
        "message": message,
        "content": message,  # Discord compatibility
        "text": message,  # Slack compatibility
        "timestamp": datetime.now().isoformat(),
    }

    if not notifier.url:
        raise HTTPException(status_code=400, detail="Webhook URL is missing")

    delivered = await WebhookNotifier(
        notifier.name,
        notifier.url,
        events=["test"],
        language=notifier.language,
    ).send_payload(payload)
    if not delivered:
        raise HTTPException(status_code=400, detail="Webhook test failed")
    return {"message": "Test notification sent successfully"}
