from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import (
    EXECUTOR_BINDABLE_SOURCE_TYPES,
    EXECUTOR_GROUPED_BINDING_TARGET_MODES,
    ExecutorConfig,
    ExecutorServiceBinding,
    normalize_executor_target_ref,
)
from ..dependencies import get_current_user, get_executor_scheduler, get_storage
from ..executor_scheduler import ExecutorScheduler
from ..executors import (
    DockerRuntimeAdapter,
    KubernetesRuntimeAdapter,
    PodmanRuntimeAdapter,
    PortainerRuntimeAdapter,
)
from ..models import ExecutorStatus
from ..services.runtime_credentials import materialize_runtime_connection_credentials
from ..storage.sqlite import SQLiteStorage

router = APIRouter(prefix="/api/executors", tags=["executors"])


def _serialize_executor_config(executor: ExecutorConfig) -> dict[str, Any]:
    return executor.model_dump()


def _serialize_executor_status(status: ExecutorStatus | None) -> dict[str, Any] | None:
    return status.model_dump() if status else None


async def _build_executor_list_item(
    storage: SQLiteStorage, executor: ExecutorConfig
) -> dict[str, Any]:
    status = await storage.get_executor_status(executor.id) if executor.id is not None else None
    runtime_connection = await storage.get_runtime_connection(executor.runtime_connection_id)
    return {
        **_serialize_executor_config(executor),
        "status": _serialize_executor_status(status),
        "runtime_connection_name": runtime_connection.name if runtime_connection else None,
    }


def _get_runtime_adapter(runtime_connection):
    if runtime_connection.type == "docker":
        return DockerRuntimeAdapter(runtime_connection)
    if runtime_connection.type == "podman":
        return PodmanRuntimeAdapter(runtime_connection)
    if runtime_connection.type == "kubernetes":
        return KubernetesRuntimeAdapter(runtime_connection)
    if runtime_connection.type == "portainer":
        return PortainerRuntimeAdapter(runtime_connection)
    raise HTTPException(status_code=400, detail=f"不支持的运行时类型: {runtime_connection.type}")


def _resolve_bound_release_channels(aggregate_tracker, tracker_config, binding_source) -> list:
    del tracker_config
    bound_source = next(
        (
            source
            for source in aggregate_tracker.sources
            if source.source_key == binding_source.source_key
        ),
        None,
    )
    if bound_source is None:
        return []
    return list(bound_source.release_channels)


async def _validate_executor_payload(
    storage: SQLiteStorage,
    executor_data: dict[str, Any],
    *,
    existing_executor: ExecutorConfig | None = None,
) -> ExecutorConfig:
    name = executor_data.get("name", existing_executor.name if existing_executor else None)
    runtime_type = executor_data.get(
        "runtime_type", existing_executor.runtime_type if existing_executor else None
    )
    runtime_connection_id = executor_data.get(
        "runtime_connection_id",
        existing_executor.runtime_connection_id if existing_executor else None,
    )
    tracker_name = executor_data.get(
        "tracker_name", existing_executor.tracker_name if existing_executor else None
    )
    tracker_source_id = executor_data.get(
        "tracker_source_id", existing_executor.tracker_source_id if existing_executor else None
    )
    channel_name = executor_data.get(
        "channel_name", existing_executor.channel_name if existing_executor else None
    )
    enabled = executor_data.get("enabled", existing_executor.enabled if existing_executor else True)
    update_mode = executor_data.get(
        "update_mode", existing_executor.update_mode if existing_executor else "manual"
    )
    image_selection_mode = executor_data.get(
        "image_selection_mode",
        (
            existing_executor.image_selection_mode
            if existing_executor
            else "replace_tag_on_current_image"
        ),
    )
    image_reference_mode = executor_data.get(
        "image_reference_mode",
        existing_executor.image_reference_mode if existing_executor else "digest",
    )
    target_ref = executor_data.get(
        "target_ref", existing_executor.target_ref if existing_executor else {}
    )
    service_bindings_payload = executor_data.get(
        "service_bindings",
        (
            [binding.model_dump() for binding in existing_executor.service_bindings]
            if existing_executor
            else []
        ),
    )
    maintenance_window = executor_data.get(
        "maintenance_window",
        (
            existing_executor.maintenance_window.model_dump()
            if existing_executor and existing_executor.maintenance_window
            else None
        ),
    )
    description = executor_data.get(
        "description", existing_executor.description if existing_executor else None
    )

    runtime_connection = await storage.get_runtime_connection(runtime_connection_id)
    if not runtime_connection:
        raise HTTPException(status_code=400, detail="运行时连接不存在")

    if runtime_type != runtime_connection.type:
        raise HTTPException(status_code=400, detail="执行器运行时类型必须与运行时连接一致")

    try:
        normalized_target_ref = normalize_executor_target_ref(target_ref, runtime_type=runtime_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_mode = normalized_target_ref.get("mode")

    def _normalized_service_bindings_payload() -> list[dict[str, Any]]:
        if not isinstance(service_bindings_payload, list):
            raise HTTPException(status_code=400, detail="service_bindings must be an array")
        normalized_items: list[dict[str, Any]] = []
        for item in service_bindings_payload:
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=400,
                    detail="service_bindings entries must be objects",
                )
            normalized_items.append(item)
        return normalized_items

    async def _validate_binding(
        *,
        tracker_source_id_value: Any,
        channel_name_value: Any,
    ) -> tuple[str, Any, Any, Any, int, str]:
        if tracker_source_id_value is None:
            raise HTTPException(status_code=400, detail="必须显式指定 tracker_source_id")
        if not isinstance(tracker_source_id_value, int):
            raise HTTPException(status_code=400, detail="tracker_source_id 必须是整数")

        binding = await storage.get_executor_binding(tracker_source_id_value)
        if binding is None:
            raise HTTPException(status_code=400, detail="追踪来源不存在")
        aggregate_tracker, binding_source = binding
        binding_tracker_name = aggregate_tracker.name

        if binding_source is None or binding_source.id is None:
            raise HTTPException(status_code=400, detail="追踪来源不存在")
        if binding_source.source_type not in EXECUTOR_BINDABLE_SOURCE_TYPES:
            raise HTTPException(status_code=400, detail="运行时执行器必须绑定可部署的镜像来源")
        if not binding_source.enabled:
            raise HTTPException(status_code=400, detail="追踪来源已禁用")

        tracker = await storage.get_tracker_config(binding_tracker_name)
        if not tracker:
            raise HTTPException(status_code=400, detail="追踪器不存在")

        if not isinstance(channel_name_value, str) or not channel_name_value.strip():
            raise HTTPException(status_code=400, detail="channel_name 不能为空")
        normalized_channel_name = channel_name_value.strip()

        return (
            binding_tracker_name,
            aggregate_tracker,
            tracker,
            binding_source,
            tracker_source_id_value,
            normalized_channel_name,
        )

    def _validate_source_compatible_with_target(binding_source: Any) -> None:
        source_type = getattr(binding_source, "source_type", None)
        if target_mode == "helm_release":
            if source_type != "helm":
                raise HTTPException(status_code=400, detail="Helm release 执行器必须绑定 Helm 来源")
            return
        if source_type != "container":
            raise HTTPException(status_code=400, detail="运行时执行器必须绑定 Docker 镜像来源")

    def _validate_bound_channel(
        *,
        aggregate_tracker: Any,
        tracker: Any,
        binding_source: Any,
        channel_name_value: str,
        binding_tracker_name_value: str,
    ) -> None:
        matched_channels = [
            channel
            for channel in _resolve_bound_release_channels(
                aggregate_tracker, tracker, binding_source
            )
            if channel.name == channel_name_value
        ]
        if not matched_channels:
            raise HTTPException(
                status_code=400,
                detail=f"追踪器 '{binding_tracker_name_value}' 不存在渠道 '{channel_name_value}'",
            )
        if not matched_channels[0].enabled:
            raise HTTPException(status_code=400, detail=f"渠道 '{channel_name_value}' 已禁用")

    binding_tracker_name = tracker_name
    binding_source = None
    service_bindings: list[ExecutorServiceBinding] = []

    if target_mode in EXECUTOR_GROUPED_BINDING_TARGET_MODES:
        validated_service_bindings: list[ExecutorServiceBinding] = []
        validated_binding_contexts: list[tuple[Any, Any, Any, str, str]] = []
        normalized_bindings_payload = _normalized_service_bindings_payload()
        for binding_payload in normalized_bindings_payload:
            (
                validated_tracker_name,
                validated_aggregate_tracker,
                validated_tracker,
                validated_source,
                validated_tracker_source_id,
                validated_channel_name,
            ) = await _validate_binding(
                tracker_source_id_value=binding_payload.get("tracker_source_id"),
                channel_name_value=binding_payload.get("channel_name"),
            )
            try:
                binding_model = ExecutorServiceBinding(
                    service=cast(Any, binding_payload.get("service")),
                    tracker_source_id=validated_tracker_source_id,
                    channel_name=validated_channel_name,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            _validate_source_compatible_with_target(validated_source)

            if binding_source is None:
                binding_source = validated_source
                binding_tracker_name = validated_tracker_name
            validated_binding_contexts.append(
                (
                    validated_aggregate_tracker,
                    validated_tracker,
                    validated_source,
                    validated_channel_name,
                    validated_tracker_name,
                )
            )
            validated_service_bindings.append(binding_model)

        if not validated_service_bindings:
            raise HTTPException(
                status_code=400,
                detail=f"{target_mode} executors must define at least one service binding",
            )

        service_bindings = validated_service_bindings
        tracker_source_id = validated_service_bindings[0].tracker_source_id
        channel_name = validated_service_bindings[0].channel_name

        if (
            image_selection_mode == "use_tracker_image_and_tag"
            and binding_source is not None
            and binding_source.source_type == "container"
            and not binding_source.source_config.get("image")
        ):
            raise HTTPException(
                status_code=400, detail="追踪来源镜像不能为空以使用 tracker 镜像模式"
            )

        for (
            validated_aggregate_tracker,
            validated_tracker,
            validated_source,
            validated_channel_name,
            validated_tracker_name,
        ) in validated_binding_contexts:
            _validate_bound_channel(
                aggregate_tracker=validated_aggregate_tracker,
                tracker=validated_tracker,
                binding_source=validated_source,
                channel_name_value=validated_channel_name,
                binding_tracker_name_value=validated_tracker_name,
            )
    else:
        (
            binding_tracker_name,
            aggregate_tracker,
            tracker,
            binding_source,
            tracker_source_id,
            channel_name,
        ) = await _validate_binding(
            tracker_source_id_value=tracker_source_id,
            channel_name_value=channel_name,
        )
        _validate_source_compatible_with_target(binding_source)

        if target_mode == "helm_release" and (service_bindings_payload or []):
            raise HTTPException(
                status_code=400, detail="Helm release 执行器不支持 service_bindings"
            )

        if (
            image_selection_mode == "use_tracker_image_and_tag"
            and binding_source is not None
            and binding_source.source_type == "container"
            and not binding_source.source_config.get("image")
        ):
            raise HTTPException(
                status_code=400, detail="追踪来源镜像不能为空以使用 tracker 镜像模式"
            )

        _validate_bound_channel(
            aggregate_tracker=aggregate_tracker,
            tracker=tracker,
            binding_source=binding_source,
            channel_name_value=channel_name,
            binding_tracker_name_value=binding_tracker_name,
        )

    executor = ExecutorConfig(
        id=existing_executor.id if existing_executor else None,
        name=name,
        runtime_type=runtime_type,
        runtime_connection_id=runtime_connection_id,
        tracker_name=binding_tracker_name,
        tracker_source_id=tracker_source_id,
        channel_name=channel_name,
        enabled=enabled,
        image_selection_mode=image_selection_mode,
        image_reference_mode=image_reference_mode,
        update_mode=update_mode,
        target_ref=normalized_target_ref,
        service_bindings=service_bindings,
        maintenance_window=maintenance_window,
        description=description,
    )

    try:
        runtime_connection = await materialize_runtime_connection_credentials(
            storage,
            runtime_connection,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    adapter = _get_runtime_adapter(runtime_connection)
    try:
        await adapter.validate_target_ref(executor.target_ref)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"目标校验失败: {exc}") from exc

    return executor


@router.get("", dependencies=[Depends(get_current_user)])
async def get_executors(
    storage: Annotated[SQLiteStorage, Depends(get_storage)], skip: int = 0, limit: int = 20
):
    total = await storage.get_total_executor_configs_count()
    executors = await storage.get_executor_configs_paginated(skip, limit)
    items = [await _build_executor_list_item(storage, executor) for executor in executors]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/runtime-connections/{runtime_connection_id}/targets", dependencies=[Depends(get_current_user)]
)
async def discover_runtime_targets(
    runtime_connection_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    namespace: Annotated[str | None, Query(min_length=1)] = None,
):
    runtime_connection = await storage.get_runtime_connection(runtime_connection_id)
    if not runtime_connection:
        raise HTTPException(status_code=404, detail="运行时连接不存在")

    try:
        runtime_connection = await materialize_runtime_connection_credentials(
            storage,
            runtime_connection,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    adapter = _get_runtime_adapter(runtime_connection)
    try:
        if runtime_connection.type == "kubernetes":
            targets = await cast(KubernetesRuntimeAdapter, adapter).discover_targets(
                namespace=namespace
            )
        else:
            if namespace is not None:
                raise ValueError("namespace filter is only supported for Kubernetes runtimes")
            targets = await adapter.discover_targets()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"运行时发现失败: {exc}") from exc

    return {
        "items": [
            {
                "runtime_type": target.runtime_type,
                "name": target.name,
                "target_ref": target.target_ref,
                "image": target.image,
            }
            for target in targets
        ],
        "total": len(targets),
    }


@router.get("/{executor_id}", dependencies=[Depends(get_current_user)])
async def get_executor_status_detail(
    executor_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    executor = await storage.get_executor_config(executor_id)
    if not executor:
        raise HTTPException(status_code=404, detail="执行器不存在")

    status = await storage.get_executor_status(executor_id)
    latest_run = await storage.get_latest_executor_run(executor_id)
    runtime_connection = await storage.get_runtime_connection(executor.runtime_connection_id)
    return {
        "id": executor.id,
        "name": executor.name,
        "runtime_type": executor.runtime_type,
        "tracker_name": executor.tracker_name,
        "tracker_source_id": executor.tracker_source_id,
        "service_bindings": [binding.model_dump() for binding in executor.service_bindings],
        "enabled": executor.enabled,
        "update_mode": executor.update_mode,
        "image_selection_mode": executor.image_selection_mode,
        "image_reference_mode": executor.image_reference_mode,
        "runtime_connection_id": executor.runtime_connection_id,
        "runtime_connection_name": runtime_connection.name if runtime_connection else None,
        "target_ref": executor.target_ref,
        "description": executor.description,
        "maintenance_window": (
            executor.maintenance_window.model_dump() if executor.maintenance_window else None
        ),
        "status": _serialize_executor_status(status),
        "latest_run": latest_run.model_dump() if latest_run else None,
    }


@router.get("/{executor_id}/config", dependencies=[Depends(get_current_user)])
async def get_executor_config_detail(
    executor_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    executor = await storage.get_executor_config(executor_id)
    if not executor:
        raise HTTPException(status_code=404, detail="执行器不存在")
    return _serialize_executor_config(executor)


@router.get("/{executor_id}/history", dependencies=[Depends(get_current_user)])
async def get_executor_history(
    executor_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    skip: int = 0,
    limit: int = 20,
    status: str | None = None,
    search: str | None = None,
):
    executor = await storage.get_executor_config(executor_id)
    if not executor:
        raise HTTPException(status_code=404, detail="执行器不存在")
    history = await storage.get_executor_run_history(
        executor_id,
        skip=skip,
        limit=limit,
        status=status,
        search=search,
    )
    total = await storage.get_total_executor_run_history_count(
        executor_id,
        status=status,
        search=search,
    )
    return {
        "items": [run.model_dump() for run in history],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.delete("/{executor_id}/history", dependencies=[Depends(get_current_user)])
async def clear_executor_history(
    executor_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    executor = await storage.get_executor_config(executor_id)
    if not executor:
        raise HTTPException(status_code=404, detail="执行器不存在")

    deleted = await storage.delete_executor_run_history(executor_id)
    return {"message": "执行历史已清空", "deleted": deleted}


@router.post("", dependencies=[Depends(get_current_user)])
async def create_executor(
    executor_data: dict[str, Any],
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ExecutorScheduler, Depends(get_executor_scheduler)],
):
    try:
        name = executor_data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="创建失败: name must be a non-empty string")

        existing = await storage.get_executor_config_by_name(name)
        if existing:
            raise HTTPException(status_code=400, detail="执行器名称已存在")

        executor = await _validate_executor_payload(storage, executor_data)
        executor_id = await storage.create_executor_config(executor)
        await scheduler.refresh_executor(executor_id)
        return {"message": f"执行器 {executor.name} 已创建", "id": executor_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"创建失败: {exc}") from exc


@router.put("/{executor_id}", dependencies=[Depends(get_current_user)])
async def update_executor(
    executor_id: int,
    executor_data: dict[str, Any],
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ExecutorScheduler, Depends(get_executor_scheduler)],
):
    try:
        existing = await storage.get_executor_config(executor_id)
        if not existing:
            raise HTTPException(status_code=404, detail="执行器不存在")

        new_name = executor_data.get("name", existing.name)
        same_name = await storage.get_executor_config_by_name(new_name)
        if same_name and same_name.id != executor_id:
            raise HTTPException(status_code=400, detail="执行器名称已存在")

        executor = await _validate_executor_payload(
            storage, executor_data, existing_executor=existing
        )
        await storage.update_executor_config(executor_id, executor)
        await scheduler.refresh_executor(executor_id)
        return {"message": f"执行器 {executor.name} 已更新"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"更新失败: {exc}") from exc


@router.delete("/{executor_id}", dependencies=[Depends(get_current_user)])
async def delete_executor(
    executor_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ExecutorScheduler, Depends(get_executor_scheduler)],
):
    executor = await storage.get_executor_config(executor_id)
    if not executor:
        raise HTTPException(status_code=404, detail="执行器不存在")

    await storage.delete_executor_config(executor_id)
    await storage.delete_executor_status(executor_id)
    await storage.delete_executor_run_history(executor_id)
    await scheduler.remove_executor(executor_id)
    return {"message": f"执行器 {executor.name} 已删除"}


@router.post("/{executor_id}/run", dependencies=[Depends(get_current_user)])
async def run_executor(
    executor_id: int,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ExecutorScheduler, Depends(get_executor_scheduler)],
):
    executor = await storage.get_executor_config(executor_id)
    if not executor:
        raise HTTPException(status_code=404, detail="执行器不存在")

    try:
        run_id = await scheduler.run_executor_now_async(executor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"执行失败: {exc}") from exc

    return {"status": "queued", "run_id": run_id}
