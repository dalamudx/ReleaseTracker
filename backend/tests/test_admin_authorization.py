from fastapi.routing import APIRoute

from releasetracker.dependencies import get_current_admin_user
from releasetracker.main import app

PROTECTED_PREFIXES = (
    "/api/trackers",
    "/api/stats",
    "/api/releases",
    "/api/executors",
    "/api/runtime-connections",
    "/api/credentials",
    "/api/notifiers",
    "/api/settings",
    "/api/oidc-providers",
)


def test_business_route_inventory_requires_stable_admin_dependency():
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(PROTECTED_PREFIXES):
            continue
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        if get_current_admin_user not in dependency_calls:
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert missing == []


def test_non_admin_can_use_self_service_but_not_business_routes(non_admin_client):
    me_response = non_admin_client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "nonadmin"

    for path in (
        "/api/stats",
        "/api/releases",
        "/api/trackers",
        "/api/settings",
        "/api/credentials",
        "/api/runtime-connections",
        "/api/executors",
        "/api/notifiers",
        "/api/oidc-providers",
    ):
        response = non_admin_client.get(path)
        assert response.status_code == 403, (path, response.text)
