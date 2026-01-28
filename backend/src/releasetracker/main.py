"""FastAPI 应用主文件"""

from contextlib import asynccontextmanager
from pathlib import Path
import logging
from datetime import datetime


from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


from .config import AppConfig, StorageConfig
from .models import Release, ReleaseStats, TrackerStatus
from .scheduler import ReleaseScheduler
from .services.auth import AuthService
from .storage.sqlite import SQLiteStorage
from .logger import LogConfig, DEFAULT_LOG_FORMAT
from .routers import auth, notifiers, settings
from .dependencies import get_current_user
from fastapi import Depends

logger = logging.getLogger(__name__)

# 全局变量
storage: SQLiteStorage | None = None
scheduler: ReleaseScheduler | None = None
app_config: AppConfig | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global storage, scheduler, app_config

    # 初始化存储
    # data/releases.db relative to backend root
    # main.py is in backend/src/releasetracker/main.py
    base_dir = Path(__file__).resolve().parent.parent.parent
    base_dir = Path(__file__).resolve().parent.parent.parent
    db_path = str(base_dir / "data" / "releases.db")
    
    # 初始化日志
    LogConfig.setup_logging()
    
    storage = SQLiteStorage(db_path)
    await storage.initialize()

    # 初始化配置 (临时空配置，用于 Notifiers 占位)
    current_config = AppConfig(
        storage=StorageConfig(path=db_path),
        trackers=[], 
        notifiers=[]
    )
    app_config = current_config 
    
    # 将全局变量也绑定到 app.state 以供路由使用（避免循环依赖）
    app.state.storage = storage
    app.state.config = app_config
    
    # 确保存在管理员用户
    auth_service = AuthService(storage, app_config)
    await auth_service.ensure_admin_user()

    # 初始化调度器
    scheduler = ReleaseScheduler(current_config, storage)
    await scheduler.initialize()
    await scheduler.start()

    yield

    # 关闭时清理
    if scheduler:
        scheduler.scheduler.shutdown()



# 创建 FastAPI 应用
app = FastAPI(
    title="ReleaseTracker API",
    description="版本追踪软件 REST API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




app.include_router(auth.router)
app.include_router(notifiers.router)
app.include_router(settings.router)


# ==================== API 路由 ====================


@app.get("/")
async def root():
    """根路径"""
    return {"message": "ReleaseTracker API", "version": "0.1.0"}


@app.get("/api/stats", response_model=ReleaseStats, dependencies=[Depends(get_current_user)])
async def get_stats():
    """获取统计信息"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    return await storage.get_stats()


@app.get("/api/trackers", dependencies=[Depends(get_current_user)])
async def get_trackers(
    skip: int = 0,
    limit: int = 20,
    search: str | None = None
):
    """获取追踪器列表（分页）"""
    if not storage:
        raise HTTPException(status_code=503, detail="服务未初始化")
    
    # 获取总数
    total = await storage.get_total_tracker_configs_count()

    # 分页获取配置
    configs = await storage.get_tracker_configs_paginated(skip, limit)
    
    # 获取所有状态 (优化：后续可改为批量获取指定列表的状态)
    statuses = await storage.get_all_tracker_status()
    status_map = {s.name: s for s in statuses}
    
    result = []
    
    for config in configs:
        # 获取跨所有启用渠道的最新版本
        latest_release = await storage.get_latest_release_for_channels(config.name, config.channels)
        latest_version = latest_release.version if latest_release else None
        
        # 计算启用状态
        calculated_enabled = False
        if config.channels:
            calculated_enabled = any(c.enabled for c in config.channels)

        if config.name in status_map:
            status = status_map[config.name]
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
                error="Pending check",
                channel_count=len(config.channels) if config.channels else 0
            ))
            
    return {
        "items": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }


@app.get("/api/trackers/{tracker_name}", response_model=TrackerStatus, dependencies=[Depends(get_current_user)])
async def get_tracker(tracker_name: str):
    """获取单个追踪器状态"""
    if not storage or not app_config:
        raise HTTPException(status_code=503, detail="服务未完全初始化")

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





@app.post("/api/trackers/{tracker_name}/check", dependencies=[Depends(get_current_user)])
async def check_tracker(tracker_name: str):
    """手动触发追踪器检查"""
    if not scheduler:
        raise HTTPException(status_code=503, detail="调度器未初始化")

    # 使用新版检查逻辑 (支持多渠道、多版本保存)
    try:
        status = await scheduler.check_tracker_now_v2(tracker_name)
        return status
    except ValueError as e:
        # 常见错误如 Token 缺失
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Check failed: {e}")
        raise HTTPException(status_code=500, detail=f"检查失败: {str(e)}")


@app.get("/api/releases", dependencies=[Depends(get_current_user)])
async def get_releases(
    tracker: str | None = None,
    skip: int = 0,
    limit: int = 20,
    search: str | None = None,
    prerelease: bool | None = None,
    include_history: bool = True  # 默认包含历史记录
):
    """获取版本列表（分页）"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")

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


@app.get("/api/releases/latest", response_model=list[Release], dependencies=[Depends(get_current_user)])
async def get_latest_releases():
    """获取最近更新的版本列表（全局最近5个）"""
    if not storage:
        raise HTTPException(status_code=503, detail="服务未初始化")

    return await storage.get_releases(limit=5)


@app.get("/api/config", dependencies=[Depends(get_current_user)])
async def get_config():
    """获取当前配置（部分模拟，主要返回追踪器列表）"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    
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


@app.post("/api/trackers", dependencies=[Depends(get_current_user)])
async def create_tracker(tracker_data: dict):
    """创建新追踪器"""
    if not storage or not scheduler:
        raise HTTPException(status_code=503, detail="服务未初始化")
    
    try:
        from .config import TrackerConfig
        
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


@app.put("/api/trackers/{tracker_name}", dependencies=[Depends(get_current_user)])
async def update_tracker(tracker_name: str, tracker_data: dict):
    """更新追踪器配置"""
    if not storage or not scheduler:
        raise HTTPException(status_code=503, detail="服务未初始化")
    
    try:
        # 检查是否存在
        existing = await storage.get_tracker_config(tracker_name)
        if not existing:
            raise HTTPException(status_code=404, detail="追踪器不存在")
        
        if tracker_data.get("name") and tracker_data.get("name") != tracker_name:
             raise HTTPException(status_code=400, detail="不支持修改追踪器名称")

        from .config import TrackerConfig
        updated_tracker = TrackerConfig(**tracker_data)
        
        # 保存更新（注意：Credential name 等关联字段会一并更新）
        await storage.save_tracker_config(updated_tracker)
        
        # 动态更新调度器
        await scheduler.refresh_tracker(updated_tracker.name)
        
        return {"message": f"追踪器 {tracker_name} 已更新"}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {str(e)}")


@app.delete("/api/trackers/{tracker_name}", dependencies=[Depends(get_current_user)])
async def delete_tracker(tracker_name: str):
    """删除追踪器"""
    if not storage or not scheduler:
        raise HTTPException(status_code=503, detail="服务未初始化")
    
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

@app.get("/api/trackers/{tracker_name}/config", dependencies=[Depends(get_current_user)])
async def get_tracker_config(tracker_name: str):
    """获取单个追踪器配置详情"""
    if not storage:
        raise HTTPException(status_code=503, detail="服务未初始化")
    
    config = await storage.get_tracker_config(tracker_name)
    if not config:
        raise HTTPException(status_code=404, detail="追踪器不存在")
        
    return config.model_dump()


# ==================== 凭证管理 API ====================

@app.get("/api/credentials", dependencies=[Depends(get_current_user)])
async def get_credentials(
    skip: int = 0,
    limit: int = 20
):
    """获取凭证列表（分页）"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    
    total = await storage.get_total_credentials_count()
    credentials = await storage.get_credentials_paginated(skip, limit)
    
    # 隐藏 token 的完整内容，仅显示部分用于识别
    result = []
    for cred in credentials:
        cred_dict = cred.model_dump()
        if cred.token:
            # 显示前4位和后4位
            cred_dict["token"] = f"{cred.token[:4]}...{cred.token[-4:]}" if len(cred.token) > 8 else "****"
        result.append(cred_dict)
    
    return {
        "items": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }


@app.get("/api/credentials/{credential_id}", dependencies=[Depends(get_current_user)])
async def get_credential(credential_id: int):
    """获取单个凭证（不包含 token）"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    
    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")
    
    # 安全起见，读取时也隐藏 token
    cred_dict = credential.model_dump()
    if cred_dict.get("token"):
        cred_dict["token"] = f"{credential.token[:4]}...{credential.token[-4:]}" if len(credential.token) > 8 else "****"
    
    return cred_dict


@app.post("/api/credentials", dependencies=[Depends(get_current_user)])
async def create_credential(credential_data: dict):
    """创建新凭证"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    
    try:
        from .models import Credential
        
        # 检查名称是否重复
        existing = await storage.get_credential_by_name(credential_data.get("name"))
        if existing:
            raise HTTPException(status_code=400, detail="凭证名称已存在")
        
        credential = Credential(**credential_data)
        credential_id = await storage.create_credential(credential)
        
        return {"message": f"凭证 {credential.name} 已创建", "id": credential_id}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"创建失败: {str(e)}")


@app.put("/api/credentials/{credential_id}", dependencies=[Depends(get_current_user)])
async def update_credential(credential_id: int, credential_data: dict):
    """更新凭证"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    
    try:
        from .models import Credential
        
        existing = await storage.get_credential(credential_id)
        if not existing:
            raise HTTPException(status_code=404, detail="凭证不存在")
        
        if "name" in credential_data:
            del credential_data["name"]

        # 更新：保持 name 不变，只更新其他字段
        # 如果前端传来的 token 为空或不存在，则保持原样
        new_token = credential_data.get("token")
        if not new_token:
             new_token = existing.token
             
        credential = Credential(
            name=existing.name,  # 名称不允许修改
            type=credential_data.get("type", existing.type),
            token=new_token,
            description=credential_data.get("description", existing.description),
            created_at=existing.created_at,
            updated_at=datetime.now()
        )
        
        await storage.update_credential(credential_id, credential)
        
        return {"message": f"凭证 {existing.name} 已更新"}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {str(e)}")


@app.delete("/api/credentials/{credential_id}", dependencies=[Depends(get_current_user)])
async def delete_credential(credential_id: int):
    """删除凭证"""
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    
    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")
    
    # TODO: 检查是否有追踪器正在使用此凭证
    # 如果有，应该警告或禁止删除
    
    await storage.delete_credential(credential_id)
    
    return {"message": f"凭证 {credential.name} 已删除"}
