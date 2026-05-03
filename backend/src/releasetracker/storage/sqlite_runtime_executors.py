from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import aiosqlite

from ..config import (
    ExecutorConfig,
    ExecutorServiceBinding,
    MaintenanceWindowConfig,
    RuntimeConnectionConfig,
    normalize_executor_target_ref,
)
from ..models import (
    Credential,
    ExecutorDesiredState,
    ExecutorRunHistory,
    ExecutorSnapshot,
    ExecutorStatus,
)

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


logger = logging.getLogger(__name__)


async def create_runtime_connection(
    storage: "SQLiteStorage", runtime_connection: RuntimeConnectionConfig
) -> int:
    now = datetime.now().isoformat()

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    columns = await _runtime_connection_columns(db)
    if "credential_id" in columns:
        cursor = await db.execute(
            """
            INSERT INTO runtime_connections
            (name, type, enabled, config, credential_id, secrets, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runtime_connection.name,
                runtime_connection.type,
                1 if runtime_connection.enabled else 0,
                storage._dump_json(runtime_connection.config),
                runtime_connection.credential_id,
                "{}",
                runtime_connection.description,
                now,
                now,
            ),
        )
    else:
        cursor = await db.execute(
            """
            INSERT INTO runtime_connections
            (name, type, enabled, config, secrets, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runtime_connection.name,
                runtime_connection.type,
                1 if runtime_connection.enabled else 0,
                storage._dump_json(runtime_connection.config),
                "{}",
                runtime_connection.description,
                now,
                now,
            ),
        )
    await db.commit()
    runtime_connection_id = cursor.lastrowid
    if runtime_connection_id is None:
        raise ValueError("Failed to create runtime connection")
    return runtime_connection_id


async def get_total_runtime_connections_count(storage: "SQLiteStorage") -> int:
    db = await storage._get_connection()
    cursor = await db.execute("SELECT COUNT(*) FROM runtime_connections")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_runtime_connections_paginated(
    storage: "SQLiteStorage", skip: int = 0, limit: int = 20
) -> list[RuntimeConnectionConfig]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM runtime_connections ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, skip),
    )
    rows = await cursor.fetchall()
    return [_row_to_runtime_connection(storage, row) for row in rows]


async def get_runtime_connection(
    storage: "SQLiteStorage", runtime_connection_id: int
) -> RuntimeConnectionConfig | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM runtime_connections WHERE id = ?", (runtime_connection_id,)
    )
    row = await cursor.fetchone()
    return _row_to_runtime_connection(storage, row) if row else None


async def get_runtime_connection_by_name(
    storage: "SQLiteStorage", name: str
) -> RuntimeConnectionConfig | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM runtime_connections WHERE name = ?", (name,))
    row = await cursor.fetchone()
    return _row_to_runtime_connection(storage, row) if row else None


async def update_runtime_connection(
    storage: "SQLiteStorage",
    runtime_connection_id: int,
    runtime_connection: RuntimeConnectionConfig,
) -> bool:
    db = await storage._get_connection()
    columns = await _runtime_connection_columns(db)
    if "credential_id" in columns:
        await db.execute(
            """
            UPDATE runtime_connections
            SET name = ?, type = ?, enabled = ?, config = ?, credential_id = ?, secrets = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                runtime_connection.name,
                runtime_connection.type,
                1 if runtime_connection.enabled else 0,
                storage._dump_json(runtime_connection.config),
                runtime_connection.credential_id,
                "{}",
                runtime_connection.description,
                datetime.now().isoformat(),
                runtime_connection_id,
            ),
        )
    else:
        await db.execute(
            """
            UPDATE runtime_connections
            SET name = ?, type = ?, enabled = ?, config = ?, secrets = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                runtime_connection.name,
                runtime_connection.type,
                1 if runtime_connection.enabled else 0,
                storage._dump_json(runtime_connection.config),
                "{}",
                runtime_connection.description,
                datetime.now().isoformat(),
                runtime_connection_id,
            ),
        )
    await db.commit()
    return True


async def delete_runtime_connection(storage: "SQLiteStorage", runtime_connection_id: int) -> bool:
    db = await storage._get_connection()
    await db.execute("DELETE FROM runtime_connections WHERE id = ?", (runtime_connection_id,))
    await db.commit()
    return True


async def _runtime_connection_columns(db: aiosqlite.Connection) -> set[str]:
    rows = await (await db.execute("PRAGMA table_info(runtime_connections)")).fetchall()
    return {str(row[1]) for row in rows}


def _row_to_runtime_connection(storage: "SQLiteStorage", row: Any) -> RuntimeConnectionConfig:
    decrypted_secrets = storage._decrypt_nested_strings(storage._load_json(row["secrets"]))
    keys = set(row.keys())
    return RuntimeConnectionConfig(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        enabled=bool(row["enabled"]),
        config=storage._load_json(row["config"]),
        credential_id=row["credential_id"] if "credential_id" in keys else None,
        secrets=cast(dict[str, Any], decrypted_secrets),
        description=row["description"],
    )


def _row_to_executor_config(
    storage: "SQLiteStorage",
    row: Any,
    *,
    service_bindings: list[ExecutorServiceBinding] | None = None,
) -> ExecutorConfig:
    maintenance_window_payload = storage._load_json(row["maintenance_window"])
    maintenance_window = (
        MaintenanceWindowConfig(**maintenance_window_payload)
        if maintenance_window_payload
        else None
    )
    normalized_target_ref = normalize_executor_target_ref(
        storage._load_json(row["target_ref"]),
        runtime_type=row["runtime_type"],
    )

    payload = {
        "id": row["id"],
        "name": row["name"],
        "runtime_type": row["runtime_type"],
        "runtime_connection_id": row["runtime_connection_id"],
        "tracker_name": row["tracker_name"],
        "tracker_source_id": (
            row["tracker_source_id"] if "tracker_source_id" in row.keys() else None
        ),
        "channel_name": row["channel_name"] if "channel_name" in row.keys() else None,
        "enabled": bool(row["enabled"]),
        "image_selection_mode": (
            row["image_selection_mode"]
            if "image_selection_mode" in row.keys()
            else "replace_tag_on_current_image"
        ),
        "image_reference_mode": row["image_reference_mode"],
        "update_mode": row["update_mode"],
        "target_ref": normalized_target_ref,
        "service_bindings": service_bindings or [],
        "maintenance_window": maintenance_window,
        "description": row["description"],
    }

    return ExecutorConfig(**payload)


def _try_row_to_executor_config(
    storage: "SQLiteStorage",
    row: Any,
    *,
    service_bindings: list[ExecutorServiceBinding] | None = None,
) -> ExecutorConfig:
    try:
        return _row_to_executor_config(
            storage,
            row,
            service_bindings=service_bindings,
        )
    except ValueError as exc:
        logger.error(
            "Marking invalid executor config id=%s name=%s runtime_type=%s: %s",
            row["id"],
            row["name"],
            row["runtime_type"],
            exc,
        )
        return ExecutorConfig.model_construct(
            id=row["id"],
            name=row["name"],
            runtime_type=row["runtime_type"],
            runtime_connection_id=row["runtime_connection_id"],
            tracker_name=row["tracker_name"],
            tracker_source_id=(
                row["tracker_source_id"] if "tracker_source_id" in row.keys() else None
            ),
            channel_name=row["channel_name"] if "channel_name" in row.keys() else None,
            enabled=False,
            image_selection_mode=(
                row["image_selection_mode"]
                if "image_selection_mode" in row.keys()
                else "replace_tag_on_current_image"
            ),
            image_reference_mode=row["image_reference_mode"],
            update_mode=row["update_mode"],
            target_ref=storage._load_json(row["target_ref"]),
            service_bindings=service_bindings or [],
            maintenance_window=None,
            description=row["description"],
            invalid_config_error=str(exc),
        )


async def _load_executor_service_bindings(
    storage: "SQLiteStorage", executor_id: int
) -> list[ExecutorServiceBinding]:
    if not await storage._table_exists("executor_service_bindings"):
        return []

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT service, tracker_source_id, channel_name
            FROM executor_service_bindings
            WHERE executor_id = ?
            ORDER BY service ASC
            """,
            (executor_id,),
        )
    ).fetchall()
    return [
        ExecutorServiceBinding(
            service=row["service"],
            tracker_source_id=row["tracker_source_id"],
            channel_name=row["channel_name"],
        )
        for row in rows
    ]


async def _load_executor_service_bindings_map(
    storage: "SQLiteStorage", executor_ids: list[int]
) -> dict[int, list[ExecutorServiceBinding]]:
    if not executor_ids:
        return {}

    if not await storage._table_exists("executor_service_bindings"):
        return {executor_id: [] for executor_id in executor_ids}

    placeholders = ", ".join("?" for _ in executor_ids)
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            f"""
            SELECT executor_id, service, tracker_source_id, channel_name
            FROM executor_service_bindings
            WHERE executor_id IN ({placeholders})
            ORDER BY executor_id ASC, service ASC
            """,
            tuple(executor_ids),
        )
    ).fetchall()

    by_executor: dict[int, list[ExecutorServiceBinding]] = {
        executor_id: [] for executor_id in executor_ids
    }
    for row in rows:
        by_executor[row["executor_id"]].append(
            ExecutorServiceBinding(
                service=row["service"],
                tracker_source_id=row["tracker_source_id"],
                channel_name=row["channel_name"],
            )
        )
    return by_executor


async def _replace_executor_service_bindings(
    storage: "SQLiteStorage",
    db: aiosqlite.Connection,
    *,
    executor_id: int,
    service_bindings: list[ExecutorServiceBinding],
) -> None:
    if not await storage._table_exists("executor_service_bindings"):
        if service_bindings:
            raise ValueError(
                "executor_service_bindings table is required for grouped executor bindings"
            )
        return

    await db.execute("DELETE FROM executor_service_bindings WHERE executor_id = ?", (executor_id,))

    if not service_bindings:
        return

    now = datetime.now().isoformat()
    for binding in service_bindings:
        await db.execute(
            """
            INSERT INTO executor_service_bindings
            (executor_id, service, tracker_source_id, channel_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                executor_id,
                binding.service,
                binding.tracker_source_id,
                binding.channel_name,
                now,
                now,
            ),
        )


def _row_to_executor_status(_storage: "SQLiteStorage", row: Any) -> ExecutorStatus:
    return ExecutorStatus(
        id=row["id"],
        executor_id=row["executor_id"],
        last_run_at=datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None,
        last_result=row["last_result"],
        last_error=row["last_error"],
        last_version=row["last_version"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_executor_run_history(storage: "SQLiteStorage", row: Any) -> ExecutorRunHistory:
    return ExecutorRunHistory(
        id=row["id"],
        executor_id=row["executor_id"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        status=row["status"],
        from_version=row["from_version"],
        to_version=row["to_version"],
        message=row["message"],
        diagnostics=storage._load_json(row["diagnostics"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_executor_snapshot(storage: "SQLiteStorage", row: Any) -> ExecutorSnapshot:
    return ExecutorSnapshot(
        id=row["id"],
        executor_id=row["executor_id"],
        snapshot_data=storage._load_json(row["snapshot_data"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _normalize_non_empty_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _serialize_desired_target(desired_target: dict[str, Any]) -> tuple[str, str]:
    payload = json.dumps(desired_target, sort_keys=True, separators=(",", ":"))
    return payload, payload


def _row_to_executor_desired_state(storage: "SQLiteStorage", row: Any) -> ExecutorDesiredState:
    return ExecutorDesiredState(
        id=row["id"],
        executor_id=row["executor_id"],
        desired_state_revision=row["desired_state_revision"],
        desired_target=storage._load_json(row["desired_target"]),
        desired_target_fingerprint=row["desired_target_fingerprint"],
        pending=bool(row["pending"]),
        next_eligible_at=(
            datetime.fromisoformat(row["next_eligible_at"]) if row["next_eligible_at"] else None
        ),
        claimed_by=row["claimed_by"],
        claimed_at=datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None,
        claim_until=datetime.fromisoformat(row["claim_until"]) if row["claim_until"] else None,
        last_completed_revision=row["last_completed_revision"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


async def create_executor_config(storage: "SQLiteStorage", executor_config: ExecutorConfig) -> int:
    now = datetime.now().isoformat()

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        INSERT INTO executors
        (name, runtime_type, runtime_connection_id, tracker_name, tracker_source_id, channel_name, enabled, image_selection_mode,
         image_reference_mode, update_mode, target_ref, maintenance_window, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            executor_config.name,
            executor_config.runtime_type,
            executor_config.runtime_connection_id,
            executor_config.tracker_name,
            executor_config.tracker_source_id,
            executor_config.channel_name,
            1 if executor_config.enabled else 0,
            executor_config.image_selection_mode,
            executor_config.image_reference_mode,
            executor_config.update_mode,
            storage._dump_json(executor_config.target_ref),
            storage._dump_json(
                executor_config.maintenance_window.model_dump()
                if executor_config.maintenance_window
                else None
            ),
            executor_config.description,
            now,
            now,
        ),
    )
    executor_id = cursor.lastrowid
    if executor_id is None:
        raise ValueError("Failed to create executor")

    try:
        await _replace_executor_service_bindings(
            storage,
            db,
            executor_id=executor_id,
            service_bindings=executor_config.service_bindings,
        )
    except aiosqlite.IntegrityError as exc:
        raise ValueError("duplicate grouped service bindings are not allowed") from exc

    await db.commit()
    return executor_id


async def save_executor_config(storage: "SQLiteStorage", executor_config: ExecutorConfig) -> int:
    if executor_config.id is not None:
        await update_executor_config(storage, executor_config.id, executor_config)
        return executor_config.id

    existing = await get_executor_config_by_name(storage, executor_config.name)
    if existing and existing.id is not None:
        await update_executor_config(storage, existing.id, executor_config)
        return existing.id

    return await create_executor_config(storage, executor_config)


async def get_total_executor_configs_count(storage: "SQLiteStorage") -> int:
    db = await storage._get_connection()
    cursor = await db.execute("SELECT COUNT(*) FROM executors")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_all_executor_configs(storage: "SQLiteStorage") -> list[ExecutorConfig]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM executors ORDER BY name ASC")
    rows = await cursor.fetchall()
    executor_ids = [row["id"] for row in rows]
    bindings_map = await _load_executor_service_bindings_map(
        storage,
        executor_ids,
    )
    return [
        _try_row_to_executor_config(
            storage,
            row,
            service_bindings=bindings_map.get(row["id"], []),
        )
        for row in rows
    ]


async def get_executor_configs_paginated(
    storage: "SQLiteStorage", skip: int = 0, limit: int = 20
) -> list[ExecutorConfig]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM executors ORDER BY name ASC LIMIT ? OFFSET ?", (limit, skip)
    )
    rows = await cursor.fetchall()
    executor_ids = [row["id"] for row in rows]
    bindings_map = await _load_executor_service_bindings_map(
        storage,
        executor_ids,
    )
    return [
        _try_row_to_executor_config(
            storage,
            row,
            service_bindings=bindings_map.get(row["id"], []),
        )
        for row in rows
    ]


async def get_executor_config(storage: "SQLiteStorage", executor_id: int) -> ExecutorConfig | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM executors WHERE id = ?", (executor_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _try_row_to_executor_config(
        storage,
        row,
        service_bindings=await _load_executor_service_bindings(storage, executor_id),
    )


async def get_executor_config_by_name(storage: "SQLiteStorage", name: str) -> ExecutorConfig | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM executors WHERE name = ?", (name,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _try_row_to_executor_config(
        storage,
        row,
        service_bindings=await _load_executor_service_bindings(storage, row["id"]),
    )


async def update_executor_config(
    storage: "SQLiteStorage", executor_id: int, executor_config: ExecutorConfig
) -> bool:
    db = await storage._get_connection()
    await db.execute(
        """
        UPDATE executors
        SET name = ?, runtime_type = ?, runtime_connection_id = ?, tracker_name = ?,
            tracker_source_id = ?, channel_name = ?, enabled = ?, image_selection_mode = ?, image_reference_mode = ?, update_mode = ?,
            target_ref = ?, maintenance_window = ?,
            description = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            executor_config.name,
            executor_config.runtime_type,
            executor_config.runtime_connection_id,
            executor_config.tracker_name,
            executor_config.tracker_source_id,
            executor_config.channel_name,
            1 if executor_config.enabled else 0,
            executor_config.image_selection_mode,
            executor_config.image_reference_mode,
            executor_config.update_mode,
            storage._dump_json(executor_config.target_ref),
            storage._dump_json(
                executor_config.maintenance_window.model_dump()
                if executor_config.maintenance_window
                else None
            ),
            executor_config.description,
            datetime.now().isoformat(),
            executor_id,
        ),
    )
    try:
        await _replace_executor_service_bindings(
            storage,
            db,
            executor_id=executor_id,
            service_bindings=executor_config.service_bindings,
        )
    except aiosqlite.IntegrityError as exc:
        raise ValueError("duplicate grouped service bindings are not allowed") from exc
    await db.commit()
    return True


async def delete_executor_config(storage: "SQLiteStorage", executor_id: int) -> bool:
    db = await storage._get_connection()
    await db.execute("DELETE FROM executors WHERE id = ?", (executor_id,))
    await db.commit()
    return True


async def update_executor_status(storage: "SQLiteStorage", status: ExecutorStatus) -> None:
    updated_at = status.updated_at.isoformat() if status.updated_at else datetime.now().isoformat()

    db = await storage._get_connection()
    await db.execute(
        """
        INSERT INTO executor_status
        (executor_id, last_run_at, last_result, last_error, last_version, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(executor_id) DO UPDATE SET
            last_run_at = excluded.last_run_at,
            last_result = excluded.last_result,
            last_error = excluded.last_error,
            last_version = excluded.last_version,
            updated_at = excluded.updated_at
        """,
        (
            status.executor_id,
            status.last_run_at.isoformat() if status.last_run_at else None,
            status.last_result,
            status.last_error,
            status.last_version,
            updated_at,
        ),
    )
    await db.commit()


async def get_executor_status(storage: "SQLiteStorage", executor_id: int) -> ExecutorStatus | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM executor_status WHERE executor_id = ?", (executor_id,))
    row = await cursor.fetchone()
    return _row_to_executor_status(storage, row) if row else None


async def get_all_executor_status(storage: "SQLiteStorage") -> list[ExecutorStatus]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM executor_status ORDER BY executor_id ASC")
    rows = await cursor.fetchall()
    return [_row_to_executor_status(storage, row) for row in rows]


async def delete_executor_status(storage: "SQLiteStorage", executor_id: int) -> None:
    db = await storage._get_connection()
    await db.execute("DELETE FROM executor_status WHERE executor_id = ?", (executor_id,))
    await db.commit()


async def create_executor_run(storage: "SQLiteStorage", run: ExecutorRunHistory) -> int:
    db = await storage._get_connection()
    cursor = await db.execute(
        """
        INSERT INTO executor_run_history
        (executor_id, started_at, finished_at, status, from_version, to_version, message, diagnostics, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.executor_id,
            run.started_at.isoformat(),
            run.finished_at.isoformat() if run.finished_at else None,
            run.status,
            run.from_version,
            run.to_version,
            run.message,
            storage._dump_json(run.diagnostics),
            run.created_at.isoformat(),
        ),
    )
    await db.commit()
    run_id = cursor.lastrowid
    if run_id is None:
        raise ValueError("Failed to create executor run history")
    return run_id


async def enqueue_executor_projection_trigger_work(
    storage: "SQLiteStorage",
    *,
    executor_id: int,
    tracker_name: str,
    previous_version: str | None,
    current_version: str,
    previous_identity_key: str | None = None,
    current_identity_key: str | None = None,
    queued_at: datetime | None = None,
) -> bool:
    del queued_at

    normalized_tracker_name = _normalize_non_empty_text(
        tracker_name,
        field="tracker_name",
    )
    normalized_current_version = _normalize_non_empty_text(
        current_version,
        field="current_version",
    )
    normalized_current_identity_key = _normalize_non_empty_text(
        current_identity_key or normalized_current_version,
        field="current_identity_key",
    )
    revision = f"{normalized_tracker_name}:{normalized_current_identity_key}"
    desired_target = {
        "tracker_name": normalized_tracker_name,
        "previous_version": previous_version,
        "current_version": normalized_current_version,
        "previous_identity_key": previous_identity_key,
        "current_identity_key": normalized_current_identity_key,
    }

    return await upsert_executor_desired_state(
        storage,
        executor_id=executor_id,
        desired_state_revision=revision,
        desired_target=desired_target,
    )


async def finalize_executor_run(
    storage: "SQLiteStorage",
    run_id: int,
    *,
    status: str,
    from_version: str | None = None,
    finished_at: datetime | None = None,
    to_version: str | None = None,
    message: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    db = await storage._get_connection()
    await db.execute(
        """
        UPDATE executor_run_history
        SET finished_at = ?, status = ?, from_version = ?, to_version = ?, message = ?, diagnostics = ?
        WHERE id = ?
        """,
        (
            (finished_at or datetime.now()).isoformat(),
            status,
            from_version,
            to_version,
            message,
            storage._dump_json(diagnostics),
            run_id,
        ),
    )
    await db.commit()
    return True


async def set_executor_run_status(storage: "SQLiteStorage", run_id: int, status: str) -> None:
    db = await storage._get_connection()
    await db.execute("UPDATE executor_run_history SET status = ? WHERE id = ?", (status, run_id))
    await db.commit()


async def update_executor_target_ref(
    storage: "SQLiteStorage", executor_id: int, target_ref: dict[str, Any]
) -> None:
    db = await storage._get_connection()
    await db.execute(
        "UPDATE executors SET target_ref = ?, updated_at = ? WHERE id = ?",
        (storage._dump_json(target_ref), datetime.now().isoformat(), executor_id),
    )
    await db.commit()



async def get_executor_run(storage: "SQLiteStorage", run_id: int) -> ExecutorRunHistory | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM executor_run_history WHERE id = ?", (run_id,))
    row = await cursor.fetchone()
    return _row_to_executor_run_history(storage, row) if row else None


async def get_executor_run_history(
    storage: "SQLiteStorage",
    executor_id: int,
    skip: int = 0,
    limit: int | None = 50,
    *,
    status: str | None = None,
    search: str | None = None,
) -> list[ExecutorRunHistory]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    query = """
        SELECT * FROM executor_run_history
        WHERE executor_id = ?
    """
    params: list[object] = [executor_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if search:
        query += " AND (COALESCE(from_version, '') LIKE ? OR COALESCE(to_version, '') LIKE ? OR COALESCE(message, '') LIKE ?)"
        like_term = f"%{search}%"
        params.extend([like_term, like_term, like_term])

    query += " ORDER BY started_at DESC, id DESC"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, skip])

    cursor = await db.execute(query, tuple(params))
    rows = await cursor.fetchall()
    return [_row_to_executor_run_history(storage, row) for row in rows]


async def get_total_executor_run_history_count(
    storage: "SQLiteStorage",
    executor_id: int,
    *,
    status: str | None = None,
    search: str | None = None,
) -> int:
    db = await storage._get_connection()
    query = "SELECT COUNT(*) FROM executor_run_history WHERE executor_id = ?"
    params: list[object] = [executor_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if search:
        query += " AND (COALESCE(from_version, '') LIKE ? OR COALESCE(to_version, '') LIKE ? OR COALESCE(message, '') LIKE ?)"
        like_term = f"%{search}%"
        params.extend([like_term, like_term, like_term])

    cursor = await db.execute(query, tuple(params))
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_latest_executor_run(
    storage: "SQLiteStorage", executor_id: int
) -> ExecutorRunHistory | None:
    runs = await get_executor_run_history(storage, executor_id, limit=1)
    return runs[0] if runs else None


async def delete_executor_run_history(storage: "SQLiteStorage", executor_id: int) -> int:
    db = await storage._get_connection()
    result = await db.execute(
        "DELETE FROM executor_run_history WHERE executor_id = ?", (executor_id,)
    )
    await db.commit()
    return result.rowcount or 0


async def prune_old_executor_runs(storage: "SQLiteStorage", days: int = 90) -> int:
    db = await storage._get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    result = await db.execute(
        "DELETE FROM executor_run_history WHERE started_at < ? AND status IN ('success', 'skipped', 'failed')",
        (cutoff,),
    )
    await db.commit()
    deleted = result.rowcount or 0
    if deleted > 0:
        logger.info(f"历史执行记录清理完成：已删除 {deleted} 条超过 {days} 天的记录")
    return deleted


async def save_executor_snapshot(storage: "SQLiteStorage", snapshot: ExecutorSnapshot) -> None:
    created_at = (
        snapshot.created_at.isoformat() if snapshot.created_at else datetime.now().isoformat()
    )
    updated_at = (
        snapshot.updated_at.isoformat() if snapshot.updated_at else datetime.now().isoformat()
    )

    db = await storage._get_connection()
    await db.execute(
        """
        INSERT INTO executor_snapshots
        (executor_id, snapshot_data, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(executor_id) DO UPDATE SET
            snapshot_data = excluded.snapshot_data,
            updated_at = excluded.updated_at
        """,
        (
            snapshot.executor_id,
            storage._dump_json(snapshot.snapshot_data),
            created_at,
            updated_at,
        ),
    )
    await db.commit()


async def get_executor_snapshot(
    storage: "SQLiteStorage", executor_id: int
) -> ExecutorSnapshot | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM executor_snapshots WHERE executor_id = ?", (executor_id,)
    )
    row = await cursor.fetchone()
    return _row_to_executor_snapshot(storage, row) if row else None


async def upsert_executor_desired_state(
    storage: "SQLiteStorage",
    *,
    executor_id: int,
    desired_state_revision: str,
    desired_target: dict[str, Any],
    next_eligible_at: datetime | None = None,
) -> bool:
    if not isinstance(desired_target, dict):
        raise ValueError("desired_target must be an object")

    normalized_revision = _normalize_non_empty_text(
        desired_state_revision,
        field="desired_state_revision",
    )
    desired_target_payload, desired_target_fingerprint = _serialize_desired_target(desired_target)

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row

    existing_row = await (
        await db.execute(
            "SELECT * FROM executor_desired_state WHERE executor_id = ?",
            (executor_id,),
        )
    ).fetchone()
    now = datetime.now().isoformat()
    next_eligible_at_iso = next_eligible_at.isoformat() if next_eligible_at else None

    if existing_row is None:
        await db.execute(
            """
            INSERT INTO executor_desired_state
            (executor_id, desired_state_revision, desired_target, desired_target_fingerprint,
             pending, next_eligible_at, claimed_by, claimed_at, claim_until,
             last_completed_revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                executor_id,
                normalized_revision,
                desired_target_payload,
                desired_target_fingerprint,
                next_eligible_at_iso,
                now,
                now,
            ),
        )
        await db.commit()
        return True

    pending_revision_matches = existing_row["desired_state_revision"] == normalized_revision
    target_matches = existing_row["desired_target_fingerprint"] == desired_target_fingerprint
    pending = bool(existing_row["pending"])
    completed_revision_matches = existing_row["last_completed_revision"] == normalized_revision

    already_queued_same_work = pending and pending_revision_matches and target_matches
    already_completed_same_work = (not pending) and completed_revision_matches and target_matches
    if already_queued_same_work or already_completed_same_work:
        return False

    await db.execute(
        """
        UPDATE executor_desired_state
        SET desired_state_revision = ?,
            desired_target = ?,
            desired_target_fingerprint = ?,
            pending = 1,
            next_eligible_at = ?,
            claimed_by = NULL,
            claimed_at = NULL,
            claim_until = NULL,
            updated_at = ?
        WHERE executor_id = ?
        """,
        (
            normalized_revision,
            desired_target_payload,
            desired_target_fingerprint,
            next_eligible_at_iso,
            now,
            executor_id,
        ),
    )
    await db.commit()
    return True


async def get_executor_desired_state(
    storage: "SQLiteStorage", executor_id: int
) -> ExecutorDesiredState | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    row = await (
        await db.execute(
            "SELECT * FROM executor_desired_state WHERE executor_id = ?",
            (executor_id,),
        )
    ).fetchone()
    return _row_to_executor_desired_state(storage, row) if row else None


async def list_pending_executor_desired_states(
    storage: "SQLiteStorage",
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[ExecutorDesiredState]:
    effective_now = (now or datetime.now()).isoformat()
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT *
            FROM executor_desired_state
            WHERE pending = 1
              AND (next_eligible_at IS NULL OR next_eligible_at <= ?)
            ORDER BY updated_at ASC, id ASC
            LIMIT ?
            """,
            (effective_now, limit),
        )
    ).fetchall()
    return [_row_to_executor_desired_state(storage, row) for row in rows]


async def claim_pending_executor_desired_states(
    storage: "SQLiteStorage",
    *,
    claimed_by: str,
    now: datetime | None = None,
    limit: int = 100,
    lease_seconds: int = 300,
) -> list[ExecutorDesiredState]:
    normalized_claimed_by = _normalize_non_empty_text(claimed_by, field="claimed_by")
    claim_now = now or datetime.now()
    claim_now_iso = claim_now.isoformat()
    claim_until_iso = (claim_now + timedelta(seconds=max(1, lease_seconds))).isoformat()

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    candidate_rows = await (
        await db.execute(
            """
            SELECT id
            FROM executor_desired_state
            WHERE pending = 1
              AND (next_eligible_at IS NULL OR next_eligible_at <= ?)
              AND (claim_until IS NULL OR claim_until <= ?)
            ORDER BY updated_at ASC, id ASC
            LIMIT ?
            """,
            (claim_now_iso, claim_now_iso, limit),
        )
    ).fetchall()

    if not candidate_rows:
        return []

    claimed_ids: list[int] = []
    for row in candidate_rows:
        result = await db.execute(
            """
            UPDATE executor_desired_state
            SET claimed_by = ?,
                claimed_at = ?,
                claim_until = ?,
                updated_at = ?
            WHERE id = ?
              AND pending = 1
              AND (next_eligible_at IS NULL OR next_eligible_at <= ?)
              AND (claim_until IS NULL OR claim_until <= ?)
            """,
            (
                normalized_claimed_by,
                claim_now_iso,
                claim_until_iso,
                claim_now_iso,
                row["id"],
                claim_now_iso,
                claim_now_iso,
            ),
        )
        if result.rowcount:
            claimed_ids.append(row["id"])

    if not claimed_ids:
        await db.commit()
        return []

    placeholders = ", ".join("?" for _ in claimed_ids)
    claimed_rows = await (
        await db.execute(
            f"""
            SELECT *
            FROM executor_desired_state
            WHERE id IN ({placeholders})
              AND claimed_by = ?
            ORDER BY updated_at ASC, id ASC
            """,
            (*claimed_ids, normalized_claimed_by),
        )
    ).fetchall()
    await db.commit()
    return [_row_to_executor_desired_state(storage, row) for row in claimed_rows]


async def defer_executor_desired_state(
    storage: "SQLiteStorage",
    executor_id: int,
    *,
    next_eligible_at: datetime,
    claimed_by: str | None = None,
) -> bool:
    now = datetime.now().isoformat()
    db = await storage._get_connection()
    if claimed_by is None:
        result = await db.execute(
            """
            UPDATE executor_desired_state
            SET pending = 1,
                next_eligible_at = ?,
                claimed_by = NULL,
                claimed_at = NULL,
                claim_until = NULL,
                updated_at = ?
            WHERE executor_id = ?
            """,
            (next_eligible_at.isoformat(), now, executor_id),
        )
    else:
        normalized_claimed_by = _normalize_non_empty_text(claimed_by, field="claimed_by")
        result = await db.execute(
            """
            UPDATE executor_desired_state
            SET pending = 1,
                next_eligible_at = ?,
                claimed_by = NULL,
                claimed_at = NULL,
                claim_until = NULL,
                updated_at = ?
            WHERE executor_id = ?
              AND claimed_by = ?
            """,
            (next_eligible_at.isoformat(), now, executor_id, normalized_claimed_by),
        )
    await db.commit()
    return bool(result.rowcount)


async def release_executor_desired_state_claim(
    storage: "SQLiteStorage",
    executor_id: int,
    *,
    claimed_by: str,
) -> bool:
    normalized_claimed_by = _normalize_non_empty_text(claimed_by, field="claimed_by")
    db = await storage._get_connection()
    result = await db.execute(
        """
        UPDATE executor_desired_state
        SET claimed_by = NULL,
            claimed_at = NULL,
            claim_until = NULL,
            updated_at = ?
        WHERE executor_id = ?
          AND claimed_by = ?
        """,
        (datetime.now().isoformat(), executor_id, normalized_claimed_by),
    )
    await db.commit()
    return bool(result.rowcount)


async def complete_executor_desired_state(
    storage: "SQLiteStorage",
    executor_id: int,
    *,
    claimed_by: str | None = None,
) -> bool:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    row = await (
        await db.execute(
            "SELECT desired_state_revision, claimed_by FROM executor_desired_state WHERE executor_id = ?",
            (executor_id,),
        )
    ).fetchone()
    if row is None:
        return False

    if claimed_by is not None:
        normalized_claimed_by = _normalize_non_empty_text(claimed_by, field="claimed_by")
        if row["claimed_by"] != normalized_claimed_by:
            return False

    now = datetime.now().isoformat()
    result = await db.execute(
        """
        UPDATE executor_desired_state
        SET pending = 0,
            next_eligible_at = NULL,
            claimed_by = NULL,
            claimed_at = NULL,
            claim_until = NULL,
            last_completed_revision = ?,
            updated_at = ?
        WHERE executor_id = ?
        """,
        (row["desired_state_revision"], now, executor_id),
    )
    await db.commit()
    return bool(result.rowcount)
