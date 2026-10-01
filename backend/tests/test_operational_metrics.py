import pytest

from releasetracker.services.operational_metrics import render_metrics


@pytest.mark.asyncio
async def test_metrics_use_finite_labels_and_preserve_hidden_tasks(storage):
    await storage.tasks.enqueue(
        kind="fetch",
        resource_key="secret-url",
        dedupe_key="secret-token",
        payload={"password": "never-expose"},
        target_label="private-name",
        trigger_mode="manual",
    )
    text = await render_metrics(storage)
    assert 'releasetracker_tasks{kind="fetch",state="queued"} 1' in text
    assert 'releasetracker_tasks{kind="deploy",state="needs_attention"} 0' in text
    assert "# TYPE releasetracker_tasks gauge" in text
    assert "never-expose" not in text and "secret" not in text and "private-name" not in text
    assert 'releasetracker_notification_oldest_pending_seconds{queue="executor"} 0' in text


@pytest.mark.asyncio
async def test_metrics_measure_attempt_duration_and_bounded_errors(storage):
    import time

    now = time.time()
    task = await storage.tasks.enqueue(
        kind="fetch",
        resource_key="source",
        dedupe_key="metric-test",
        payload={},
        target_label="source",
        trigger_mode="manual",
    )
    async with storage.tasks.transaction() as db:
        for index, code in enumerate(
            ["upstream_tls_failed", "security_validation_failed", "private-error-with-url"]
        ):
            await db.execute(
                "INSERT INTO task_attempts(task_id,attempt,started_at,finished_at,state,error_code,owner) VALUES (?,?,?,?,?,?,'metrics-test')",
                (task["id"], index + 1, now - 20, now - 10, "failed", code),
            )
    text = await render_metrics(storage)
    assert 'releasetracker_task_attempts_24h{kind="fetch"} 3' in text
    assert 'releasetracker_task_attempt_duration_seconds_24h{kind="fetch"} 30' in text
    assert 'releasetracker_task_errors_24h{kind="fetch",code="upstream_tls_failed"} 1' in text
    assert 'releasetracker_task_errors_24h{kind="fetch",code="other"} 1' in text
    assert "private-error-with-url" not in text


def test_metrics_require_dedicated_credential(client, monkeypatch):
    monkeypatch.delenv("RELEASETRACKER_METRICS_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 404
    token = "m" * 48
    monkeypatch.setenv("RELEASETRACKER_METRICS_TOKEN", token)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
