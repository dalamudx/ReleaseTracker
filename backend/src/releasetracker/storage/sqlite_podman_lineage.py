"""Transactional Podman managed-target lineage storage."""

from __future__ import annotations

import json
import time
from typing import Any

from ..services.podman_target_lineage import normalize_members, validate_snapshot_binding


class LineageConflict(ValueError):
    pass


class PodmanLineageStore:
    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        result = dict(row)
        result["members"] = json.loads(result["members"])
        return result

    async def get(self, executor_id: int):
        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                "SELECT * FROM podman_target_lineages WHERE executor_id=?", (executor_id,)
            )
        ).fetchone()
        return self._decode(row)

    async def establish(
        self,
        *,
        executor_id: int,
        target_id: str,
        mode: str,
        target_fingerprint: str,
        members: list[dict[str, Any]],
        now=None,
    ):
        members = normalize_members(members)
        now = time.time() if now is None else now
        async with self.storage.tasks.transaction() as db:
            existing = await (
                await db.execute(
                    "SELECT * FROM podman_target_lineages WHERE executor_id=?", (executor_id,)
                )
            ).fetchone()
            occupied = await (
                await db.execute(
                    "SELECT executor_id FROM podman_target_lineages WHERE target_id=?", (target_id,)
                )
            ).fetchone()
            if occupied and occupied["executor_id"] != executor_id:
                raise LineageConflict("Podman managed target already belongs to another executor")
            if existing:
                current = self._decode(existing)
                if (
                    current["target_id"] != target_id
                    or current["mode"] != mode
                    or current["target_fingerprint"] != target_fingerprint
                ):
                    raise LineageConflict("Podman managed target identity changed")
                if current["members"] != members:
                    raise LineageConflict(
                        "Podman target lineage already established with different members"
                    )
                return current
            await db.execute(
                "INSERT INTO podman_target_lineages(executor_id,target_id,mode,target_fingerprint,generation,members,created_at,updated_at) VALUES (?,?,?,?,1,?,?,?)",
                (
                    executor_id,
                    target_id,
                    mode,
                    target_fingerprint,
                    json.dumps(members, sort_keys=True),
                    now,
                    now,
                ),
            )
        return await self.get(executor_id)

    async def validate_snapshot(self, executor_id: int, snapshot: dict[str, Any]):
        current = await self.get(executor_id)
        if current is None:
            raise LineageConflict("Podman managed target has no recorded lineage")
        binding = validate_snapshot_binding(snapshot, current)
        generation = binding["generation"]
        if generation == current["generation"]:
            expected = current["members"]
        else:
            db = await self.storage._get_connection()
            row = await (
                await db.execute(
                    "SELECT old_members FROM podman_target_lineage_transitions WHERE executor_id=? AND from_generation=?",
                    (executor_id, generation),
                )
            ).fetchone()
            if row is None:
                raise LineageConflict("snapshot Podman lineage generation was not recorded")
            expected = json.loads(row["old_members"])
        if binding["members"] != expected:
            raise LineageConflict("snapshot Podman lineage members were not recorded")
        return current, binding

    async def transition(
        self,
        *,
        executor_id: int,
        expected_generation: int,
        expected_members: list[dict[str, Any]],
        new_members: list[dict[str, Any]],
        task_id: int | None = None,
        executor_run_id: int | None = None,
        now=None,
    ):
        if (task_id is None) == (executor_run_id is None):
            raise ValueError("exactly one lineage transition owner is required")
        old = normalize_members(expected_members)
        new = normalize_members(new_members)
        now = time.time() if now is None else now
        async with self.storage.tasks.transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM podman_target_lineages WHERE executor_id=?", (executor_id,)
                )
            ).fetchone()
            current = self._decode(row)
            if (
                current is None
                or current["generation"] != expected_generation
                or current["members"] != old
            ):
                raise LineageConflict("Podman target lineage changed")
            next_generation = expected_generation + 1
            try:
                await db.execute(
                    "INSERT INTO podman_target_lineage_transitions(executor_id,task_id,executor_run_id,from_generation,to_generation,old_members,new_members,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        executor_id,
                        task_id,
                        executor_run_id,
                        expected_generation,
                        next_generation,
                        json.dumps(old, sort_keys=True),
                        json.dumps(new, sort_keys=True),
                        now,
                    ),
                )
                await db.execute(
                    "UPDATE podman_target_lineages SET generation=?,members=?,updated_at=? WHERE executor_id=? AND generation=?",
                    (
                        next_generation,
                        json.dumps(new, sort_keys=True),
                        now,
                        executor_id,
                        expected_generation,
                    ),
                )
            except Exception as exc:
                raise LineageConflict("Podman target lineage transition conflicted") from exc
        return await self.get(executor_id)
