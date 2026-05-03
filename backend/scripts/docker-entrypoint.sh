#!/bin/sh
set -eu

DB_PATH="${RELEASETRACKER_DB_PATH:-/app/backend/data/releases.db}"
DATABASE_URL="${DATABASE_URL:-sqlite://${DB_PATH}}"
DBMATE_MIGRATIONS_DIR="${DBMATE_MIGRATIONS_DIR:-/app/backend/dbmate/migrations}"

run_migrate() {
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
  *)
    exec "$cmd" "$@"
    ;;
esac
