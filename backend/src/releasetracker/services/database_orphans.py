"""Conservative repair of legacy deleted-parent history; never prune live tasks.

Only the schema's CASCADE/SET NULL rules on these known history tables apply.
Other corruption continues to fail strict backup/restore validation.
"""

from __future__ import annotations

HISTORY_TABLES = frozenset(
    {
        "aggregate_tracker_sources",
        "source_fetch_runs",
        "source_release_observations",
        "canonical_releases",
        "canonical_release_observations",
        "source_release_history",
        "source_release_aliases",
        "source_release_alias_run_observations",
        "source_release_run_observations",
        "tracker_release_history",
        "tracker_release_history_sources",
        "tracker_current_releases",
        "tracker_release_history_tombstones",
        "executor_snapshots",
        "executor_run_history",
        "executor_status",
        "executor_service_bindings",
        "executor_desired_state",
        "ssh_compose_ownership",
        "repository_webhooks",
        "webhook_deliveries",
        "source_refresh_requests",
    }
)


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def prune_orphans(db):
    """Repair known missing-parent rows in the caller's transaction, returning counts."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rules = []
    for table in sorted(HISTORY_TABLES & tables):
        for fk in db.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
            _, _, parent, column, parent_column, _, on_delete, _ = fk
            if parent not in tables or on_delete not in {"CASCADE", "SET NULL"}:
                continue
            condition = (
                f"{_quote(column)} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {_quote(parent)} p "
                f"WHERE p.{_quote(parent_column)} = {_quote(table)}.{_quote(column)})"
            )
            statement = (
                f"DELETE FROM {_quote(table)} WHERE {condition}"
                if on_delete == "CASCADE"
                else f"UPDATE {_quote(table)} SET {_quote(column)}=NULL WHERE {condition}"
            )
            rules.append((table, statement))
    counts = {}
    while True:
        changed = 0
        for table, statement in rules:
            rows = db.execute(statement).rowcount
            if rows:
                counts[table] = counts.get(table, 0) + rows
                changed += rows
        if not changed:
            return counts
