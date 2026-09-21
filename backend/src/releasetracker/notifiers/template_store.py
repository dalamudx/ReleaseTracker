"""Independent short transactions for reusable notification template definitions."""

import json
from datetime import datetime, timezone

import aiosqlite

from .templates import builtin


def decode(row):
    return {**dict(row), "translations": json.loads(row["translations"])}


async def list_templates(storage):
    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT t.*, (SELECT COUNT(*) FROM notifiers n WHERE n.template_id=t.id) AS references_count FROM notification_templates t ORDER BY name"
            )
        ).fetchall()
        return [decode(row) for row in rows]


async def get_template(storage, template_id):
    if template_id is None:
        return builtin()
    if type(template_id) is not int or template_id <= 0:
        raise ValueError("notification_template_not_found")
    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM notification_templates WHERE id=?", (template_id,))
        ).fetchone()
        if row is None:
            raise ValueError("notification_template_not_found")
        return decode(row)


async def save_template(storage, data, template_id=None):
    values = (
        data["name"],
        data["title"],
        data["body"],
        json.dumps(data["translations"], ensure_ascii=False),
        datetime.now(timezone.utc).isoformat(),
    )
    async with aiosqlite.connect(storage.db_path) as db:
        try:
            if template_id is None:
                cursor = await db.execute(
                    "INSERT INTO notification_templates(name,title,body,translations,updated_at) VALUES (?,?,?,?,?)",
                    values,
                )
                template_id = cursor.lastrowid
            else:
                cursor = await db.execute(
                    "UPDATE notification_templates SET name=?,title=?,body=?,translations=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?",
                    (*values, template_id, data["revision"]),
                )
                if not cursor.rowcount:
                    raise ValueError("notification_template_conflict")
            await db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError("notification_template_name_exists") from None
    return await get_template(storage, template_id)


async def delete_template(storage, template_id):
    async with aiosqlite.connect(storage.db_path) as db:
        try:
            cursor = await db.execute(
                "DELETE FROM notification_templates WHERE id=?", (template_id,)
            )
            if not cursor.rowcount:
                raise ValueError("notification_template_not_found")
            await db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError("notification_template_in_use") from None
