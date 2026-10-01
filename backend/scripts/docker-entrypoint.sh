#!/bin/sh
set -eu

DB_PATH="${RELEASETRACKER_DB_PATH:-/app/backend/data/releases.db}"
DATABASE_URL="${DATABASE_URL:-sqlite://${DB_PATH}}"
DBMATE_MIGRATIONS_DIR="${DBMATE_MIGRATIONS_DIR:-/app/backend/dbmate/migrations}"

run_migrate() {
  # Keep a restore point for the exact pre-upgrade schema. Set
  # RELEASETRACKER_PRE_MIGRATION_BACKUP=0 only if another tool already does this.
  python -m releasetracker.cli pre-migration-backup
  dbmate --url "$DATABASE_URL" --migrations-dir "$DBMATE_MIGRATIONS_DIR" migrate
}

run_serve() {
  exec uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000
}

cmd="${1:-serve}"
shift || true

case "$cmd" in
  migrate)
    run_migrate
    ;;
  migrate-and-serve)
    run_migrate
    run_serve
    ;;
  serve)
    run_serve
    ;;
  reset-admin-password)
    exec python -m releasetracker.cli reset-admin-password "$@"
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
