from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..models import Credential, CredentialType
from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_current_user, get_storage

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


class CreateCredentialRequest(BaseModel):
    name: str
    type: CredentialType
    token: str = ""
    secrets: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class UpdateCredentialRequest(BaseModel):
    type: CredentialType | None = None
    token: str | None = None
    secrets: dict[str, Any] | None = None
    description: str | None = None


def _mask_secret_value(value: Any):
    if isinstance(value, dict):
        return {key: _mask_secret_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_secret_value(item) for item in value]
    if isinstance(value, str):
        return f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "****"
    return value


def _serialize_credential(credential: Credential) -> dict[str, Any]:
    payload = credential.model_dump()
    payload["token"] = _mask_secret_value(credential.token) if credential.token else ""
    payload["secrets"] = _mask_secret_value(credential.secrets)
    payload["secret_keys"] = sorted(credential.secrets.keys())
    return payload


def _credential_reference_counts(references: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {key: len(items) for key, items in references.items()}


@router.get("", dependencies=[Depends(get_current_user)])
async def get_credentials(
    storage: Annotated[SQLiteStorage, Depends(get_storage)], skip: int = 0, limit: int = 20
):
    """Get the paginated credential list"""

    total = await storage.get_total_credentials_count()
    credentials = await storage.get_credentials_paginated(skip, limit)

    result = [_serialize_credential(credential) for credential in credentials]

    return {"items": result, "total": total, "skip": skip, "limit": limit}


@router.get("/{credential_id}", dependencies=[Depends(get_current_user)])
async def get_credential(
    credential_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """Get a single credential without the token"""

    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")

    return _serialize_credential(credential)


@router.post("", dependencies=[Depends(get_current_user)])
async def create_credential(
    credential_data: CreateCredentialRequest,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    """Create a new credential"""
    try:
        # Check whether the name is duplicated
        existing = await storage.get_credential_by_name(credential_data.name)
        if existing:
            raise HTTPException(status_code=400, detail="凭证名称已存在")

        credential = Credential(**credential_data.model_dump())
        credential_id = await storage.create_credential(credential)

        return {"message": f"凭证 {credential.name} 已创建", "id": credential_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"创建失败: {str(e)}")


@router.get("/{credential_id}/references", dependencies=[Depends(get_current_user)])
async def get_credential_references(
    credential_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")

    references = await storage.get_credential_references(credential)
    return {
        "credential_id": credential_id,
        "references": references,
        "counts": _credential_reference_counts(references),
        "deletable": not any(references.values()),
    }


@router.put("/{credential_id}", dependencies=[Depends(get_current_user)])
async def update_credential(
    credential_id: int,
    credential_data: UpdateCredentialRequest,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    """Update a credential"""

    try:
        existing = await storage.get_credential(credential_id)
        if not existing:
            raise HTTPException(status_code=404, detail="凭证不存在")

        payload = credential_data.model_dump(exclude_unset=True)

        # Update other fields while keeping the name unchanged
        # Keep the existing token if the frontend sends an empty or missing token
        new_token = payload.get("token")
        if not new_token:
            new_token = existing.token

        new_secrets = payload.get("secrets")
        if new_secrets is None:
            new_secrets = existing.secrets

        credential = Credential(
            name=existing.name,
            type=payload.get("type", existing.type),
            token=new_token,
            secrets=new_secrets,
            description=payload.get("description", existing.description),
            created_at=existing.created_at,
            updated_at=datetime.now(),
        )

        await storage.update_credential(credential_id, credential)

        return {"message": f"凭证 {existing.name} 已更新"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {str(e)}")


@router.delete("/{credential_id}", dependencies=[Depends(get_current_user)])
async def delete_credential(
    credential_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    """Delete a credential"""

    credential = await storage.get_credential(credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="凭证不存在")

    references = await storage.get_credential_references(credential)
    reference_counts = _credential_reference_counts(references)
    if any(count > 0 for count in reference_counts.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "凭证正在被使用，不能删除",
                "references": references,
                "counts": reference_counts,
            },
        )

    await storage.delete_credential(credential_id)

    return {"message": f"凭证 {credential.name} 已删除"}
