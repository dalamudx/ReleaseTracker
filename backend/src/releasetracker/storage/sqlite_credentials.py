from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

import aiosqlite

from ..models import Credential

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


async def create_credential(storage: "SQLiteStorage", credential: Credential) -> int:
    encrypted_token = storage._encrypt(credential.token) if credential.token else None
    encrypted_secrets = storage._encrypt_nested_strings(credential.secrets)

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    columns = await _credential_columns(db)
    if "secrets" in columns:
        cursor = await db.execute(
            """
            INSERT INTO credentials (name, type, token, secrets, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                credential.name,
                credential.type,
                encrypted_token or "",
                storage._dump_json(encrypted_secrets),
                credential.description,
                credential.created_at.isoformat(),
                credential.updated_at.isoformat(),
            ),
        )
    else:
        cursor = await db.execute(
            """
            INSERT INTO credentials (name, type, token, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                credential.name,
                credential.type,
                encrypted_token or "",
                credential.description,
                credential.created_at.isoformat(),
                credential.updated_at.isoformat(),
            ),
        )
    await db.commit()
    credential_id = cursor.lastrowid
    if credential_id is None:
        raise ValueError("Failed to create credential")
    return credential_id


async def get_all_credentials(storage: "SQLiteStorage") -> list[Credential]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM credentials ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [_row_to_credential(storage, row) for row in rows]


async def get_credentials_paginated(
    storage: "SQLiteStorage", skip: int = 0, limit: int = 20
) -> list[Credential]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM credentials ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, skip)
    )
    rows = await cursor.fetchall()
    return [_row_to_credential(storage, row) for row in rows]


async def get_total_credentials_count(storage: "SQLiteStorage") -> int:
    db = await storage._get_connection()
    cursor = await db.execute("SELECT COUNT(*) FROM credentials")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_credential(storage: "SQLiteStorage", credential_id: int) -> Credential | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM credentials WHERE id = ?", (credential_id,))
    row = await cursor.fetchone()
    return _row_to_credential(storage, row) if row else None


async def get_credential_by_name(storage: "SQLiteStorage", name: str) -> Credential | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM credentials WHERE name = ?", (name,))
    row = await cursor.fetchone()
    return _row_to_credential(storage, row) if row else None


async def update_credential(
    storage: "SQLiteStorage", credential_id: int, credential: Credential
) -> bool:
    encrypted_token = storage._encrypt(credential.token) if credential.token else None
    encrypted_secrets = storage._encrypt_nested_strings(credential.secrets)

    db = await storage._get_connection()
    columns = await _credential_columns(db)
    if "secrets" in columns:
        await db.execute(
            """
            UPDATE credentials
            SET type = ?, token = ?, secrets = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                credential.type,
                encrypted_token or "",
                storage._dump_json(encrypted_secrets),
                credential.description,
                datetime.now().isoformat(),
                credential_id,
            ),
        )
    else:
        await db.execute(
            """
            UPDATE credentials
            SET type = ?, token = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                credential.type,
                encrypted_token or "",
                credential.description,
                datetime.now().isoformat(),
                credential_id,
            ),
        )
    await db.commit()
    return True


async def delete_credential(storage: "SQLiteStorage", credential_id: int) -> bool:
    db = await storage._get_connection()
    await db.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
    await db.commit()
    return True


async def get_credential_references(
    storage: "SQLiteStorage", credential: Credential
) -> dict[str, list[dict[str, Any]]]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    references: dict[str, list[dict[str, Any]]] = {
        "runtime_connections": [],
        "aggregate_tracker_sources": [],
        "trackers": [],
    }

    if await storage._table_exists("runtime_connections"):
        columns = await _table_columns(db, "runtime_connections")
        if "credential_id" in columns and credential.id is not None:
            rows = await (
                await db.execute(
                    "SELECT id, name, type FROM runtime_connections WHERE credential_id = ? ORDER BY name",
                    (credential.id,),
                )
            ).fetchall()
            references["runtime_connections"] = [
                {"id": row["id"], "name": row["name"], "type": row["type"]} for row in rows
            ]

    if await storage._table_exists("aggregate_tracker_sources"):
        rows = await (
            await db.execute(
                """
                SELECT ats.id, ats.source_key, ats.source_type, ats.aggregate_tracker_id, at.name AS tracker_name
                FROM aggregate_tracker_sources ats
                JOIN aggregate_trackers at ON at.id = ats.aggregate_tracker_id
                WHERE ats.credential_name = ?
                ORDER BY at.name, ats.source_rank, ats.source_key
                """,
                (credential.name,),
            )
        ).fetchall()
        references["aggregate_tracker_sources"] = [
            {
                "id": row["id"],
                "name": row["source_key"],
                "type": row["source_type"],
                "tracker_id": row["aggregate_tracker_id"],
                "tracker_name": row["tracker_name"],
            }
            for row in rows
        ]

    if await storage._table_exists("trackers"):
        columns = await _table_columns(db, "trackers")
        if "credential_name" in columns:
            rows = await (
                await db.execute(
                    "SELECT name, type FROM trackers WHERE credential_name = ? ORDER BY name",
                    (credential.name,),
                )
            ).fetchall()
            references["trackers"] = [{"name": row["name"], "type": row["type"]} for row in rows]

    return references


async def get_credential_reference_counts(
    storage: "SQLiteStorage", credential: Credential
) -> dict[str, int]:
    references = await get_credential_references(storage, credential)
    return {key: len(items) for key, items in references.items()}


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    rows = await (await db.execute(f"PRAGMA table_info({table_name})")).fetchall()
    return {str(row[1]) for row in rows}


async def _credential_columns(db: aiosqlite.Connection) -> set[str]:
    return await _table_columns(db, "credentials")


def _row_to_credential(storage: "SQLiteStorage", row: Any) -> Credential:
    keys = set(row.keys())
    token = storage._decrypt(row["token"]) or ""
    secrets_payload = {}
    if "secrets" in keys:
        secrets_payload = storage._decrypt_nested_strings(storage._load_json(row["secrets"]))
    secrets = cast(dict[str, Any], secrets_payload or {})
    if token and "token" not in secrets:
        secrets["token"] = token
    return Credential(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        token=token,
        secrets=secrets,
        description=row["description"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
