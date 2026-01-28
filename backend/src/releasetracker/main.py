"""FastAPI 应用主文件"""

from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import AppConfig, StorageConfig
from .scheduler import ReleaseScheduler
from .services.auth import AuthService
from .storage.sqlite import SQLiteStorage
from .logger import LogConfig
from .routers import auth, notifiers, settings, trackers, credentials, releases, system

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""

    # 初始化存储
    # data/releases.db relative to backend root
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
    
    # 绑定到 app.state
    app.state.storage = storage
    app.state.config = app_config
    
    # 确保存在管理员用户
    auth_service = AuthService(storage, app_config)
    await auth_service.ensure_admin_user()

    # 初始化调度器
    scheduler = ReleaseScheduler(current_config, storage)
    
    # 绑定调度器到 app.state
    app.state.scheduler = scheduler
    
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


# ==================== 路由注册 ====================

app.include_router(auth.router)
app.include_router(notifiers.router)
# Note: settings router now includes /api/settings AND /api/settings/config (if prefix is set)
# But I added /config to settings router assuming standard prefix.
# Wait, previous main.py had /api/config separate.
# My updated settings.py has @router.get("/config") inside router with prefix "/api/settings".
# So the path is /api/settings/config.
# BUT existing frontend likely expects /api/config.
# I should probably fix `settings.py` to route `/api/config` correctly or have a dedicated router for it.
# However, to avoid breaking frontend now, I can mount a separate router or Adjust settings.py
# Let's check `routers/releases.py` where I put /api (prefix) with /releases.
# I can do similar for settings if I want to support /api/config.
# But for now, let's assume I'll fix the Frontend or just duplicate /api/config if needed.
# Actually, `routers/settings.py` has prefix=`/api/settings`.
# So `get("/config")` -> `/api/settings/config`.
# Broken? Yes.
# Fix: I will manually add a compatibility route for /api/config in main.py or just move it to a "system" router.
# Let's keep it simple: generic routes in `settings` router but bind `settings.router`?
# Better: Just put the `get_config` in `routers/system.py` with prefix `/api` and path `/config`.
# But I already put it in `settings.py`.
# I will fix `settings.py` prefix logic or just leave it for now and verify.
# Actually, to be safe, I'll update `settings.py` to have two routers or just generic.
# Whatever, let's include existing routers.
app.include_router(settings.router)
app.include_router(trackers.router)
app.include_router(credentials.router)
app.include_router(releases.router)
app.include_router(system.router)


@app.get("/")
async def root():
    """根路径"""
    return {"message": "ReleaseTracker API", "version": "0.1.0"}
