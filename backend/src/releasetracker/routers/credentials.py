from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated
from datetime import datetime

from ..models import Credential
from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user, get_storage

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


@router.get("", dependencies=[Depends(get_current_user)])
async def get_credentials(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    skip: int = 0,
    limit: int = 20
):
    """获取凭证列表（分页）"""

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


@router.get("/{credential_id}", dependencies=[Depends(get_current_user)])
async def get_credential(
    credential_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """获取单个凭证（不包含 token）"""

    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")
    
    # 安全起见，读取时也隐藏 token
    cred_dict = credential.model_dump()
    if cred_dict.get("token"):
        cred_dict["token"] = f"{credential.token[:4]}...{credential.token[-4:]}" if len(credential.token) > 8 else "****"
    
    return cred_dict


@router.post("", dependencies=[Depends(get_current_user)])
async def create_credential(
    credential_data: dict,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """创建新凭证"""
    try:
        # 检查名称是否重复
        existing = await storage.get_credential_by_name(credential_data.get("name"))
        if existing:
            raise HTTPException(status_code=400, detail="凭证名称已存在")
        
        credential = Credential(**credential_data)
        credential_id = await storage.create_credential(credential)
        
        return {"message": f"凭证 {credential.name} 已创建", "id": credential_id}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"创建失败: {str(e)}")


@router.put("/{credential_id}", dependencies=[Depends(get_current_user)])
async def update_credential(
    credential_id: int, 
    credential_data: dict,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """更新凭证"""

    try:
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


@router.delete("/{credential_id}", dependencies=[Depends(get_current_user)])
async def delete_credential(
    credential_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """删除凭证"""
    
    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")
    
    # TODO: 检查是否有追踪器正在使用此凭证
    # 如果有，应该警告或禁止删除
    
    await storage.delete_credential(credential_id)
    
    return {"message": f"凭证 {credential.name} 已删除"}
