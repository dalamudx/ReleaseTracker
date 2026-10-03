"""Administrator task visibility. Private payloads and lease tokens never leave storage."""

from typing import Annotated, Literal
from datetime import datetime
import time
import json
import logging

from pydantic import BaseModel, Field
from ..models import User
from ..dependencies import get_executor_scheduler

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..dependencies import get_current_admin_user, get_storage
from ..storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(get_current_admin_user)]
)


def public_task(task):
    result = {
        key: value
        for key, value in task.items()
        if key not in {"payload", "owner", "lease_until", "dedupe_key", "resource_key"}
    }
    if "approval_pending" in result:
        # SQLite stores this flag as INTEGER; keep the public API contract
        # boolean so clients do not treat an approval task as missing its plan.
        result["approval_pending"] = bool(result["approval_pending"])
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


class ReadTask(BaseModel):
    id: int = Field(gt=0)
    updated_at: float = Field(ge=0, allow_inf_nan=False)


class ClearReadTasks(BaseModel):
    read_tasks: list[ReadTask] = Field(max_length=100)


@router.post("/clear")
async def clear_finished_tasks(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    request: ClearReadTasks | None = None,
    settled_before: float | None = Query(None, ge=0, allow_inf_nan=False),
):
    return {
        "cleared": await storage.tasks.clear_finished(
            settled_before=settled_before,
            read_tasks=(
                [(item.id, item.updated_at) for item in request.read_tasks]
                if request is not None
                else None
            ),
        )
    }


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
async def deployment_plan(
    task_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler=Depends(get_executor_scheduler),
    refresh: bool = False,
):
    from ..storage.sqlite_deployment_admission import DeploymentAdmissionStore

    admission_store = DeploymentAdmissionStore(storage)
    plan = await admission_store.latest(task_id)
    if plan is None:
        raise HTTPException(404, "Deployment plan not found")

    task = await storage.tasks.get(task_id)
    if task and task.get("approval_pending") and task.get("kind") == "deploy":
        payload = task.get("payload") or {}
        executor_id = payload.get("executor_id")
        if executor_id is not None:
            executor = await storage.get_executor_config(executor_id)
            if executor is None:
                async with storage.tasks.transaction() as db:
                    now_ts = time.time()
                    await db.execute(
                        """UPDATE tasks SET state='superseded', approval_pending=0,
                           error_code='executor_deleted', message='Executor was deleted',
                           updated_at=? WHERE id=? AND state IN ('queued', 'retry_wait')""",
                        (now_ts, task_id),
                    )
                    await db.execute(
                        "UPDATE deployment_plans SET state='superseded' WHERE task_id=?",
                        (task_id,),
                    )
                raise HTTPException(410, "Executor was deleted; task has been superseded")

            if refresh or (plan["state"] == "pending" and plan.get("expires_at", 0) <= time.time()):
                from ..services.deploy_tasks import DeployTasks

                deploy_tasks = DeployTasks(storage, scheduler)
                try:
                    evidence = await deploy_tasks._collect_admission_evidence(executor, task)
                    refreshed_plan = await deploy_tasks.admission.stage(task, evidence)
                    return refreshed_plan
                except Exception:
                    # Do not return stale configuration as a successful refresh.
                    raise HTTPException(409, "deployment_evidence_unavailable") from None

    return plan


@router.post("/{task_id}/approve")
async def approve_deployment(
    task_id: int,
    body: DeploymentApproval,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    actor: Annotated[User, Depends(get_current_admin_user)],
    scheduler=Depends(get_executor_scheduler),
):
    from ..storage.sqlite_deployment_admission import AdmissionConflict, DeploymentAdmissionStore

    task = await storage.tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    payload = task.get("payload") or {}
    executor_id = payload.get("executor_id")
    executor = await storage.get_executor_config(executor_id) if executor_id is not None else None

    if executor_id is not None and executor is None:
        async with storage.tasks.transaction() as db:
            now_ts = time.time()
            await db.execute(
                """UPDATE tasks SET state='superseded', approval_pending=0,
                   error_code='executor_deleted', message='Executor was deleted',
                   updated_at=? WHERE id=? AND state IN ('queued', 'retry_wait')""",
                (now_ts, task_id),
            )
            await db.execute(
                "UPDATE deployment_plans SET state='superseded' WHERE task_id=?",
                (task_id,),
            )
        raise HTTPException(410, "Executor was deleted; task has been superseded")

    admission_store = DeploymentAdmissionStore(storage)
    latest_plan = await admission_store.latest(task_id)
    # New live-configuration plans must be re-inspected even while their TTL is valid.
    if (
        executor is not None
        and task.get("approval_pending")
        and latest_plan
        and latest_plan["summary"].get("configuration_diff") is not None
    ):
        from ..services.deploy_tasks import DeployTasks

        deploy_tasks = DeployTasks(storage, scheduler)
        if latest_plan["id"] != body.plan_id or latest_plan["fingerprint"] != body.fingerprint:
            raise HTTPException(409, "deployment_plan_changed")
        try:
            evidence = await deploy_tasks._collect_admission_evidence(executor, task)
            refreshed = await deploy_tasks.admission.stage(task, evidence)
        except Exception:
            raise HTTPException(409, "deployment_evidence_unavailable") from None
        if refreshed["fingerprint"] != body.fingerprint or refreshed["state"] not in {
            "pending",
            "approved",
        }:
            raise HTTPException(409, "deployment_plan_changed")
        body = body.model_copy(update={"plan_id": refreshed["id"]})
    try:
        plan = await admission_store.approve(
            task_id, body.plan_id, body.fingerprint, actor.username
        )
    except AdmissionConflict as exc:
        if str(exc) == "approval_stale_or_blocked" and executor is not None:
            latest_plan = await admission_store.latest(task_id)
            if (
                latest_plan
                and latest_plan["state"] == "pending"
                and latest_plan.get("expires_at", 0) <= time.time()
            ):
                from ..services.deploy_tasks import DeployTasks

                deploy_tasks = DeployTasks(storage, scheduler)
                try:
                    evidence = await deploy_tasks._collect_admission_evidence(executor, task)
                    refreshed = await deploy_tasks.admission.stage(task, evidence)
                    if (
                        refreshed["fingerprint"] == body.fingerprint
                        and refreshed["state"] == "pending"
                    ):
                        plan = await admission_store.approve(
                            task_id, refreshed["id"], refreshed["fingerprint"], actor.username
                        )
                        return JSONResponse(
                            status_code=202,
                            content={"task_id": task_id, "plan_id": plan["id"], "status": "queued"},
                        )
                    else:
                        raise HTTPException(409, "deployment_plan_changed")
                except HTTPException:
                    raise
                except Exception as refresh_exc:
                    logger.warning(
                        "Failed to auto-refresh expired plan on approve for task %s: %s",
                        task_id,
                        refresh_exc,
                    )
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
