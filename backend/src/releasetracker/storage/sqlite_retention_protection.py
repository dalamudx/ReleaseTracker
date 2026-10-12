"""Keep audit records referenced by unsettled deployment and recovery tasks."""
from __future__ import annotations

import json


async def protected_artifacts(storage) -> tuple[set[int], set[str]]:
    db = await storage._get_connection()
    rows = await (await db.execute("SELECT payload,result FROM tasks WHERE kind IN ('deploy','recover') AND state NOT IN ('succeeded','no_change','skipped','failed','cancelled','superseded')")).fetchall()
    ids: set[int] = set()
    artifacts: set[str] = set()

    def visit(value, key=None):
        if isinstance(value, dict):
            for name, item in value.items():
                visit(item, name)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, int) and key == "tracker_release_history_id":
            ids.add(value)
        elif isinstance(value, str):
            artifacts.add(value)
            if "@sha256:" in value:
                artifacts.add(value.split("@", 1)[1])

    for row in rows:
        for payload in row:
            if payload:
                visit(json.loads(payload))
    return ids, artifacts
