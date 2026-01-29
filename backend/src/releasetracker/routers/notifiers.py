"""通知器路由"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Annotated, List
from datetime import datetime

from ..models import Notifier, User
# ...

from ..services.auth import AuthService
from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user

router = APIRouter(prefix="/api/notifiers", tags=["notifiers"])

def get_storage(request):
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    return storage

@router.get("", dependencies=[Depends(get_current_user)])
async def get_notifiers(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    skip: int = 0,
    limit: int = 20
):
    """获取所有通知器（分页）"""
    storage: SQLiteStorage = get_storage(request)
    
    total = await storage.get_total_notifiers_count()
    notifiers = await storage.get_notifiers_paginated(skip, limit)
    
    return {
        "items": notifiers,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.get("/{notifier_id}", response_model=Notifier, dependencies=[Depends(get_current_user)])
async def get_notifier(
    notifier_id: int,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取单个通知器"""
    storage: SQLiteStorage = get_storage(request)
    notifier = await storage.get_notifier(notifier_id)
    if not notifier:
        raise HTTPException(status_code=404, detail="Notifier not found")
    return notifier

@router.post("", response_model=Notifier, status_code=status.HTTP_201_CREATED, dependencies=[Depends(get_current_user)])
async def create_notifier(
    notifier_data: dict,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """创建通知器"""
    storage: SQLiteStorage = get_storage(request)
    
    # 简单的验证
    if "name" not in notifier_data or not notifier_data["name"]:
        raise HTTPException(status_code=400, detail="Name is required")
    if "url" not in notifier_data or not notifier_data["url"]:
        raise HTTPException(status_code=400, detail="URL is required")
        
    try:
        return await storage.create_notifier(notifier_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{notifier_id}", response_model=Notifier, dependencies=[Depends(get_current_user)])
async def update_notifier(
    notifier_id: int,
    notifier_data: dict,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """更新通知器"""
    storage: SQLiteStorage = get_storage(request)
    try:
        return await storage.update_notifier(notifier_id, notifier_data)
    except ValueError as e:
        if "not found" in str(e):
             raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{notifier_id}", dependencies=[Depends(get_current_user)])
async def delete_notifier(
    notifier_id: int,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """删除通知器"""
    storage: SQLiteStorage = get_storage(request)
    try:
        await storage.delete_notifier(notifier_id)
        return {"message": "Notifier deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{notifier_id}/test", dependencies=[Depends(get_current_user)])
async def test_notifier(
    notifier_id: int,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """测试通知器"""
    storage: SQLiteStorage = get_storage(request)
    notifier = await storage.get_notifier(notifier_id)
    if not notifier:
        raise HTTPException(status_code=404, detail="Notifier not found")
        
    import httpx
    import logging
    
    logger = logging.getLogger(__name__)
    
    message = "This is a test notification from ReleaseTracker"
    payload = {
        "event": "test",
        "message": message,
        "content": message, # 兼容 Discord
        "text": message,    # 兼容 Slack
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                notifier.url,
                json=payload,
                timeout=10.0
            )
            response.raise_for_status()
            logger.info(f"Webhook test sent to {notifier.url}, status: {response.status_code}")
            return {"message": f"Test notification sent to {notifier.url}. Status: {response.status_code}"}
    except Exception as e:
        logger.error(f"Webhook test failed for {notifier.url}: {e}")
        raise HTTPException(status_code=400, detail=f"Webhook test failed: {str(e)}")
