"""Prune obsolete merged audit rows without deleting task or canonical references."""
from __future__ import annotations


async def prune_merged(storage, tracker_id, task_ids, task_artifacts):
    db = await storage._get_connection()
    rows = await (await db.execute("SELECT id,digest,merged_into_tracker_release_history_id FROM tracker_release_history WHERE aggregate_tracker_id=? AND merged_into_tracker_release_history_id IS NOT NULL", (tracker_id,))).fetchall()
    protected = {row[0] for row in rows if row[0] in task_ids or row[1] in task_artifacts}
    parents = {row[0]: row[2] for row in rows}
    pending = list(protected)
    while pending:
        parent = parents.get(pending.pop())
        if parent is not None and parent not in protected:
            protected.add(parent)
            pending.append(parent)
    remove = [row[0] for row in rows if row[0] not in protected]
    if not remove:
        return 0, len(protected.intersection(parents))
    placeholders = ",".join("?" for _ in remove)
    for table, column in (("tracker_current_releases", "tracker_release_history_id"), ("tracker_release_history_sources", "tracker_release_history_id")):
        await db.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", remove)
    await db.execute(f"DELETE FROM tracker_release_history WHERE id IN ({placeholders})", remove)
    return len(remove), len(protected.intersection(parents))
