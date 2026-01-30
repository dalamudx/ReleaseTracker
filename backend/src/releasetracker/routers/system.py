from fastapi import APIRouter, Depends, Request
from typing import Annotated

from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user, get_storage

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/config", dependencies=[Depends(get_current_user)])
async def get_config(request: Request, storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    """获取当前配置（部分模拟，主要返回追踪器列表）"""

    # Notifiers 现在从数据库获取
    db_notifiers = await storage.get_notifiers()
    notifiers = [n.model_dump() for n in db_notifiers]

    trackers = await storage.get_all_tracker_configs()

    return {
        "storage": {"type": "sqlite", "path": storage.db_path},
        "trackers": [t.model_dump() for t in trackers],
        "notifiers": notifiers,
    }
