import pytest

from releasetracker.notifiers.templates import builtin

pytestmark = pytest.mark.asyncio


async def test_admin_template_crud_preview_and_binding(authed_client):
    client = authed_client
    catalog = client.get("/api/notification-templates")
    assert catalog.status_code == 200
    assert len(catalog.json()["events"]) == 10
    template = builtin() | {"name": "Example template"}
    created = client.post("/api/notification-templates", json=template)
    assert created.status_code == 201, created.text
    template = created.json()
    for language in ("zh", "en"):
        preview = client.post(
            "/api/notification-templates/preview",
            json=template
            | {"event": "executor_run_failed", "language": language, "scenario": "timeout"},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["locale"] == language
    bad = client.post(
        "/api/notification-templates/preview", json=template | {"body": "{{ missing.field }}"}
    )
    assert bad.status_code == 422
    channel = client.post(
        "/api/notifiers",
        json={
            "name": "Example channel",
            "url": "https://example.test/hook",
            "template_id": template["id"],
        },
    )
    assert channel.status_code == 201
    assert client.delete(f"/api/notification-templates/{template['id']}").status_code == 409
    assert (
        client.put(
            f"/api/notification-templates/{template['id']}",
            json=template | {"body": "{{ subject.name }}"},
        ).status_code
        == 200
    )
    assert (
        client.put(f"/api/notification-templates/{template['id']}", json=template).status_code
        == 409
    )
    assert (
        client.put(f"/api/notifiers/{channel.json()['id']}", json={"template_id": None}).status_code
        == 200
    )
    assert client.delete(f"/api/notification-templates/{template['id']}").status_code == 200
    assert (
        client.post(
            "/api/notifiers",
            json={"name": "Invalid", "url": "https://example.test/hook", "template_id": 9999},
        ).status_code
        == 400
    )


async def test_templates_require_admin(non_admin_client):
    for method, path, data in [
        ("get", "/api/notification-templates", None),
        ("post", "/api/notification-templates/preview", builtin()),
    ]:
        response = getattr(non_admin_client, method)(path, **({"json": data} if data else {}))
        assert response.status_code in (401, 403)
