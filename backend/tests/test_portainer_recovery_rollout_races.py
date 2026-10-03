"""Native Portainer recovery retries only confirmed container-replacement races."""

from copy import deepcopy
from urllib.parse import quote

import httpx
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors import portainer_recovery
from releasetracker.executors.portainer import (
    PortainerResourceNotFoundError,
    PortainerRuntimeAdapter,
)

PREFIX = "/api/endpoints/1/docker"
REF = {"endpoint_id": 1, "stack_name": "owned-test"}
IMAGE = "registry.test/api:1.0"
EVIDENCE = {
    "engine_id": "owned-engine",
    "services": {
        "api": {
            "image": IMAGE,
            "image_id": "sha256:" + "a" * 64,
            "replicas": 1,
            "healthcheck": False,
        }
    },
}


def attrs():
    return {
        "Id": "replacement",
        "Image": EVIDENCE["services"]["api"]["image_id"],
        "Config": {
            "Image": IMAGE,
            "Labels": {
                "com.docker.compose.project": "owned-test",
                "com.docker.compose.service": "api",
            },
        },
        "State": {"Status": "running", "Running": True},
        "RestartCount": 0,
    }


def adapter(client):
    return PortainerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="test",
            type="portainer",
            credential_id=1,
            config={"base_url": "http://portainer.test", "endpoint_id": 1},
            secrets={"api_key": "synthetic-key"},
        ),
        client=client,
    )


def transport(*, missing=None, inspect_status=404, replacement_attrs=None):
    calls = []

    def respond(request):
        assert request.method == "GET", "recovery probe must never mutate the engine"
        path = request.url.path
        calls.append(path)
        if path == missing:
            return httpx.Response(404, json={"message": "synthetic missing resource"})
        if path == PREFIX + "/info":
            return httpx.Response(200, json={"ID": "owned-engine"})
        if path == PREFIX + "/containers/json":
            identifier = (
                "replacement"
                if replacement_attrs is not None or calls.count(path) > 1
                else "retiring"
            )
            return httpx.Response(200, json=[{"Id": identifier}])
        if path == PREFIX + "/containers/retiring/json":
            return httpx.Response(inspect_status, json={"message": "synthetic inspect error"})
        if path == PREFIX + "/containers/replacement/json":
            return httpx.Response(200, json=replacement_attrs or attrs())
        raise AssertionError(f"unexpected request: {path}")

    return httpx.MockTransport(respond)


async def test_rollout_retries_inspect_404_then_verifies_replacement():
    async with httpx.AsyncClient(base_url="http://portainer.test", transport=transport()) as client:
        runtime = adapter(client)
        assert await portainer_recovery.probe(runtime, REF, EVIDENCE) == (False, ())
        ready, signature = await portainer_recovery.probe(runtime, REF, EVIDENCE)
        assert ready
        assert signature == (("replacement", EVIDENCE["services"]["api"]["image_id"], 0),)


async def test_capture_stays_strict_when_container_disappears():
    async with httpx.AsyncClient(base_url="http://portainer.test", transport=transport()) as client:
        with pytest.raises(PortainerResourceNotFoundError, match="recovery container disappeared"):
            await portainer_recovery.capture(
                adapter(client), REF, f"services:\n  api:\n    image: {IMAGE}\n"
            )


@pytest.mark.parametrize("path", ["/info", "/containers/json"])
async def test_missing_engine_or_inventory_is_not_silently_retried(path):
    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=transport(missing=PREFIX + path)
    ) as client:
        with pytest.raises(PortainerResourceNotFoundError):
            await portainer_recovery.probe(adapter(client), REF, EVIDENCE)


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_inspect_auth_and_server_errors_are_not_silently_retried(status):
    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=transport(inspect_status=status)
    ) as client:
        with pytest.raises(RuntimeError, match=f"request failed \\({status}\\)"):
            await portainer_recovery.probe(adapter(client), REF, EVIDENCE)


async def test_identity_mismatch_still_fails_closed():
    row = attrs()
    row["Config"]["Labels"]["com.docker.compose.project"] = "foreign-project"
    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=transport(replacement_attrs=row)
    ) as client:
        with pytest.raises(ValueError, match="project identity mismatch"):
            await portainer_recovery.probe(adapter(client), REF, EVIDENCE)


async def test_wrong_immutable_image_never_reports_ready():
    row = attrs()
    row["Image"] = "sha256:" + "b" * 64
    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=transport(replacement_attrs=row)
    ) as client:
        assert await portainer_recovery.probe(adapter(client), REF, EVIDENCE) == (False, ())


async def test_error_message_alone_does_not_authorize_retry(monkeypatch):
    async def missing(*args, **kwargs):
        raise ValueError("recovery container disappeared")

    async def identity(*args, **kwargs):
        return EVIDENCE["engine_id"]

    monkeypatch.setattr(portainer_recovery, "containers", missing)
    monkeypatch.setattr(portainer_recovery, "engine_id", identity)
    with pytest.raises(ValueError, match="recovery container disappeared"):
        await portainer_recovery.probe(None, REF, deepcopy(EVIDENCE))


@pytest.mark.parametrize(
    "reference",
    [
        "registry.test:5000/team/api:1.2",
        "registry.test/team/api@sha256:" + "c" * 64,
        "registry.test/team/api:synthetic?query=value#fragment",
    ],
)
async def test_image_inspect_preserves_namespaces_but_not_query_delimiters(reference):
    evidence = deepcopy(EVIDENCE)
    evidence["services"]["api"]["image"] = reference

    def respond(request):
        assert request.method == "GET"
        if request.url.path == PREFIX + "/info":
            return httpx.Response(200, json={"ID": evidence["engine_id"]})
        # Real Portainer rejects opaque percent-escaped repository separators.
        if b"%2F" in request.url.raw_path:
            return httpx.Response(403, json={"message": "access denied to resource"})
        assert request.url.raw_path == f"{PREFIX}/images/{quote(reference, safe='/')}/json".encode()
        assert not request.url.query
        assert not request.url.fragment
        return httpx.Response(200, json={"Id": evidence["services"]["api"]["image_id"]})

    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=httpx.MockTransport(respond)
    ) as client:
        await portainer_recovery.preflight(adapter(client), REF, evidence)


@pytest.mark.parametrize("reference", ["../api:1", "team/../../info", "/api:1", "team//api:1"])
async def test_image_path_traversal_is_rejected_before_image_request(reference):
    evidence = deepcopy(EVIDENCE)
    evidence["services"]["api"]["image"] = reference

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == PREFIX + "/info", "invalid ref must never reach an image request"
        return httpx.Response(200, json={"ID": evidence["engine_id"]})

    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(ValueError, match="image reference invalid"):
            await portainer_recovery.preflight(adapter(client), REF, evidence)
