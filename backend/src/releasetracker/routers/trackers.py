from fastapi import APIRouter, Depends, HTTPException, status
from typing import Annotated

from ..models import TrackerStatus
from ..storage.sqlite import SQLiteStorage
from ..scheduler import ReleaseScheduler
from ..dependencies import get_current_user, get_storage, get_scheduler

router = APIRouter(prefix="/api/trackers", tags=["trackers"])

@router.get("", dependencies=[Depends(get_current_user)])
async def get_trackers(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    skip: int = 0,
    limit: int = 20,
    search: str | None = None
):
    """获取追踪器列表（分页）"""
    
    # 获取总数
    total = await storage.get_total_tracker_configs_count()

    # 分页获取配置
    configs = await storage.get_tracker_configs_paginated(skip, limit)
    
    # 获取所有状态
    statuses = await storage.get_all_tracker_status()
    status_map = {s.name: s for s in statuses}
    
    # 批量获取所有追踪器的最近版本 (Optimization for N+1 query)
    tracker_names = [config.name for config in configs]
    bulk_releases = await storage.get_releases_for_trackers_bulk(tracker_names)
    
    result = []
    
    for config in configs:
        # 在内存中计算最新版本 (using static method)
        releases = bulk_releases.get(config.name, [])
        latest_release = SQLiteStorage.select_best_release(releases, config.channels)
        latest_version = latest_release.version if latest_release else None
        
        # 计算启用状态
        calculated_enabled = False
        if config.channels:
            calculated_enabled = any(c.enabled for c in config.channels)

        if config.name in status_map:
            status = status_map[config.name]
            # 仅仅在这里覆盖 enabled 状态为了展示，数据库里实际可能不同步，但调度器会同步
            status.enabled = calculated_enabled
            status.channel_count = len(config.channels) if config.channels else 0
            if latest_version:
                status.last_version = latest_version
            result.append(status)
        else:
            result.append(TrackerStatus(
                name=config.name,
                type=config.type,
                enabled=calculated_enabled,
                last_check=None,
                last_version=latest_version,
                error=None,  # 初始无错误，等待首次检查
                channel_count=len(config.channels) if config.channels else 0
            ))
            
    return {
        "items": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.get("/{tracker_name}", response_model=TrackerStatus, dependencies=[Depends(get_current_user)])
async def get_tracker(
    tracker_name: str,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """获取单个追踪器状态"""

    status = await storage.get_tracker_status(tracker_name)
    
    # 查找配置 (从数据库获取)
    tracker_config = await storage.get_tracker_config(tracker_name)
    if not tracker_config:
        # 如果配置里没有，即使数据库有，也视为不存在（或残留）
        raise HTTPException(status_code=404, detail="追踪器配置不存在")

    # 计算启用状态
    calculated_enabled = False
    if tracker_config.channels:
        calculated_enabled = any(c.enabled for c in tracker_config.channels)

    if not status:
        # 如果只有配置没有状态，返回初始状态
        return TrackerStatus(
            name=tracker_config.name,
            type=tracker_config.type,
            enabled=calculated_enabled
        )
    
    # 同步 enabled 状态
    status.enabled = calculated_enabled
    return status


@router.get("/{tracker_name}/config", dependencies=[Depends(get_current_user)])
async def get_tracker_config_detail(
    tracker_name: str,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """获取单个追踪器配置详情"""
    config = await storage.get_tracker_config(tracker_name)
    if not config:
        raise HTTPException(status_code=404, detail="追踪器不存在")
        
    return config.model_dump()


@router.post("/{tracker_name}/check", dependencies=[Depends(get_current_user)])
async def check_tracker(
    tracker_name: str,
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)]
):
    """手动触发追踪器检查"""

    # 使用新版检查逻辑 (支持多渠道、多版本保存)
    try:
        status = await scheduler.check_tracker_now_v2(tracker_name)
        return status
    except ValueError as e:
        # 常见错误如 Token 缺失
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:

        raise HTTPException(status_code=500, detail=f"检查失败: {str(e)}")


@router.post("", dependencies=[Depends(get_current_user)])
async def create_tracker(
    tracker_data: dict,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)]
):
    """创建新追踪器"""
    try:
        from ..config import TrackerConfig
        
        # 检查名称是否重复
        existing = await storage.get_tracker_config(tracker_data.get("name"))
        if existing:
            raise HTTPException(status_code=400, detail="追踪器名称已存在")
        
        # 验证并保存
        new_tracker = TrackerConfig(**tracker_data)
        await storage.save_tracker_config(new_tracker)
        
        # 动态更新调度器
        await scheduler.refresh_tracker(new_tracker.name)
        
        return {"message": f"追踪器 {tracker_data['name']} 已创建"}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"创建失败: {str(e)}")


@router.put("/{tracker_name}", dependencies=[Depends(get_current_user)])
async def update_tracker(
    tracker_name: str, 
    tracker_data: dict,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)]
):
    """更新追踪器配置"""
    try:
        # 检查是否存在
        existing = await storage.get_tracker_config(tracker_name)
        if not existing:
            raise HTTPException(status_code=404, detail="追踪器不存在")
        
        if tracker_data.get("name") and tracker_data.get("name") != tracker_name:
             raise HTTPException(status_code=400, detail="不支持修改追踪器名称")

        from ..config import TrackerConfig
        updated_tracker = TrackerConfig(**tracker_data)
        
        # 保存更新（注意：Credential name 等关联字段会一并更新）
        await storage.save_tracker_config(updated_tracker)
        
        # 动态更新调度器
        await scheduler.refresh_tracker(updated_tracker.name)
        
        return {"message": f"追踪器 {tracker_name} 已更新"}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {str(e)}")


@router.delete("/{tracker_name}", dependencies=[Depends(get_current_user)])
async def delete_tracker(
    tracker_name: str,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)]
):
    """删除追踪器"""
    
    # 检查是否存在
    existing = await storage.get_tracker_config(tracker_name)
    if not existing:
        raise HTTPException(status_code=404, detail="追踪器不存在")
    
    # 从数据库删除配置
    await storage.delete_tracker_config(tracker_name)
    
    # 从数据库删除状态
    await storage.delete_tracker_status(tracker_name)

    # 从数据库删除该追踪器的版本历史
    await storage.delete_releases_by_tracker(tracker_name)
    
    # 从调度器移除
    await scheduler.remove_tracker(tracker_name)
    
    return {"message": f"追踪器 {tracker_name} 已删除"}
