"""Persistent single-owner Compose reservations. Call write() inside config transaction."""

import json

import aiosqlite


class ComposeOwnershipConflict(ValueError):
    pass


IMMUTABLE_TARGET_FIELDS = (
    "project",
    "working_dir",
    "config_files",
    "env_files",
    "profiles",
    "tool",
    "discovery_id",
)


async def get(storage, executor_id):
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    row = await (
        await db.execute("SELECT * FROM ssh_compose_ownership WHERE executor_id=?", (executor_id,))
    ).fetchone()
    return dict(row) if row else None


async def occupants(storage, runtime_key, project):
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            "SELECT o.*, e.name FROM ssh_compose_ownership o JOIN executors e ON e.id=o.executor_id WHERE o.runtime_key=? AND o.project=?",
            (runtime_key, project),
        )
    ).fetchall()
    return [dict(row) for row in rows]


async def busy(db, executor_id):
    row = await (
        await db.execute(
            """SELECT EXISTS(SELECT 1 FROM executor_run_history WHERE executor_id=? AND status IN ('queued','running'))
        OR EXISTS(SELECT 1 FROM executor_snapshots WHERE executor_id=? AND locked=1)
        OR EXISTS(SELECT 1 FROM executor_snapshot_claims WHERE executor_id=?)""",
            (executor_id, executor_id, executor_id),
        )
    ).fetchone()
    return bool(row[0])


async def validate_update(storage, db, executor_id, config):
    db.row_factory = aiosqlite.Row
    row = await (await db.execute("SELECT * FROM executors WHERE id=?", (executor_id,))).fetchone()
    if not row or (row["runtime_type"] != "ssh" and config.runtime_type != "ssh"):
        return
    old = json.loads(row["target_ref"])
    if (
        row["runtime_type"] != config.runtime_type
        or row["runtime_connection_id"] != config.runtime_connection_id
        or any(old.get(k) != config.target_ref.get(k) for k in IMMUTABLE_TARGET_FIELDS)
    ):
        raise ComposeOwnershipConflict(
            "SSH Compose project is fixed; delete the executor after recovery before binding another project"
        )
    if await busy(db, executor_id):
        claim = await get(storage, executor_id)
        saved = await storage.get_executor_config(executor_id)
        active = await (
            await db.execute(
                "SELECT 1 FROM executor_run_history WHERE executor_id=? AND status IN ('queued','running')",
                (executor_id,),
            )
        ).fetchone()
        # Allow only identity confirmation of an unchanged legacy configuration, so
        # an old locked snapshot can still be recovered after the migration.
        confirmation_only = (
            not active
            and claim
            and not claim["runtime_key"]
            and config._ssh_ownership
            and saved.model_dump(exclude={"id"}) == config.model_dump(exclude={"id"})
        )
        if not confirmation_only:
            raise ComposeOwnershipConflict("SSH Compose executor is running or requires recovery")


async def write(storage, db, executor_id, config):
    if config.runtime_type != "ssh":
        return
    proof = config._ssh_ownership
    existing = await get(storage, executor_id)
    if existing and existing["runtime_key"]:
        if (
            proof is None
            or proof["runtime_key"] != existing["runtime_key"]
            or proof["working_dir"] != existing["working_dir"]
        ):
            raise ComposeOwnershipConflict("SSH Compose runtime identity changed or is unverified")
    key = proof["runtime_key"] if proof else None
    directory = proof["working_dir"] if proof else config.target_ref["working_dir"]
    try:
        await db.execute(
            """INSERT INTO ssh_compose_ownership(executor_id, runtime_key, project, working_dir) VALUES(?,?,?,?)
            ON CONFLICT(executor_id) DO UPDATE SET runtime_key=excluded.runtime_key, working_dir=excluded.working_dir""",
            (executor_id, key, config.target_ref["project"], directory),
        )
    except aiosqlite.IntegrityError:
        raise ComposeOwnershipConflict(
            "Compose project is already managed by another executor"
        ) from None


async def assert_owner(storage, executor, proof, *, recovery=False):
    row = await get(storage, executor.id)
    if not row or not row["runtime_key"]:
        raise ComposeOwnershipConflict(
            "SSH Compose project ownership requires verification; edit and save this executor"
        )
    if any(row[k] != proof[k] for k in ("runtime_key", "project", "working_dir")):
        raise ComposeOwnershipConflict("SSH Compose runtime or project identity changed")
    if not recovery:
        db = await storage._get_connection()
        pending = await (
            await db.execute(
                "SELECT 1 FROM executor_snapshots WHERE executor_id=? AND locked=1 LIMIT 1",
                (executor.id,),
            )
        ).fetchone()
        if pending:
            raise ComposeOwnershipConflict(
                "SSH Compose project requires recovery before another update"
            )
    # A queued task must not run an outdated configuration after an edit.
    saved = await storage.get_executor_config(executor.id)
    if (
        saved is None
        or saved.runtime_connection_id != executor.runtime_connection_id
        or saved.target_ref != executor.target_ref
        or [binding.model_dump() for binding in saved.service_bindings]
        != [binding.model_dump() for binding in executor.service_bindings]
    ):
        raise ComposeOwnershipConflict(
            "SSH Compose executor configuration changed; retry with current configuration"
        )
