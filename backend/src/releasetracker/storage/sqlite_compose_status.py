"""Non-network ownership state for executor lists."""

from .sqlite_compose_ownership import get


async def public_status(storage, executor):
    if executor.runtime_type != "ssh":
        return None
    db = await storage._get_connection()
    pending = await (
        await db.execute(
            "SELECT 1 FROM executor_snapshots WHERE executor_id=? AND locked=1 LIMIT 1",
            (executor.id,),
        )
    ).fetchone()
    if pending:
        return "recovery_required"
    claim = await get(storage, executor.id)
    if claim and claim["runtime_key"]:
        return "verified"
    duplicates = await (
        await db.execute(
            """SELECT 1 FROM executors WHERE runtime_type='ssh' AND id != ? AND runtime_connection_id=?
        AND json_extract(target_ref, '$.project')=? LIMIT 1""",
            (executor.id, executor.runtime_connection_id, executor.target_ref.get("project")),
        )
    ).fetchone()
    return "conflict" if duplicates else "verification_required"
