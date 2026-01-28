from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated

from ..models import Release, ReleaseStats
from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user, get_storage

router = APIRouter(prefix="/api", tags=["releases"]) 
# Note: prefix is /api because endpoints are /api/releases and /api/stats. 
# Wait, standard convention is router prefix includes resource.
# But here we have /api/stats (singular) and /api/releases (plural).
# I will use prefix="" and explicit paths, or group them.

# Let's use prefix="/api/releases" for releases and a separate one for stats?
# Or just put stats in this router with path "/stats" (resulting in /api/releases/stats)?
# Current API is /api/stats. Changing it to /api/releases/stats might break frontend.
# I will use `router = APIRouter(prefix="/api")` and specify full paths relative to that like "/stats" and "/releases".

@router.get("/stats", response_model=ReleaseStats, dependencies=[Depends(get_current_user)])
async def get_stats(storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    """获取统计信息"""
    return await storage.get_stats()


@router.get("/releases", dependencies=[Depends(get_current_user)])
async def get_releases(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    tracker: str | None = None,
    skip: int = 0,
    limit: int = 20,
    search: str | None = None,
    prerelease: bool | None = None,
    include_history: bool = True  # 默认包含历史记录
):
    """获取版本列表（分页）"""
    if limit > 100:
        limit = 100

    total = await storage.get_total_count(
        tracker_name=tracker,
        search=search,
        prerelease=prerelease,
        include_history=include_history
    )
    
    items = await storage.get_releases(
        tracker_name=tracker,
        skip=skip,
        limit=limit,
        search=search,
        prerelease=prerelease,
        include_history=include_history
    )
    
    return {
        "total": total,
        "items": items,
        "skip": skip,
        "limit": limit
    }


@router.get("/releases/latest", response_model=list[Release], dependencies=[Depends(get_current_user)])
async def get_latest_releases(storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    """获取最近更新的版本列表（全局最近5个）"""
    return await storage.get_releases(limit=5)
