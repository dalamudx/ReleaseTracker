from datetime import datetime

import pytest

from releasetracker.storage.sqlite_retention import prune_fetch_runs


@pytest.mark.asyncio
async def test_prune_only_old_unreferenced_runs_in_batches(storage):
    db = await storage._get_connection()
    # Existing schema connections intentionally run without foreign key enforcement.
    assert (await (await db.execute("PRAGMA foreign_keys")).fetchone())[0] == 0
    ids = []
    for source, date in [
        (91, "2024-01-01"),
        (91, "2024-01-02"),
        (91, "2024-01-03"),
        (92, "2024-01-04"),
        (92, "2025-01-01"),
        (93, "2024-01-05"),
    ]:
        cursor = await db.execute(
            "INSERT INTO source_fetch_runs(tracker_source_id,trigger_mode,started_at,finished_at,status,created_at) "
            "VALUES (?,'manual',?,?,'success',?)",
            (source, date, date, date),
        )
        ids.append(cursor.lastrowid)
    # A permanent release-history anchor and a webhook receipt must survive.
    await db.execute(
        "INSERT INTO source_release_history(tracker_source_id,first_source_fetch_run_id,source_type,"
        "source_release_key,version,identity_key,name,tag_name,published_at,url,raw_payload,"
        "first_observed_at,created_at) VALUES (91,?,'github','x','1','x','x','x','2024','url',"
        "'{}','2024','2024')",
        (ids[0],),
    )
    await db.execute(
        "INSERT INTO source_refresh_requests(delivery_id,tracker_source_id,webhook_generation,"
        "source_identity,state,due_at,source_fetch_run_id) VALUES (99,92,1,'x','done',0,?)",
        (ids[3],),
    )
    await db.execute(
        "INSERT INTO source_release_run_observations(source_fetch_run_id,source_release_history_id,"
        "observed_at,created_at) VALUES (?,1,'2024','2024')",
        (ids[1],),
    )
    await db.commit()
    now = datetime(2025, 4, 1)
    assert await prune_fetch_runs(storage, days=90, batch=1, now=now) == 1
    assert await prune_fetch_runs(storage, days=90, batch=1, now=now) == 0
    remaining = [
        row[0] for row in await (await db.execute("SELECT id FROM source_fetch_runs")).fetchall()
    ]
    assert set(ids) - set(remaining) == {ids[1]}
    assert not await (
        await db.execute(
            "SELECT 1 FROM source_release_run_observations WHERE source_fetch_run_id=?", (ids[1],)
        )
    ).fetchone()
    assert (await (await db.execute("PRAGMA integrity_check")).fetchone())[0] == "ok"
