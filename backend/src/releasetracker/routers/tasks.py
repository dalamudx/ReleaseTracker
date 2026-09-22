"""Administrator task visibility. Private payloads and lease tokens never leave storage."""

from typing import Annotated, Literal
from datetime import datetime
import time
import json
from pydantic import BaseModel
from ..models import User
from ..dependencies import get_executor_scheduler

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..dependencies import get_current_admin_user, get_storage
from ..storage.sqlite import SQLiteStorage

router = APIRouter(
    prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(get_current_admin_user)]
)


def public_task(task):
    result = {
        key: value
        for key, value in task.items()
        if key not in {"payload", "owner", "lease_until", "dedupe_key", "resource_key"}
    }
    result["target"] = {
        key: value
        for key, value in task["payload"].items()
        if key in {"tracker_id", "tracker_name", "source_ids", "executor_id", "snapshot_id"}
    }
    if "attempt_history" in result:
        result["attempt_history"] = [
            {key: value for key, value in attempt.items() if key != "owner"}
            for attempt in result["attempt_history"]
        ]
    return result


@router.get("")
async def list_tasks(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    limit: int = Query(50, ge=1, le=100),
    before: int | None = None,
    state: str | None = None,
):
    return [
        public_task(task)
        for task in await storage.tasks.list(limit=limit, before=before, state=state)
    ]


@router.post("/clear")
async def clear_finished_tasks(storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    return {"cleared": await storage.tasks.clear_finished()}


@router.get("/{task_id}")
async def task_detail(task_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    task = await storage.tasks.detail(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return public_task(task)


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    if not await storage.tasks.cancel(task_id):
        raise HTTPException(409, "Only queued or waiting tasks can be cancelled")
    return {"status": "cancelled"}


class DeploymentApproval(BaseModel):
    plan_id: int
    fingerprint: str
    plan_reviewed: Literal[True]


@router.get("/{task_id}/deployment-plan")
async def deployment_plan(task_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    from ..storage.sqlite_deployment_admission import DeploymentAdmissionStore

    plan = await DeploymentAdmissionStore(storage).latest(task_id)
    if plan is None:
        raise HTTPException(404, "Deployment plan not found")
    return plan


@router.post("/{task_id}/approve")
async def approve_deployment(
    task_id: int,
    body: DeploymentApproval,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    actor: Annotated[User, Depends(get_current_admin_user)],
):
    from ..storage.sqlite_deployment_admission import AdmissionConflict, DeploymentAdmissionStore

    try:
        plan = await DeploymentAdmissionStore(storage).approve(
            task_id, body.plan_id, body.fingerprint, actor.username
        )
    except AdmissionConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return JSONResponse(
        status_code=202, content={"task_id": task_id, "plan_id": plan["id"], "status": "queued"}
    )


class ManualResolution(BaseModel):
    remote_stopped: Literal[True]
    state_verified: Literal[True]


@router.post("/{task_id}/resolve")
async def resolve_task(
    task_id: int,
    body: ManualResolution,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    actor: Annotated[User, Depends(get_current_admin_user)],
    scheduler=Depends(get_executor_scheduler),
):
    """Record explicit human verification; never claim a deployment succeeded."""
    task = await storage.tasks.get(task_id)
    if not task or task["kind"] not in {"deploy", "recover"} or task["state"] != "needs_attention":
        raise HTTPException(409, "Only interrupted deployment/recovery tasks can be resolved")
    executor_id = task["payload"].get("executor_id")
    if executor_id in scheduler._running_executor_ids:
        raise HTTPException(409, "Executor is still running")
    async with storage.tasks.transaction() as db:
        locked = await (
            await db.execute(
                "SELECT 1 FROM executor_snapshots WHERE executor_id=? AND locked=1 LIMIT 1",
                (executor_id,),
            )
        ).fetchone()
        if locked:
            raise HTTPException(409, "Locked snapshots require verified executor recovery first")
        result = (task.get("result") or {}) | {
            "manual_resolution": {"actor": actor.username, "at": time.time()}
        }
        cursor = await db.execute(
            "UPDATE tasks SET state='failed',error_code='operator_reconciled',result=?,updated_at=? WHERE id=? AND state='needs_attention'",
            (json.dumps(result), time.time(), task_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "Task state changed")
        await db.execute(
            "UPDATE executor_run_history SET status='failed',finished_at=?,message='Operator confirmed remote work stopped and state verified' WHERE executor_id=? AND status IN ('queued','running')",
            (datetime.now().isoformat(), executor_id),
        )
    return {"status": "failed"}


@router.post("/{task_id}/recheck")
async def recheck_readiness(
    task_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler=Depends(get_executor_scheduler),
):
    """Queue read-only observation of the frozen deployment, never repeat a write."""
    task = await storage.tasks.get(task_id)
    observer = getattr(scheduler, "readiness", None)
    if not task or task["kind"] not in {"deploy", "recover"} or observer is None:
        raise HTTPException(409, "Readiness observation is unavailable")
    try:
        result = await observer.enqueue_recheck(task)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return JSONResponse(status_code=202, content=result)


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: int, request: Request, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    task = await storage.tasks.get(task_id)
    if not task or task["kind"] != "fetch" or task["state"] != "failed":
        raise HTTPException(409, "Only failed fetch tasks can be retried here")
    payload = task["payload"]
    try:
        result = await request.app.state.fetch_tasks.enqueue(
            payload["tracker_name"],
            source_ids=set(payload["source_ids"]),
            trigger_mode="manual",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return JSONResponse(status_code=202, content=result)
