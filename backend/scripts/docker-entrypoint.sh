#!/bin/sh
set -eu

# One fixed data location; the app, CLI, backups and dbmate must never diverge.
DB_PATH="$(python -c 'from releasetracker.paths import database_path; print(database_path())')"
DATABASE_URL="sqlite://${DB_PATH}"
DBMATE_MIGRATIONS_DIR="${DBMATE_MIGRATIONS_DIR:-/app/backend/dbmate/migrations}"

run_migrate() {
  python -m releasetracker.cli recover-online-restore
  # The saved system.pre_migration_backup setting decides; disable it only when
  # another tool already keeps a restore point for the pre-upgrade schema.
  python -m releasetracker.cli pre-migration-backup
  dbmate --url "$DATABASE_URL" --migrations-dir "$DBMATE_MIGRATIONS_DIR" migrate
}

run_serve() {
  # The saved inbound policy, not uvicorn, decides which peers are trusted.
  exec uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
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
  import-legacy-settings)
    # Explicit one-time upgrade step; run it after migrations and restart to apply.
    exec python -m releasetracker.cli import-legacy-settings
    ;;
  reset-admin-password)
    exec python -m releasetracker.cli reset-admin-password "$@"
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
