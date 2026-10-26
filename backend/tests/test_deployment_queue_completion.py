"""Manual requests share admission protections and settle only proven identical work."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services.deploy_tasks import DeployTasks
from releasetracker.services.deployment_baseline import BaselineResult
from releasetracker.services.deployment_task_settlement import (
    settle_fulfilled_requests,
    reconcile_fulfilled_requests,
)
from releasetracker.services.deployment_plan import managed_markers
from releasetracker.versioning import SELECTION_RULES_VERSION
from test_deployment_admission import staged, task

pytestmark = pytest.mark.asyncio


async def managed(storage):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(claimed)
    await store.mark_applied(claimed, observed)
    await storage.tasks.finish(
        claimed, "succeeded", result={"mutation_started": True, "baseline_status": "verified"}
    )
    return store, replace(
        observed,
        markers=(managed_markers(await store.installation_id(), plan["target_id"], queued["id"]),),
    )


@pytest.mark.parametrize("manual", [True, False])
async def test_clean_forward_update_has_no_extra_manual_approval(storage, manual):
    store, observed = await managed(storage)
    await task(storage)
    claimed = await storage.tasks.claim("deploy")
    claimed["payload"]["manual"] = manual
    live = replace(
        observed,
        configuration_diff={
            "scope": "container_configuration",
            "lines": [{"operation": "+", "path": "/image", "value": "new-image"}],
            "truncated": False,
        },
    )
    # Configuration diff is planned; the managed baseline itself is unchanged.
    db = await storage._get_connection()
    await db.execute(
        "UPDATE tasks SET payload=? WHERE id=?", (json.dumps(claimed["payload"]), claimed["id"])
    )
    await db.commit()
    admitted = await store.stage(claimed, live)
    assert admitted["state"] == "approved"
    assert admitted["reason"] == "managed"
    assert admitted["approved_by"] == "managed_baseline"


@pytest.mark.parametrize("risk", ["drift", "downgrade", "unknown", "incomplete", "foreign"])
async def test_manual_update_keeps_risk_gates(storage, risk):
    store, observed = await managed(storage)
    await task(storage)
    claimed = await storage.tasks.claim("deploy")
    claimed["payload"]["manual"] = True
    if risk == "drift":
        live = replace(observed, configuration=observed.configuration | {"env": ["EXTRA=changed"]})
    elif risk == "foreign":
        live = replace(observed, markers=({"releasetracker.io/managed-by": "foreign"},))
    elif risk == "incomplete":
        live = replace(observed, configuration_diff={"truncated": True, "lines": []})
    else:
        live = replace(
            observed,
            recovery="helm_revision",
            configuration=observed.configuration
            | {"current_chart_version": "2.0.0" if risk == "downgrade" else None},
        )
        claimed["payload"]["targets"] = [{"chart_version": "1.0.0"}]
    db = await storage._get_connection()
    await db.execute(
        "UPDATE tasks SET payload=? WHERE id=?", (json.dumps(claimed["payload"]), claimed["id"])
    )
    await db.commit()
    plan = await store.stage(claimed, live)
    assert plan["state"] in {"pending", "blocked"}
    assert plan["reason"] != "manual_update_review"


def payload(**updates):
    return {
        "executor_id": 1,
        "manual": True,
        "config_identity": "same-config",
        "selection_revision": SELECTION_RULES_VERSION,
        "targets": [
            {
                "source_id": 1,
                "channel": "stable",
                "tracker_name": "demo",
                "target": ["1.2.0", "sha256:" + "a" * 64],
            }
        ],
        **updates,
    }


async def enqueue(storage, data, tag):
    return await storage.tasks.enqueue(
        kind="deploy",
        resource_key="isolated",
        dedupe_key=tag,
        target_label="demo",
        payload=data,
        trigger_mode="manual" if data["manual"] else "automatic",
    )


async def approve_source(storage, source):
    from test_deployment_admission import _seed_executor, evidence
    from releasetracker.storage.sqlite_deployment_admission import DeploymentAdmissionStore

    await _seed_executor(storage, source["payload"]["executor_id"])
    claimed = await storage.tasks.claim("deploy")
    assert claimed["id"] == source["id"]
    store = DeploymentAdmissionStore(storage)
    plan = await store.stage(claimed, evidence())
    await store.approve(source["id"], plan["id"], plan["fingerprint"], "admin")


@pytest.mark.parametrize(
    "difference",
    [
        None,
        "config",
        "digest",
        "alias",
        "chart_digest",
        "executor",
        "running",
        "mutated",
        "newer_write",
        "failed_source",
        "no_change_source",
        "missing_mutation",
        "missing_plan",
    ],
)
async def test_completed_automatic_target_settles_only_identical_unstarted_requests(
    storage, difference
):
    original = payload()
    older = await enqueue(storage, original, "older")
    source_payload = payload(manual=False)
    if difference == "config":
        source_payload["config_identity"] = "changed-config"
    if difference == "executor":
        source_payload["executor_id"] = 2
    if difference in {"digest", "alias", "chart_digest"}:
        key = {"digest": "target", "alias": "deploy_alias", "chart_digest": "chart_digest"}[
            difference
        ]
        source_payload["targets"][0][key] = (
            ["1.2.0", "sha256:" + "b" * 64] if difference == "digest" else "different"
        )
    source = await enqueue(storage, source_payload, "source")
    db = await storage._get_connection()
    await db.execute("UPDATE tasks SET approval_pending=1 WHERE id=?", (older["id"],))
    await db.commit()
    if difference != "missing_plan":
        await approve_source(storage, source)
    await db.execute(
        "UPDATE tasks SET state=?,result=? WHERE id=?",
        (
            (
                "failed"
                if difference == "failed_source"
                else "no_change" if difference == "no_change_source" else "succeeded"
            ),
            json.dumps({"mutation_started": difference != "missing_mutation"}),
            source["id"],
        ),
    )
    if difference == "running":
        await db.execute(
            "UPDATE tasks SET state='running',owner='active' WHERE id=?", (older["id"],)
        )
    if difference == "mutated":
        await db.execute(
            "UPDATE tasks SET result=? WHERE id=?",
            (json.dumps({"mutation_started": True}), older["id"]),
        )
    if difference == "newer_write":
        await db.commit()
        newer = await enqueue(storage, payload(), "newer")
        await db.execute(
            "UPDATE tasks SET state='failed',result=? WHERE id=?",
            (json.dumps({"mutation_started": True}), newer["id"]),
        )
    await db.commit()
    changed = await settle_fulfilled_requests(storage, source)
    saved = await storage.tasks.get(older["id"])
    if difference is None:
        assert changed == 1 and saved["state"] == "superseded"
        assert saved["result"]["fulfilled_by_task_id"] == source["id"]
        assert not saved["approval_pending"] and saved["attempts"] == 0
        assert await settle_fulfilled_requests(storage, source) == 0
    else:
        assert changed == 0 and saved["state"] != "superseded"


async def test_restart_reconciles_parked_request_without_redeployment(storage):
    old = await enqueue(storage, payload(), "old")
    newer = await enqueue(storage, payload(manual=False), "new")
    db = await storage._get_connection()
    await db.execute("UPDATE tasks SET approval_pending=1 WHERE id=?", (old["id"],))
    await db.commit()
    await approve_source(storage, newer)
    await db.execute(
        "UPDATE tasks SET state='succeeded',result=? WHERE id=?",
        (json.dumps({"mutation_started": True}), newer["id"]),
    )
    await db.commit()
    await reconcile_fulfilled_requests(storage)
    assert (await storage.tasks.get(old["id"]))["state"] == "superseded"


async def test_exhaustion_retains_specific_cause_without_leaking_exception(storage, monkeypatch):
    queued = await task(storage)
    db = await storage._get_connection()
    await db.execute(
        "UPDATE tasks SET state='succeeded',result=? WHERE id=?",
        (json.dumps({"baseline_attempts": 119}), queued["id"]),
    )
    await db.commit()
    handler = DeployTasks(storage, SimpleNamespace())
    await handler._baseline_receipt(
        queued["id"], BaselineResult("pending", "chart_receipt_unavailable")
    )
    saved = await storage.tasks.get(queued["id"])
    assert saved["state"] == "succeeded"
    assert saved["result"]["baseline_reason"] == "evidence_exhausted"
    assert saved["result"]["baseline_last_reason"] == "chart_receipt_unavailable"


@pytest.mark.parametrize(
    "error", [TimeoutError("secret-url"), RuntimeError("Authorization: secret")]
)
async def test_baseline_read_failures_store_only_finite_reason(storage, monkeypatch, error):
    store, observed = await managed(storage)
    queued = await task(storage)
    handler = DeployTasks(storage, SimpleNamespace())
    monkeypatch.setattr(handler, "identity", AsyncMock(return_value="synthetic-config"))
    monkeypatch.setattr(handler, "_collect_admission_evidence", AsyncMock(side_effect=error))
    await handler.refresh_admission_baseline(queued["id"], 1)
    saved = await storage.tasks.get(queued["id"])
    assert saved["result"]["baseline_stage"] == "inspection"
    assert saved["result"]["baseline_last_reason"] in {"inspection_timeout", "inspection_failed"}
    assert "secret" not in json.dumps(saved["result"])
