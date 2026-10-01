"""Conservative, bounded pruning of unreferenced fetch observations.

The historical first/last fetch pointers, webhook receipts, and the latest run
per source are audit anchors. Task trigger keys are intentionally never pruned.
"""

from __future__ import annotations

from datetime import datetime, timedelta


async def prune_fetch_runs(storage, *, days: int = 90, batch: int = 500, now=None) -> int:
    if days < 1 or not 1 <= batch <= 1000:
        raise ValueError("Invalid retention bounds")
    cutoff = ((now or datetime.now()) - timedelta(days=days)).isoformat()
    async with storage.tasks.transaction() as db:
        rows = await (
            await db.execute(
                """SELECT r.id FROM source_fetch_runs r
                   WHERE r.finished_at IS NOT NULL AND r.finished_at < ?
                     AND r.id != (SELECT MAX(id) FROM source_fetch_runs
                                  WHERE tracker_source_id=r.tracker_source_id)
                     AND NOT EXISTS (SELECT 1 FROM source_release_history h
                                     WHERE h.first_source_fetch_run_id=r.id)
                     AND NOT EXISTS (SELECT 1 FROM source_release_aliases a
                                     WHERE a.first_source_fetch_run_id=r.id
                                        OR a.last_source_fetch_run_id=r.id)
                     AND NOT EXISTS (SELECT 1 FROM source_refresh_requests w
                                     WHERE w.source_fetch_run_id=r.id)
                   ORDER BY r.id LIMIT ?""",
                (cutoff, batch),
            )
        ).fetchall()
        ids = [row[0] for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            # The application's SQLite connections do not enable foreign_keys;
            # remove these dependent observation rows explicitly.
            for table in (
                "source_release_run_observations",
                "source_release_alias_run_observations",
            ):
                await db.execute(
                    f"DELETE FROM {table} WHERE source_fetch_run_id IN ({placeholders})", ids
                )
            await db.execute(f"DELETE FROM source_fetch_runs WHERE id IN ({placeholders})", ids)
        return len(ids)
