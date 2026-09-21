"""Executor-only SSH Compose discovery and analysis of saved connections."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import get_current_admin_user, get_storage
from ..services.ssh_compose_deploy import analyze_update_project
from ..services.ssh_compose_discovery import discover_compose_projects
from ..services.ssh_compose_plan import SSHComposeTarget
from ..storage.sqlite import SQLiteStorage

router = APIRouter(
    prefix="/api/executors/ssh/compose",
    tags=["executors"],
    dependencies=[Depends(get_current_admin_user)],
)


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_connection_id: int = Field(gt=0)


class AnalysisRequest(ConnectionRequest):
    target: SSHComposeTarget


async def _connection(storage, connection_id):
    connection = await storage.get_runtime_connection(connection_id)
    if connection is None:
        raise HTTPException(404, "Runtime connection not found")
    if connection.type != "ssh" or not connection.enabled:
        raise HTTPException(400, "An enabled SSH connection is required")
    return connection


@router.post("/discover")
async def discover(
    payload: ConnectionRequest, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    connection = await _connection(storage, payload.runtime_connection_id)
    try:
        return await discover_compose_projects(storage, connection)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/analyze")
async def analyze(
    payload: AnalysisRequest, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    connection = await _connection(storage, payload.runtime_connection_id)
    try:
        return await analyze_update_project(storage, connection, payload.target)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
