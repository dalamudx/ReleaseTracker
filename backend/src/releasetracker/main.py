"""FastAPI 应用主文件"""

from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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

    # 初始化配置 (无需 AppConfig)

    # 绑定到 app.state
    app.state.storage = storage
    # app.state.config = app_config # REMOVED

    # 确保存在管理员用户
    auth_service = AuthService(storage)
    await auth_service.ensure_admin_user()

    # 初始化调度器
    scheduler = ReleaseScheduler(storage)

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
app.include_router(settings.router)
app.include_router(trackers.router)
app.include_router(credentials.router)
app.include_router(releases.router)
app.include_router(system.router)


@app.get("/")
async def root():
    """根路径"""
    return {"message": "ReleaseTracker API", "version": "0.1.0"}
