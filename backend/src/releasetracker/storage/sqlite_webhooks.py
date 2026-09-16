"""Durable webhook inbox and refresh requests, sharing SQLiteStorage connections."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager

from .sqlite_aggregate_trackers import row_to_tracker_source


def identity(source) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "type": source.source_type,
                "config": source.source_config,
                "credential": source.credential_name,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class WebhookStore:
    def __init__(self, storage):
        self.storage = storage

    @asynccontextmanager
    async def transaction(self):
        db = await self.storage._get_connection()
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    async def source(self, source_id):
        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                """SELECT s.*, t.name AS tracker_name,
            t.enabled AS tracker_enabled FROM aggregate_tracker_sources s
            JOIN aggregate_trackers t ON t.id=s.aggregate_tracker_id WHERE s.id=?""",
                (source_id,),
            )
        ).fetchone()
        return (
            (row_to_tracker_source(row), row["tracker_name"], bool(row["tracker_enabled"]))
            if row
            else None
        )

    async def get(self, hook_id):
        db = await self.storage._get_connection()
        row = await (
            await db.execute("SELECT * FROM repository_webhooks WHERE id=?", (hook_id,))
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["config"] = json.loads(result["config"])
        return result

    async def list(self):
        db = await self.storage._get_connection()
        rows = await (
            await db.execute("""SELECT h.*, t.name AS tracker_name, s.source_key
            FROM repository_webhooks h JOIN aggregate_tracker_sources s ON s.id=h.tracker_source_id
            JOIN aggregate_trackers t ON t.id=s.aggregate_tracker_id ORDER BY h.created_at DESC""")
        ).fetchall()
        return [dict(row) | {"config": json.loads(row["config"])} for row in rows]

    async def save(self, data, hook_id=None):
        context = await self.source(data.tracker_source_id)
        if not context:
            raise ValueError("Source not found")
        source, _, _ = context
        expected_type = "gitea" if data.provider == "forgejo" else data.provider
        if source.source_type != expected_type:
            raise ValueError("Provider does not match the Git source")
        for target_id in data.linked_source_ids:
            target = await self.source(target_id)
            if (
                not target
                or target[0].aggregate_tracker_id != source.aggregate_tracker_id
                or not target[0].enabled
                or target[0].source_type not in {"container", "helm"}
            ):
                raise ValueError(
                    "Linked sources must be enabled container or Helm sources of this tracker"
                )
        existing = await self.get(hook_id) if hook_id else None
        if hook_id and not existing:
            raise ValueError("Webhook not found")
        if existing and existing["tracker_source_id"] != source.id:
            raise ValueError("A webhook cannot be moved to another source")
        if (
            existing
            and (existing["provider"], existing["auth_mode"]) != (data.provider, data.auth_mode)
            and not data.secret
        ):
            raise ValueError("Provide a new secret when changing authentication")
        if not existing and not data.secret:
            raise ValueError("Secret is required")
        if data.auth_mode == "gitlab_signing" and data.secret:
            import base64

            try:
                key = base64.b64decode(data.secret.removeprefix("whsec_"), validate=True)
                if len(key) < 16:
                    raise ValueError()
            except ValueError:
                raise ValueError("Invalid GitLab signing token") from None
        now = time.time()
        config = data.model_dump(
            exclude={"secret", "provider", "enabled", "auth_mode", "tracker_source_id"}
        )
        async with self.storage.encryption_rotation_lock:
            async with self.transaction() as db:
                encrypted = (
                    self.storage._encrypt(data.secret) if data.secret else existing["secret"]
                )
                if existing:
                    await db.execute(
                        """UPDATE repository_webhooks SET provider=?,enabled=?,auth_mode=?,
                        secret=?,config=?,source_identity=?,generation=generation+1,updated_at=? WHERE id=?""",
                        (
                            data.provider,
                            int(data.enabled),
                            data.auth_mode,
                            encrypted,
                            json.dumps(config),
                            identity(source),
                            now,
                            hook_id,
                        ),
                    )
                    await db.execute(
                        """UPDATE source_refresh_requests SET state='ignored',reason='configuration_changed'
                        WHERE delivery_id IN (SELECT id FROM webhook_deliveries WHERE webhook_id=?)
                        AND state IN ('pending','deferred')""",
                        (hook_id,),
                    )
                else:
                    hook_id = secrets.token_urlsafe(24)
                    await db.execute(
                        """INSERT INTO repository_webhooks
                        (id,tracker_source_id,provider,enabled,auth_mode,secret,config,source_identity,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            hook_id,
                            source.id,
                            data.provider,
                            int(data.enabled),
                            data.auth_mode,
                            encrypted,
                            json.dumps(config),
                            identity(source),
                            now,
                            now,
                        ),
                    )
        return await self.get(hook_id)

    async def delete(self, hook_id):
        async with self.transaction() as db:
            # Explicit child cleanup also covers legacy connections with foreign_keys disabled.
            await db.execute(
                "DELETE FROM source_refresh_requests WHERE delivery_id IN (SELECT id FROM webhook_deliveries WHERE webhook_id=?)",
                (hook_id,),
            )
            await db.execute("DELETE FROM webhook_deliveries WHERE webhook_id=?", (hook_id,))
            await db.execute("DELETE FROM repository_webhooks WHERE id=?", (hook_id,))

    async def receive(self, hook, key, body_hash, event, reason, now=None):
        now = time.time() if now is None else now
        targets = []
        if not reason:
            ids = [hook["tracker_source_id"]]
            if event.kind == "workflow":
                ids += hook["config"]["linked_source_ids"]
            for source_id in ids:
                context = await self.source(source_id)
                if context and context[0].enabled and context[2]:
                    targets.append((source_id, identity(context[0])))
            if not targets:
                reason = "source_disabled"
        async with self.transaction() as db:
            current = await (
                await db.execute(
                    "SELECT generation,enabled FROM repository_webhooks WHERE id=?", (hook["id"],)
                )
            ).fetchone()
            if not current or current["generation"] != hook["generation"] or not current["enabled"]:
                raise ValueError("Webhook configuration changed")
            # Authenticate first. A changed body under the same delivery id is a conflict.
            duplicate = await (
                await db.execute(
                    "SELECT * FROM webhook_deliveries WHERE webhook_id=? AND delivery_key=?",
                    (hook["id"], key),
                )
            ).fetchone()
            if duplicate and duplicate["payload_hash"] != body_hash:
                raise ValueError("Delivery identity conflict")
            if not duplicate:
                duplicate = await (
                    await db.execute(
                        """SELECT * FROM webhook_deliveries WHERE webhook_id=? AND payload_hash=?
                    AND received_at>=? ORDER BY id DESC LIMIT 1""",
                        (hook["id"], body_hash, now - 30),
                    )
                ).fetchone()
            if duplicate:
                await db.execute(
                    "UPDATE webhook_deliveries SET duplicates=duplicates+1 WHERE id=?",
                    (duplicate["id"],),
                )
                return {"id": duplicate["id"], "state": "duplicate"}
            pending = await (
                await db.execute(
                    """SELECT count(*) FROM source_refresh_requests r JOIN webhook_deliveries d ON d.id=r.delivery_id
                WHERE d.webhook_id=? AND r.state IN ('pending','running','deferred')""",
                    (hook["id"],),
                )
            ).fetchone()
            if pending[0] >= 1000:
                raise OverflowError("Webhook queue is full")
            # No-id payload dedupe is deliberately short-lived, not a permanent event ban.
            if key.startswith("body:"):
                key += ":" + secrets.token_hex(8)
            cursor = await db.execute(
                """INSERT INTO webhook_deliveries
                (webhook_id,delivery_key,payload_hash,summary,state,reason,received_at) VALUES (?,?,?,?,?,?,?)""",
                (
                    hook["id"],
                    key,
                    body_hash,
                    json.dumps(event.summary()),
                    "ignored" if reason else "queued",
                    reason,
                    now,
                ),
            )
            delivery_id = cursor.lastrowid
            await db.executemany(
                """INSERT INTO source_refresh_requests
                (delivery_id,tracker_source_id,webhook_generation,source_identity,due_at) VALUES (?,?,?,?,?)""",
                [
                    (delivery_id, sid, hook["generation"], ident, now + 5)
                    for sid, ident in targets
                    if not reason
                ],
            )
        return {"id": delivery_id, "state": "ignored" if reason else "queued", "reason": reason}

    async def deliveries(self, hook_id, limit=30):
        db = await self.storage._get_connection()
        rows = await (
            await db.execute(
                "SELECT * FROM webhook_deliveries WHERE webhook_id=? ORDER BY id DESC LIMIT ?",
                (hook_id, limit),
            )
        ).fetchall()
        result = []
        for row in rows:
            item = {k: row[k] for k in ("id", "state", "reason", "duplicates", "received_at")}
            item["summary"] = json.loads(row["summary"])
            requests = [
                dict(r)
                for r in await (
                    await db.execute(
                        """SELECT r.tracker_source_id,s.source_key,r.state,r.reason,r.due_at,
                               r.attempts,r.source_fetch_run_id
                FROM source_refresh_requests r
                JOIN aggregate_tracker_sources s ON s.id=r.tracker_source_id
                WHERE r.delivery_id=? ORDER BY r.tracker_source_id""",
                        (row["id"],),
                    )
                ).fetchall()
            ]
            item["requests"] = requests
            if requests:
                states = {r["state"] for r in requests}
                item["state"] = next(
                    (
                        s
                        for s in (
                            "running",
                            "deferred",
                            "pending",
                            "failed",
                            "completed",
                            "no_change",
                            "ignored",
                        )
                        if s in states
                    ),
                    "completed",
                )
            result.append(item)
        return result

    async def last_source_run_at(self, source_ids):
        if not source_ids:
            return None
        db = await self.storage._get_connection()
        placeholders = ",".join("?" for _ in source_ids)
        row = await (
            await db.execute(
                f"SELECT MAX(started_at) FROM source_fetch_runs WHERE tracker_source_id IN ({placeholders})",
                tuple(source_ids),
            )
        ).fetchone()
        return row[0] if row else None

    async def source_revision_tokens(self, source_ids):
        db = await self.storage._get_connection()
        result = {}
        for source_id in source_ids:
            history = await (
                await db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM source_release_history WHERE tracker_source_id=?",
                    (source_id,),
                )
            ).fetchone()
            aliases = await (
                await db.execute(
                    """WITH ranked AS (
                        SELECT sra.normalized_alias,sra.source_release_history_id,srh.digest,
                               ROW_NUMBER() OVER (
                                   PARTITION BY sra.normalized_alias
                                   ORDER BY sra.last_observed_at DESC,sra.id DESC
                               ) AS rank
                        FROM source_release_aliases sra
                        JOIN source_release_history srh ON srh.id=sra.source_release_history_id
                        WHERE sra.tracker_source_id=?
                    )
                    SELECT normalized_alias,source_release_history_id,COALESCE(digest,'')
                    FROM ranked WHERE rank=1 ORDER BY normalized_alias""",
                    (source_id,),
                )
            ).fetchall()
            result[source_id] = (history[0], tuple(tuple(row) for row in aliases))
        return result

    async def recover_interrupted_requests(self, now=None):
        """Release work owned by the previous single-instance process."""
        now = time.time() if now is None else now
        async with self.transaction() as db:
            cursor = await db.execute(
                """UPDATE source_refresh_requests
                SET state='deferred',due_at=?,lease_until=NULL,reason='worker_restarted'
                WHERE state='running'""",
                (now,),
            )
        return max(cursor.rowcount, 0)

    async def claim(self, now=None):
        now = time.time() if now is None else now
        async with self.transaction() as db:
            await db.execute(
                "UPDATE source_refresh_requests SET state='pending',lease_until=NULL WHERE state='running' AND lease_until<?",
                (now,),
            )
            row = await (
                await db.execute(
                    """SELECT s.aggregate_tracker_id FROM source_refresh_requests r
                JOIN aggregate_tracker_sources s ON s.id=r.tracker_source_id
                WHERE r.state IN ('pending','deferred') AND r.due_at<=? ORDER BY r.due_at,r.id LIMIT 1""",
                    (now,),
                )
            ).fetchone()
            if not row:
                return []
            rows = await (
                await db.execute(
                    """SELECT r.*,d.webhook_id,d.summary,h.generation,h.enabled,
                t.name AS tracker_name FROM source_refresh_requests r
                JOIN webhook_deliveries d ON d.id=r.delivery_id JOIN repository_webhooks h ON h.id=d.webhook_id
                JOIN aggregate_tracker_sources s ON s.id=r.tracker_source_id
                JOIN aggregate_trackers t ON t.id=s.aggregate_tracker_id
                WHERE s.aggregate_tracker_id=? AND r.state IN ('pending','deferred') AND r.due_at<=?
                ORDER BY r.id LIMIT 1000""",
                    (row[0], now),
                )
            ).fetchall()
            await db.executemany(
                "UPDATE source_refresh_requests SET state='running',lease_until=? WHERE id=?",
                [(now + 600, r["id"]) for r in rows],
            )
        return [dict(row) for row in rows]

    async def finish(self, requests, state, reason="", due_at=None, run_ids=None, attempt=False):
        if not requests:
            return
        async with self.transaction() as db:
            await db.executemany(
                """UPDATE source_refresh_requests SET state=?,reason=?,due_at=?,lease_until=NULL,
                attempts=attempts+?,source_fetch_run_id=COALESCE(?,source_fetch_run_id) WHERE id=?""",
                [
                    (
                        state,
                        reason[:200],
                        due_at or time.time(),
                        int(attempt),
                        (run_ids or {}).get(r["tracker_source_id"]),
                        r["id"],
                    )
                    for r in requests
                ],
            )

    async def cleanup(self):
        async with self.transaction() as db:
            # Keep active requests even past retention. Bound diagnostic retention to seven days.
            rows = await (
                await db.execute(
                    """SELECT d.id FROM webhook_deliveries d WHERE received_at<?
                AND NOT EXISTS (SELECT 1 FROM source_refresh_requests r WHERE r.delivery_id=d.id AND r.state IN ('pending','running','deferred')) LIMIT 1000""",
                    (time.time() - 7 * 86400,),
                )
            ).fetchall()
            await db.executemany(
                "DELETE FROM source_refresh_requests WHERE delivery_id=?", [(r[0],) for r in rows]
            )
            await db.executemany(
                "DELETE FROM webhook_deliveries WHERE id=?", [(r[0],) for r in rows]
            )
