from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path


def backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def dbmate_migrations_dir() -> Path:
    return backend_root() / "dbmate" / "migrations"


def iter_dbmate_up_sql(migrations_dir: Path) -> list[tuple[Path, str]]:
    statements: list[tuple[Path, str]] = []
    for migration_path in sorted(migrations_dir.glob("*.sql")):
        content = migration_path.read_text(encoding="utf-8")
        up_section = content.split("-- migrate:down", 1)[0]
        up_sql = up_section.split("-- migrate:up", 1)[1].strip()
        statements.append((migration_path, up_sql))
    return statements


def iter_dbmate_down_sql(migrations_dir: Path) -> list[tuple[Path, str]]:
    statements: list[tuple[Path, str]] = []
    for migration_path in sorted(migrations_dir.glob("*.sql"), reverse=True):
        content = migration_path.read_text(encoding="utf-8")
        down_sql = content.split("-- migrate:down", 1)[1].strip()
        statements.append((migration_path, down_sql))
    return statements


def apply_dbmate_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    applied_versions: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
        conn.commit()

        for migration_path, up_sql in iter_dbmate_up_sql(migrations_dir):
            version = migration_path.name.split("_", 1)[0]
            conn.executescript(up_sql)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            conn.commit()
            applied_versions.append(version)
    finally:
        conn.close()

    return applied_versions


def rollback_dbmate_migrations(
    db_path: Path, migrations_dir: Path, steps: int | None = None
) -> list[str]:
    rolled_back_versions: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        for index, (migration_path, down_sql) in enumerate(iter_dbmate_down_sql(migrations_dir)):
            if steps is not None and index >= steps:
                break

            version = migration_path.name.split("_", 1)[0]
            conn.executescript(down_sql)
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
            conn.commit()
            rolled_back_versions.append(version)
    finally:
        conn.close()

    return rolled_back_versions


async def initialize_storage_with_schema(storage) -> None:
    apply_dbmate_migrations(Path(storage.db_path), dbmate_migrations_dir())
    await storage.initialize()


def clone_sqlite_database(template_db_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_db_path, db_path)
