from datetime import datetime
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from ..config import RuntimeConnectionConfig
from ..dependencies import get_current_user, get_storage
from ..executors.kubernetes import KubernetesRuntimeAdapter
from ..services.runtime_credentials import materialize_runtime_connection_credentials
from ..storage.sqlite import SQLiteStorage

router = APIRouter(prefix="/api/runtime-connections", tags=["runtime-connections"])


async def _serialize_runtime_connection(
    storage: SQLiteStorage, runtime_connection: RuntimeConnectionConfig
) -> dict[str, Any]:
    payload = runtime_connection.model_dump()
    payload["endpoint"] = await _resolve_runtime_connection_endpoint(storage, runtime_connection)
    payload["secrets"] = {}
    payload["uses_credentials"] = runtime_connection.credential_id is not None
    payload["has_inline_secrets"] = False
    payload["credential_name"] = None
    if runtime_connection.credential_id is not None:
        credential = await storage.get_credential(runtime_connection.credential_id)
        if credential is not None:
            payload["credential_name"] = credential.name
            payload["credential_type"] = credential.type
    return payload


async def _resolve_runtime_connection_endpoint(
    storage: SQLiteStorage, runtime_connection: RuntimeConnectionConfig
) -> str | None:
    if runtime_connection.type != "kubernetes":
        return None

    if runtime_connection.config.get("in_cluster") is True:
        return "in-cluster"

    try:
        materialized = await materialize_runtime_connection_credentials(
            storage,
            runtime_connection,
        )
    except ValueError:
        return None

    return _extract_kubeconfig_server(materialized.secrets.get("kubeconfig"))


def _extract_kubeconfig_server(kubeconfig: Any) -> str | None:
    if not isinstance(kubeconfig, str) or not kubeconfig.strip():
        return None

    try:
        data = yaml.safe_load(kubeconfig)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None

    clusters = data.get("clusters")
    if not isinstance(clusters, list):
        return None

    for item in clusters:
        if not isinstance(item, dict):
            continue
        cluster = item.get("cluster")
        if not isinstance(cluster, dict):
            continue
        server = cluster.get("server")
        if isinstance(server, str) and server.strip():
            return server.strip()
    return None


async def _build_runtime_connection_config(
    storage: SQLiteStorage,
    runtime_connection_data: dict[str, Any],
) -> RuntimeConnectionConfig:
    runtime_connection_id = runtime_connection_data.get("id")
    existing = None
    if isinstance(runtime_connection_id, int):
        existing = await storage.get_runtime_connection(runtime_connection_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="运行时连接不存在")

    if existing is None:
        return RuntimeConnectionConfig(
            **{key: value for key, value in runtime_connection_data.items() if key != "secrets"}
        )

    return RuntimeConnectionConfig(
        id=runtime_connection_id,
        name=runtime_connection_data.get("name", existing.name),
        type=runtime_connection_data.get("type", existing.type),
        enabled=runtime_connection_data.get("enabled", existing.enabled),
        config=runtime_connection_data.get("config", existing.config),
        credential_id=runtime_connection_data.get("credential_id", existing.credential_id),
        secrets={},
        description=runtime_connection_data.get("description", existing.description),
    )


@router.get("", dependencies=[Depends(get_current_user)])
async def get_runtime_connections(
    storage: Annotated[SQLiteStorage, Depends(get_storage)], skip: int = 0, limit: int = 20
):
    total = await storage.get_total_runtime_connections_count()
    runtime_connections = await storage.get_runtime_connections_paginated(skip, limit)
    return {
        "items": [
            await _serialize_runtime_connection(storage, item) for item in runtime_connections
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{runtime_connection_id}", dependencies=[Depends(get_current_user)])
async def get_runtime_connection(
    runtime_connection_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    runtime_connection = await storage.get_runtime_connection(runtime_connection_id)
    if not runtime_connection:
        raise HTTPException(status_code=404, detail="运行时连接不存在")
    return await _serialize_runtime_connection(storage, runtime_connection)


@router.post("", dependencies=[Depends(get_current_user)])
async def create_runtime_connection(
    runtime_connection_data: dict[str, Any],
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    try:
        name = runtime_connection_data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="创建失败: name must be a non-empty string")

        existing = await storage.get_runtime_connection_by_name(name)
        if existing:
            raise HTTPException(status_code=400, detail="运行时连接名称已存在")

        runtime_connection = RuntimeConnectionConfig(
            **{key: value for key, value in runtime_connection_data.items() if key != "secrets"}
        )
        runtime_connection_id = await storage.create_runtime_connection(runtime_connection)
        return {
            "message": f"运行时连接 {runtime_connection.name} 已创建",
            "id": runtime_connection_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"创建失败: {str(e)}")


@router.put("/{runtime_connection_id}", dependencies=[Depends(get_current_user)])
async def update_runtime_connection(
    runtime_connection_id: int,
    runtime_connection_data: dict[str, Any],
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    try:
        existing = await storage.get_runtime_connection(runtime_connection_id)
        if not existing:
            raise HTTPException(status_code=404, detail="运行时连接不存在")

        new_name = runtime_connection_data.get("name", existing.name)
        if new_name != existing.name:
            same_name = await storage.get_runtime_connection_by_name(new_name)
            if same_name and same_name.id != runtime_connection_id:
                raise HTTPException(status_code=400, detail="运行时连接名称已存在")

        runtime_connection = RuntimeConnectionConfig(
            id=runtime_connection_id,
            name=new_name,
            type=runtime_connection_data.get("type", existing.type),
            enabled=runtime_connection_data.get("enabled", existing.enabled),
            config=runtime_connection_data.get("config", existing.config),
            credential_id=runtime_connection_data.get("credential_id", existing.credential_id),
            secrets={},
            description=runtime_connection_data.get("description", existing.description),
        )

        await storage.update_runtime_connection(runtime_connection_id, runtime_connection)
        return {
            "message": f"运行时连接 {runtime_connection.name} 已更新",
            "updated_at": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {str(e)}")


@router.delete("/{runtime_connection_id}", dependencies=[Depends(get_current_user)])
async def delete_runtime_connection(
    runtime_connection_id: int, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    runtime_connection = await storage.get_runtime_connection(runtime_connection_id)
    if not runtime_connection:
        raise HTTPException(status_code=404, detail="运行时连接不存在")

    await storage.delete_runtime_connection(runtime_connection_id)
    return {"message": f"运行时连接 {runtime_connection.name} 已删除"}


@router.post("/discover-kubernetes-namespaces", dependencies=[Depends(get_current_user)])
async def discover_kubernetes_namespaces(
    runtime_connection_data: dict[str, Any],
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    try:
        runtime_connection = await _build_runtime_connection_config(
            storage, runtime_connection_data
        )
        if runtime_connection.type != "kubernetes":
            raise HTTPException(status_code=400, detail="仅支持 Kubernetes 运行时连接发现命名空间")

        runtime_connection = await materialize_runtime_connection_credentials(
            storage,
            runtime_connection,
        )
        adapter = KubernetesRuntimeAdapter(runtime_connection)
        namespaces = await adapter.discover_namespaces()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"命名空间发现失败: {exc}") from exc

    return {"items": namespaces}
