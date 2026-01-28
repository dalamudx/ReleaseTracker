from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Annotated

from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user, get_storage

router = APIRouter(prefix="/api", tags=["system"])

@router.get("/config", dependencies=[Depends(get_current_user)])
async def get_config(
    request: Request,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """获取当前配置（部分模拟，主要返回追踪器列表）"""
    
    app_config = getattr(request.app.state, "config", None)
    
    trackers = await storage.get_all_tracker_configs()
    
    # Notifiers 暂时还未完全入库，这里暂时返回空或内存中的
    notifiers = []
    if app_config:
        notifiers = [n.model_dump() for n in app_config.notifiers]

    return {
        "storage": {"type": "sqlite", "path": storage.db_path},
        "trackers": [t.model_dump() for t in trackers],
        "notifiers": notifiers,
    }
