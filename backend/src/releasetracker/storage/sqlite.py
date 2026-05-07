"""SQLite Storage module"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

import aiosqlite

from . import (
    sqlite_aggregate_trackers,
    sqlite_auth_oidc,
    sqlite_credentials,
    sqlite_runtime_executors,
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
SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"
SYSTEM_LOG_LEVEL_SETTING_KEY = "system.log_level"
SYSTEM_BASE_URL_SETTING_KEY = "system.base_url"
DEFAULT_RELEASE_HISTORY_RETENTION_COUNT = 20
DEFAULT_SYSTEM_TIMEZONE = "UTC"
DEFAULT_SYSTEM_LOG_LEVEL = "INFO"
DEFAULT_SYSTEM_BASE_URL = ""
ALLOWED_SYSTEM_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
MIN_RELEASE_HISTORY_RETENTION_COUNT = 1
MAX_RELEASE_HISTORY_RETENTION_COUNT = 1000
_DOCKER_DISPLAY_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?([.\-].*)?$")


class SQLiteStorage:
    """SQLite database storage"""

    def __init__(self, db_path: str, system_key_manager: "SystemKeyManager" | None = None):
        self.db_path = db_path
        self.system_key_manager = system_key_manager
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Persistent database connection, lazily created via _get_connection()
        self._db: aiosqlite.Connection | None = None

        # Notifier in-memory cache, invalidated after CRUD operations
        self._notifiers_cache: list | None = None

        if system_key_manager is None:
            raise RuntimeError("SQLiteStorage requires SystemKeyManager")

        self.set_encryption_key(system_key_manager.encryption_key)

    async def _get_connection(self) -> aiosqlite.Connection:
        """Get the persistent database connection, creating it lazily on first use"""
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            # Enable WAL mode to allow concurrent reads and writes and improve high-load concurrency
            await self._db.execute("PRAGMA journal_mode=WAL")
            # Set busy timeout in milliseconds to avoid immediate database is locked errors under concurrency
            await self._db.execute("PRAGMA busy_timeout=5000")
            # Increase cache size in pages; default is 4KB per page, here about 16MB
            await self._db.execute("PRAGMA cache_size=-16384")
            await self._db.commit()
            logger.info(f"SQLite 持久化连接已建立，WAL 模式已启用：{self.db_path}")
        return self._db

    async def close(self) -> None:
        """Close the persistent database connection at application shutdown."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            await asyncio.sleep(0)
            logger.info("SQLite 持久化连接已关闭")

    def invalidate_notifiers_cache(self) -> None:
        """Invalidate notifier in-memory cache after CRUD operations"""
        self._notifiers_cache = None

    @staticmethod
    def _normalize_notifier_language(value: Any) -> str:
        if value in {"en", "zh"}:
            return cast(str, value)
        raise ValueError("notifier language must be one of: en, zh")

    def set_encryption_key(self, key: str) -> None:
        try:
            self.fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
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
        try:
            return self.fernet.decrypt(enc.encode()).decode()
        except InvalidToken:
            # Assume legacy plaintext data
            return enc
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
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
                raise ValueError("encrypted value cannot be decrypted with the current encryption key") from exc
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
            return sum(cls._count_undecryptable_nested_strings(item, old_fernet) for item in value.values())
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
                undecryptable_count += self._count_undecryptable_nested_strings(secrets_payload, self.fernet)

        cursor = await db.execute("SELECT client_secret FROM oauth_providers")
        for row in await cursor.fetchall():
            if row["client_secret"]:
                inventory["oauth_provider_client_secret"] += 1
                undecryptable_count += self._count_undecryptable_string(row["client_secret"], self.fernet)

        cursor = await db.execute("SELECT secrets FROM runtime_connections")
        for row in await cursor.fetchall():
            secrets_payload = self._load_json(row["secrets"])
            nested_count = self._count_nested_strings(secrets_payload)
            inventory["runtime_connection_secrets"] += nested_count
            if nested_count:
                undecryptable_count += self._count_undecryptable_nested_strings(secrets_payload, self.fernet)

        return {"inventory": inventory, "undecryptable_count": undecryptable_count}

    async def rotate_encrypted_data(self, new_key: str) -> dict[str, Any]:
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
            },
            "rotated": {
                "credentials_token": 0,
                "credentials_secrets": 0,
                "oauth_provider_client_secret": 0,
                "runtime_connection_secrets": 0,
            },
            "plaintext_reencrypted": 0,
            "undecryptable_count": 0,
        }
        credential_updates: list[tuple[str, str, int]] = []
        oauth_provider_updates: list[tuple[str, int]] = []
        runtime_connection_updates: list[tuple[str, int]] = []

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
                stats["inventory"]["credentials_secrets"] += self._count_nested_strings(secrets_payload)
                rotated_secrets, nested_stats = self._rotate_nested_strings_for_encryption_key(
                    secrets_payload,
                    old_fernet,
                    new_fernet,
                )
                stats["rotated"]["credentials_secrets"] += nested_stats["encrypted"] + nested_stats["plaintext"]
                stats["plaintext_reencrypted"] += nested_stats["plaintext"]
                credential_updates.append((rotated_token, self._dump_json(rotated_secrets), row["id"]))

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
                stats["inventory"]["runtime_connection_secrets"] += self._count_nested_strings(secrets_payload)
                rotated_secrets, nested_stats = self._rotate_nested_strings_for_encryption_key(
                    secrets_payload,
                    old_fernet,
                    new_fernet,
                )
                stats["rotated"]["runtime_connection_secrets"] += nested_stats["encrypted"] + nested_stats["plaintext"]
                stats["plaintext_reencrypted"] += nested_stats["plaintext"]
                runtime_connection_updates.append((self._dump_json(rotated_secrets), row["id"]))
        except ValueError:
            stats["undecryptable_count"] = (await self.get_encryption_key_inventory())["undecryptable_count"]
            raise

        try:
            await db.execute("BEGIN")
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
            await db.commit()
        except Exception:
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

    async def _rebuild_canonical_releases_for_tracker(
        self,
        db: aiosqlite.Connection,
        aggregate_tracker_id: int,
    ) -> None:
        observation_rows = await (
            await db.execute(
                """
                SELECT sro.*, ats.source_type
                FROM source_release_observations sro
                JOIN aggregate_tracker_sources ats ON ats.id = sro.tracker_source_id
                WHERE ats.aggregate_tracker_id = ?
                ORDER BY sro.id ASC
                """,
                (aggregate_tracker_id,),
            )
        ).fetchall()
        current_canonical_keys = {
            self._source_observation_identity_key(row) for row in observation_rows
        }
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
    ) -> int:
        primary_observation = await self._select_primary_canonical_observation(
            db, aggregate_tracker_id, immutable_key
        )
        observation_rows = await self._list_canonical_version_observations(
            db, aggregate_tracker_id, immutable_key
        )
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
        db = await self._get_connection()
        timestamp = (started_at or datetime.now()).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO source_fetch_runs
            (tracker_source_id, trigger_mode, started_at, status, created_at)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (tracker_source_id, trigger_mode, timestamp, timestamp),
        )
        await db.commit()
        return self._require_lastrowid(cursor.lastrowid, "source fetch run")

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
        db = await self._get_connection()
        finished_at_value = (finished_at or datetime.now()).isoformat()
        await db.execute(
            """
            UPDATE source_fetch_runs
            SET status = ?,
                fetched_count = ?,
                filtered_in_count = ?,
                error_message = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                fetched_count,
                filtered_in_count,
                error_message,
                finished_at_value,
                source_fetch_run_id,
            ),
        )
        await db.commit()

    async def append_source_history_for_run(
        self,
        source_fetch_run_id: int,
        tracker_source: TrackerSource,
        releases: list[Release],
        *,
        aggregate_tracker_id: int | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, int]:
        if tracker_source.id is None:
            raise ValueError("tracker_source.id is required to append source history")

        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        timestamp = (observed_at or datetime.now()).isoformat()
        source_history_ids_by_identity: dict[str, int] = {}

        for release in releases:
            version, app_version, chart_version = self._release_version_metadata(
                release, source_type=tracker_source.source_type
            )
            source_release_key = self._source_release_key_for_release(
                release, source_type=tracker_source.source_type
            )
            digest = self._release_digest_value(release, source_type=tracker_source.source_type)
            identity_key = self.release_identity_key_for_source(
                release, source_type=tracker_source.source_type
            )
            tag_name = chart_version or self._normalize_release_value(release.tag_name) or version

            raw_payload: dict[str, Any] = {
                "aggregate_tracker_id": aggregate_tracker_id,
                "source_key": tracker_source.source_key,
                "source_type": tracker_source.source_type,
                "channel_name": release.channel_name,
            }
            if app_version is not None:
                raw_payload["appVersion"] = app_version
            if chart_version is not None:
                raw_payload["chartVersion"] = chart_version

            await db.execute(
                """
                INSERT OR IGNORE INTO source_release_history
                (tracker_source_id, first_source_fetch_run_id, source_type, source_release_key, version, digest, digest_algorithm, digest_media_type, digest_platform, identity_key, immutable_key, name, tag_name, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, first_observed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tracker_source.id,
                    source_fetch_run_id,
                    tracker_source.source_type,
                    source_release_key,
                    version,
                    digest,
                    "sha256" if digest else None,
                    None,
                    None,
                    identity_key,
                    identity_key,
                    release.name,
                    tag_name,
                    release.published_at.isoformat(),
                    release.url,
                    getattr(release, "changelog_url", None),
                    1 if release.prerelease else 0,
                    release.body,
                    release.commit_sha,
                    self._dump_json(raw_payload),
                    timestamp,
                    timestamp,
                ),
            )

            source_row = await (
                await db.execute(
                    """
                    SELECT id, version, tag_name, published_at, commit_sha
                    FROM source_release_history
                    WHERE tracker_source_id = ? AND immutable_key = ?
                    """,
                    (tracker_source.id, identity_key),
                )
            ).fetchone()
            if source_row is None:
                raise ValueError("Failed to read source release history row")
            source_history_id = source_row["id"]
            source_history_ids_by_identity[identity_key] = source_history_id

            normalized_existing_commit = self._normalize_release_value(source_row["commit_sha"])
            normalized_new_commit = self._normalize_release_value(release.commit_sha)
            preserved_published_at = release.published_at.isoformat()
            if tracker_source.source_type != "container" and (
                source_row["version"] == version
                and normalized_existing_commit is not None
                and normalized_existing_commit == normalized_new_commit
            ):
                preserved_published_at = source_row["published_at"]

            if self._should_replace_source_history_display(
                source_type=tracker_source.source_type,
                version=version,
                tag_name=tag_name,
                existing_version=source_row["version"],
                existing_tag_name=source_row["tag_name"],
            ):
                await db.execute(
                    """
                    UPDATE source_release_history
                    SET source_release_key = ?,
                        version = ?,
                        digest = ?,
                        digest_algorithm = ?,
                        name = ?,
                        tag_name = ?,
                        published_at = ?,
                        url = ?,
                        changelog_url = ?,
                        prerelease = ?,
                        body = ?,
                        commit_sha = ?,
                        raw_payload = ?
                    WHERE id = ?
                    """,
                    (
                        source_release_key,
                        version,
                        digest,
                        "sha256" if digest else None,
                        release.name,
                        tag_name,
                        preserved_published_at,
                        release.url,
                        getattr(release, "changelog_url", None),
                        1 if release.prerelease else 0,
                        release.body,
                        release.commit_sha,
                        self._dump_json(raw_payload),
                        source_history_id,
                    ),
                )

            await db.execute(
                """
                INSERT OR IGNORE INTO source_release_run_observations
                (source_fetch_run_id, source_release_history_id, observed_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (source_fetch_run_id, source_history_id, timestamp, timestamp),
            )

        await db.commit()
        return source_history_ids_by_identity

    async def get_source_release_history_releases_by_source(
        self,
        tracker_source_id: int,
    ) -> list[Release]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT *
                FROM source_release_history
                WHERE tracker_source_id = ?
                ORDER BY published_at DESC, id DESC
                """,
                (tracker_source_id,),
            )
        ).fetchall()

        releases: list[Release] = []
        for row in rows:
            raw_payload = self._load_json(row["raw_payload"])
            releases.append(
                Release(
                    tracker_name="",
                    tracker_type=row["source_type"],
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
                    channel_name=raw_payload.get("channel_name"),
                    commit_sha=row["commit_sha"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )

        return releases

    async def get_source_release_history_id(
        self,
        tracker_source_id: int,
        identity_key: str,
    ) -> int | None:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT id
                FROM source_release_history
                WHERE tracker_source_id = ? AND immutable_key = ?
                """,
                (tracker_source_id, identity_key),
            )
        ).fetchone()
        return row["id"] if row else None

    async def upsert_tracker_release_history(
        self,
        aggregate_tracker_id: int,
        release: Release,
        *,
        primary_source_release_history_id: int,
        supporting_source_release_history_ids: list[int] | None = None,
        source_type: str | None = None,
    ) -> tuple[int, bool]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row

        identity_key = self.release_identity_key_for_source(release, source_type=source_type)
        digest = self._release_digest_value(release, source_type=source_type)
        version, _, chart_version = self._release_version_metadata(release, source_type=source_type)
        persisted_version = chart_version or version
        timestamp = datetime.now().isoformat()

        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO tracker_release_history
            (aggregate_tracker_id, identity_key, immutable_key, version, digest, digest_algorithm, digest_media_type, digest_platform, primary_source_release_history_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker_id,
                identity_key,
                identity_key,
                persisted_version,
                digest,
                "sha256" if digest else None,
                None,
                None,
                primary_source_release_history_id,
                timestamp,
            ),
        )
        is_new = cursor.rowcount > 0

        row = await (
            await db.execute(
                """
                SELECT id
                FROM tracker_release_history
                WHERE aggregate_tracker_id = ? AND immutable_key = ?
                """,
                (aggregate_tracker_id, identity_key),
            )
        ).fetchone()
        if row is None:
            raise ValueError("Failed to read tracker release history row")
        tracker_release_history_id = row["id"]

        await db.execute(
            """
            UPDATE tracker_release_history
            SET version = ?,
                digest = ?,
                digest_algorithm = ?,
                digest_media_type = ?,
                digest_platform = ?,
                primary_source_release_history_id = ?
            WHERE id = ?
            """,
            (
                persisted_version,
                digest,
                "sha256" if digest else None,
                None,
                None,
                primary_source_release_history_id,
                tracker_release_history_id,
            ),
        )

        await db.execute(
            """
            UPDATE tracker_release_history_sources
            SET contribution_kind = 'supporting'
            WHERE tracker_release_history_id = ?
              AND contribution_kind = 'primary'
            """,
            (tracker_release_history_id,),
        )

        await db.execute(
            """
            INSERT OR IGNORE INTO tracker_release_history_sources
            (tracker_release_history_id, source_release_history_id, contribution_kind, created_at)
            VALUES (?, ?, 'primary', ?)
            """,
            (tracker_release_history_id, primary_source_release_history_id, timestamp),
        )
        await db.execute(
            """
            UPDATE tracker_release_history_sources
            SET contribution_kind = 'primary'
            WHERE tracker_release_history_id = ?
              AND source_release_history_id = ?
            """,
            (tracker_release_history_id, primary_source_release_history_id),
        )

        for source_release_history_id in supporting_source_release_history_ids or []:
            if source_release_history_id == primary_source_release_history_id:
                continue
            await db.execute(
                """
                INSERT OR IGNORE INTO tracker_release_history_sources
                (tracker_release_history_id, source_release_history_id, contribution_kind, created_at)
                VALUES (?, ?, 'supporting', ?)
                """,
                (tracker_release_history_id, source_release_history_id, timestamp),
            )

        await db.commit()
        return tracker_release_history_id, is_new

    async def get_tracker_release_history_releases(
        self,
        aggregate_tracker_id: int,
    ) -> list[Release]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT trh.id AS tracker_release_history_id,
                       trh.identity_key,
                       trh.version AS tracker_version,
                       trh.digest,
                       trh.created_at AS tracker_created_at,
                       srh.source_type,
                       srh.name,
                       srh.tag_name,
                       srh.version,
                       srh.published_at,
                       srh.url,
                       srh.changelog_url,
                       srh.prerelease,
                       srh.body,
                       srh.commit_sha,
                       srh.raw_payload
                FROM tracker_release_history trh
                JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
                WHERE trh.aggregate_tracker_id = ?
                ORDER BY trh.created_at DESC, trh.id DESC
                """,
                (aggregate_tracker_id,),
            )
        ).fetchall()

        releases: list[Release] = []
        for row in rows:
            raw_payload = self._load_json(row["raw_payload"])
            releases.append(
                Release(
                    id=row["tracker_release_history_id"],
                    tracker_name="",
                    tracker_type=row["source_type"],
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
                    channel_name=raw_payload.get("channel_name"),
                    commit_sha=row["commit_sha"],
                    created_at=datetime.fromisoformat(row["tracker_created_at"]),
                )
            )

        return releases

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

            if enabled_channels:
                for channel in enabled_channels:
                    channel_source_type = (
                        channel.get("source_type")
                        if isinstance(channel, dict)
                        else getattr(channel, "source_type", None)
                    )
                    keep_ids.update(
                        int(release.id)
                        for release in self.select_top_releases_for_channel(
                            history_releases,
                            channel,
                            limit=retention,
                            sort_mode=sort_mode,
                            channel_source_type=channel_source_type,
                        )
                        if release.id is not None
                    )
            else:
                deduped_releases = self.dedupe_releases_by_immutable_identity(history_releases)
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
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        projection_releases = releases
        projected_at = datetime.now().isoformat()

        await db.execute(
            "DELETE FROM tracker_current_releases WHERE aggregate_tracker_id = ?",
            (aggregate_tracker_id,),
        )

        for release in self.dedupe_releases_by_immutable_identity(projection_releases):
            identity_key = self.release_identity_key_for_source(release, source_type=source_type)
            digest = self._release_digest_value(release, source_type=source_type)
            history_row = await (
                await db.execute(
                    """
                    SELECT id
                    FROM tracker_release_history
                    WHERE aggregate_tracker_id = ? AND immutable_key = ?
                    """,
                    (aggregate_tracker_id, identity_key),
                )
            ).fetchone()
            if history_row is None:
                continue

            await db.execute(
                """
                INSERT INTO tracker_current_releases
                (aggregate_tracker_id, identity_key, immutable_key, version, digest, tracker_release_history_id, name, tag_name, published_at, url, changelog_url, prerelease, body, projected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregate_tracker_id,
                    identity_key,
                    identity_key,
                    release.version,
                    digest,
                    history_row["id"],
                    release.name,
                    release.tag_name,
                    release.published_at.isoformat(),
                    release.url,
                    release.changelog_url,
                    1 if release.prerelease else 0,
                    release.body,
                    projected_at,
                    projected_at,
                ),
            )

        await db.commit()

    async def get_tracker_current_releases(self, aggregate_tracker_id: int) -> list[Release]:
        projection_rows = await self._get_tracker_current_projection_rows(aggregate_tracker_id)
        return [row["release"] for row in projection_rows]

    async def _get_tracker_current_projection_rows(
        self, aggregate_tracker_id: int
    ) -> list[dict[str, Any]]:
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT tcr.*,
                       trh.id AS tracker_release_history_id,
                       trh.created_at AS tracker_created_at,
                       srh.id AS primary_source_release_history_id,
                       ats.source_key AS primary_source_key,
                       ats.source_type AS primary_source_type,
                       srh.commit_sha,
                       srh.raw_payload
                FROM tracker_current_releases tcr
                JOIN tracker_release_history trh ON trh.id = tcr.tracker_release_history_id
                JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
                LEFT JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
                WHERE tcr.aggregate_tracker_id = ?
                ORDER BY tcr.published_at DESC, tcr.id DESC
                """,
                (aggregate_tracker_id,),
            )
        ).fetchall()

        projection_rows: list[dict[str, Any]] = []
        for row in rows:
            raw_payload = self._load_json(row["raw_payload"])
            projection_rows.append(
                {
                    "tracker_release_history_id": row["tracker_release_history_id"],
                    "identity_key": row["identity_key"],
                    "version": row["version"],
                    "digest": row["digest"],
                    "published_at": datetime.fromisoformat(row["published_at"]),
                    "name": row["name"],
                    "tag_name": row["tag_name"],
                    "prerelease": bool(row["prerelease"]),
                    "url": row["url"],
                    "changelog_url": row["changelog_url"],
                    "body": row["body"],
                    "projected_at": datetime.fromisoformat(row["projected_at"]),
                    "primary_source": (
                        {
                            "source_key": row["primary_source_key"],
                            "source_type": row["primary_source_type"],
                            "source_release_history_id": row["primary_source_release_history_id"],
                        }
                        if row["primary_source_release_history_id"] is not None
                        else None
                    ),
                    "release": Release(
                        id=row["tracker_release_history_id"],
                        tracker_name="",
                        tracker_type=row["primary_source_type"] or "github",
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
                        channel_name=raw_payload.get("channel_name"),
                        commit_sha=row["commit_sha"],
                        created_at=datetime.fromisoformat(row["tracker_created_at"]),
                    ),
                }
            )
        return projection_rows

    @classmethod
    def _select_top_current_projection_release(
        cls,
        releases: list[Release],
        channels: list[Any],
        sort_mode: str,
    ) -> Release | None:
        if not releases:
            return None

        unique_releases = cls.dedupe_releases_by_immutable_identity(releases)
        enabled_channels = [channel for channel in channels if channel.enabled] if channels else []
        for channel in enabled_channels:
            channel_source_type = getattr(channel, "source_type", None)
            channel_name = getattr(channel, "name", "")
            if isinstance(channel, dict):
                channel_source_type = channel.get("source_type")
                channel_name = str(channel.get("name") or "")
            channel_candidates = [
                release
                for release in unique_releases
                if cls._release_matches_channel(
                    release, channel, channel_source_type=channel_source_type
                )
            ]
            if channel_candidates:
                winner = max(
                    channel_candidates,
                    key=lambda release: cls._release_order_key(release, sort_mode),
                )
                return cls._copy_release_with_channel_name(winner, channel_name)

        if enabled_channels:
            return None

        return max(unique_releases, key=lambda release: cls._release_order_key(release, sort_mode))

    @classmethod
    def _filter_projection_rows_by_channels(
        cls,
        rows: list[dict[str, Any]],
        channels: list[Any],
    ) -> list[dict[str, Any]]:
        enabled_channels = [channel for channel in channels if channel.enabled] if channels else []
        if not enabled_channels:
            return rows

        visible_rows: list[dict[str, Any]] = []
        for row in rows:
            release = row["release"]
            if any(
                cls._release_matches_channel(
                    release,
                    channel,
                    channel_source_type=(
                        channel.get("source_type")
                        if isinstance(channel, dict)
                        else getattr(channel, "source_type", None)
                    ),
                )
                for channel in enabled_channels
            ):
                visible_rows.append(row)

        return visible_rows

    async def get_tracker_current_release_rows(self, tracker_name: str) -> list[dict[str, Any]]:
        aggregate_tracker = await self.get_aggregate_tracker(tracker_name)
        if aggregate_tracker is None or aggregate_tracker.id is None:
            return []

        current_rows = await self._get_tracker_current_projection_rows(aggregate_tracker.id)
        tracker_config = await self.get_tracker_config(tracker_name)
        channels = tracker_config.channels if tracker_config is not None else []
        return self._filter_projection_rows_by_channels(current_rows, channels)

    async def get_tracker_latest_current_release_summary(
        self, tracker_name: str
    ) -> dict[str, Any] | None:
        current_rows = await self.get_tracker_current_release_rows(tracker_name)
        if not current_rows:
            return None

        tracker_config = await self.get_tracker_config(tracker_name)
        sort_mode = (
            tracker_config.version_sort_mode if tracker_config is not None else "published_at"
        )
        channels = tracker_config.channels if tracker_config is not None else []
        current_releases = [
            row["release"].model_copy(update={"tracker_name": tracker_name}) for row in current_rows
        ]
        latest_release = self._select_top_current_projection_release(
            current_releases,
            channels,
            sort_mode,
        )
        if latest_release is None:
            return None

        latest_identity_key = self.release_identity_key_for_source(
            latest_release,
            source_type=latest_release.tracker_type,
        )
        latest_row = next(
            (row for row in current_rows if row["identity_key"] == latest_identity_key),
            None,
        )
        if latest_row is None:
            return None

        return {
            "tracker_name": tracker_name,
            "tracker_release_history_id": latest_row["tracker_release_history_id"],
            "identity_key": latest_row["identity_key"],
            "version": latest_release.version,
            "digest": latest_row["digest"],
            "published_at": latest_row["published_at"],
            "prerelease": latest_release.prerelease,
            "name": latest_release.name,
            "tag_name": latest_release.tag_name,
            "url": latest_release.url,
            "changelog_url": latest_release.changelog_url,
            "body": latest_release.body,
            "channel_name": latest_row["release"].channel_name,
            "primary_source": latest_row["primary_source"],
            "primary_source_type": (
                latest_row["primary_source"]["source_type"]
                if latest_row["primary_source"] is not None
                else None
            ),
            "projected_at": latest_row["projected_at"],
            "release": latest_release,
        }

    async def get_tracker_current_status_derivation(self, tracker_name: str) -> dict[str, Any]:
        tracker_status = await self.get_tracker_status(tracker_name)
        latest_summary = await self.get_tracker_latest_current_release_summary(tracker_name)
        return {
            "tracker_name": tracker_name,
            "last_check": tracker_status.last_check if tracker_status is not None else None,
            "error": tracker_status.error if tracker_status is not None else None,
            "latest_identity_key": (
                latest_summary["identity_key"] if latest_summary is not None else None
            ),
            "latest_version": latest_summary["version"] if latest_summary is not None else None,
            "latest_tracker_release_history_id": (
                latest_summary["tracker_release_history_id"] if latest_summary is not None else None
            ),
            "projected_at": latest_summary["projected_at"] if latest_summary is not None else None,
        }

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
        comparison_version, app_version, chart_version = self._release_version_metadata(
            release, source_type=source_type
        )
        tag_name = (
            chart_version or self._normalize_release_value(release.tag_name) or comparison_version
        )
        source_release_key = tag_name
        if not source_release_key:
            raise ValueError("release tag_name or version must be a non-empty string")

        observed_at_iso = observed_at.isoformat()
        persisted_raw_payload = dict(raw_payload or {})
        if app_version is not None:
            persisted_raw_payload["appVersion"] = app_version
        if chart_version is not None:
            persisted_raw_payload["chartVersion"] = chart_version
        raw_payload_json = self._dump_json(persisted_raw_payload)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, version, published_at, commit_sha
            FROM source_release_observations
            WHERE tracker_source_id = ? AND source_release_key = ?
            """,
            (tracker_source_id, source_release_key),
        )
        existing_row = await cursor.fetchone()

        if existing_row is None:
            cursor = await db.execute(
                """
                INSERT INTO source_release_observations
                (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tracker_source_id,
                    source_release_key,
                    release.name,
                    tag_name,
                    comparison_version,
                    release.published_at.isoformat(),
                    release.url,
                    changelog_url,
                    1 if release.prerelease else 0,
                    release.body,
                    release.commit_sha,
                    raw_payload_json,
                    observed_at_iso,
                    observed_at_iso,
                    observed_at_iso,
                ),
            )
            return self._require_lastrowid(cursor.lastrowid, "source release observation")

        persisted_published_at = release.published_at.isoformat()
        normalized_existing_commit = self._normalize_release_value(existing_row["commit_sha"])
        normalized_new_commit = self._normalize_release_value(release.commit_sha)
        if source_type != "container" and (
            existing_row["version"] == comparison_version
            and normalized_existing_commit is not None
            and normalized_existing_commit == normalized_new_commit
        ):
            persisted_published_at = existing_row["published_at"]

        await db.execute(
            """
            UPDATE source_release_observations
            SET name = ?,
                tag_name = ?,
                version = ?,
                published_at = ?,
                url = ?,
                changelog_url = ?,
                prerelease = ?,
                body = ?,
                commit_sha = ?,
                raw_payload = ?,
                observed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                release.name,
                tag_name,
                comparison_version,
                persisted_published_at,
                release.url,
                changelog_url,
                1 if release.prerelease else 0,
                release.body,
                release.commit_sha,
                raw_payload_json,
                observed_at_iso,
                observed_at_iso,
                existing_row["id"],
            ),
        )
        return existing_row["id"]

    async def save_source_observations(
        self,
        aggregate_tracker_id: int,
        tracker_source: TrackerSource,
        releases: list[Release],
        *,
        observed_at: datetime | None = None,
        append_truth: bool = True,
    ) -> list[int]:
        if tracker_source.id is None:
            raise ValueError("tracker_source.id is required to save source observations")

        persisted_observation_ids: list[int] = []
        timestamp = observed_at or datetime.now()

        if append_truth:
            source_fetch_run_id = await self.create_source_fetch_run(
                tracker_source.id,
                trigger_mode="bootstrap",
                started_at=timestamp,
            )
            await self.append_source_history_for_run(
                source_fetch_run_id,
                tracker_source,
                releases,
                aggregate_tracker_id=aggregate_tracker_id,
                observed_at=timestamp,
            )
            await self.finalize_source_fetch_run(
                source_fetch_run_id,
                status="success",
                fetched_count=len(releases),
                filtered_in_count=len(releases),
                finished_at=timestamp,
            )

        source_release_keys = {
            self._source_release_key_for_release(release, source_type=tracker_source.source_type)
            for release in releases
        }

        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        for release in releases:
            observation_id = await self._upsert_source_release_observation(
                db,
                tracker_source.id,
                release,
                observed_at=timestamp,
                raw_payload={
                    "aggregate_tracker_id": aggregate_tracker_id,
                    "source_key": tracker_source.source_key,
                    "source_type": tracker_source.source_type,
                    "channel_name": release.channel_name,
                },
                changelog_url=getattr(release, "changelog_url", None),
                source_type=tracker_source.source_type,
            )
            persisted_observation_ids.append(observation_id)

        if source_release_keys:
            placeholders = ", ".join("?" for _ in source_release_keys)
            await db.execute(
                f"DELETE FROM source_release_observations WHERE tracker_source_id = ? AND source_release_key NOT IN ({placeholders})",
                (tracker_source.id, *source_release_keys),
            )
        else:
            await db.execute(
                "DELETE FROM source_release_observations WHERE tracker_source_id = ?",
                (tracker_source.id,),
            )

        await self._rebuild_canonical_releases_for_tracker(db, aggregate_tracker_id)

        await db.commit()

        return persisted_observation_ids

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
        aggregate_trackers: list[AggregateTracker] = []
        if tracker_name:
            aggregate_tracker = await self.get_aggregate_tracker(tracker_name)
            if aggregate_tracker is not None:
                aggregate_trackers = [aggregate_tracker]
        else:
            aggregate_trackers = await self.get_all_aggregate_trackers()

        releases: list[Release] = []
        for aggregate_tracker in aggregate_trackers:
            tracker_releases: list[Release] = []
            if aggregate_tracker.id is not None:
                if include_history:
                    tracker_releases = await self.get_tracker_release_history_releases(
                        aggregate_tracker.id
                    )
                else:
                    tracker_releases = await self.get_tracker_current_releases(aggregate_tracker.id)

            if tracker_releases:
                if not include_history:
                    tracker_config = await self.get_tracker_config(aggregate_tracker.name)
                    channels = tracker_config.channels if tracker_config is not None else []
                    if channels:
                        tracker_releases = list(
                            self.select_best_releases_by_channel(
                                tracker_releases,
                                channels,
                                sort_mode=(
                                    tracker_config.version_sort_mode
                                    if tracker_config is not None
                                    else "published_at"
                                ),
                                use_immutable_identity=True,
                            ).values()
                        )
                for tracker_release in tracker_releases:
                    tracker_release.tracker_name = aggregate_tracker.name
                releases.extend(tracker_releases)
                continue

            if not include_history:
                continue

            canonical_releases = await self.get_canonical_releases(aggregate_tracker.name)
            source_observations = await self.get_source_release_observations(aggregate_tracker.name)
            observations_by_id = {
                observation.id: observation
                for observation in source_observations
                if observation.id is not None
            }
            sources_by_id = {
                source.id: source for source in aggregate_tracker.sources if source.id is not None
            }
            visible_canonical_releases = [
                canonical_release
                for canonical_release in canonical_releases
                if self._canonical_release_should_be_listed_in_history(
                    aggregate_tracker,
                    canonical_release,
                    observations_by_id,
                    sources_by_id,
                )
            ]
            releases.extend(
                self._canonical_release_to_release(
                    aggregate_tracker,
                    canonical_release,
                    observations_by_id,
                )
                for canonical_release in visible_canonical_releases
            )

        releases = [
            release
            for release in releases
            if self._release_matches_filters(
                release,
                tracker_name=tracker_name,
                search=search,
                prerelease=prerelease,
            )
        ]
        releases = sorted(releases, key=self._release_listing_sort_key, reverse=True)

        if limit is None:
            return releases[skip:]
        return releases[skip : skip + limit]

    @staticmethod
    def _tracker_type_for_canonical_release(
        tracker: AggregateTracker, canonical_release: CanonicalRelease
    ) -> str:
        if canonical_release.primary_observation_id is not None:
            observation = next(
                (
                    source
                    for source in tracker.sources
                    if source.id is not None
                    and any(
                        obs.source_release_observation_id
                        == canonical_release.primary_observation_id
                        for obs in canonical_release.observations
                    )
                ),
                None,
            )
            if observation is not None:
                return observation.source_type

        runtime_source = SQLiteStorage._select_runtime_source(tracker)
        if runtime_source is not None:
            return runtime_source.source_type
        return "github"

    @staticmethod
    def _aggregate_tracker_prefers_repo_history(tracker: AggregateTracker) -> bool:
        return any(
            source.source_type in {"github", "gitlab", "gitea"} for source in tracker.sources
        )

    @classmethod
    def _canonical_release_should_be_listed_in_history(
        cls,
        tracker: AggregateTracker,
        canonical_release: CanonicalRelease,
        observations_by_id: dict[int, SourceReleaseObservation],
        sources_by_id: dict[int, TrackerSource],
    ) -> bool:
        if not cls._aggregate_tracker_prefers_repo_history(tracker):
            return True

        for observation in canonical_release.observations:
            source_observation = observations_by_id.get(observation.source_release_observation_id)
            if source_observation is None:
                continue
            source = sources_by_id.get(source_observation.tracker_source_id)
            if source and source.source_type in {"github", "gitlab", "gitea"}:
                return True

        return False

    @classmethod
    def _canonical_release_to_release(
        cls,
        tracker: AggregateTracker,
        canonical_release: CanonicalRelease,
        observations_by_id: dict[int, SourceReleaseObservation],
    ) -> Release:
        tracker_type = cls._tracker_type_for_canonical_release(tracker, canonical_release)
        primary_observation_id = canonical_release.primary_observation_id
        primary_observation = (
            observations_by_id.get(primary_observation_id)
            if primary_observation_id is not None
            else None
        )
        if primary_observation is not None:
            app_version = primary_observation.app_version
            chart_version = primary_observation.chart_version
            if tracker_type == "helm" and app_version is None:
                app_version = primary_observation.version
            if tracker_type == "helm" and chart_version is None:
                chart_version = primary_observation.tag_name
            release_version = primary_observation.version
            release_name = primary_observation.name
            release_tag_name = primary_observation.tag_name
            release_published_at = primary_observation.published_at
            release_url = primary_observation.url
            release_prerelease = primary_observation.prerelease
            release_body = primary_observation.body
            release_commit_sha = primary_observation.commit_sha
            release_channel_name = primary_observation.raw_payload.get("channel_name")
        else:
            app_version = canonical_release.version if tracker_type == "helm" else None
            chart_version = canonical_release.tag_name if tracker_type == "helm" else None
            release_version = canonical_release.version
            release_name = canonical_release.name
            release_tag_name = canonical_release.tag_name
            release_published_at = canonical_release.published_at
            release_url = canonical_release.url
            release_prerelease = canonical_release.prerelease
            release_body = canonical_release.body
            release_commit_sha = None
            release_channel_name = None

        return Release(
            id=canonical_release.id,
            tracker_name=tracker.name,
            tracker_type=tracker_type,
            name=release_name,
            tag_name=release_tag_name,
            version=release_version,
            app_version=app_version,
            chart_version=chart_version,
            published_at=release_published_at,
            url=release_url,
            prerelease=release_prerelease,
            body=release_body,
            channel_name=release_channel_name,
            commit_sha=release_commit_sha,
            created_at=canonical_release.created_at,
        )

    @staticmethod
    def _release_matches_filters(
        release: Release,
        *,
        tracker_name: str | None = None,
        search: str | None = None,
        prerelease: bool | None = None,
    ) -> bool:
        if tracker_name and release.tracker_name != tracker_name:
            return False
        if prerelease is not None and release.prerelease != prerelease:
            return False
        if search:
            search_value = search.lower()
            haystacks = [
                release.tracker_name,
                release.name,
                release.tag_name,
                release.version,
            ]
            if not any(search_value in haystack.lower() for haystack in haystacks):
                return False
        return True

    @staticmethod
    def _release_listing_sort_key(release: Release) -> tuple[float, float, int]:
        created_at = release.created_at.timestamp() if release.created_at else 0.0
        return (release.published_at.timestamp(), created_at, release.id or 0)

    async def get_latest_tracker_releases(self, limit: int = 5) -> list[Release]:
        """Provide global recent releases for the dashboard, capped at one per tracker to avoid flooding"""
        releases = await self.get_releases(limit=None, include_history=True)
        latest_by_tracker: dict[str, Release] = {}
        for release in releases:
            if release.tracker_name not in latest_by_tracker:
                latest_by_tracker[release.tracker_name] = release
        return list(latest_by_tracker.values())[:limit]

    async def get_total_count(
        self,
        tracker_name: str | None = None,
        search: str | None = None,
        prerelease: bool | None = None,
        include_history: bool = True,
    ) -> int:
        """Get count of matching records"""
        releases = await self.get_releases(
            tracker_name=tracker_name,
            search=search,
            prerelease=prerelease,
            limit=None,
            include_history=include_history,
        )
        return len(releases)

    async def get_releases_for_trackers_bulk(
        self, tracker_names: list[str], limit_per_tracker: int = 200
    ) -> dict[str, list[Release]]:
        """
        Fetch recent release records for multiple trackers in one query

        Use a window function to avoid N+1 queries
        Returns:{tracker_name: [Release, ...]}
        """
        if not tracker_names:
            return {}

        result = {name: [] for name in tracker_names}
        for tracker_name in tracker_names:
            result[tracker_name] = await self.get_releases(
                tracker_name=tracker_name,
                limit=limit_per_tracker,
                include_history=False,
            )

        return result

    async def get_latest_release(self, tracker_name: str) -> Release | None:
        """Get the latest release for a tracker"""
        latest_current_release = await self.get_tracker_latest_current_release_summary(tracker_name)
        if latest_current_release is not None:
            return latest_current_release["release"]
        releases = await self.get_releases(tracker_name, limit=1)
        return releases[0] if releases else None

    async def get_latest_release_for_channels(
        self, tracker_name: str, channels: list
    ) -> Release | None:
        """Get the latest releases across all enabled channels for a tracker"""
        if not channels:
            return await self.get_latest_release(tracker_name)

        all_releases = await self.get_releases(
            tracker_name,
            limit=None,
            include_history=False,
        )
        sort_mode = await self.get_setting("version_sort_mode") or "published_at"
        channel_winners = self.select_best_releases_by_channel(
            all_releases,
            channels,
            sort_mode=sort_mode,
            use_immutable_identity=True,
        )
        return self.select_best_release(
            list(channel_winners.values()),
            channels,
            sort_mode=sort_mode,
            use_immutable_identity=True,
        )

    @staticmethod
    def release_identity_key(release: Release) -> tuple[str, str]:
        return (release.tracker_name, release.tag_name)

    @classmethod
    def immutable_release_identity_key(cls, release: Release) -> tuple[str, str]:
        source_type = cls._normalize_release_value(release.tracker_type) or "github"
        return (
            release.tracker_name,
            cls.release_identity_key_for_source(release, source_type=source_type),
        )

    @staticmethod
    def dedupe_releases_by_identity(releases: list[Release]) -> list[Release]:
        unique_by_identity: dict[tuple[str, str], Release] = {}

        for release in releases:
            unique_by_identity[SQLiteStorage.release_identity_key(release)] = release

        return list(unique_by_identity.values())

    @classmethod
    def dedupe_releases_by_immutable_identity(cls, releases: list[Release]) -> list[Release]:
        unique_by_identity: dict[tuple[str, str], Release] = {}

        for release in releases:
            identity_key = cls.immutable_release_identity_key(release)
            existing_release = unique_by_identity.get(identity_key)
            if existing_release is None or cls._should_replace_source_history_display(
                source_type=release.tracker_type,
                version=release.version,
                tag_name=release.tag_name,
                existing_version=existing_release.version,
                existing_tag_name=existing_release.tag_name,
            ):
                unique_by_identity[identity_key] = release

        return list(unique_by_identity.values())

    @staticmethod
    def _release_matches_channel(
        release: Release, channel, *, channel_source_type: str | None = None
    ) -> bool:
        from ..config import Channel
        import re

        if isinstance(channel, dict):
            channel = Channel(**channel)

        if SQLiteStorage._supports_release_type_filter(channel_source_type):
            if channel.type == "release" and release.prerelease:
                return False
            if channel.type == "prerelease" and not release.prerelease:
                return False

        if channel.include_pattern:
            try:
                if not re.search(channel.include_pattern, release.tag_name):
                    return False
            except re.error:
                pass

        if channel.exclude_pattern:
            try:
                if any(
                    re.search(channel.exclude_pattern, candidate)
                    for candidate in SQLiteStorage._channel_exclude_match_candidates(release)
                ):
                    return False
            except re.error:
                pass

        return True

    @staticmethod
    def _supports_release_type_filter(source_type: str | None) -> bool:
        return source_type is None or source_type in {"github", "gitlab", "gitea"}

    @staticmethod
    def _channel_exclude_match_candidates(release: Release) -> list[str]:
        return [release.tag_name]

    @staticmethod
    def _release_order_key(release: Release, sort_mode: str = "published_at") -> tuple:
        from packaging.version import InvalidVersion, parse as parse_version

        normalized_version = SQLiteStorage._normalize_version_for_ordering(release.version)
        semver_key: tuple[int, Any] | None = None
        try:
            semver_key = (1, parse_version(normalized_version))
        except InvalidVersion:
            semver_key = None

        if sort_mode == "semver":
            if semver_key is not None:
                return (*semver_key, release.published_at.timestamp())
            return (0, release.published_at.timestamp())

        if semver_key is not None:
            return (*semver_key, release.published_at.timestamp())

        return (0, release.published_at.timestamp())

    @staticmethod
    def _channel_selection_key(channel, index: int) -> str:
        release_channel_key = getattr(channel, "release_channel_key", None)
        if isinstance(channel, dict):
            release_channel_key = channel.get("release_channel_key") or channel.get("channel_key")
        if release_channel_key:
            return str(release_channel_key)

        channel_name = getattr(channel, "name", None)
        if isinstance(channel, dict):
            channel_name = channel.get("name")
        return str(channel_name or f"legacy-channel-{index}")

    @staticmethod
    def _copy_release_with_channel_name(release: Release, channel_name: str) -> Release:
        return release.model_copy(update={"channel_name": channel_name})

    @staticmethod
    def select_best_releases_by_channel(
        releases: list[Release],
        channels: list,
        sort_mode: str = "published_at",
        *,
        channel_source_type: str | None = None,
        use_immutable_identity: bool = False,
    ) -> dict[str, Release]:
        if not releases or not channels:
            return {}

        if not any(
            ch.get("enabled", True) if isinstance(ch, dict) else ch.enabled for ch in channels
        ):
            return {}

        unique_releases = (
            SQLiteStorage.dedupe_releases_by_immutable_identity(releases)
            if use_immutable_identity
            else SQLiteStorage.dedupe_releases_by_identity(releases)
        )
        winners: dict[str, Release] = {}

        for index, channel in enumerate(channels):
            if isinstance(channel, dict):
                if not channel.get("enabled", True):
                    continue
                channel_name = channel.get("name")
            else:
                if not channel.enabled:
                    continue
                channel_name = channel.name

            if not channel_name:
                continue
            channel_candidates = [
                release
                for release in unique_releases
                if SQLiteStorage._release_matches_channel(
                    release, channel, channel_source_type=channel_source_type
                )
            ]

            if not channel_candidates:
                continue

            winner = max(
                channel_candidates,
                key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
            )
            winner = SQLiteStorage._copy_release_with_channel_name(winner, channel_name)
            winners[SQLiteStorage._channel_selection_key(channel, index)] = winner

        return winners

    @staticmethod
    def select_best_releases_for_tracker_channel(
        releases: list[Release],
        tracker_channel,
        sort_mode: str = "published_at",
        *,
        use_immutable_identity: bool = False,
    ) -> dict[str, Release]:
        if tracker_channel is None:
            return {}

        release_channels = getattr(tracker_channel, "release_channels", None)
        channel_source_type = getattr(tracker_channel, "source_type", None)
        if isinstance(tracker_channel, dict):
            release_channels = tracker_channel.get("release_channels")
            channel_source_type = tracker_channel.get("source_type")

        return SQLiteStorage.select_best_releases_by_channel(
            releases,
            list(release_channels or []),
            sort_mode=sort_mode,
            channel_source_type=channel_source_type,
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
        """
        Select the latest release from the release list according to channel rules
        """
        if not releases:
            return None

        if not channels:
            return max(
                (
                    SQLiteStorage.dedupe_releases_by_immutable_identity(releases)
                    if use_immutable_identity
                    else SQLiteStorage.dedupe_releases_by_identity(releases)
                ),
                key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
            )

        enabled_channels = [ch for ch in channels if ch.enabled]
        if not enabled_channels:
            return max(
                (
                    SQLiteStorage.dedupe_releases_by_immutable_identity(releases)
                    if use_immutable_identity
                    else SQLiteStorage.dedupe_releases_by_identity(releases)
                ),
                key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
            )

        channel_winners = SQLiteStorage.select_best_releases_by_channel(
            releases,
            enabled_channels,
            sort_mode=sort_mode,
            use_immutable_identity=use_immutable_identity,
        )
        if not channel_winners:
            return None

        return max(
            channel_winners.values(),
            key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
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

            release_type = "prerelease" if release.prerelease else "stable"
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
        return await sqlite_runtime_executors.create_runtime_connection(self, runtime_connection)

    async def get_total_runtime_connections_count(self) -> int:
        return await sqlite_runtime_executors.get_total_runtime_connections_count(self)

    async def get_runtime_connections_paginated(self, skip: int = 0, limit: int = 20) -> list:
        return await sqlite_runtime_executors.get_runtime_connections_paginated(self, skip, limit)

    async def get_runtime_connection(self, runtime_connection_id: int):
        return await sqlite_runtime_executors.get_runtime_connection(self, runtime_connection_id)

    async def get_runtime_connection_by_name(self, name: str):
        return await sqlite_runtime_executors.get_runtime_connection_by_name(self, name)

    async def update_runtime_connection(
        self, runtime_connection_id: int, runtime_connection: RuntimeConnectionConfig
    ) -> bool:
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

    async def get_total_executor_configs_count(self) -> int:
        return await sqlite_runtime_executors.get_total_executor_configs_count(self)

    async def get_all_executor_configs(self) -> list[ExecutorConfig]:
        return await sqlite_runtime_executors.get_all_executor_configs(self)

    async def get_executor_configs_paginated(
        self, skip: int = 0, limit: int = 20
    ) -> list[ExecutorConfig]:
        return await sqlite_runtime_executors.get_executor_configs_paginated(self, skip, limit)

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

    async def enqueue_executor_projection_trigger_work(
        self,
        *,
        executor_id: int,
        tracker_name: str,
        previous_version: str | None,
        current_version: str,
        previous_identity_key: str | None = None,
        current_identity_key: str | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.enqueue_executor_projection_trigger_work(
            self,
            executor_id=executor_id,
            tracker_name=tracker_name,
            previous_version=previous_version,
            current_version=current_version,
            previous_identity_key=previous_identity_key,
            current_identity_key=current_identity_key,
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

    async def get_executor_snapshot(self, executor_id: int) -> ExecutorSnapshot | None:
        return await sqlite_runtime_executors.get_executor_snapshot(self, executor_id)

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
        claimed_by: str | None = None,
    ) -> bool:
        return await sqlite_runtime_executors.complete_executor_desired_state(
            self,
            executor_id,
            claimed_by=claimed_by,
        )

    # ==================== Auth Methods ====================

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

    async def get_total_notifiers_count(self) -> int:
        """Get the notifier count."""
        db = await self._get_connection()
        async with db.execute("SELECT COUNT(*) FROM notifiers") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_notifiers_paginated(self, skip: int = 0, limit: int = 20) -> list[Notifier]:
        """Get notifiers with pagination"""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM notifiers ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, skip)
        ) as cursor:
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
        """Get all system settings"""
        db = await self._get_connection()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM settings") as cursor:
            rows = await cursor.fetchall()
            return {row["key"]: row["value"] for row in rows}

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
        log_level = str(value or DEFAULT_SYSTEM_LOG_LEVEL).strip().upper() or DEFAULT_SYSTEM_LOG_LEVEL
        return log_level if log_level in ALLOWED_SYSTEM_LOG_LEVELS else DEFAULT_SYSTEM_LOG_LEVEL

    async def get_system_base_url(self) -> str:
        value = await self.get_setting(SYSTEM_BASE_URL_SETTING_KEY)
        return str(value or DEFAULT_SYSTEM_BASE_URL).strip().rstrip("/")

    async def set_setting(self, key: str, value: str):
        """Save system setting"""
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

    async def delete_setting(self, key: str):
        """Delete a system setting"""
        db = await self._get_connection()
        await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        await db.commit()

    # ==================== OIDC Provider Operations ====================

    async def save_oauth_provider(self, provider):
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
        await sqlite_auth_oidc.update_oauth_provider(self, provider_id, provider)

    async def delete_oauth_provider(self, provider_id: int) -> None:
        await sqlite_auth_oidc.delete_oauth_provider(self, provider_id)

    def _row_to_oidc_provider(self, row, decrypt_secret: bool = False):
        return sqlite_auth_oidc._row_to_oidc_provider(self, row, decrypt_secret)

    # ==================== OAuth State Operations ====================

    async def save_oauth_state(self, state: str, provider_slug: str, code_verifier: str) -> None:
        await sqlite_auth_oidc.save_oauth_state(self, state, provider_slug, code_verifier)

    async def get_and_delete_oauth_state(self, state: str):
        return await sqlite_auth_oidc.get_and_delete_oauth_state(self, state)

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
        self, user_id: int, email: str | None = None, avatar_url: str | None = None
    ) -> None:
        await sqlite_auth_oidc.update_user_oidc_info(self, user_id, email, avatar_url)
