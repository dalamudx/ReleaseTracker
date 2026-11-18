import pytest
from fastapi import HTTPException
from starlette.requests import Request

from releasetracker.services.inbound_policy import (
    CORS_ORIGINS_SETTING,
    DEFAULT_POLICY,
    FORWARDED_ALLOW_IPS_SETTING,
    InboundPolicy,
    build_policy,
    load_policy,
)


@pytest.fixture(autouse=True)
def restore_inbound_policy():
    """Every test leaves the shared application with a fresh-install policy."""
    from releasetracker.main import app

    try:
        yield app
    finally:
        try:
            del app.state.inbound_policy
        except (AttributeError, KeyError):
            pass


def _browser_request(app, origin):
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/tasks",
            "raw_path": b"/api/tasks",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"x-releasetracker-browser", b"1"),
                (b"origin", origin.encode()),
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }
    )


def test_unset_settings_fall_back_to_documented_defaults():
    assert build_policy({}) == DEFAULT_POLICY
    assert DEFAULT_POLICY.trusted_proxies == ("127.0.0.1",)
    assert DEFAULT_POLICY.cors_origins == ()


def test_saved_values_are_parsed_and_unreadable_entries_stay_closed():
    policy = build_policy(
        {
            CORS_ORIGINS_SETTING: "https://ui.example.test\nhttps://ui.example.test/",
            FORWARDED_ALLOW_IPS_SETTING: "10.0.0.0/8\n192.0.2.1",
        }
    )
    assert policy.cors_origins == ("https://ui.example.test",)
    assert policy.trusted_proxies == ("10.0.0.0/8", "192.0.2.1")

    corrupted = build_policy(
        {CORS_ORIGINS_SETTING: "https://*.evil.test", FORWARDED_ALLOW_IPS_SETTING: "not-an-ip"}
    )
    assert corrupted == DEFAULT_POLICY


@pytest.mark.asyncio
async def test_load_policy_reads_saved_values(storage):
    authed_storage = storage
    await authed_storage.set_setting(CORS_ORIGINS_SETTING, "https://ui.example.test")
    await authed_storage.set_setting(FORWARDED_ALLOW_IPS_SETTING, "*")
    policy = await load_policy(authed_storage)
    assert policy.cors_origins == ("https://ui.example.test",)
    assert policy.trusted_proxies == ("*",)


@pytest.mark.asyncio
async def test_saved_origins_drive_cors_and_browser_session_checks(authed_client, storage):
    from releasetracker.main import app
    from releasetracker.services.browser_sessions import require_browser_request

    assert (
        authed_client.post(
            "/api/settings",
            json={"key": CORS_ORIGINS_SETTING, "value": "https://ui.example.test"},
        ).status_code
        == 200
    )
    preflight = authed_client.options(
        "/api/tasks",
        headers={"Origin": "https://ui.example.test", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://ui.example.test"
    refused = authed_client.options(
        "/api/tasks",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert refused.status_code == 400

    await require_browser_request(_browser_request(app, "https://ui.example.test"), storage)
    with pytest.raises(HTTPException) as rejected:
        await require_browser_request(_browser_request(app, "https://evil.test"), storage)
    assert rejected.value.status_code == 403

    # A restart reloads the same saved policy instead of the retired environment.
    del app.state.inbound_policy
    app.state.inbound_policy = await load_policy(storage)
    assert (
        authed_client.get("/", headers={"Origin": "https://ui.example.test"}).headers[
            "access-control-allow-origin"
        ]
        == "https://ui.example.test"
    )


@pytest.mark.asyncio
async def test_saving_trusted_proxies_updates_the_running_policy(authed_client, storage):
    from releasetracker.main import app

    await storage.set_setting(CORS_ORIGINS_SETTING, "https://ui.example.test")
    assert (
        authed_client.post(
            "/api/settings",
            json={"key": FORWARDED_ALLOW_IPS_SETTING, "value": "10.0.0.0/8\n192.0.2.1"},
        ).status_code
        == 200
    )
    assert app.state.inbound_policy == InboundPolicy(
        cors_origins=("https://ui.example.test",),
        trusted_proxies=("10.0.0.0/8", "192.0.2.1"),
    )

    assert (
        authed_client.post(
            "/api/settings",
            json={"key": FORWARDED_ALLOW_IPS_SETTING, "value": "not-an-ip"},
        ).status_code
        == 400
    )
    assert app.state.inbound_policy.trusted_proxies == ("10.0.0.0/8", "192.0.2.1")

    assert authed_client.delete(f"/api/settings/{FORWARDED_ALLOW_IPS_SETTING}").status_code == 200
    assert app.state.inbound_policy.trusted_proxies == ("127.0.0.1",)


async def test_second_worker_observes_grants_and_revocations(
    authed_client, storage, system_key_manager
):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from releasetracker.services.http_security import configure_http_security
    from releasetracker.storage.sqlite import SQLiteStorage

    reader = FastAPI()
    reader.state.storage = SQLiteStorage(storage.db_path, system_key_manager=system_key_manager)
    configure_http_security(reader)

    @reader.get("/peer")
    async def peer(request: Request):
        return request.url.scheme

    origin = "https://ui.example.test"
    headers = {"Origin": origin, "X-Forwarded-Proto": "https"}
    try:
        with TestClient(reader, client=("192.0.2.1", 50000)) as client:
            for granted in (True, False):
                for key, value in (
                    (CORS_ORIGINS_SETTING, origin),
                    (FORWARDED_ALLOW_IPS_SETTING, "192.0.2.1"),
                ):
                    response = authed_client.post(
                        "/api/settings", json={"key": key, "value": value if granted else ""}
                    )
                    assert response.status_code == 200
                response = client.get("/peer", headers=headers)
                assert response.json() == ("https" if granted else "http")
                assert response.headers.get("access-control-allow-origin") == (
                    origin if granted else None
                )
    finally:
        await reader.state.storage.close()
