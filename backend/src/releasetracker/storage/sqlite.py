"""SQLite Storage module"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from zoneinfo import ZoneInfo

import aiosqlite

from . import (
    sqlite_aggregate_trackers,
    sqlite_auth_oidc,
    sqlite_credentials,
    sqlite_runtime_executors,
    sqlite_release_history,
    sqlite_release_aliases,
    sqlite_current_releases,
    sqlite_source_observations,
    sqlite_release_queries,
    sqlite_webhooks,
)
from ..models import (
    Release,
    ReleaseStats,
    TrackerStatus,
    ExecutorDesiredState,
    ExecutorStatus,
    ExecutorRunHistory,
    ExecutorSnapshot,
    User,
    Session,
    Notifier,
    AggregateTracker,
    TrackerSource,
    TrackerSourceType,
    SourceReleaseObservation,
    CanonicalRelease,
    CanonicalReleaseObservation,
)
from ..config import (
    RuntimeConnectionConfig,
    ExecutorConfig,
)
from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from ..services.system_keys import SystemKeyManager

logger = logging.getLogger(__name__)

SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY = "system.release_history_retention_count"
SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY = "system.executor_snapshot_retention_count"
SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"
SYSTEM_LOG_LEVEL_SETTING_KEY = "system.log_level"
SYSTEM_BASE_URL_SETTING_KEY = "system.base_url"
SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY = "system.oci_registry_redirects_enabled"
ADMIN_USER_ID_SETTING_KEY = sqlite_auth_oidc.ADMIN_USER_ID_SETTING_KEY
BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY = sqlite_auth_oidc.BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY
ADMIN_PASSWORD_RESET_REQUIRED_SETTING_KEY = (
    sqlite_auth_oidc.ADMIN_PASSWORD_RESET_REQUIRED_SETTING_KEY
)
ADMIN_OIDC_ISSUER_SETTING_KEY = sqlite_auth_oidc.ADMIN_OIDC_ISSUER_SETTING_KEY
ADMIN_OIDC_SUBJECT_SETTING_KEY = sqlite_auth_oidc.ADMIN_OIDC_SUBJECT_SETTING_KEY
RESERVED_AUTH_SETTING_KEYS = sqlite_auth_oidc.RESERVED_AUTH_SETTING_KEYS
DEFAULT_RELEASE_HISTORY_RETENTION_COUNT = 20
DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT = 10
DEFAULT_SYSTEM_TIMEZONE = "UTC"
DEFAULT_SYSTEM_LOG_LEVEL = "INFO"
DEFAULT_SYSTEM_BASE_URL = ""
DEFAULT_OCI_REGISTRY_REDIRECTS_ENABLED = False
CANONICAL_BOOLEAN_TRUE = "true"
CANONICAL_BOOLEAN_FALSE = "false"
ALLOWED_SYSTEM_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
MIN_RELEASE_HISTORY_RETENTION_COUNT = 1
MAX_RELEASE_HISTORY_RETENTION_COUNT = 1000
MIN_EXECUTOR_SNAPSHOT_RETENTION_COUNT = 1
MAX_EXECUTOR_SNAPSHOT_RETENTION_COUNT = 1000
_DOCKER_DISPLAY_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?([.\-].*)?$")


class SQLiteStorage:
    """SQLite database storage"""

    def __init__(self, db_path: str, system_key_manager: "SystemKeyManager" | None = None):
        self.db_path = db_path
        self.system_key_manager = system_key_manager
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Each asyncio task receives its own connection. Reusing one connection for
        # concurrent tasks lets one task commit or roll back another task's work.
        self._task_connections: dict[asyncio.Task[Any], aiosqlite.Connection] = {}
        self._task_connection_cleanup_tasks: set[asyncio.Task[None]] = set()
        # Storage can be exercised from TestClient's worker loop as well as
        # the caller's loop. This lock protects bookkeeping across both.
        self._connection_lock = threading.RLock()
        self._transaction_lock = asyncio.Lock()
        # All writes that encrypt user-managed secrets share this lock with
        # key rotation, so a row cannot miss a rotation scan.
        self._encryption_rotation_lock = asyncio.Lock()
        self._fallback_fernets: tuple[Fernet, ...] = ()

        # Notifier in-memory cache, invalidated after CRUD operations
        self._notifiers_cache: list | None = None
        self.webhooks = sqlite_webhooks.WebhookStore(self)

        if system_key_manager is None:
            raise RuntimeError("SQLiteStorage requires SystemKeyManager")

        self.set_encryption_key(system_key_manager.encryption_key)

    async def _open_connection(self) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(self.db_path)
        connection.row_factory = aiosqlite.Row
        # WAL permits readers to continue while a writer holds its transaction.
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.execute("PRAGMA cache_size=-16384")
        await connection.commit()
        return connection

    def _schedule_task_connection_cleanup(self, task: asyncio.Task[Any]) -> None:
        loop = task.get_loop()
        if loop.is_closed():
            return
        cleanup_task = loop.create_task(self._close_task_connection(task))
        with self._connection_lock:
            self._task_connection_cleanup_tasks.add(cleanup_task)
        cleanup_task.add_done_callback(self._discard_task_connection_cleanup)

    def _discard_task_connection_cleanup(self, cleanup_task: asyncio.Task[None]) -> None:
        with self._connection_lock:
            self._task_connection_cleanup_tasks.discard(cleanup_task)

    async def _close_task_connection(self, task: asyncio.Task[Any]) -> None:
        with self._connection_lock:
            connection = self._task_connections.get(task)
        if connection is None:
            return

        closed = False
        try:
            if connection.in_transaction:
                await connection.rollback()
            await connection.close()
            closed = True
        finally:
            # Keep a cancelled cleanup's connection discoverable so close()
            # can finish it from a still-running application loop.
            if closed:
                with self._connection_lock:
                    if self._task_connections.get(task) is connection:
                        self._task_connections.pop(task, None)

    async def _get_connection(self) -> aiosqlite.Connection:
        """Return a task-scoped SQLite connection with isolated transactions."""
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("SQLite storage access requires an asyncio task")

        with self._connection_lock:
            connection = self._task_connections.get(task)
        if connection is not None:
            return connection

        connection = await self._open_connection()
        with self._connection_lock:
            existing_connection = self._task_connections.get(task)
            if existing_connection is None:
                self._task_connections[task] = connection
                task.add_done_callback(self._schedule_task_connection_cleanup)
                logger.debug("SQLite task-scoped connection established: %s", self.db_path)
                return connection

        await connection.close()
        return existing_connection

    async def close_current_task_connection(self) -> None:
        """Release the request/job connection before its event loop can shut down."""
        task = asyncio.current_task()
        if task is not None:
            await self._close_task_connection(task)

    async def close(self) -> None:
        """Close every task-scoped database connection at application shutdown."""
        current_loop = asyncio.get_running_loop()
        with self._connection_lock:
            cleanup_tasks = tuple(
                task
                for task in self._task_connection_cleanup_tasks
                if task.get_loop() is current_loop
            )
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        with self._connection_lock:
            connections = list(self._task_connections.values())
            self._task_connections.clear()
            self._task_connection_cleanup_tasks.clear()
        for connection in connections:
            try:
                if connection.in_transaction:
                    await connection.rollback()
            finally:
                await connection.close()
        if connections or cleanup_tasks:
            logger.info("SQLite task-scoped connections closed")

    def invalidate_notifiers_cache(self) -> None:
        """Invalidate notifier in-memory cache after CRUD operations"""
        self._notifiers_cache = None

    @staticmethod
    def _normalize_notifier_language(value: Any) -> str:
        if value in {"en", "zh"}:
            return cast(str, value)
        raise ValueError("notifier language must be one of: en, zh")

    @property
    def encryption_rotation_lock(self) -> asyncio.Lock:
        return self._encryption_rotation_lock

    def set_encryption_key(self, key: str) -> None:
        self.set_encryption_keys(key)

    def set_encryption_keys(self, primary_key: str, fallback_keys: tuple[str, ...] = ()) -> None:
        try:
            self.fernet = Fernet(
                primary_key.encode("utf-8") if isinstance(primary_key, str) else primary_key
            )
            self._fallback_fernets = tuple(
                Fernet(key.encode("utf-8") if isinstance(key, str) else key)
                for key in fallback_keys
                if key != primary_key
            )
        except Exception as e:
            logger.error(f"Invalid ENCRYPTION_KEY: {e}")
            raise

    def _encrypt(self, raw: str) -> str | None:
        if not raw:
            return None
        try:
            return self.fernet.encrypt(raw.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return raw

    def _decrypt(self, enc: str) -> str | None:
        if not enc:
            return None
        for fernet in (self.fernet, *self._fallback_fernets):
            try:
                return fernet.decrypt(enc.encode()).decode()
            except InvalidToken:
                continue
            except Exception as e:
                logger.error(f"Decryption failed: {e}")
                return enc
        # Assume legacy plaintext data only after all known rotation keys fail.
        return enc

    @staticmethod
    def _looks_like_fernet_token(value: str) -> bool:
        return value.startswith("gAAAAA")

    @staticmethod
    def _encrypt_with_fernet(raw: str, fernet: Fernet) -> str:
        return fernet.encrypt(raw.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _decrypt_for_rotation(value: str, old_fernet: Fernet) -> tuple[str, bool]:
        try:
            return old_fernet.decrypt(value.encode("utf-8")).decode("utf-8"), True
        except InvalidToken as exc:
            if SQLiteStorage._looks_like_fernet_token(value):
                raise ValueError(
                    "encrypted value cannot be decrypted with the current encryption key"
                ) from exc
            return value, False

    @classmethod
    def _rotate_string_for_encryption_key(
        cls,
        value: str,
        old_fernet: Fernet,
        new_fernet: Fernet,
    ) -> tuple[str, bool]:
        plain, was_encrypted = cls._decrypt_for_rotation(value, old_fernet)
        return cls._encrypt_with_fernet(plain, new_fernet), was_encrypted

    @classmethod
    def _rotate_nested_strings_for_encryption_key(
        cls,
        value,
        old_fernet: Fernet,
        new_fernet: Fernet,
    ) -> tuple[Any, dict[str, int]]:
        stats = {"encrypted": 0, "plaintext": 0}

        def rotate(item):
            if isinstance(item, dict):
                return {key: rotate(child) for key, child in item.items()}
            if isinstance(item, list):
                return [rotate(child) for child in item]
            if isinstance(item, str):
                rotated, was_encrypted = cls._rotate_string_for_encryption_key(
                    item,
                    old_fernet,
                    new_fernet,
                )
                stats["encrypted" if was_encrypted else "plaintext"] += 1
                return rotated
            return item

        return rotate(value), stats

    def _encrypt_nested_strings(self, value):
        if isinstance(value, dict):
            return {key: self._encrypt_nested_strings(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._encrypt_nested_strings(item) for item in value]
        if isinstance(value, str):
            return self._encrypt(value)
        return value

    def _decrypt_nested_strings(self, value):
        if isinstance(value, dict):
            return {key: self._decrypt_nested_strings(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._decrypt_nested_strings(item) for item in value]
        if isinstance(value, str):
            return self._decrypt(value)
        return value

    @staticmethod
    def _dump_json(value) -> str:
        return json.dumps(value or {})

    @staticmethod
    def _load_json(value: str | None):
        if not value:
            return {}
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}

    @classmethod
    def _count_nested_strings(cls, value) -> int:
        if isinstance(value, dict):
            return sum(cls._count_nested_strings(item) for item in value.values())
        if isinstance(value, list):
            return sum(cls._count_nested_strings(item) for item in value)
        return 1 if isinstance(value, str) and value else 0

    @classmethod
    def _count_undecryptable_nested_strings(cls, value, old_fernet: Fernet) -> int:
        if isinstance(value, dict):
            return sum(
                cls._count_undecryptable_nested_strings(item, old_fernet) for item in value.values()
            )
        if isinstance(value, list):
            return sum(cls._count_undecryptable_nested_strings(item, old_fernet) for item in value)
        if not isinstance(value, str) or not value:
            return 0
        try:
            cls._decrypt_for_rotation(value, old_fernet)
        except ValueError:
            return 1
        return 0

    @staticmethod
    def _count_undecryptable_string(value: str | None, old_fernet: Fernet) -> int:
        if not value:
            return 0
        try:
            SQLiteStorage._decrypt_for_rotation(value, old_fernet)
        except ValueError:
            return 1
        return 0

    async def get_encryption_key_inventory(self) -> dict[str, Any]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        inventory = {
            "credentials_token": 0,
            "credentials_secrets": 0,
            "oauth_provider_client_secret": 0,
            "runtime_connection_secrets": 0,
            "repository_webhook_secret": 0,
        }
        undecryptable_count = 0

        cursor = await db.execute("SELECT token, secrets FROM credentials")
        for row in await cursor.fetchall():
            if row["token"]:
                inventory["credentials_token"] += 1
                undecryptable_count += self._count_undecryptable_string(row["token"], self.fernet)
            secrets_payload = self._load_json(row["secrets"])
            nested_count = self._count_nested_strings(secrets_payload)
            inventory["credentials_secrets"] += nested_count
            if nested_count:
                undecryptable_count += self._count_undecryptable_nested_strings(
                    secrets_payload, self.fernet
                )

        cursor = await db.execute("SELECT client_secret FROM oauth_providers")
        for row in await cursor.fetchall():
            if row["client_secret"]:
                inventory["oauth_provider_client_secret"] += 1
                undecryptable_count += self._count_undecryptable_string(
                    row["client_secret"], self.fernet
                )

        cursor = await db.execute("SELECT secrets FROM runtime_connections")
        for row in await cursor.fetchall():
            secrets_payload = self._load_json(row["secrets"])
            nested_count = self._count_nested_strings(secrets_payload)
            inventory["runtime_connection_secrets"] += nested_count
            if nested_count:
                undecryptable_count += self._count_undecryptable_nested_strings(
                    secrets_payload, self.fernet
                )

        if await self._table_exists("repository_webhooks"):
            cursor = await db.execute("SELECT secret FROM repository_webhooks")
            for row in await cursor.fetchall():
                if row["secret"]:
                    inventory["repository_webhook_secret"] += 1
                    undecryptable_count += self._count_undecryptable_string(
                        row["secret"], self.fernet
                    )

        return {"inventory": inventory, "undecryptable_count": undecryptable_count}

    async def rotate_encrypted_data(self, new_key: str) -> dict[str, Any]:
        async with self._encryption_rotation_lock:
            return await self._rotate_encrypted_data_locked(new_key)

    async def _rotate_encrypted_data_locked(self, new_key: str) -> dict[str, Any]:
        old_fernet = self.fernet
        new_fernet = Fernet(new_key.encode("utf-8"))
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        stats = {
            "inventory": {
                "credentials_token": 0,
                "credentials_secrets": 0,
                "oauth_provider_client_secret": 0,
                "runtime_connection_secrets": 0,
                "repository_webhook_secret": 0,
            },
            "rotated": {
                "credentials_token": 0,
                "credentials_secrets": 0,
                "oauth_provider_client_secret": 0,
                "runtime_connection_secrets": 0,
                "repository_webhook_secret": 0,
            },
            "plaintext_reencrypted": 0,
            "undecryptable_count": 0,
        }
        credential_updates: list[tuple[str, str, int]] = []
        oauth_provider_updates: list[tuple[str, int]] = []
        runtime_connection_updates: list[tuple[str, int]] = []
        repository_webhook_updates: list[tuple[str, str]] = []

        try:
            cursor = await db.execute("SELECT id, token, secrets FROM credentials")
            for row in await cursor.fetchall():
                encrypted_token = row["token"]
                rotated_token = encrypted_token
                if encrypted_token:
                    stats["inventory"]["credentials_token"] += 1
                    rotated_token, was_encrypted = self._rotate_string_for_encryption_key(
                        encrypted_token,
                        old_fernet,
                        new_fernet,
                    )
                    stats["rotated"]["credentials_token"] += 1
                    if not was_encrypted:
                        stats["plaintext_reencrypted"] += 1

                secrets_payload = self._load_json(row["secrets"])
                stats["inventory"]["credentials_secrets"] += self._count_nested_strings(
                    secrets_payload
                )
                rotated_secrets, nested_stats = self._rotate_nested_strings_for_encryption_key(
                    secrets_payload,
                    old_fernet,
                    new_fernet,
                )
                stats["rotated"]["credentials_secrets"] += (
                    nested_stats["encrypted"] + nested_stats["plaintext"]
                )
                stats["plaintext_reencrypted"] += nested_stats["plaintext"]
                credential_updates.append(
                    (rotated_token, self._dump_json(rotated_secrets), row["id"])
                )

            cursor = await db.execute("SELECT id, client_secret FROM oauth_providers")
            for row in await cursor.fetchall():
                client_secret = row["client_secret"]
                if not client_secret:
                    continue
                stats["inventory"]["oauth_provider_client_secret"] += 1
                rotated_secret, was_encrypted = self._rotate_string_for_encryption_key(
                    client_secret,
                    old_fernet,
                    new_fernet,
                )
                stats["rotated"]["oauth_provider_client_secret"] += 1
                if not was_encrypted:
                    stats["plaintext_reencrypted"] += 1
                oauth_provider_updates.append((rotated_secret, row["id"]))

            cursor = await db.execute("SELECT id, secrets FROM runtime_connections")
            for row in await cursor.fetchall():
                secrets_payload = self._load_json(row["secrets"])
                stats["inventory"]["runtime_connection_secrets"] += self._count_nested_strings(
                    secrets_payload
                )
                rotated_secrets, nested_stats = self._rotate_nested_strings_for_encryption_key(
                    secrets_payload,
                    old_fernet,
                    new_fernet,
                )
                stats["rotated"]["runtime_connection_secrets"] += (
                    nested_stats["encrypted"] + nested_stats["plaintext"]
                )
                stats["plaintext_reencrypted"] += nested_stats["plaintext"]
                runtime_connection_updates.append((self._dump_json(rotated_secrets), row["id"]))

            if await self._table_exists("repository_webhooks"):
                cursor = await db.execute("SELECT id, secret FROM repository_webhooks")
                for row in await cursor.fetchall():
                    if not row["secret"]:
                        continue
                    stats["inventory"]["repository_webhook_secret"] += 1
                    rotated_secret, was_encrypted = self._rotate_string_for_encryption_key(
                        row["secret"], old_fernet, new_fernet
                    )
                    stats["rotated"]["repository_webhook_secret"] += 1
                    if not was_encrypted:
                        stats["plaintext_reencrypted"] += 1
                    repository_webhook_updates.append((rotated_secret, row["id"]))
        except ValueError:
            stats["undecryptable_count"] = (await self.get_encryption_key_inventory())[
                "undecryptable_count"
            ]
            raise

        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.executemany(
                "UPDATE credentials SET token = ?, secrets = ? WHERE id = ?",
                credential_updates,
            )
            await db.executemany(
                "UPDATE oauth_providers SET client_secret = ? WHERE id = ?",
                oauth_provider_updates,
            )
            await db.executemany(
                "UPDATE runtime_connections SET secrets = ? WHERE id = ?",
                runtime_connection_updates,
            )
            await db.executemany(
                "UPDATE repository_webhooks SET secret = ? WHERE id = ?",
                repository_webhook_updates,
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

        return stats

    @staticmethod
    def _dump_tracker_channels(channels) -> str:
        if not channels:
            return "[]"
        valid_channels = [channel for channel in channels if channel is not None]
        return json.dumps([channel.model_dump() for channel in valid_channels])

    @staticmethod
    def _load_tracker_channels(value: str | None):
        from ..config import Channel

        if not value:
            return []

        try:
            channels_data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []

        channels = []
        for channel_data in channels_data:
            try:
                channels.append(Channel(**channel_data))
            except Exception:
                continue
        return channels

    @staticmethod
    def _require_lastrowid(lastrowid: int | None, entity: str) -> int:
        if lastrowid is None:
            raise ValueError(f"Failed to persist {entity}")
        return lastrowid

    @staticmethod
    def _normalize_tracker_source(source: TrackerSource) -> TrackerSource:
        return sqlite_aggregate_trackers.normalize_tracker_source(source)

    @staticmethod
    def _row_to_tracker_source(row) -> TrackerSource:
        return sqlite_aggregate_trackers.row_to_tracker_source(row)

    @staticmethod
    def _select_runtime_source(tracker: AggregateTracker) -> TrackerSource | None:
        return sqlite_aggregate_trackers.select_runtime_source(tracker)

    @staticmethod
    def _flatten_runtime_release_channels(
        tracker: AggregateTracker,
        runtime_config,
        selected_source: TrackerSource | None,
    ) -> list[dict[str, Any]]:
        return sqlite_aggregate_trackers.flatten_runtime_release_channels(
            tracker, runtime_config, selected_source
        )

    @staticmethod
    def authoritative_release_channels_for_tracker(
        tracker: AggregateTracker,
    ) -> list[dict[str, Any]]:
        channels: list[dict[str, Any]] = []
        enabled_sources = [source for source in tracker.sources if source.enabled]
        for source in sorted(enabled_sources, key=lambda item: item.source_rank):
            for release_channel in source.release_channels:
                payload = (
                    release_channel.model_dump()
                    if hasattr(release_channel, "model_dump")
                    else dict(release_channel)
                )
                release_channel_key = payload.get("release_channel_key") or payload.get(
                    "channel_key"
                )
                if not release_channel_key:
                    continue
                canonical_key = str(release_channel_key)
                payload["release_channel_key"] = canonical_key
                payload["channel_key"] = canonical_key
                payload["source_type"] = source.source_type
                payload["source_key"] = source.source_key
                if source.id is not None:
                    payload["tracker_source_id"] = source.id
                channels.append(payload)
        return channels

    @classmethod
    def resolve_tracker_release_channel(
        cls,
        tracker: AggregateTracker,
        selector: str,
    ) -> dict[str, Any] | None:
        for channel_rank, release_channel in enumerate(
            cls.authoritative_release_channels_for_tracker(tracker)
        ):
            channel_key = cls._channel_selection_key(release_channel, channel_rank)
            if selector == channel_key:
                return release_channel
        return None

    @classmethod
    def _aggregate_tracker_to_runtime_config(
        cls,
        tracker: AggregateTracker,
        runtime_row: aiosqlite.Row | None = None,
    ):
        from ..config import TrackerConfig

        selected_source = cls._select_runtime_source(tracker)
        if selected_source is None:
            return None

        runtime_config = None
        if runtime_row is not None and cls._is_runtime_only_trackers_row(runtime_row):
            runtime_config = cls._row_to_tracker_config(runtime_row)

        source_config = selected_source.source_config
        return TrackerConfig(
            name=tracker.name,
            type=cast(TrackerSourceType, selected_source.source_type),
            enabled=tracker.enabled,
            repo=source_config.get("repo"),
            project=source_config.get("project"),
            instance=source_config.get("instance"),
            chart=source_config.get("chart"),
            image=source_config.get("image"),
            registry=source_config.get("registry"),
            published_at_mode=cast(
                Literal["auto", "prefer_real", "first_observed"],
                source_config.get("published_at_mode") or "auto",
            ),
            credential_name=selected_source.credential_name,
            interval=runtime_config.interval if runtime_config else 360,
            version_sort_mode=(
                runtime_config.version_sort_mode if runtime_config else "published_at"
            ),
            fetch_limit=runtime_config.fetch_limit if runtime_config else 10,
            fetch_timeout=runtime_config.fetch_timeout if runtime_config else 15,
            fallback_tags=runtime_config.fallback_tags if runtime_config else False,
            github_fetch_mode=runtime_config.github_fetch_mode if runtime_config else "rest_first",
            channels=cast(
                list[Any],
                cls._flatten_runtime_release_channels(tracker, runtime_config, selected_source),
            ),
        )

    @staticmethod
    def _is_runtime_only_trackers_row(row: aiosqlite.Row) -> bool:
        legacy_source_columns = ("repo", "project", "instance", "chart", "image", "registry")
        return all(row[column] is None for column in legacy_source_columns)

    @staticmethod
    def _row_to_source_release_observation(row) -> SourceReleaseObservation:
        raw_payload = SQLiteStorage._load_json(row["raw_payload"])
        return SourceReleaseObservation(
            id=row["id"],
            tracker_source_id=row["tracker_source_id"],
            source_release_key=row["source_release_key"],
            name=row["name"],
            tag_name=row["tag_name"],
            version=row["version"],
            app_version=raw_payload.get("appVersion"),
            chart_version=raw_payload.get("chartVersion"),
            published_at=datetime.fromisoformat(row["published_at"]),
            url=row["url"],
            changelog_url=row["changelog_url"],
            prerelease=bool(row["prerelease"]),
            body=row["body"],
            commit_sha=row["commit_sha"],
            raw_payload=raw_payload,
            observed_at=datetime.fromisoformat(row["observed_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_canonical_release(
        row, observations: list[CanonicalReleaseObservation] | None = None
    ) -> CanonicalRelease:
        return CanonicalRelease(
            id=row["id"],
            aggregate_tracker_id=row["aggregate_tracker_id"],
            canonical_key=row["canonical_key"],
            version=row["version"],
            name=row["name"],
            tag_name=row["tag_name"],
            published_at=datetime.fromisoformat(row["published_at"]),
            url=row["url"],
            changelog_url=row["changelog_url"],
            prerelease=bool(row["prerelease"]),
            body=row["body"],
            primary_observation_id=row["primary_observation_id"],
            observations=observations or [],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def _load_tracker_sources(
        self, db: aiosqlite.Connection, aggregate_tracker_id: int
    ) -> list[TrackerSource]:
        return await sqlite_aggregate_trackers.load_tracker_sources(self, db, aggregate_tracker_id)

    async def _load_aggregate_tracker_from_row(
        self, db: aiosqlite.Connection, row: aiosqlite.Row
    ) -> AggregateTracker:
        return await sqlite_aggregate_trackers.load_aggregate_tracker_from_row(self, db, row)

    async def _persist_tracker_sources(
        self,
        db: aiosqlite.Connection,
        aggregate_tracker_id: int,
        sources: list[TrackerSource],
        primary_changelog_source_key: str | None,
    ) -> int | None:
        return await sqlite_aggregate_trackers.persist_tracker_sources(
            self, db, aggregate_tracker_id, sources, primary_changelog_source_key
        )

    async def _cleanup_removed_tracker_sources(
        self,
        db: aiosqlite.Connection,
        aggregate_tracker_id: int,
        removed_source_ids: list[int],
    ) -> None:
        placeholders = ", ".join("?" for _ in removed_source_ids)

        if await self._table_exists("executors"):
            await db.execute(
                f"UPDATE executors SET tracker_source_id = NULL WHERE tracker_source_id IN ({placeholders})",
                tuple(removed_source_ids),
            )

        await db.execute(
            f"DELETE FROM canonical_release_observations WHERE source_release_observation_id IN (SELECT id FROM source_release_observations WHERE tracker_source_id IN ({placeholders}))",
            tuple(removed_source_ids),
        )
        await db.execute(
            f"DELETE FROM source_release_observations WHERE tracker_source_id IN ({placeholders})",
            tuple(removed_source_ids),
        )

        canonical_release_ids = await (
            await db.execute(
                "SELECT id FROM canonical_releases WHERE aggregate_tracker_id = ?",
                (aggregate_tracker_id,),
            )
        ).fetchall()
        if canonical_release_ids:
            canonical_ids = [row["id"] for row in canonical_release_ids]
            canonical_placeholders = ", ".join("?" for _ in canonical_ids)
            await db.execute(
                f"DELETE FROM canonical_release_observations WHERE canonical_release_id IN ({canonical_placeholders})",
                tuple(canonical_ids),
            )
        await db.execute(
            "DELETE FROM canonical_releases WHERE aggregate_tracker_id = ?",
            (aggregate_tracker_id,),
        )

    @staticmethod
    def _is_floating_release_alias(value: str) -> bool:
        return value.strip().lower() in {
            "latest",
            "stable",
            "dev",
            "main",
            "master",
            "edge",
            "nightly",
        }

    def _group_canonical_observation_rows(
        self,
        observation_rows: list[aiosqlite.Row],
    ) -> dict[str, list[aiosqlite.Row]]:
        """Correlate container aliases with authoritative release tags.

        Digest remains the immutable artifact identity. An exact, non-floating
        alias may associate that artifact with one logical repository release.
        Ambiguous matches deliberately remain separate.
        """
        repository_types = {"github", "gitlab", "gitea"}
        release_rows_by_key: dict[str, list[aiosqlite.Row]] = {}
        artifact_rows_by_key: dict[str, list[aiosqlite.Row]] = {}
        other_rows: list[aiosqlite.Row] = []

        for row in observation_rows:
            source_type = row["source_type"]
            version = self._normalize_release_value(
                row["tag_name"]
            ) or self._normalize_release_value(row["version"])
            if source_type in repository_types and version is not None:
                key = self._canonical_key_for_version(version).lower()
                release_rows_by_key.setdefault(key, []).append(row)
            elif source_type == "container":
                artifact_rows_by_key.setdefault(
                    self._source_observation_identity_key(row), []
                ).append(row)
            else:
                other_rows.append(row)

        groups: dict[str, list[aiosqlite.Row]] = {
            key: list(rows) for key, rows in release_rows_by_key.items()
        }
        for artifact_key, artifact_rows in artifact_rows_by_key.items():
            matched_keys = {
                self._canonical_key_for_version(alias).lower()
                for row in artifact_rows
                if (
                    alias := (
                        self._normalize_release_value(row["tag_name"])
                        or self._normalize_release_value(row["version"])
                    )
                )
                is not None
                and not self._is_floating_release_alias(alias)
                and self._canonical_key_for_version(alias).lower() in release_rows_by_key
            }
            group_key = next(iter(matched_keys)) if len(matched_keys) == 1 else artifact_key
            groups.setdefault(group_key, []).extend(artifact_rows)

        for row in other_rows:
            groups.setdefault(self._source_observation_identity_key(row), []).append(row)
        return groups

    async def _rebuild_canonical_releases_for_tracker(
        self,
        db: aiosqlite.Connection,
        aggregate_tracker_id: int,
    ) -> None:
        observation_rows = await (
            await db.execute(
                """
                SELECT sro.*, ats.source_type, ats.source_rank
                FROM source_release_observations sro
                JOIN aggregate_tracker_sources ats ON ats.id = sro.tracker_source_id
                WHERE ats.aggregate_tracker_id = ?
                ORDER BY sro.id ASC
                """,
                (aggregate_tracker_id,),
            )
        ).fetchall()
        observation_groups = self._group_canonical_observation_rows(observation_rows)
        current_canonical_keys = set(observation_groups)
        existing_canonical_rows = await (
            await db.execute(
                "SELECT id, canonical_key FROM canonical_releases WHERE aggregate_tracker_id = ?",
                (aggregate_tracker_id,),
            )
        ).fetchall()
        stale_canonical_ids = [
            row["id"]
            for row in existing_canonical_rows
            if row["canonical_key"] not in current_canonical_keys
        ]
        if stale_canonical_ids:
            placeholders = ", ".join("?" for _ in stale_canonical_ids)
            await db.execute(
                f"DELETE FROM canonical_release_observations WHERE canonical_release_id IN ({placeholders})",
                tuple(stale_canonical_ids),
            )
            await db.execute(
                f"DELETE FROM canonical_releases WHERE id IN ({placeholders})",
                tuple(stale_canonical_ids),
            )
        rebuilt_at = datetime.now().isoformat()
        for canonical_key in sorted(current_canonical_keys):
            await self._upsert_canonical_release_for_immutable_key(
                db,
                aggregate_tracker_id,
                canonical_key,
                rebuilt_at,
                observation_groups[canonical_key],
            )

    async def initialize(self):
        """Initialize the database; only empty bootstrap or current canonical schema is supported, legacy-only non-empty databases are unsupported"""
        existing_tables = await self._list_user_tables()

        if self._is_empty_database(existing_tables):
            await self._bootstrap_empty_database()
            return

        if await self._has_current_schema(existing_tables):
            return

        if self._is_legacy_only_database(existing_tables):
            raise RuntimeError(
                "Unsupported legacy-only database schema detected. "
                "Automatic legacy-to-canonical startup migration is unsupported in this pre-release build. "
                "Reset the local/dev database and restart to bootstrap a fresh canonical schema."
            )

        raise RuntimeError(
            "Database schema is not at the current canonical version. "
            "Automatic startup migration for partial/outdated schemas is unsupported; "
            "reset the local/dev database and restart with an empty DB."
        )

    async def _list_user_tables(self) -> set[str]:
        db = await self._get_connection()
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row[0] for row in await cursor.fetchall()}

    @staticmethod
    def _is_empty_database(existing_tables: set[str]) -> bool:
        return not existing_tables or existing_tables <= {"schema_migrations"}

    @staticmethod
    def _is_legacy_only_database(existing_tables: set[str]) -> bool:
        legacy_tables = {"trackers", "releases", "release_history", "tracker_status"}
        canonical_tables = {
            "aggregate_trackers",
            "aggregate_tracker_sources",
            "source_release_observations",
            "canonical_releases",
            "canonical_release_observations",
        }
        return bool(existing_tables & legacy_tables) and not bool(
            existing_tables & canonical_tables
        )

    @staticmethod
    def _dbmate_migrations_dir() -> Path:
        return Path(__file__).resolve().parents[3] / "dbmate" / "migrations"

    @classmethod
    def _iter_dbmate_up_migrations(cls) -> list[tuple[str, str]]:
        statements: list[tuple[str, str]] = []
        for migration_path in sorted(cls._dbmate_migrations_dir().glob("*.sql")):
            content = migration_path.read_text(encoding="utf-8")
            up_section = content.split("-- migrate:down", 1)[0]
            up_sql = up_section.split("-- migrate:up", 1)[1].strip()
            statements.append((migration_path.name.split("_", 1)[0], up_sql))
        return statements

    async def _bootstrap_empty_database(self) -> None:
        db = await self._get_connection()
        await db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")

        for version, up_sql in self._iter_dbmate_up_migrations():
            await db.executescript(up_sql)
            await db.execute(
                "INSERT OR REPLACE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

        await db.commit()
        logger.info(
            "Bootstrapped empty SQLite database with bundled current schema: %s", self.db_path
        )

    async def _applied_schema_versions(self) -> set[str]:
        if not await self._table_exists("schema_migrations"):
            return set()

        db = await self._get_connection()
        cursor = await db.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in await cursor.fetchall()}

    async def _has_current_schema(self, existing_tables: set[str]) -> bool:
        required_tables = {
            "schema_migrations",
            "trackers",
            "aggregate_trackers",
            "aggregate_tracker_sources",
            "source_fetch_runs",
            "source_release_history",
            "source_release_run_observations",
            "source_release_aliases",
            "source_release_alias_run_observations",
            "repository_webhooks",
            "webhook_deliveries",
            "source_refresh_requests",
            "tracker_release_history",
            "tracker_release_history_sources",
            "tracker_current_releases",
        }
        if not required_tables.issubset(existing_tables):
            return False

        expected_versions = {version for version, _ in self._iter_dbmate_up_migrations()}
        applied_versions = await self._applied_schema_versions()
        return applied_versions == expected_versions

    async def _table_exists(self, table_name: str) -> bool:
        db = await self._get_connection()
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        return await cursor.fetchone() is not None

    async def _aggregate_schema_available(self) -> bool:
        return await self._table_exists("aggregate_trackers")

    async def save_tracker_config(self, config) -> None:
        """Save tracker configuration by creating or updating it."""
        source_key = self._source_key_for_tracker_type(config.type)
        source_config: dict[str, str] = {}
        if config.type in {"github", "gitea"} and config.repo:
            source_config["repo"] = config.repo
            normalized_instance = self._normalize_optional_string(config.instance)
            if normalized_instance is not None:
                source_config["instance"] = normalized_instance
        elif config.type == "gitlab" and config.project:
            source_config["project"] = config.project
            normalized_instance = self._normalize_optional_string(config.instance)
            if normalized_instance is not None:
                source_config["instance"] = normalized_instance
        elif config.type == "helm" and config.repo and config.chart:
            source_config["repo"] = config.repo
            source_config["chart"] = config.chart
        elif config.type == "container" and config.image:
            source_config["image"] = config.image
            normalized_registry = self._normalize_optional_string(config.registry)
            if normalized_registry is not None:
                source_config["registry"] = normalized_registry
            # Only persist the mode when it diverges from the default "auto",
            # so legacy trackers stay clean of the new key.
            if config.published_at_mode and config.published_at_mode != "auto":
                source_config["published_at_mode"] = config.published_at_mode

        aggregate_tracker = AggregateTracker(
            name=config.name,
            enabled=config.enabled,
            description=config.description if hasattr(config, "description") else None,
            primary_changelog_source_key=source_key,
            sources=[
                TrackerSource(
                    source_key=source_key,
                    source_type=cast(TrackerSourceType, config.type),
                    enabled=config.enabled,
                    credential_name=self._normalize_optional_string(config.credential_name),
                    source_config=source_config,
                    source_rank=0,
                )
            ],
        )
        await self.create_aggregate_tracker(aggregate_tracker)
        await self.save_tracker_runtime_config(config)

    async def save_tracker_runtime_config(self, config) -> None:
        channels_json = self._dump_tracker_channels(config.channels)

        db = await self._get_connection()
        now = datetime.now().isoformat()
        await db.execute(
            """
            INSERT OR REPLACE INTO trackers
            (name, type, enabled, repo, project, instance, chart, image, registry, credential_name, channels, interval, description, version_sort_mode, fetch_limit, fetch_timeout, fallback_tags, github_fetch_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config.name,
                config.type,
                1 if config.enabled else 0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                channels_json,
                config.interval,
                config.description if hasattr(config, "description") else None,
                config.version_sort_mode,
                config.fetch_limit,
                config.fetch_timeout,
                1 if config.fallback_tags else 0,
                config.github_fetch_mode,
                now,
                now,
            ),
        )
        await db.commit()

    async def get_all_tracker_configs(self) -> list:
        """Get all tracker configurations."""

        await self.cleanup_blank_tracker_rows()
        has_trackers_table = await self._table_exists("trackers")
        has_aggregate_schema = await self._aggregate_schema_available()

        if not has_trackers_table and not has_aggregate_schema:
            return []

        if not has_aggregate_schema:
            return []

        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        tracker_rows = (
            await (await db.execute("SELECT * FROM trackers")).fetchall()
            if has_trackers_table
            else []
        )
        tracker_rows_by_name = {row["name"]: row for row in tracker_rows}

        aggregate_rows = (
            await (
                await db.execute("SELECT * FROM aggregate_trackers ORDER BY name ASC")
            ).fetchall()
            if has_aggregate_schema
            else []
        )
        aggregate_trackers = [
            await self._load_aggregate_tracker_from_row(db, row) for row in aggregate_rows
        ]

        configs = [
            self._aggregate_tracker_to_runtime_config(
                tracker, tracker_rows_by_name.get(tracker.name)
            )
            for tracker in aggregate_trackers
        ]
        return sorted(
            (config for config in configs if config is not None), key=lambda config: config.name
        )

    async def get_tracker_configs_paginated(self, skip: int = 0, limit: int = 20) -> list:
        """Get tracker configurations with pagination."""
        configs = await self.get_all_tracker_configs()
        return configs[skip : skip + limit]

    async def get_total_tracker_configs_count(self) -> int:
        """Get the total tracker configuration count."""
        return len(await self.get_all_tracker_configs())

    async def cleanup_blank_tracker_rows(self) -> None:
        db = await self._get_connection()
        if await self._table_exists("tracker_status"):
            await db.execute("DELETE FROM tracker_status WHERE TRIM(name) = ''")
        if await self._table_exists("trackers"):
            await db.execute("DELETE FROM trackers WHERE TRIM(name) = ''")
        await db.commit()

    async def get_tracker_config(self, name: str):
        """Get a single tracker configuration."""
        await self.cleanup_blank_tracker_rows()
        has_trackers_table = await self._table_exists("trackers")
        has_aggregate_schema = await self._aggregate_schema_available()
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        runtime_row = (
            await (await db.execute("SELECT * FROM trackers WHERE name = ?", (name,))).fetchone()
            if has_trackers_table
            else None
        )
        aggregate_row = (
            await (
                await db.execute("SELECT * FROM aggregate_trackers WHERE name = ?", (name,))
            ).fetchone()
            if has_aggregate_schema
            else None
        )
        if aggregate_row is not None:
            aggregate_tracker = await self._load_aggregate_tracker_from_row(db, aggregate_row)
            return self._aggregate_tracker_to_runtime_config(aggregate_tracker, runtime_row)
        return None

    async def delete_tracker_config(self, name: str) -> None:
        """Delete a tracker configuration."""
        db = await self._get_connection()
        await db.execute("DELETE FROM trackers WHERE name = ?", (name,))
        await db.commit()

    async def create_aggregate_tracker(self, tracker: AggregateTracker) -> AggregateTracker:
        return await sqlite_aggregate_trackers.create_aggregate_tracker(self, tracker)

    async def get_aggregate_tracker(self, name: str) -> AggregateTracker | None:
        return await sqlite_aggregate_trackers.get_aggregate_tracker(self, name)

    async def get_all_aggregate_trackers(self) -> list[AggregateTracker]:
        return await sqlite_aggregate_trackers.get_all_aggregate_trackers(self)

    async def get_aggregate_trackers_page(
        self, *, skip: int, limit: int, search: str | None = None
    ) -> tuple[list[AggregateTracker], int]:
        return await sqlite_aggregate_trackers.get_aggregate_trackers_page(
            self, skip=skip, limit=limit, search=search
        )

    async def get_tracker_source(self, tracker_source_id: int) -> TrackerSource | None:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM aggregate_tracker_sources WHERE id = ?", (tracker_source_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_tracker_source(row) if row else None

    async def get_executor_binding(
        self, tracker_source_id: int
    ) -> tuple[AggregateTracker, TrackerSource] | None:
        return await sqlite_aggregate_trackers.get_executor_binding(self, tracker_source_id)

    async def get_source_release_observations_by_source(
        self, tracker_source_id: int
    ) -> list[SourceReleaseObservation]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM source_release_observations
            WHERE tracker_source_id = ?
            ORDER BY published_at DESC, id DESC
            """,
            (tracker_source_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_source_release_observation(row) for row in rows]

    async def update_aggregate_tracker(self, tracker: AggregateTracker) -> AggregateTracker:
        return await sqlite_aggregate_trackers.update_aggregate_tracker(self, tracker)

    async def delete_aggregate_tracker(self, name: str) -> None:
        await sqlite_aggregate_trackers.delete_aggregate_tracker(self, name)

    async def get_canonical_releases(self, aggregate_tracker_name: str) -> list[CanonicalRelease]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM aggregate_trackers WHERE name = ?", (aggregate_tracker_name,)
        )
        tracker_row = await cursor.fetchone()
        if tracker_row is None:
            return []

        canonical_cursor = await db.execute(
            """
            SELECT *
            FROM canonical_releases
            WHERE aggregate_tracker_id = ?
            ORDER BY published_at DESC, id DESC
            """,
            (tracker_row["id"],),
        )
        canonical_rows = await canonical_cursor.fetchall()
        return [await self._load_canonical_release_from_row(db, row) for row in canonical_rows]

    async def get_source_release_observations(
        self, aggregate_tracker_name: str
    ) -> list[SourceReleaseObservation]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM aggregate_trackers WHERE name = ?", (aggregate_tracker_name,)
        )
        tracker_row = await cursor.fetchone()
        if tracker_row is None:
            return []

        observation_cursor = await db.execute(
            """
            SELECT sro.*
            FROM source_release_observations sro
            JOIN aggregate_tracker_sources ats ON ats.id = sro.tracker_source_id
            WHERE ats.aggregate_tracker_id = ?
            ORDER BY sro.published_at DESC, sro.id DESC
            """,
            (tracker_row["id"],),
        )
        observation_rows = await observation_cursor.fetchall()
        return [self._row_to_source_release_observation(row) for row in observation_rows]

    async def _load_canonical_release_from_row(
        self, db: aiosqlite.Connection, row: aiosqlite.Row
    ) -> CanonicalRelease:
        observation_cursor = await db.execute(
            """
            SELECT source_release_observation_id, contribution_kind, created_at
            FROM canonical_release_observations
            WHERE canonical_release_id = ?
            ORDER BY created_at ASC, source_release_observation_id ASC
            """,
            (row["id"],),
        )
        observation_rows = await observation_cursor.fetchall()
        observations = [
            CanonicalReleaseObservation(
                source_release_observation_id=observation_row["source_release_observation_id"],
                contribution_kind=observation_row["contribution_kind"],
                created_at=datetime.fromisoformat(observation_row["created_at"]),
            )
            for observation_row in observation_rows
        ]
        return self._row_to_canonical_release(row, observations)

    @staticmethod
    def _normalize_version_for_ordering(version: str) -> str:
        normalized_version = version.strip()
        lowered = normalized_version.lower()

        for prefix in ("version/", "release/"):
            if lowered.startswith(prefix):
                normalized_version = normalized_version[len(prefix) :]
                lowered = normalized_version.lower()
                break

        if (
            normalized_version.startswith("v")
            and len(normalized_version) > 1
            and normalized_version[1].isdigit()
        ):
            normalized_version = normalized_version[1:]

        return normalized_version

    @staticmethod
    def _canonical_key_for_version(version: str) -> str:
        canonical_key = SQLiteStorage._normalize_version_for_ordering(version)
        if not canonical_key:
            raise ValueError("version must be a non-empty string")
        return canonical_key

    @staticmethod
    def _normalize_release_value(value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        return normalized_value or None

    @classmethod
    def _release_version_metadata(
        cls, release: Release, *, source_type: str | None = None
    ) -> tuple[str, str | None, str | None]:
        effective_source_type = source_type or release.tracker_type
        version = cls._normalize_release_value(release.version)
        if effective_source_type != "helm":
            if version is None:
                raise ValueError("release.version must be a non-empty string")
            return version, None, None

        app_version = cls._normalize_release_value(release.app_version)
        chart_version = cls._normalize_release_value(release.chart_version)
        if app_version is None:
            raise ValueError("Helm releases require an app version")
        return app_version, app_version, chart_version

    @classmethod
    def _release_core_identity_value(
        cls, release: Release, *, source_type: str | None = None
    ) -> str | None:
        effective_source_type = source_type or release.tracker_type
        normalized_commit = cls._normalize_release_value(release.commit_sha)

        if effective_source_type == "container":
            return normalized_commit

        if effective_source_type in {"github", "gitlab", "gitea", "helm"}:
            return normalized_commit

        return normalized_commit

    @classmethod
    def _release_digest_value(
        cls, release: Release, *, source_type: str | None = None
    ) -> str | None:
        effective_source_type = source_type or release.tracker_type
        if effective_source_type not in {"container", "helm"}:
            return None
        digest = cls._normalize_release_value(release.commit_sha)
        return digest.lower() if digest is not None else None

    @staticmethod
    def _identity_key(version: str, digest: str | None) -> str:
        normalized_identity = SQLiteStorage._normalize_release_value(digest)
        if normalized_identity is None:
            normalized_identity = SQLiteStorage._normalize_release_value(version)
        if normalized_identity is None:
            raise ValueError("release version must be a non-empty string")
        return normalized_identity.lower()

    @classmethod
    def _source_observation_identity_key(cls, observation_row: aiosqlite.Row) -> str:
        identity_value = cls._normalize_release_value(observation_row["commit_sha"])
        return cls._identity_key(str(observation_row["version"]), identity_value)

    @classmethod
    def release_identity_key_for_source(
        cls, release: Release, *, source_type: str | None = None
    ) -> str:
        version, _, chart_version = cls._release_version_metadata(release, source_type=source_type)
        identity_version = chart_version or version
        identity_value = cls._release_core_identity_value(release, source_type=source_type)
        return cls._identity_key(identity_version, identity_value)

    @classmethod
    def _source_release_key_for_release(
        cls, release: Release, *, source_type: str | None = None
    ) -> str:
        version, _, chart_version = cls._release_version_metadata(release, source_type=source_type)
        source_release_key = (
            chart_version or cls._normalize_release_value(release.tag_name) or version
        )
        if source_release_key is None:
            raise ValueError("release tag_name or version must be a non-empty string")
        return source_release_key

    @classmethod
    def _source_history_display_priority(
        cls,
        *,
        source_type: str,
        version: str,
        tag_name: str | None,
    ) -> tuple[int, int, int]:
        normalized_version = cls._normalize_version_for_ordering(version)
        normalized_tag = cls._normalize_release_value(tag_name)
        if source_type != "container" or normalized_tag is None:
            return (0, 0, 0)

        normalized_tag_for_ordering = cls._normalize_version_for_ordering(normalized_tag)
        tag_parts = _DOCKER_DISPLAY_VERSION_RE.match(normalized_tag_for_ordering)
        has_patch = 1 if tag_parts is not None and tag_parts.group(3) is not None else 0
        is_plain_numeric = 1 if tag_parts is not None and tag_parts.group(4) is None else 0

        if normalized_tag_for_ordering == normalized_version:
            return (3, has_patch, is_plain_numeric)
        if normalized_tag.lower() in {"latest", "stable", "edge", "nightly", "main"}:
            return (1, 0, 0)
        if tag_parts is not None:
            return (2, has_patch, is_plain_numeric)
        return (0, 0, 0)

    @classmethod
    def _should_replace_source_history_display(
        cls,
        *,
        source_type: str,
        version: str,
        tag_name: str | None,
        existing_version: str,
        existing_tag_name: str | None,
    ) -> bool:
        if source_type != "container":
            return True
        new_priority = cls._source_history_display_priority(
            source_type=source_type,
            version=version,
            tag_name=tag_name,
        )
        existing_priority = cls._source_history_display_priority(
            source_type=source_type,
            version=existing_version,
            tag_name=existing_tag_name,
        )
        return new_priority >= existing_priority

    async def _select_primary_canonical_observation(
        self, db: aiosqlite.Connection, aggregate_tracker_id: int, immutable_key: str
    ) -> aiosqlite.Row:
        cursor = await db.execute(
            """
            SELECT sro.*, ats.source_rank, ats.source_type
            FROM source_release_observations sro
            JOIN aggregate_tracker_sources ats ON ats.id = sro.tracker_source_id
            WHERE ats.aggregate_tracker_id = ?
            """,
            (aggregate_tracker_id,),
        )
        observation_rows = [
            row
            for row in await cursor.fetchall()
            if self._source_observation_identity_key(row) == immutable_key
            or self._canonical_key_for_version(row["version"]) == immutable_key
        ]
        observation_rows.sort(
            key=lambda row: (
                0 if row["source_type"] in {"github", "gitlab", "gitea"} else 1,
                row["source_rank"],
                -(
                    datetime.fromisoformat(row["published_at"]).timestamp()
                    if row["published_at"]
                    else 0
                ),
                row["id"],
            )
        )
        selected_observation = observation_rows[0] if observation_rows else None
        if selected_observation is None:
            raise ValueError(
                f"No source release observations found for aggregate tracker {aggregate_tracker_id}"
            )
        return selected_observation

    async def _list_canonical_version_observations(
        self, db: aiosqlite.Connection, aggregate_tracker_id: int, immutable_key: str
    ) -> list[aiosqlite.Row]:
        cursor = await db.execute(
            """
            SELECT sro.*, ats.source_rank, ats.source_type
            FROM source_release_observations sro
            JOIN aggregate_tracker_sources ats ON ats.id = sro.tracker_source_id
            WHERE ats.aggregate_tracker_id = ?
            """,
            (aggregate_tracker_id,),
        )
        observation_rows = [
            row
            for row in await cursor.fetchall()
            if self._source_observation_identity_key(row) == immutable_key
            or self._canonical_key_for_version(row["version"]) == immutable_key
        ]
        observation_rows.sort(
            key=lambda row: (
                row["source_rank"],
                -(
                    datetime.fromisoformat(row["published_at"]).timestamp()
                    if row["published_at"]
                    else 0
                ),
                row["id"],
            )
        )
        return observation_rows

    async def _upsert_canonical_release_for_immutable_key(
        self,
        db: aiosqlite.Connection,
        aggregate_tracker_id: int,
        immutable_key: str,
        created_at: str,
        observation_rows: list[aiosqlite.Row] | None = None,
    ) -> int:
        if observation_rows is None:
            primary_observation = await self._select_primary_canonical_observation(
                db, aggregate_tracker_id, immutable_key
            )
            observation_rows = await self._list_canonical_version_observations(
                db, aggregate_tracker_id, immutable_key
            )
        else:
            observation_rows = sorted(
                observation_rows,
                key=lambda row: (
                    0 if row["source_type"] in {"github", "gitlab", "gitea"} else 1,
                    row["source_rank"],
                    -(
                        datetime.fromisoformat(row["published_at"]).timestamp()
                        if row["published_at"]
                        else 0
                    ),
                    row["id"],
                ),
            )
            primary_observation = observation_rows[0]
        display_version = primary_observation["version"]
        if self._canonical_key_for_version(display_version) == immutable_key:
            display_version = immutable_key

        cursor = await db.execute(
            "SELECT id, created_at FROM canonical_releases WHERE aggregate_tracker_id = ? AND canonical_key = ?",
            (aggregate_tracker_id, immutable_key),
        )
        existing_row = await cursor.fetchone()

        if existing_row is None:
            cursor = await db.execute(
                """
                INSERT INTO canonical_releases
                (aggregate_tracker_id, canonical_key, version, name, tag_name, published_at, url, changelog_url, prerelease, body, primary_observation_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregate_tracker_id,
                    immutable_key,
                    display_version,
                    primary_observation["name"],
                    primary_observation["tag_name"],
                    primary_observation["published_at"],
                    primary_observation["url"],
                    primary_observation["changelog_url"],
                    1 if primary_observation["prerelease"] else 0,
                    primary_observation["body"],
                    primary_observation["id"],
                    created_at,
                    primary_observation["updated_at"],
                ),
            )
            canonical_release_id = self._require_lastrowid(cursor.lastrowid, "canonical release")
        else:
            canonical_release_id = existing_row["id"]
            await db.execute(
                """
                UPDATE canonical_releases
                SET version = ?,
                    name = ?,
                    tag_name = ?,
                    published_at = ?,
                    url = ?,
                    changelog_url = ?,
                    prerelease = ?,
                    body = ?,
                    primary_observation_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    display_version,
                    primary_observation["name"],
                    primary_observation["tag_name"],
                    primary_observation["published_at"],
                    primary_observation["url"],
                    primary_observation["changelog_url"],
                    1 if primary_observation["prerelease"] else 0,
                    primary_observation["body"],
                    primary_observation["id"],
                    primary_observation["updated_at"],
                    canonical_release_id,
                ),
            )

        await self._sync_canonical_release_observations(
            db,
            canonical_release_id,
            observation_rows,
            primary_observation["id"],
            created_at,
        )
        return canonical_release_id

    async def _upsert_canonical_release_for_version(
        self,
        db: aiosqlite.Connection,
        aggregate_tracker_id: int,
        version: str,
        created_at: str,
    ) -> int:
        return await self._upsert_canonical_release_for_immutable_key(
            db,
            aggregate_tracker_id,
            self._canonical_key_for_version(version),
            created_at,
        )

    async def _sync_canonical_release_observations(
        self,
        db: aiosqlite.Connection,
        canonical_release_id: int,
        observation_rows: list[aiosqlite.Row],
        primary_observation_id: int,
        created_at: str,
    ) -> None:
        observation_ids = [observation_row["id"] for observation_row in observation_rows]
        if not observation_ids:
            return

        placeholders = ", ".join("?" for _ in observation_ids)
        await db.execute(
            f"""
            DELETE FROM canonical_release_observations
            WHERE canonical_release_id = ?
              AND source_release_observation_id NOT IN ({placeholders})
            """,
            (canonical_release_id, *observation_ids),
        )

        for observation_row in observation_rows:
            contribution_kind = (
                "primary" if observation_row["id"] == primary_observation_id else "supporting"
            )
            await self._upsert_canonical_release_observation(
                db,
                canonical_release_id,
                observation_row["id"],
                created_at,
                contribution_kind,
            )

    @staticmethod
    def _normalize_optional_string(value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        return normalized_value or None

    @staticmethod
    def _source_key_for_tracker_type(tracker_type: str) -> str:
        source_keys = {
            "github": "repo",
            "gitea": "repo",
            "gitlab": "project",
            "helm": "chart",
            "container": "image",
        }
        try:
            return source_keys[tracker_type]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported tracker type for source key mapping: {tracker_type}"
            ) from exc

    @classmethod
    def _backfill_helm_observation_values(
        cls, observation_row: aiosqlite.Row
    ) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
        raw_payload = cls._load_json(observation_row["raw_payload"])
        app_version = cls._normalize_release_value(raw_payload.get("appVersion"))
        chart_version = cls._normalize_release_value(raw_payload.get("chartVersion"))
        if chart_version is None:
            fallback_candidates = [
                observation_row["tag_name"],
                observation_row["source_release_key"],
            ]
            for candidate in fallback_candidates:
                normalized_candidate = cls._normalize_release_value(candidate)
                if normalized_candidate is not None:
                    chart_version = normalized_candidate
                    break

        tag_name = chart_version or cls._normalize_release_value(observation_row["tag_name"])
        source_release_key = tag_name or cls._normalize_release_value(
            observation_row["source_release_key"]
        )

        persisted_raw_payload = dict(raw_payload)
        if app_version is not None:
            persisted_raw_payload["appVersion"] = app_version
        if chart_version is not None:
            persisted_raw_payload["chartVersion"] = chart_version

        return app_version, chart_version, source_release_key, persisted_raw_payload

    async def _backfill_existing_helm_observations_and_canonicals(
        self, db: aiosqlite.Connection
    ) -> None:
        db.row_factory = aiosqlite.Row
        observation_rows = await (await db.execute("""
                SELECT sro.*, ats.aggregate_tracker_id
                FROM source_release_observations sro
                JOIN aggregate_tracker_sources ats ON ats.id = sro.tracker_source_id
                WHERE ats.source_type = 'helm'
                ORDER BY ats.aggregate_tracker_id ASC, sro.id ASC
                """)).fetchall()
        for observation_row in observation_rows:
            app_version, chart_version, source_release_key, raw_payload = (
                self._backfill_helm_observation_values(observation_row)
            )
            if app_version is None or source_release_key is None:
                continue

            await db.execute(
                """
                UPDATE source_release_observations
                SET source_release_key = ?,
                    tag_name = ?,
                    version = ?,
                    raw_payload = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    source_release_key,
                    chart_version or source_release_key,
                    app_version,
                    self._dump_json(raw_payload),
                    datetime.now().isoformat(),
                    observation_row["id"],
                ),
            )

        helm_tracker_rows = await (
            await db.execute(
                "SELECT DISTINCT aggregate_tracker_id FROM aggregate_tracker_sources WHERE source_type = 'helm'"
            )
        ).fetchall()
        for tracker_row in helm_tracker_rows:
            await self._rebuild_canonical_releases_for_tracker(
                db, tracker_row["aggregate_tracker_id"]
            )

    async def create_source_fetch_run(
        self,
        tracker_source_id: int,
        *,
        trigger_mode: str,
        started_at: datetime | None = None,
    ) -> int:
        return await sqlite_release_history.create_source_fetch_run(
            self, tracker_source_id, trigger_mode=trigger_mode, started_at=started_at
        )

    async def finalize_source_fetch_run(
        self,
        source_fetch_run_id: int,
        *,
        status: str,
        fetched_count: int,
        filtered_in_count: int,
        error_message: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        return await sqlite_release_history.finalize_source_fetch_run(
            self,
            source_fetch_run_id,
            status=status,
            fetched_count=fetched_count,
            filtered_in_count=filtered_in_count,
            error_message=error_message,
            finished_at=finished_at,
        )

    async def reconcile_interrupted_source_fetch_runs(
        self, *, finished_at: datetime | None = None
    ) -> int:
        return await sqlite_release_history.reconcile_interrupted_source_fetch_runs(
            self, finished_at=finished_at
        )

    async def append_source_history_for_run(
        self,
        source_fetch_run_id: int,
        tracker_source: TrackerSource,
        releases: list[Release],
        *,
        aggregate_tracker_id: int | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, int]:
        return await sqlite_release_history.append_source_history_for_run(
            self,
            source_fetch_run_id,
            tracker_source,
            releases,
            aggregate_tracker_id=aggregate_tracker_id,
            observed_at=observed_at,
        )

    async def get_source_release_aliases_by_history_ids(
        self, source_release_history_ids: list[int]
    ) -> dict[int, list[dict[str, Any]]]:
        return await sqlite_release_aliases.get_source_release_aliases_by_history_ids(
            self, source_release_history_ids
        )

    async def get_current_source_release_aliases_by_history_ids(
        self, source_release_history_ids: list[int]
    ) -> dict[int, list[dict[str, Any]]]:
        return await sqlite_release_aliases.get_current_source_release_aliases_by_history_ids(
            self, source_release_history_ids
        )

    async def get_source_release_aliases_for_source(
        self, tracker_source_id: int
    ) -> dict[int, list[dict[str, Any]]]:
        return await sqlite_release_aliases.get_source_release_aliases_for_source(
            self, tracker_source_id
        )

    async def get_source_alias_last_observed(self, tracker_source_id: int) -> dict[str, datetime]:
        return await sqlite_release_aliases.get_source_alias_last_observed(self, tracker_source_id)

    async def get_source_alias_latest_digests(self, tracker_source_id: int) -> dict[str, str]:
        return await sqlite_release_aliases.get_source_alias_latest_digests(self, tracker_source_id)

    async def get_source_release_history_releases_by_source(
        self,
        tracker_source_id: int,
    ) -> list[Release]:
        return await sqlite_release_history.get_source_release_history_releases_by_source(
            self, tracker_source_id
        )

    async def get_source_release_history_id(
        self,
        tracker_source_id: int,
        identity_key: str,
    ) -> int | None:
        return await sqlite_release_history.get_source_release_history_id(
            self, tracker_source_id, identity_key
        )

    async def get_source_release_history_digests(
        self,
        tracker_source_id: int,
        tag_names: list[str],
    ) -> dict[str, str | None]:
        return await sqlite_release_history.get_source_release_history_digests(
            self, tracker_source_id, tag_names
        )

    async def get_correlated_release_candidates(
        self, aggregate_tracker_id: int
    ) -> list[dict[str, Any]]:
        return await sqlite_release_history.get_correlated_release_candidates(
            self, aggregate_tracker_id
        )

    async def merge_tracker_release_history_sources(
        self,
        *,
        aggregate_tracker_id: int,
        canonical_tracker_release_history_id: int,
        source_history_ids: list[int],
        artifact_digest: str | None,
    ) -> None:
        await sqlite_release_history.merge_tracker_release_history_sources(
            self,
            aggregate_tracker_id=aggregate_tracker_id,
            canonical_tracker_release_history_id=canonical_tracker_release_history_id,
            source_history_ids=source_history_ids,
            artifact_digest=artifact_digest,
        )

    async def upsert_tracker_release_history(
        self,
        aggregate_tracker_id: int,
        release: Release,
        *,
        primary_source_release_history_id: int,
        supporting_source_release_history_ids: list[int] | None = None,
        source_type: str | None = None,
    ) -> tuple[int, bool]:
        return await sqlite_release_history.upsert_tracker_release_history(
            self,
            aggregate_tracker_id,
            release,
            primary_source_release_history_id=primary_source_release_history_id,
            supporting_source_release_history_ids=supporting_source_release_history_ids,
            source_type=source_type,
        )

    async def get_tracker_release_history_releases(
        self,
        aggregate_tracker_id: int,
    ) -> list[Release]:
        return await sqlite_release_history.get_tracker_release_history_releases(
            self, aggregate_tracker_id
        )

    @staticmethod
    def select_top_releases_for_channel(
        releases: list[Release],
        channel,
        *,
        limit: int,
        sort_mode: str = "published_at",
        channel_source_type: str | None = None,
        use_immutable_identity: bool = True,
    ) -> list[Release]:
        if limit <= 0:
            return []
        unique_releases = (
            SQLiteStorage.dedupe_releases_by_immutable_identity(releases)
            if use_immutable_identity
            else SQLiteStorage.dedupe_releases_by_identity(releases)
        )
        channel_name = (
            channel.get("name") if isinstance(channel, dict) else getattr(channel, "name", None)
        )
        candidates = [
            release
            for release in unique_releases
            if SQLiteStorage._release_matches_channel(
                release,
                channel,
                channel_source_type=channel_source_type,
            )
        ]
        selected = sorted(
            candidates,
            key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
            reverse=True,
        )[:limit]
        if not channel_name:
            return selected
        return [
            SQLiteStorage._copy_release_with_channel_name(release, str(channel_name))
            for release in selected
        ]

    @classmethod
    def _source_identity_for_retention(
        cls,
        *,
        tracker_source_id: Any = None,
        source_key: Any = None,
    ) -> str:
        if tracker_source_id is not None:
            return str(tracker_source_id)
        normalized_source_key = cls._normalize_release_value(
            str(source_key) if source_key is not None else None
        )
        return normalized_source_key or "__unknown_source__"

    @classmethod
    def _channel_identity_for_retention(cls, channel_name: Any = None) -> str:
        normalized_channel_name = cls._normalize_release_value(
            str(channel_name) if channel_name is not None else None
        )
        return normalized_channel_name or "__default__"

    async def _get_tracker_release_history_retention_groups(
        self,
        aggregate_tracker_id: int,
    ) -> dict[int, tuple[str, str]]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT trh.id AS tracker_release_history_id,
                       srh.tracker_source_id,
                       ats.source_key,
                       srh.raw_payload
                FROM tracker_release_history trh
                JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
                LEFT JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
                WHERE trh.aggregate_tracker_id = ?
                """,
                (aggregate_tracker_id,),
            )
        ).fetchall()

        groups: dict[int, tuple[str, str]] = {}
        for row in rows:
            raw_payload = self._load_json(row["raw_payload"])
            source_identity = self._source_identity_for_retention(
                tracker_source_id=row["tracker_source_id"],
                source_key=raw_payload.get("source_key") or row["source_key"],
            )
            channel_identity = self._channel_identity_for_retention(raw_payload.get("channel_name"))
            groups[int(row["tracker_release_history_id"])] = (source_identity, channel_identity)

        return groups

    @staticmethod
    def _channel_value(channel: Any, key: str) -> Any:
        if isinstance(channel, dict):
            return channel.get(key)
        return getattr(channel, key, None)

    @classmethod
    def _release_matches_retention_source_channel(
        cls,
        release: Release,
        retention_groups: dict[int, tuple[str, str]],
        channel: Any,
    ) -> bool:
        if release.id is None:
            return False
        release_group = retention_groups.get(int(release.id))
        if release_group is None:
            return False
        channel_source_identity = cls._source_identity_for_retention(
            tracker_source_id=cls._channel_value(channel, "tracker_source_id"),
            source_key=cls._channel_value(channel, "source_key"),
        )
        if release_group[0] != channel_source_identity:
            return False
        channel_identity = cls._channel_identity_for_retention(cls._channel_value(channel, "name"))
        return release_group[1] in {channel_identity, "__default__"}

    async def cleanup_release_history(
        self,
        *,
        retention_count: int | None = None,
    ) -> dict[str, Any]:
        retention = retention_count or await self.get_release_history_retention_count()
        retention = max(
            MIN_RELEASE_HISTORY_RETENTION_COUNT,
            min(MAX_RELEASE_HISTORY_RETENTION_COUNT, int(retention)),
        )
        result: dict[str, Any] = {
            "retention_count": retention,
            "trackers_scanned": 0,
            "tracker_release_history_deleted": 0,
            "tracker_release_history_sources_deleted": 0,
            "source_release_history_deleted": 0,
            "source_release_run_observations_deleted": 0,
            "sqlite_optimize_performed": False,
            "wal_checkpoint_performed": False,
            "vacuum_performed": False,
        }

        aggregate_trackers = await self.get_all_aggregate_trackers()
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row

        for aggregate_tracker in aggregate_trackers:
            if aggregate_tracker.id is None:
                continue
            result["trackers_scanned"] += 1
            history_releases = await self.get_tracker_release_history_releases(aggregate_tracker.id)
            if not history_releases:
                continue

            keep_ids: set[int] = set()
            current_rows = await self._get_tracker_current_projection_rows(aggregate_tracker.id)
            keep_ids.update(
                int(row["tracker_release_history_id"])
                for row in current_rows
                if row["tracker_release_history_id"] is not None
            )

            tracker_config = await self.get_tracker_config(aggregate_tracker.name)
            sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"
            enabled_channels = [
                channel
                for channel in self.authoritative_release_channels_for_tracker(aggregate_tracker)
                if channel.get("enabled", True)
            ]

            retention_groups = await self._get_tracker_release_history_retention_groups(
                aggregate_tracker.id
            )
            if enabled_channels:
                for channel in enabled_channels:
                    channel_source_type = self._channel_value(channel, "source_type")
                    channel_candidates = [
                        release
                        for release in history_releases
                        if self._release_matches_retention_source_channel(
                            release,
                            retention_groups,
                            channel,
                        )
                        and self._release_matches_channel(
                            release,
                            channel,
                            channel_source_type=channel_source_type,
                        )
                    ]
                    deduped_releases = self.dedupe_releases_by_immutable_identity(
                        channel_candidates
                    )
                    top_releases = sorted(
                        deduped_releases,
                        key=lambda release: self._release_order_key(release, sort_mode),
                        reverse=True,
                    )[:retention]
                    keep_ids.update(
                        int(release.id) for release in top_releases if release.id is not None
                    )
            else:
                releases_by_group: dict[tuple[str, str], list[Release]] = {}
                for release in history_releases:
                    if release.id is None:
                        continue
                    group_key = retention_groups.get(
                        int(release.id), ("__unknown_source__", "__default__")
                    )
                    releases_by_group.setdefault(group_key, []).append(release)

                for group_releases in releases_by_group.values():
                    deduped_releases = self.dedupe_releases_by_immutable_identity(group_releases)
                    top_releases = sorted(
                        deduped_releases,
                        key=lambda release: self._release_order_key(release, sort_mode),
                        reverse=True,
                    )[:retention]
                    keep_ids.update(
                        int(release.id) for release in top_releases if release.id is not None
                    )

            all_ids = {int(release.id) for release in history_releases if release.id is not None}
            delete_ids = all_ids - keep_ids
            if not delete_ids:
                continue

            placeholders = ",".join("?" for _ in delete_ids)
            source_ids_before_rows = await (
                await db.execute(
                    f"""
                    SELECT DISTINCT source_release_history_id
                    FROM tracker_release_history_sources
                    WHERE tracker_release_history_id IN ({placeholders})
                    """,
                    tuple(delete_ids),
                )
            ).fetchall()
            source_ids_before = {int(row[0]) for row in source_ids_before_rows}
            primary_source_rows = await (
                await db.execute(
                    f"""
                    SELECT DISTINCT primary_source_release_history_id
                    FROM tracker_release_history
                    WHERE id IN ({placeholders}) AND primary_source_release_history_id IS NOT NULL
                    """,
                    tuple(delete_ids),
                )
            ).fetchall()
            source_ids_before.update(int(row[0]) for row in primary_source_rows)

            cursor = await db.execute(
                f"DELETE FROM tracker_release_history_sources WHERE tracker_release_history_id IN ({placeholders})",
                tuple(delete_ids),
            )
            result["tracker_release_history_sources_deleted"] += max(cursor.rowcount, 0)

            cursor = await db.execute(
                f"DELETE FROM tracker_release_history WHERE id IN ({placeholders})",
                tuple(delete_ids),
            )
            result["tracker_release_history_deleted"] += max(cursor.rowcount, 0)

            if source_ids_before:
                source_placeholders = ",".join("?" for _ in source_ids_before)
                orphan_source_rows = await (
                    await db.execute(
                        f"""
                        SELECT id
                        FROM source_release_history
                        WHERE id IN ({source_placeholders})
                          AND NOT EXISTS (
                              SELECT 1 FROM tracker_release_history trh
                              WHERE trh.primary_source_release_history_id = source_release_history.id
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM tracker_release_history_sources trhs
                              WHERE trhs.source_release_history_id = source_release_history.id
                          )
                        """,
                        tuple(source_ids_before),
                    )
                ).fetchall()
                orphan_source_ids = {int(row[0]) for row in orphan_source_rows}
                if orphan_source_ids:
                    orphan_placeholders = ",".join("?" for _ in orphan_source_ids)
                    cursor = await db.execute(
                        f"""
                        DELETE FROM source_release_run_observations
                        WHERE source_release_history_id IN ({orphan_placeholders})
                        """,
                        tuple(orphan_source_ids),
                    )
                    result["source_release_run_observations_deleted"] += max(cursor.rowcount, 0)
                    cursor = await db.execute(
                        f"DELETE FROM source_release_history WHERE id IN ({orphan_placeholders})",
                        tuple(orphan_source_ids),
                    )
                    result["source_release_history_deleted"] += max(cursor.rowcount, 0)

        await db.commit()
        deleted_rows = sum(
            int(result[key])
            for key in (
                "tracker_release_history_deleted",
                "tracker_release_history_sources_deleted",
                "source_release_history_deleted",
                "source_release_run_observations_deleted",
            )
        )
        reclaim_result = await self.reclaim_sqlite_space_after_history_cleanup(
            deleted_rows=deleted_rows
        )
        result.update(reclaim_result)
        return result

    async def reclaim_sqlite_space_after_history_cleanup(
        self,
        *,
        deleted_rows: int,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sqlite_optimize_performed": False,
            "wal_checkpoint_performed": False,
            "vacuum_performed": False,
        }
        if deleted_rows <= 0:
            return result

        db = await self._get_connection()
        await db.execute("PRAGMA optimize")
        result["sqlite_optimize_performed"] = True
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result["wal_checkpoint_performed"] = True

        page_count_row = await (await db.execute("PRAGMA page_count")).fetchone()
        freelist_count_row = await (await db.execute("PRAGMA freelist_count")).fetchone()
        page_count = int(page_count_row[0]) if page_count_row else 0
        freelist_count = int(freelist_count_row[0]) if freelist_count_row else 0
        should_vacuum = deleted_rows >= 1000 or (
            freelist_count >= 1000 and page_count > 0 and (freelist_count / page_count) >= 0.2
        )
        if should_vacuum:
            await db.execute("VACUUM")
            result["vacuum_performed"] = True
        await db.commit()
        return result

    async def refresh_tracker_current_releases(
        self,
        aggregate_tracker_id: int,
        releases: list[Release],
        *,
        source_type: str | None = None,
    ) -> None:
        return await sqlite_current_releases.refresh_tracker_current_releases(
            self, aggregate_tracker_id, releases, source_type=source_type
        )

    async def _get_tracker_current_projection_rows_by_aggregate_tracker_id(
        self, aggregate_tracker_ids: list[int]
    ) -> dict[int, list[dict[str, Any]]]:
        return await sqlite_current_releases._get_tracker_current_projection_rows_by_aggregate_tracker_id(
            self, aggregate_tracker_ids
        )

    async def get_tracker_current_releases(self, aggregate_tracker_id: int) -> list[Release]:
        return await sqlite_current_releases.get_tracker_current_releases(
            self, aggregate_tracker_id
        )

    async def _get_tracker_current_projection_rows(
        self, aggregate_tracker_id: int
    ) -> list[dict[str, Any]]:
        return await sqlite_current_releases._get_tracker_current_projection_rows(
            self, aggregate_tracker_id
        )

    @classmethod
    def _select_top_current_projection_release(
        cls,
        releases: list[Release],
        channels: list[Any],
        sort_mode: str,
    ) -> Release | None:
        return sqlite_current_releases._select_top_current_projection_release(
            cls, releases, channels, sort_mode
        )

    @classmethod
    def _filter_projection_rows_by_channels(
        cls,
        rows: list[dict[str, Any]],
        channels: list[Any],
    ) -> list[dict[str, Any]]:
        return sqlite_current_releases._filter_projection_rows_by_channels(cls, rows, channels)

    async def get_tracker_current_release_rows(self, tracker_name: str) -> list[dict[str, Any]]:
        return await sqlite_current_releases.get_tracker_current_release_rows(self, tracker_name)

    async def get_tracker_latest_current_release_summary(
        self, tracker_name: str
    ) -> dict[str, Any] | None:
        return await sqlite_current_releases.get_tracker_latest_current_release_summary(
            self, tracker_name
        )

    async def get_tracker_current_status_derivation(self, tracker_name: str) -> dict[str, Any]:
        return await sqlite_current_releases.get_tracker_current_status_derivation(
            self, tracker_name
        )

    async def get_tracker_runtime_configs_for_aggregate_trackers(
        self, trackers: list[AggregateTracker]
    ) -> dict[str, Any]:
        return await sqlite_current_releases.get_tracker_runtime_configs_for_aggregate_trackers(
            self, trackers
        )

    async def get_tracker_current_release_rows_for_aggregate_trackers(
        self,
        trackers: list[AggregateTracker],
        runtime_configs: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        return (
            await sqlite_current_releases.get_tracker_current_release_rows_for_aggregate_trackers(
                self, trackers, runtime_configs
            )
        )

    async def get_tracker_current_status_derivations_for_aggregate_trackers(
        self,
        trackers: list[AggregateTracker],
        current_rows_by_tracker_name: dict[str, list[dict[str, Any]]],
        runtime_configs: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return await sqlite_current_releases.get_tracker_current_status_derivations_for_aggregate_trackers(
            self, trackers, current_rows_by_tracker_name, runtime_configs
        )

    async def _upsert_source_release_observation(
        self,
        db: aiosqlite.Connection,
        tracker_source_id: int,
        release: Release,
        *,
        observed_at: datetime,
        raw_payload: dict[str, Any] | None = None,
        changelog_url: str | None = None,
        source_type: str | None = None,
    ) -> int:
        return await sqlite_source_observations._upsert_source_release_observation(
            self,
            db,
            tracker_source_id,
            release,
            observed_at=observed_at,
            raw_payload=raw_payload,
            changelog_url=changelog_url,
            source_type=source_type,
        )

    async def save_source_observations(
        self,
        aggregate_tracker_id: int,
        tracker_source: TrackerSource,
        releases: list[Release],
        *,
        observed_at: datetime | None = None,
        append_truth: bool = True,
    ) -> list[int]:
        return await sqlite_source_observations.save_source_observations(
            self,
            aggregate_tracker_id,
            tracker_source,
            releases,
            observed_at=observed_at,
            append_truth=append_truth,
        )

    async def _upsert_canonical_release_observation(
        self,
        db: aiosqlite.Connection,
        canonical_release_id: int,
        source_release_observation_id: int,
        created_at: str,
        contribution_kind: str,
    ) -> None:
        await db.execute(
            """
            INSERT INTO canonical_release_observations
            (canonical_release_id, source_release_observation_id, contribution_kind, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(canonical_release_id, source_release_observation_id) DO UPDATE SET
                contribution_kind = excluded.contribution_kind
            """,
            (canonical_release_id, source_release_observation_id, contribution_kind, created_at),
        )

    @staticmethod
    def _row_to_tracker_config(row):
        """Convert a database row to a TrackerConfig object"""
        from ..config import TrackerConfig

        channels = (
            SQLiteStorage._load_tracker_channels(row["channels"])
            if "channels" in row.keys()
            else []
        )

        # Get check interval, already normalized to integer by related migrations
        raw_interval = row["interval"]
        interval_minutes = int(raw_interval) if raw_interval is not None else 60  # Fallback

        return TrackerConfig(
            name=row["name"],
            type=cast(TrackerSourceType, row["type"]),
            enabled=bool(row["enabled"]),
            repo=row["repo"],
            project=row["project"],
            instance=row["instance"],
            chart=row["chart"],
            image=row["image"] if "image" in row.keys() else None,
            registry=row["registry"] if "registry" in row.keys() else None,
            credential_name=row["credential_name"],
            interval=interval_minutes,
            version_sort_mode=(
                row["version_sort_mode"]
                if "version_sort_mode" in row.keys() and row["version_sort_mode"]
                else "published_at"
            ),
            fetch_limit=(
                int(row["fetch_limit"])
                if "fetch_limit" in row.keys() and row["fetch_limit"] is not None
                else 10
            ),
            fetch_timeout=(
                int(row["fetch_timeout"])
                if "fetch_timeout" in row.keys() and row["fetch_timeout"] is not None
                else 15
            ),
            fallback_tags=bool(row["fallback_tags"]) if "fallback_tags" in row.keys() else False,
            github_fetch_mode=(
                row["github_fetch_mode"]
                if "github_fetch_mode" in row.keys() and row["github_fetch_mode"]
                else "rest_first"
            ),
            channels=channels,
        )

    @staticmethod
    def _row_to_tracker_status(row) -> TrackerStatus:
        """Convert a database row to a TrackerStatus object"""
        return TrackerStatus(
            name=row["name"],
            type=row["type"],
            enabled=bool(row["enabled"]),
            last_check=datetime.fromisoformat(row["last_check"]) if row["last_check"] else None,
            last_version=row["last_version"],
            error=row["error"],
        )

    async def get_releases(
        self,
        tracker_name: str | None = None,
        skip: int = 0,
        limit: int | None = 50,
        search: str | None = None,
        prerelease: bool | None = None,
        include_history: bool = True,
    ) -> list[Release]:
        return await sqlite_release_queries.get_releases(
            self, tracker_name, skip, limit, search, prerelease, include_history
        )

    @staticmethod
    def _tracker_type_for_canonical_release(
        tracker: AggregateTracker, canonical_release: CanonicalRelease
    ) -> str:
        return sqlite_release_queries._tracker_type_for_canonical_release(
            SQLiteStorage, tracker, canonical_release
        )

    @staticmethod
    def _aggregate_tracker_prefers_repo_history(tracker: AggregateTracker) -> bool:
        return sqlite_release_queries._aggregate_tracker_prefers_repo_history(tracker)

    @classmethod
    def _canonical_release_should_be_listed_in_history(
        cls,
        tracker: AggregateTracker,
        canonical_release: CanonicalRelease,
        observations_by_id: dict[int, SourceReleaseObservation],
        sources_by_id: dict[int, TrackerSource],
    ) -> bool:
        return sqlite_release_queries._canonical_release_should_be_listed_in_history(
            cls, tracker, canonical_release, observations_by_id, sources_by_id
        )

    @classmethod
    def _canonical_release_to_release(
        cls,
        tracker: AggregateTracker,
        canonical_release: CanonicalRelease,
        observations_by_id: dict[int, SourceReleaseObservation],
    ) -> Release:
        return sqlite_release_queries._canonical_release_to_release(
            cls, tracker, canonical_release, observations_by_id
        )

    @staticmethod
    def _release_matches_filters(
        release: Release,
        *,
        tracker_name: str | None = None,
        search: str | None = None,
        prerelease: bool | None = None,
    ) -> bool:
        return sqlite_release_queries._release_matches_filters(
            release, tracker_name=tracker_name, search=search, prerelease=prerelease
        )

    @staticmethod
    def _release_listing_sort_key(release: Release) -> tuple[float, float, int]:
        return sqlite_release_queries._release_listing_sort_key(release)

    async def get_latest_tracker_releases(self, limit: int = 5) -> list[Release]:
        return await sqlite_release_queries.get_latest_tracker_releases(self, limit)

    async def get_total_count(
        self,
        tracker_name: str | None = None,
        search: str | None = None,
        prerelease: bool | None = None,
        include_history: bool = True,
    ) -> int:
        return await sqlite_release_queries.get_total_count(
            self, tracker_name, search, prerelease, include_history
        )

    async def get_releases_for_trackers_bulk(
        self, tracker_names: list[str], limit_per_tracker: int = 200
    ) -> dict[str, list[Release]]:
        return await sqlite_release_queries.get_releases_for_trackers_bulk(
            self, tracker_names, limit_per_tracker
        )

    async def get_latest_release(self, tracker_name: str) -> Release | None:
        return await sqlite_release_queries.get_latest_release(self, tracker_name)

    async def get_latest_release_for_channels(
        self, tracker_name: str, channels: list
    ) -> Release | None:
        return await sqlite_release_queries.get_latest_release_for_channels(
            self, tracker_name, channels
        )

    @staticmethod
    def release_identity_key(release: Release) -> tuple[str, str]:
        return sqlite_release_queries.release_identity_key(release)

    @classmethod
    def immutable_release_identity_key(cls, release: Release) -> tuple[str, str]:
        return sqlite_release_queries.immutable_release_identity_key(cls, release)

    @staticmethod
    def dedupe_releases_by_identity(releases: list[Release]) -> list[Release]:
        return sqlite_release_queries.dedupe_releases_by_identity(SQLiteStorage, releases)

    @classmethod
    def dedupe_releases_by_immutable_identity(cls, releases: list[Release]) -> list[Release]:
        return sqlite_release_queries.dedupe_releases_by_immutable_identity(cls, releases)

    @staticmethod
    def _release_matches_channel(
        release: Release, channel, *, channel_source_type: str | None = None
    ) -> bool:
        return sqlite_release_queries._release_matches_channel(
            SQLiteStorage, release, channel, channel_source_type=channel_source_type
        )

    @staticmethod
    def _supports_release_type_filter(source_type: str | None) -> bool:
        return sqlite_release_queries._supports_release_type_filter(source_type)

    @staticmethod
    def _channel_exclude_match_candidates(release: Release) -> list[str]:
        return sqlite_release_queries._channel_exclude_match_candidates(release)

    @staticmethod
    def _release_order_key(release: Release, sort_mode: str = "published_at") -> tuple:
        return sqlite_release_queries._release_order_key(SQLiteStorage, release, sort_mode)

    @staticmethod
    def _channel_selection_key(channel, index: int) -> str:
        return sqlite_release_queries._channel_selection_key(channel, index)

    @staticmethod
    def _release_matches_source_aliases(
        release: Release, channel, *, channel_source_type: str | None = None
    ) -> bool:
        return sqlite_release_queries._release_matches_source_aliases(
            SQLiteStorage, release, channel, channel_source_type=channel_source_type
        )

    @staticmethod
    def _copy_release_with_channel_name(release: Release, channel_name: str) -> Release:
        return sqlite_release_queries._copy_release_with_channel_name(release, channel_name)

    @staticmethod
    def select_best_releases_by_channel(
        releases: list[Release],
        channels: list,
        sort_mode: str = "published_at",
        *,
        channel_source_type: str | None = None,
        use_immutable_identity: bool = False,
        use_source_aliases: bool = False,
    ) -> dict[str, Release]:
        return sqlite_release_queries.select_best_releases_by_channel(
            SQLiteStorage,
            releases,
            channels,
            sort_mode,
            channel_source_type=channel_source_type,
            use_immutable_identity=use_immutable_identity,
            use_source_aliases=use_source_aliases,
        )

    @staticmethod
    def select_best_releases_for_tracker_channel(
        releases: list[Release],
        tracker_channel,
        sort_mode: str = "published_at",
        *,
        use_immutable_identity: bool = False,
    ) -> dict[str, Release]:
        return sqlite_release_queries.select_best_releases_for_tracker_channel(
            SQLiteStorage,
            releases,
            tracker_channel,
            sort_mode,
            use_immutable_identity=use_immutable_identity,
        )

    @staticmethod
    def select_best_release(
        releases: list[Release],
        channels: list,
        sort_mode: str = "published_at",
        *,
        use_immutable_identity: bool = False,
    ) -> Release | None:
        return sqlite_release_queries.select_best_release(
            SQLiteStorage,
            releases,
            channels,
            sort_mode,
            use_immutable_identity=use_immutable_identity,
        )

    async def update_tracker_status(self, status: TrackerStatus):
        """Update tracker status."""
        db = await self._get_connection()
        await db.execute(
            """
            INSERT OR REPLACE INTO tracker_status 
            (name, type, enabled, last_check, last_version, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                status.name,
                status.type,
                1 if status.enabled else 0,
                status.last_check.isoformat() if status.last_check else None,
                status.last_version,
                status.error,
            ),
        )
        await db.commit()

    async def get_tracker_status(self, name: str) -> TrackerStatus | None:
        """Get tracker status."""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tracker_status WHERE name = ?", (name,))
        row = await cursor.fetchone()
        return self._row_to_tracker_status(row) if row else None

    async def get_all_tracker_status(self) -> list[TrackerStatus]:
        """Get all tracker statuses."""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tracker_status")
        rows = await cursor.fetchall()
        return [self._row_to_tracker_status(row) for row in rows]

    async def delete_tracker_status(self, name: str):
        """Delete tracker status."""
        db = await self._get_connection()
        await db.execute("DELETE FROM tracker_status WHERE name = ?", (name,))
        await db.commit()

    async def get_stats(self) -> ReleaseStats:
        """Get statistics"""
        aggregate_trackers = await self.get_all_aggregate_trackers()
        trackers_by_name = {tracker.name: tracker for tracker in aggregate_trackers}
        releases: list[Release] = []
        for aggregate_tracker in aggregate_trackers:
            if aggregate_tracker.id is None:
                continue
            tracker_releases = await self.get_tracker_release_history_releases(aggregate_tracker.id)
            for tracker_release in tracker_releases:
                tracker_release.tracker_name = aggregate_tracker.name
            releases.extend(tracker_releases)

        total_trackers = await self.get_total_tracker_configs_count()
        if total_trackers == 0 and releases:
            total_trackers = len({release.tracker_name for release in releases})
        total_releases = len(releases)

        def _normalize_datetime(value: datetime) -> datetime:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        recent_releases = sum(
            1 for release in releases if _normalize_datetime(release.created_at) > yesterday
        )
        latest_update = max(
            (_normalize_datetime(release.published_at) for release in releases), default=None
        )

        target_tz_name = await self.get_system_timezone()
        target_tz = ZoneInfo(target_tz_name)

        stats_map: dict[str, dict[str, int]] = {}
        now_target = datetime.now(target_tz)
        today_target = now_target.date()
        start_date_target = today_target - timedelta(days=6)

        channel_stats: dict[str, int] = {}
        release_type_stats: dict[str, int] = {}

        for release in releases:
            channel = self._resolve_stats_channel_name(
                release,
                trackers_by_name.get(release.tracker_name),
            )
            channel_stats[channel] = channel_stats.get(channel, 0) + 1

            release_type = "prerelease" if release.prerelease else "release"
            release_type_stats[release_type] = release_type_stats.get(release_type, 0) + 1

            pub_dt = release.published_at
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=target_tz)
            local_date = pub_dt.astimezone(target_tz).date()
            if local_date < start_date_target or local_date > today_target:
                continue

            date_str = local_date.isoformat()
            stats_map.setdefault(date_str, {})
            stats_map[date_str][channel] = stats_map[date_str].get(channel, 0) + 1

        current_loop_date = start_date_target
        while current_loop_date <= today_target:
            stats_map.setdefault(current_loop_date.isoformat(), {})
            current_loop_date += timedelta(days=1)

        daily_stats = [
            {"date": date, "channels": channels} for date, channels in sorted(stats_map.items())
        ]

        return ReleaseStats(
            total_trackers=total_trackers,
            total_releases=total_releases,
            recent_releases=recent_releases,
            latest_update=latest_update,
            daily_stats=daily_stats,
            channel_stats=channel_stats,
            release_type_stats=release_type_stats,
        )

    @classmethod
    def _resolve_stats_channel_name(
        cls,
        release: Release,
        tracker: AggregateTracker | None,
    ) -> str:
        channel_name = release.channel_name.strip() if release.channel_name else ""
        if channel_name:
            return channel_name

        if tracker is not None:
            for channel in cls.authoritative_release_channels_for_tracker(tracker):
                if not channel.get("enabled", True):
                    continue
                resolved_name = channel.get("name")
                if resolved_name and cls._release_matches_channel(
                    release,
                    channel,
                    channel_source_type=channel.get("source_type"),
                ):
                    return str(resolved_name)

        return "prerelease" if release.prerelease else "stable"

    @staticmethod
    def _row_to_release(row) -> Release:
        """Convert a database row to a Release object"""
        return Release(
            id=row["id"],
            tracker_name=row["tracker_name"],
            name=row["name"],
            tag_name=row["tag_name"],
            version=row["version"],
            published_at=datetime.fromisoformat(row["published_at"]),
            url=row["url"],
            prerelease=bool(row["prerelease"]),
            body=row["body"],
            channel_name=row["channel_name"],
            commit_sha=row["commit_sha"] if "commit_sha" in row.keys() else None,
            republish_count=row["republish_count"] if "republish_count" in row.keys() else 0,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # ==================== Credential management ====================

    async def create_credential(self, credential) -> int:
        async with self._encryption_rotation_lock:
            return await sqlite_credentials.create_credential(self, credential)

    async def get_all_credentials(self) -> list:
        return await sqlite_credentials.get_all_credentials(self)

    async def get_credentials_paginated(self, skip: int = 0, limit: int = 20) -> list:
        return await sqlite_credentials.get_credentials_paginated(self, skip, limit)

    async def get_total_credentials_count(self) -> int:
        return await sqlite_credentials.get_total_credentials_count(self)

    async def get_credential(self, credential_id: int):
        return await sqlite_credentials.get_credential(self, credential_id)

    async def get_credential_by_name(self, name: str):
        return await sqlite_credentials.get_credential_by_name(self, name)

    async def update_credential(self, credential_id: int, credential) -> bool:
        async with self._encryption_rotation_lock:
            return await sqlite_credentials.update_credential(self, credential_id, credential)

    async def delete_credential(self, credential_id: int) -> bool:
        return await sqlite_credentials.delete_credential(self, credential_id)

    async def get_credential_references(self, credential) -> dict[str, list[dict[str, Any]]]:
        return await sqlite_credentials.get_credential_references(self, credential)

    async def get_credential_reference_counts(self, credential) -> dict[str, int]:
        return await sqlite_credentials.get_credential_reference_counts(self, credential)

    def _row_to_credential(self, row):
        return sqlite_credentials._row_to_credential(self, row)

    async def create_runtime_connection(self, runtime_connection: RuntimeConnectionConfig) -> int:
        async with self._encryption_rotation_lock:
            return await sqlite_runtime_executors.create_runtime_connection(
                self, runtime_connection
            )

    async def get_total_runtime_connections_count(self, search: str | None = None) -> int:
        return await sqlite_runtime_executors.get_total_runtime_connections_count(self, search)

    async def get_runtime_connections_paginated(
        self, skip: int = 0, limit: int = 20, search: str | None = None
    ) -> list:
        return await sqlite_runtime_executors.get_runtime_connections_paginated(
            self, skip, limit, search
        )

    async def get_runtime_connection(self, runtime_connection_id: int):
        return await sqlite_runtime_executors.get_runtime_connection(self, runtime_connection_id)

    async def get_runtime_connection_by_name(self, name: str):
        return await sqlite_runtime_executors.get_runtime_connection_by_name(self, name)

    async def update_runtime_connection(
        self, runtime_connection_id: int, runtime_connection: RuntimeConnectionConfig
    ) -> bool:
        async with self._encryption_rotation_lock:
            return await sqlite_runtime_executors.update_runtime_connection(
                self, runtime_connection_id, runtime_connection
            )

    async def delete_runtime_connection(self, runtime_connection_id: int) -> bool:
        return await sqlite_runtime_executors.delete_runtime_connection(self, runtime_connection_id)

    def _row_to_runtime_connection(self, row):
        return sqlite_runtime_executors._row_to_runtime_connection(self, row)

    def _row_to_executor_config(self, row):
        return sqlite_runtime_executors._row_to_executor_config(self, row)

    @staticmethod
    def _row_to_executor_status(row) -> ExecutorStatus:
        return sqlite_runtime_executors._row_to_executor_status(cast(Any, None), row)

    @staticmethod
    def _row_to_executor_run_history(row) -> ExecutorRunHistory:
        return sqlite_runtime_executors._row_to_executor_run_history(cast(Any, None), row)

    @staticmethod
    def _row_to_executor_snapshot(row) -> ExecutorSnapshot:
        return sqlite_runtime_executors._row_to_executor_snapshot(cast(Any, SQLiteStorage), row)

    @staticmethod
    def _row_to_executor_desired_state(row) -> ExecutorDesiredState:
        return sqlite_runtime_executors._row_to_executor_desired_state(
            cast(Any, SQLiteStorage), row
        )

    async def create_executor_config(self, executor_config: ExecutorConfig) -> int:
        return await sqlite_runtime_executors.create_executor_config(self, executor_config)

    async def save_executor_config(self, executor_config: ExecutorConfig) -> int:
        return await sqlite_runtime_executors.save_executor_config(self, executor_config)

    async def get_total_executor_configs_count(self, search: str | None = None) -> int:
        return await sqlite_runtime_executors.get_total_executor_configs_count(self, search)

    async def get_all_executor_configs(self) -> list[ExecutorConfig]:
        return await sqlite_runtime_executors.get_all_executor_configs(self)

    async def get_executor_configs_paginated(
        self, skip: int = 0, limit: int = 20, search: str | None = None
    ) -> list[ExecutorConfig]:
        return await sqlite_runtime_executors.get_executor_configs_paginated(
            self, skip, limit, search
        )

    async def get_executor_config(self, executor_id: int):
        return await sqlite_runtime_executors.get_executor_config(self, executor_id)

    async def get_executor_config_by_name(self, name: str):
        return await sqlite_runtime_executors.get_executor_config_by_name(self, name)

    async def update_executor_config(
        self, executor_id: int, executor_config: ExecutorConfig
    ) -> bool:
        return await sqlite_runtime_executors.update_executor_config(
            self, executor_id, executor_config
        )

    async def delete_executor_config(self, executor_id: int) -> bool:
        return await sqlite_runtime_executors.delete_executor_config(self, executor_id)

    async def update_executor_status(self, status: ExecutorStatus) -> None:
        await sqlite_runtime_executors.update_executor_status(self, status)

    async def get_executor_status(self, executor_id: int) -> ExecutorStatus | None:
        return await sqlite_runtime_executors.get_executor_status(self, executor_id)

    async def get_all_executor_status(self) -> list[ExecutorStatus]:
        return await sqlite_runtime_executors.get_all_executor_status(self)

    async def delete_executor_status(self, executor_id: int) -> None:
        await sqlite_runtime_executors.delete_executor_status(self, executor_id)

    async def create_executor_run(self, run: ExecutorRunHistory) -> int:
        return await sqlite_runtime_executors.create_executor_run(self, run)

    async def create_executor_run_if_no_active(
        self,
        run: ExecutorRunHistory,
        *,
        active_statuses: frozenset[str],
    ) -> int | None:
        return await sqlite_runtime_executors.create_executor_run_if_no_active(
            self,
            run,
            active_statuses=active_statuses,
        )

    async def enqueue_executor_projection_trigger_work(
        self,
        *,
        executor_id: int,
        tracker_name: str,
        previous_version: str | None,
        current_version: str,
        previous_identity_key: str | None = None,
        current_identity_key: str | None = None,
        binding_targets: list[dict[str, Any]] | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.enqueue_executor_projection_trigger_work(
            self,
            executor_id=executor_id,
            tracker_name=tracker_name,
            previous_version=previous_version,
            current_version=current_version,
            previous_identity_key=previous_identity_key,
            current_identity_key=current_identity_key,
            binding_targets=binding_targets,
        )

    async def finalize_executor_run(
        self,
        run_id: int,
        *,
        status: str,
        from_version: str | None = None,
        finished_at: datetime | None = None,
        to_version: str | None = None,
        message: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.finalize_executor_run(
            self,
            run_id,
            status=status,
            from_version=from_version,
            finished_at=finished_at,
            to_version=to_version,
            message=message,
            diagnostics=diagnostics,
        )

    async def set_executor_run_status(self, run_id: int, status: str) -> None:
        await sqlite_runtime_executors.set_executor_run_status(self, run_id, status)

    async def update_executor_target_ref(self, executor_id: int, target_ref: dict) -> None:
        await sqlite_runtime_executors.update_executor_target_ref(self, executor_id, target_ref)

    async def get_executor_run(self, run_id: int) -> ExecutorRunHistory | None:
        return await sqlite_runtime_executors.get_executor_run(self, run_id)

    async def get_executor_run_history(
        self,
        executor_id: int,
        skip: int = 0,
        limit: int | None = 50,
        *,
        status: str | None = None,
        search: str | None = None,
    ) -> list[ExecutorRunHistory]:
        return await sqlite_runtime_executors.get_executor_run_history(
            self,
            executor_id,
            skip,
            limit,
            status=status,
            search=search,
        )

    async def get_total_executor_run_history_count(
        self, executor_id: int, *, status: str | None = None, search: str | None = None
    ) -> int:
        return await sqlite_runtime_executors.get_total_executor_run_history_count(
            self, executor_id, status=status, search=search
        )

    async def get_latest_executor_run(self, executor_id: int) -> ExecutorRunHistory | None:
        return await sqlite_runtime_executors.get_latest_executor_run(self, executor_id)

    async def delete_executor_run_history(self, executor_id: int) -> int:
        return await sqlite_runtime_executors.delete_executor_run_history(self, executor_id)

    async def prune_old_executor_runs(self, days: int = 90) -> int:
        return await sqlite_runtime_executors.prune_old_executor_runs(self, days)

    async def save_executor_snapshot(self, snapshot: ExecutorSnapshot) -> None:
        await sqlite_runtime_executors.save_executor_snapshot(self, snapshot)

    async def create_executor_snapshot(self, snapshot: ExecutorSnapshot) -> int:
        return await sqlite_runtime_executors.create_executor_snapshot(self, snapshot)

    async def get_executor_snapshot(self, executor_id: int) -> ExecutorSnapshot | None:
        return await sqlite_runtime_executors.get_executor_snapshot(self, executor_id)

    async def list_executor_snapshots(
        self,
        executor_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ExecutorSnapshot]:
        return await sqlite_runtime_executors.list_executor_snapshots(
            self, executor_id, limit=limit, offset=offset
        )

    async def count_executor_snapshots(self, executor_id: int) -> int:
        return await sqlite_runtime_executors.count_executor_snapshots(self, executor_id)

    async def get_executor_snapshot_by_id(
        self, executor_id: int, snapshot_id: int
    ) -> ExecutorSnapshot | None:
        return await sqlite_runtime_executors.get_executor_snapshot_by_id(
            self, executor_id, snapshot_id
        )

    async def delete_executor_snapshots(self, executor_id: int, ids: list[int]) -> int:
        return await sqlite_runtime_executors.delete_executor_snapshots(self, executor_id, ids)

    async def claim_executor_snapshot_for_rollback(
        self,
        *,
        executor_id: int,
        snapshot_id: int | None,
        run: ExecutorRunHistory,
        active_statuses: frozenset[str],
    ) -> tuple[ExecutorSnapshot, int] | None:
        return await sqlite_runtime_executors.claim_executor_snapshot_for_rollback(
            self,
            executor_id=executor_id,
            snapshot_id=snapshot_id,
            run=run,
            active_statuses=active_statuses,
        )

    async def release_executor_snapshot_claim(self, *, snapshot_id: int, run_id: int) -> bool:
        return await sqlite_runtime_executors.release_executor_snapshot_claim(
            self, snapshot_id=snapshot_id, run_id=run_id
        )

    async def is_executor_snapshot_claimed(self, *, executor_id: int, snapshot_id: int) -> bool:
        return await sqlite_runtime_executors.is_executor_snapshot_claimed(
            self, executor_id=executor_id, snapshot_id=snapshot_id
        )

    async def reconcile_stale_executor_snapshot_claims(self, *, stale_before: datetime) -> int:
        return await sqlite_runtime_executors.reconcile_stale_executor_snapshot_claims(
            self, stale_before=stale_before
        )

    async def set_executor_snapshot_locked(
        self, executor_id: int, snapshot_id: int, *, locked: bool
    ) -> bool:
        return await sqlite_runtime_executors.set_executor_snapshot_locked(
            self, executor_id, snapshot_id, locked=locked
        )

    async def upsert_executor_desired_state(
        self,
        *,
        executor_id: int,
        desired_state_revision: str,
        desired_target: dict[str, Any],
        next_eligible_at: datetime | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.upsert_executor_desired_state(
            self,
            executor_id=executor_id,
            desired_state_revision=desired_state_revision,
            desired_target=desired_target,
            next_eligible_at=next_eligible_at,
        )

    async def get_executor_desired_state(self, executor_id: int) -> ExecutorDesiredState | None:
        return await sqlite_runtime_executors.get_executor_desired_state(self, executor_id)

    async def list_pending_executor_desired_states(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[ExecutorDesiredState]:
        return await sqlite_runtime_executors.list_pending_executor_desired_states(
            self,
            now=now,
            limit=limit,
        )

    async def claim_pending_executor_desired_states(
        self,
        *,
        claimed_by: str,
        now: datetime | None = None,
        limit: int = 100,
        lease_seconds: int = 300,
    ) -> list[ExecutorDesiredState]:
        return await sqlite_runtime_executors.claim_pending_executor_desired_states(
            self,
            claimed_by=claimed_by,
            now=now,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def defer_executor_desired_state(
        self,
        executor_id: int,
        *,
        next_eligible_at: datetime,
        claimed_by: str | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.defer_executor_desired_state(
            self,
            executor_id,
            next_eligible_at=next_eligible_at,
            claimed_by=claimed_by,
        )

    async def release_executor_desired_state_claim(
        self,
        executor_id: int,
        *,
        claimed_by: str,
    ) -> bool:
        return await sqlite_runtime_executors.release_executor_desired_state_claim(
            self,
            executor_id,
            claimed_by=claimed_by,
        )

    async def complete_executor_desired_state(
        self,
        executor_id: int,
        *,
        expected_revision: str,
        claimed_by: str | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.complete_executor_desired_state(
            self,
            executor_id,
            expected_revision=expected_revision,
            claimed_by=claimed_by,
        )

    # ==================== Auth Methods ====================

    async def get_admin_user_id(self) -> int | None:
        return await sqlite_auth_oidc.get_admin_user_id(self)

    async def persist_admin_identity(self, user_id: int) -> None:
        await sqlite_auth_oidc.persist_admin_identity(self, user_id)

    async def create_bootstrap_admin(self, user: User) -> User:
        return await sqlite_auth_oidc.create_bootstrap_admin(self, user)

    async def is_admin_password_reset_required(self) -> bool:
        return await sqlite_auth_oidc.is_admin_password_reset_required(self)

    async def mark_admin_password_reset_required(self, user_id: int) -> int:
        return await sqlite_auth_oidc.mark_admin_password_reset_required(self, user_id)

    async def reset_admin_password(self, password_hash: str) -> int:
        return await sqlite_auth_oidc.reset_admin_password(self, password_hash)

    async def get_admin_oidc_binding(self) -> tuple[str, str] | None:
        return await sqlite_auth_oidc.get_admin_oidc_binding(self)

    async def bind_admin_oidc_identity(self, issuer: str, subject: str) -> tuple[str, str]:
        return await sqlite_auth_oidc.bind_admin_oidc_identity(self, issuer, subject)

    async def unbind_admin_oidc_identity(self) -> None:
        await sqlite_auth_oidc.unbind_admin_oidc_identity(self)

    async def create_user(self, user: User) -> User:
        return await sqlite_auth_oidc.create_user(self, user)

    async def get_user_by_username(self, username: str) -> User | None:
        return await sqlite_auth_oidc.get_user_by_username(self, username)

    async def get_user_by_id(self, user_id: int) -> User | None:
        return await sqlite_auth_oidc.get_user_by_id(self, user_id)

    async def update_user_password(self, user_id: int, password_hash: str) -> bool:
        return await sqlite_auth_oidc.update_user_password(self, user_id, password_hash)

    async def create_session(self, session: Session) -> Session:
        return await sqlite_auth_oidc.create_session(self, session)

    async def get_session(self, token_hash: str) -> Session | None:
        return await sqlite_auth_oidc.get_session(self, token_hash)

    async def get_session_by_refresh_token(self, refresh_token_hash: str) -> Session | None:
        return await sqlite_auth_oidc.get_session_by_refresh_token(self, refresh_token_hash)

    async def delete_session(self, token_hash: str) -> None:
        await sqlite_auth_oidc.delete_session(self, token_hash)

    async def delete_all_sessions(self) -> int:
        return await sqlite_auth_oidc.delete_all_sessions(self)

    async def count_active_sessions(self) -> int:
        return await sqlite_auth_oidc.count_active_sessions(self)

    async def update_session_tokens(
        self,
        session_id: int,
        current_refresh_token_hash: str,
        token_hash: str,
        refresh_token_hash: str,
        expires_at: datetime,
    ) -> bool:
        return await sqlite_auth_oidc.update_session_tokens(
            self,
            session_id,
            current_refresh_token_hash,
            token_hash,
            refresh_token_hash,
            expires_at,
        )

    async def delete_expired_sessions(self) -> None:
        await sqlite_auth_oidc.delete_expired_sessions(self)

    @staticmethod
    def _row_to_user(row) -> User:
        return sqlite_auth_oidc._row_to_user(row)

    @staticmethod
    def _row_to_session(row) -> Session:
        return sqlite_auth_oidc._row_to_session(row)

    # ==================== Notifier Operations ====================

    @classmethod
    def _row_to_notifier(cls, row) -> Notifier:
        """Convert a database row to a Notifier object"""
        import json

        try:
            events = json.loads(row["events"]) if row["events"] else []
        except (json.JSONDecodeError, TypeError):
            events = []

        keys = set(row.keys())
        return Notifier(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            url=row["url"],
            events=events,
            enabled=bool(row["enabled"]),
            language=(
                cls._normalize_notifier_language(row["language"]) if "language" in keys else "en"
            ),
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def get_notifiers(self) -> list[Notifier]:
        """Get all notifiers, preferring the memory cache to avoid frequent database queries."""
        if self._notifiers_cache is not None:
            return list(self._notifiers_cache)
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM notifiers ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            notifiers = [self._row_to_notifier(row) for row in rows]
            self._notifiers_cache = notifiers
            return list(notifiers)

    async def get_total_notifiers_count(self, search: str | None = None) -> int:
        """Get the notifier count, optionally filtered by visible metadata."""
        db = await self._get_connection()
        normalized_search = search.strip().lower() if search and search.strip() else None
        if normalized_search is None:
            query = "SELECT COUNT(*) FROM notifiers"
            params: tuple[str, ...] = ()
        else:
            like = f"%{normalized_search}%"
            query = (
                "SELECT COUNT(*) FROM notifiers WHERE LOWER(name) LIKE ? "
                "OR LOWER(url) LIKE ? OR LOWER(COALESCE(description, '')) LIKE ?"
            )
            params = (like, like, like)
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_notifiers_paginated(
        self, skip: int = 0, limit: int = 20, search: str | None = None
    ) -> list[Notifier]:
        """Get notifiers with pagination and optional visible-metadata search."""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        normalized_search = search.strip().lower() if search and search.strip() else None
        if normalized_search is None:
            query = "SELECT * FROM notifiers ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params: tuple[int | str, ...] = (limit, skip)
        else:
            like = f"%{normalized_search}%"
            query = (
                "SELECT * FROM notifiers WHERE LOWER(name) LIKE ? "
                "OR LOWER(url) LIKE ? OR LOWER(COALESCE(description, '')) LIKE ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            params = (like, like, like, limit, skip)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_notifier(row) for row in rows]

    async def get_notifier(self, notifier_id: int) -> Notifier | None:
        """Get a single notifier"""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM notifiers WHERE id = ?", (notifier_id,)) as cursor:
            row = await cursor.fetchone()
            return self._row_to_notifier(row) if row else None

    async def get_notifier_by_name(self, name: str) -> Notifier | None:
        """Get a notifier by name"""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM notifiers WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            return self._row_to_notifier(row) if row else None

    async def create_notifier(self, notifier_data: dict) -> Notifier:
        """Create a notifier"""
        import json

        now = datetime.now().isoformat()

        # Ensure name uniqueness
        if await self.get_notifier_by_name(notifier_data["name"]):
            raise ValueError(f"Notifier '{notifier_data['name']}' already exists")

        language = self._normalize_notifier_language(notifier_data.get("language", "en"))

        db = await self._get_connection()
        cursor = await db.execute(
            """
            INSERT INTO notifiers (name, type, url, events, enabled, language, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notifier_data["name"],
                notifier_data.get("type", "webhook"),
                notifier_data["url"],
                json.dumps(notifier_data.get("events", ["new_release"])),
                1 if notifier_data.get("enabled", True) else 0,
                language,
                notifier_data.get("description"),
                now,
                now,
            ),
        )
        await db.commit()
        notifier_id = cursor.lastrowid
        if notifier_id is None:
            raise ValueError("Failed to create notifier")

        # Notifier changed; invalidate in-memory cache
        self.invalidate_notifiers_cache()
        created_notifier = await self.get_notifier(notifier_id)
        if created_notifier is None:
            raise ValueError(f"Notifier with id {notifier_id} not found")
        return created_notifier

    async def update_notifier(self, notifier_id: int, notifier_data: dict) -> Notifier:
        """Update a notifier"""
        import json

        current = await self.get_notifier(notifier_id)
        if not current:
            raise ValueError(f"Notifier with id {notifier_id} not found")

        # If name changes, check uniqueness
        if "name" in notifier_data and notifier_data["name"] != current.name:
            if await self.get_notifier_by_name(notifier_data["name"]):
                raise ValueError(f"Notifier name '{notifier_data['name']}' already exists")

        now = datetime.now().isoformat()

        fields = ["updated_at = ?"]
        values: list[object] = [now]

        if "name" in notifier_data:
            fields.append("name = ?")
            values.append(notifier_data["name"])
        if "type" in notifier_data:
            fields.append("type = ?")
            values.append(notifier_data["type"])
        if "url" in notifier_data:
            fields.append("url = ?")
            values.append(notifier_data["url"])
        if "events" in notifier_data:
            fields.append("events = ?")
            values.append(json.dumps(notifier_data["events"]))
        if "enabled" in notifier_data:
            fields.append("enabled = ?")
            values.append(1 if notifier_data["enabled"] else 0)
        if "language" in notifier_data:
            fields.append("language = ?")
            values.append(self._normalize_notifier_language(notifier_data["language"]))
        if "description" in notifier_data:
            fields.append("description = ?")
            values.append(notifier_data["description"])

        values.append(notifier_id)

        db = await self._get_connection()
        await db.execute(f"UPDATE notifiers SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()

        # Notifier changed; invalidate in-memory cache
        self.invalidate_notifiers_cache()
        updated_notifier = await self.get_notifier(notifier_id)
        if updated_notifier is None:
            raise ValueError(f"Notifier with id {notifier_id} not found")
        return updated_notifier

    async def delete_notifier(self, notifier_id: int):
        """Delete a notifier"""
        db = await self._get_connection()
        result = await db.execute("DELETE FROM notifiers WHERE id = ?", (notifier_id,))
        await db.commit()
        # Notifier changed; invalidate in-memory cache
        self.invalidate_notifiers_cache()
        if result.rowcount == 0:
            raise ValueError(f"Notifier with id {notifier_id} not found")

    async def get_all_settings(self) -> dict:
        """Get all system settings as a key/value mapping."""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM settings") as cursor:
            rows = await cursor.fetchall()
            return {row["key"]: row["value"] for row in rows}

    async def get_all_settings_with_updated_at(self) -> dict[str, tuple[str, str]]:
        """Get system settings with their persisted update timestamps."""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value, updated_at FROM settings") as cursor:
            rows = await cursor.fetchall()
            return {row["key"]: (row["value"], row["updated_at"]) for row in rows}

    async def get_setting(self, key: str) -> str | None:
        """Get one setting"""
        db = await self._get_connection()
        try:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
        except sqlite3.OperationalError as exc:
            if "no such table: settings" in str(exc):
                return None
            raise

    async def get_release_history_retention_count(self) -> int:
        value = await self.get_setting(SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY)
        try:
            count = (
                int(str(value).strip())
                if value is not None
                else DEFAULT_RELEASE_HISTORY_RETENTION_COUNT
            )
        except (TypeError, ValueError):
            return DEFAULT_RELEASE_HISTORY_RETENTION_COUNT
        if (
            count < MIN_RELEASE_HISTORY_RETENTION_COUNT
            or count > MAX_RELEASE_HISTORY_RETENTION_COUNT
        ):
            return DEFAULT_RELEASE_HISTORY_RETENTION_COUNT
        return count

    async def get_executor_snapshot_retention_count(self) -> int:
        value = await self.get_setting(SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY)
        try:
            count = (
                int(str(value).strip())
                if value is not None
                else DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT
            )
        except (TypeError, ValueError):
            return DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT
        if (
            count < MIN_EXECUTOR_SNAPSHOT_RETENTION_COUNT
            or count > MAX_EXECUTOR_SNAPSHOT_RETENTION_COUNT
        ):
            return DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT
        return count

    async def get_system_timezone(self) -> str:
        value = await self.get_setting(SYSTEM_TIMEZONE_SETTING_KEY)
        timezone_name = str(value or DEFAULT_SYSTEM_TIMEZONE).strip() or DEFAULT_SYSTEM_TIMEZONE
        try:
            ZoneInfo(timezone_name)
        except Exception:
            return DEFAULT_SYSTEM_TIMEZONE
        return timezone_name

    async def get_system_log_level(self) -> str:
        value = await self.get_setting(SYSTEM_LOG_LEVEL_SETTING_KEY)
        log_level = (
            str(value or DEFAULT_SYSTEM_LOG_LEVEL).strip().upper() or DEFAULT_SYSTEM_LOG_LEVEL
        )
        return log_level if log_level in ALLOWED_SYSTEM_LOG_LEVELS else DEFAULT_SYSTEM_LOG_LEVEL

    async def get_system_base_url(self) -> str:
        value = await self.get_setting(SYSTEM_BASE_URL_SETTING_KEY)
        return str(value or DEFAULT_SYSTEM_BASE_URL).strip().rstrip("/")

    async def get_oci_registry_redirects_enabled(self) -> bool:
        value = await self.get_setting(SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY)
        return value == CANONICAL_BOOLEAN_TRUE

    async def set_setting(self, key: str, value: str) -> str:
        """Save a system setting and return its persisted update timestamp."""
        now = datetime.now().isoformat()
        db = await self._get_connection()
        await db.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        await db.commit()
        return now

    async def delete_setting(self, key: str):
        """Delete a system setting"""
        db = await self._get_connection()
        await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        await db.commit()

    # ==================== OIDC Provider Operations ====================

    async def save_oauth_provider(self, provider):
        async with self._encryption_rotation_lock:
            return await sqlite_auth_oidc.save_oauth_provider(self, provider)

    async def get_total_oauth_providers_count(self) -> int:
        return await sqlite_auth_oidc.get_total_oauth_providers_count(self)

    async def list_oauth_providers(self, enabled_only: bool = False) -> list:
        return await sqlite_auth_oidc.list_oauth_providers(self, enabled_only)

    async def get_oauth_provider(self, slug: str):
        return await sqlite_auth_oidc.get_oauth_provider(self, slug)

    async def get_oauth_provider_by_id(self, provider_id: int):
        return await sqlite_auth_oidc.get_oauth_provider_by_id(self, provider_id)

    async def update_oauth_provider(self, provider_id: int, provider) -> None:
        async with self._encryption_rotation_lock:
            await sqlite_auth_oidc.update_oauth_provider(self, provider_id, provider)

    async def delete_oauth_provider(self, provider_id: int) -> None:
        await sqlite_auth_oidc.delete_oauth_provider(self, provider_id)

    def _row_to_oidc_provider(self, row, decrypt_secret: bool = False):
        return sqlite_auth_oidc._row_to_oidc_provider(self, row, decrypt_secret)

    # ==================== OAuth State Operations ====================

    async def save_oauth_state(
        self,
        state: str,
        provider_slug: str,
        code_verifier: str,
        nonce: str,
        flow_type: str,
        browser_binding_hash: str,
        initiating_admin_user_id: int | None = None,
    ) -> None:
        await sqlite_auth_oidc.save_oauth_state(
            self,
            state,
            provider_slug,
            code_verifier,
            nonce,
            flow_type,
            browser_binding_hash,
            initiating_admin_user_id,
        )

    async def consume_oauth_state(self, state: str, provider_slug: str, browser_binding_hash: str):
        return await sqlite_auth_oidc.consume_oauth_state(
            self, state, provider_slug, browser_binding_hash
        )

    async def cleanup_expired_oauth_states(self) -> None:
        await sqlite_auth_oidc.cleanup_expired_oauth_states(self)

    # ==================== OIDC User Operations ====================

    async def get_user_by_oauth(self, provider: str, oauth_sub: str) -> User | None:
        return await sqlite_auth_oidc.get_user_by_oauth(self, provider, oauth_sub)

    async def link_oauth_to_user(
        self, user_id: int, provider: str, oauth_sub: str, avatar_url: str | None = None
    ) -> None:
        await sqlite_auth_oidc.link_oauth_to_user(self, user_id, provider, oauth_sub, avatar_url)

    async def update_user_oidc_info(
        self,
        user_id: int,
        username: str | None = None,
        email: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        await sqlite_auth_oidc.update_user_oidc_info(self, user_id, username, email, avatar_url)
