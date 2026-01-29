"""系统设置路由"""

import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from typing import Annotated

from ..models import User
from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingItem(BaseModel):
    key: str
    value: str
    updated_at: Optional[str] = None

class EnvInfo(BaseModel):
    key: str
    value: str

def get_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if not storage:
        raise HTTPException(status_code=503, detail="存储服务未初始化")
    return storage

@router.get("", response_model=List[SettingItem], dependencies=[Depends(get_current_user)])
async def get_settings(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取所有系统设置"""
    storage: SQLiteStorage = get_storage(request)
    settings_dict = await storage.get_all_settings()
    return [
        SettingItem(key=k, value=v, updated_at=datetime.now().isoformat()) # TODO: Fetch real updated_at from DB
        for k, v in settings_dict.items()
    ]

@router.post("", response_model=SettingItem, dependencies=[Depends(get_current_user)])
async def update_setting(
    setting: SettingItem,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """更新系统设置"""
    storage: SQLiteStorage = get_storage(request)
    
    # 禁止通过此接口修改特殊系统键（如有）
    # 目前暂无特殊限制
    
    await storage.set_setting(setting.key, setting.value)
    return setting

@router.delete("/{key}", dependencies=[Depends(get_current_user)])
async def delete_setting(
    key: str,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)]
):
    """删除系统设置"""
    storage: SQLiteStorage = get_storage(request)
    await storage.delete_setting(key)
    return {"message": "Setting deleted"}

@router.get("/env", response_model=List[EnvInfo], dependencies=[Depends(get_current_user)])
async def get_env_info(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取允许查看的环境变量"""
    allowed_keys = ["ENCRYPTION_KEY", "LOG_LEVEL", "TZ"]
    
    info = []
    for key in allowed_keys:
        val = os.getenv(key)
        if val:
            if key == "ENCRYPTION_KEY":
                # 对敏感密钥进行脱敏处理
                if len(val) > 8:
                    val = f"{val[:4]}...{val[-4:]}"
                else:
                    val = "******"
            info.append(EnvInfo(key=key, value=val))
        else:
            info.append(EnvInfo(key=key, value="(Not Set)"))
            
    return info
