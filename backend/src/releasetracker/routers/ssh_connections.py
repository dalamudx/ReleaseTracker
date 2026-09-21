"""Admin-only SSH identity discovery and connectivity tests."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_current_admin_user, get_storage
from ..services.ssh_transport import discover_host_key, test_ssh_connection
from ..storage.sqlite import SQLiteStorage
from .runtime_connections import _build_runtime_connection_config

router = APIRouter(
    prefix="/api/runtime-connections",
    tags=["runtime-connections"],
    dependencies=[Depends(get_current_admin_user)],
)


@router.post("/ssh/host-key")
async def ssh_host_key(
    payload: dict[str, Any], storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    try:
        connection = await _build_runtime_connection_config(storage, payload)
        return await discover_host_key(storage, connection)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/ssh/test")
async def ssh_test(
    payload: dict[str, Any], storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    try:
        connection = await _build_runtime_connection_config(storage, payload)
        return await test_ssh_connection(storage, connection)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
