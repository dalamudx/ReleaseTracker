"""Portainer adapter snapshot / recover tests.

Uses an in-memory ``FakePortainerHttpClient`` that records requests and
returns scripted responses. The tests never touch a real Portainer server
and never re-materialize credentials — the adapter is instantiated with the
already-materialized ``RuntimeConnectionConfig`` used elsewhere in the
executor tests.
"""

from __future__ import annotations

import asyncio
import textwrap
from copy import deepcopy
from urllib.parse import quote
from typing import Any, Sequence

import httpx
import pytest
import yaml

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.portainer import (
    PortainerRequestTimeoutError,
    PortainerRuntimeAdapter,
)
from releasetracker.services.deployment_plan import MANAGED_MARKERS

# ---- Fake HTTP client -----------------------------------------------------


class _FakePortainerHttpResponse:
    def __init__(self, *, status_code: int, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _FakePortainerHttpClient:
    """Scripted Portainer HTTP client.

    ``responses`` maps ``(method, path, endpoint_id)`` to a list that is
    consumed in order per invocation so a test can script a stack detail
    that toggles from inactive to active across polls.
    """

    def __init__(self, responses: dict[tuple[str, str, int | None], Sequence[Any]]):
        self._responses = {key: list(values) for key, values in responses.items()}
        self.calls: list[dict[str, Any]] = []

    async def request(self, method, path, params=None, json=None, timeout=None):
        normalized_params = dict(params) if isinstance(params, dict) else None
        normalized_json = dict(json) if isinstance(json, dict) else None
        endpoint_id = normalized_params.get("endpointId") if normalized_params is not None else None
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": normalized_params,
                "json": normalized_json,
            }
        )
        key = (method, path, endpoint_id)
        queue = self._responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected Portainer request: {key}")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, Exception):
            raise response
        return response


# ---- Fixtures -------------------------------------------------------------


_STACK_FILE = textwrap.dedent("""\
    services:
      api:
        image: ghcr.io/acme/api:1.0.0
      worker:
        image: ghcr.io/acme/worker:1.0.0
    """)

_STACK_DETAIL_ACTIVE: dict[str, Any] = {
    "Id": 42,
    "EndpointId": 1,
    "Name": "prod-stack",
    "Type": 2,  # 2 == standalone
    "Status": 1,  # 1 == active
    "Env": [{"name": "LOG_LEVEL", "value": "info"}],
}

_STACK_DETAIL_INACTIVE: dict[str, Any] = {
    **_STACK_DETAIL_ACTIVE,
    "Status": 2,  # non-active during update
}


def _runtime_connection() -> RuntimeConnectionConfig:
    return RuntimeConnectionConfig(
        id=1,
        name="portainer-test",
        type="portainer",
        enabled=True,
        config={"base_url": "https://portainer.example.com", "endpoint_id": 1},
        credential_id=1,
        secrets={"api_key": "pk-test"},
    )


def _target_ref() -> dict[str, Any]:
    return {
        "mode": "portainer_stack",
        "endpoint_id": 1,
        "stack_id": 42,
        "stack_name": "prod-stack",
        "stack_type": "standalone",
    }


def _evidence() -> dict:
    return {
        "schema": 1,
        "engine_id": "engine-example",
        "services": {
            name: {
                "image": f"ghcr.io/acme/{name}:1.0.0",
                "image_id": "sha256:" + char * 64,
                "replicas": 1,
                "healthcheck": True,
            }
            for name, char in (("api", "a"), ("worker", "b"))
        },
    }


def _container(name: str) -> dict:
    item = _evidence()["services"][name]
    return {
        "Id": name + "-container",
        "Image": item["image_id"],
        "RestartCount": 0,
        "Config": {
            "Image": item["image"],
            "Healthcheck": {"Test": ["CMD", "true"]},
            "Labels": {
                "com.docker.compose.project": "prod-stack",
                "com.docker.compose.service": name,
            },
        },
        "State": {"Status": "running", "Running": True, "Health": {"Status": "healthy"}},
    }


def _native_responses() -> dict:
    prefix = "/api/endpoints/1/docker"
    responses = {
        ("GET", "/api/stacks/42/file", 1): [_ok({"StackFileContent": _STACK_FILE})],
        ("GET", f"{prefix}/info", None): [_ok({"ID": "engine-example"})],
        ("GET", f"{prefix}/containers/json", None): [
            _ok([{"Id": name + "-container"} for name in ("api", "worker")])
        ],
    }
    for name, item in _evidence()["services"].items():
        responses[("GET", f"{prefix}/containers/{name}-container/json", None)] = [
            _ok(_container(name))
        ]
        path = f"{prefix}/images/{quote(item['image'], safe='')}/json"
        responses[("GET", path, None)] = [_ok({"Id": item["image_id"]})]
    return responses


def _snapshot() -> dict[str, Any]:
    return {
        "runtime_type": "portainer",
        "endpoint_id": 1,
        "stack_id": 42,
        "stack_name": "prod-stack",
        "stack_type": "standalone",
        "stack_file": _STACK_FILE,
        "env": [],
        "recovery_evidence": _evidence(),
    }


def _ok(payload: Any) -> _FakePortainerHttpResponse:
    return _FakePortainerHttpResponse(status_code=200, payload=payload)


@pytest.mark.asyncio
async def test_portainer_stack_explicitly_rejects_single_image_operations():
    adapter = PortainerRuntimeAdapter(_runtime_connection())

    assert not adapter.supports_single_image_operations(_target_ref())
    with pytest.raises(NotImplementedError, match="multi-service Portainer stack"):
        await adapter.get_current_image(_target_ref())
    with pytest.raises(NotImplementedError, match="update_stack_services"):
        await adapter.update_image(_target_ref(), "ghcr.io/acme/api:2.0.0")


@pytest.mark.asyncio
async def test_portainer_update_injects_managed_markers_into_each_service():
    stack_file = textwrap.dedent("""\
        services:
          api:
            image: registry.example.test/team/service-a:1.0.0
          worker:
            image: registry.example.test/team/service-b:1.0.0
        """)
    client = _FakePortainerHttpClient(
        {
            ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
            ("GET", "/api/stacks/42/file", 1): [_ok({"StackFileContent": stack_file})],
            ("PUT", "/api/stacks/42", 1): [_ok({})],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    token = MANAGED_MARKERS.set(
        {
            "releasetracker.io/managed-by": "installation-test",
            "releasetracker.io/target-id": "target-test",
            "releasetracker.io/schema": "1",
        }
    )
    try:
        result = await adapter.update_stack_services(
            _target_ref(),
            {"api": "registry.example.test/team/service-a:2.0.0"},
        )
    finally:
        MANAGED_MARKERS.reset(token)

    assert result.updated_services == ["api"]
    put = next(call for call in client.calls if call["method"] == "PUT")
    parsed = yaml.safe_load(put["json"]["stackFileContent"])
    for service in parsed["services"].values():
        assert service["labels"]["releasetracker.io/managed-by"] == "installation-test"
        assert service["labels"]["releasetracker.io/target-id"] == "target-test"


@pytest.mark.asyncio
async def test_portainer_retries_transient_get_requests_only():
    responses = {
        ("GET", "/api/endpoints", None): [
            httpx.ReadTimeout("temporary timeout"),
            _ok([]),
        ]
    }
    client = _FakePortainerHttpClient(responses)
    runtime = _runtime_connection()
    runtime.config["operation_policy"] = {"read_timeout_seconds": 1, "read_retries": 1}
    adapter = PortainerRuntimeAdapter(runtime, client=client)

    assert await adapter.discover_endpoints() == []
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_portainer_stack_update_timeout_is_not_replayed():
    responses = {("PUT", "/api/stacks/42", 1): [httpx.WriteTimeout("write timed out")]}
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    with pytest.raises(PortainerRequestTimeoutError, match="stack update"):
        await adapter._update_stack(
            endpoint_id=1,
            stack_id=42,
            stack=_STACK_DETAIL_ACTIVE,
            stack_file_content=_STACK_FILE,
        )
    assert len(client.calls) == 1


# ---- capture_snapshot ----------------------------------------------------


@pytest.mark.asyncio
async def test_capture_snapshot_records_stack_payload():
    responses = {
        **_native_responses(),
        ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
        ("GET", "/api/stacks/42/file", 1): [
            _ok({"StackFileContent": _STACK_FILE}),
        ],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    snapshot = await adapter.capture_snapshot(_target_ref(), "ghcr.io/acme/api:1.0.0")

    assert snapshot["runtime_type"] == "portainer"
    assert snapshot["endpoint_id"] == 1
    assert snapshot["stack_id"] == 42
    assert snapshot["stack_name"] == "prod-stack"
    assert snapshot["stack_type"] == "standalone"
    assert snapshot["project_name"] == "prod-stack"
    assert snapshot["env"] == [{"name": "LOG_LEVEL", "value": "info"}]
    assert snapshot["stack_file"].startswith("services:")
    # current_image wins over stack-file inference because we were told the
    # executor target image explicitly.
    assert snapshot["image_at_capture"] == "ghcr.io/acme/api:1.0.0"


@pytest.mark.asyncio
async def test_capture_snapshot_leaves_image_null_when_current_image_missing_and_file_ambiguous():
    responses = {
        **_native_responses(),
        ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
        ("GET", "/api/stacks/42/file", 1): [_ok({"StackFileContent": _STACK_FILE})],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    snapshot = await adapter.capture_snapshot(_target_ref(), current_image="")

    # Stack has two distinct images (api + worker), so inference returns None.
    assert snapshot["image_at_capture"] is None


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_unsupported_stack_kind():
    unsupported_detail = {**_STACK_DETAIL_ACTIVE, "Type": 1}  # swarm
    responses = {
        ("GET", "/api/stacks/42", 1): [_ok(unsupported_detail)],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    with pytest.raises(ValueError, match="unsupported Portainer stack type"):
        await adapter.capture_snapshot(_target_ref(), "ghcr.io/acme/api:1.0.0")


# ---- validate_snapshot ----------------------------------------------------


@pytest.mark.asyncio
async def test_validate_snapshot_happy_path():
    responses = {
        ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    await adapter.validate_snapshot(
        _target_ref(),
        {
            **_snapshot(),
            "runtime_type": "portainer",
            "endpoint_id": 1,
            "stack_id": 42,
            "stack_type": "standalone",
            "stack_file": _STACK_FILE,
            "env": [],
            "image_at_capture": None,
        },
    )


@pytest.mark.asyncio
async def test_validate_snapshot_rejects_empty_stack_file():
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=_FakePortainerHttpClient({}))
    with pytest.raises(ValueError, match="stack_file must be a non-empty string"):
        await adapter.validate_snapshot(
            _target_ref(),
            {
                "stack_type": "standalone",
                "stack_file": "",
            },
        )


@pytest.mark.asyncio
async def test_validate_snapshot_rejects_stack_type_mismatch():
    # Live stack is now a swarm stack while snapshot recorded standalone.
    swarm_detail = {**_STACK_DETAIL_ACTIVE, "Type": 1}
    responses = {
        ("GET", "/api/stacks/42", 1): [_ok(swarm_detail)],
    }
    adapter = PortainerRuntimeAdapter(
        _runtime_connection(), client=_FakePortainerHttpClient(responses)
    )

    with pytest.raises(ValueError, match="stack_type mismatch"):
        await adapter.validate_snapshot(
            _target_ref(),
            {
                **_snapshot(),
                "stack_type": "standalone",
                "stack_file": _STACK_FILE,
            },
        )


@pytest.mark.asyncio
async def test_validate_snapshot_rejects_unsupported_snapshot_type():
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=_FakePortainerHttpClient({}))
    with pytest.raises(ValueError, match="snapshot.stack_type is unsupported"):
        await adapter.validate_snapshot(
            _target_ref(),
            {
                "stack_type": "kubernetes",
                "stack_file": _STACK_FILE,
            },
        )


# ---- recover_from_snapshot ------------------------------------------------


@pytest.mark.asyncio
async def test_recover_from_snapshot_restores_and_polls_until_active(monkeypatch):
    # First poll returns inactive (Status=2), second returns active
    # (Status=1). Asserts that recover waits for the active reading.
    responses = {
        **_native_responses(),
        # validate_snapshot detail call
        ("GET", "/api/stacks/42", 1): [
            _ok(_STACK_DETAIL_ACTIVE),  # validate_snapshot
            _ok(_STACK_DETAIL_ACTIVE),  # pre-update live stack (env reload)
            _ok(_STACK_DETAIL_INACTIVE),  # first poll after update
            _ok(_STACK_DETAIL_ACTIVE),  # second poll flips to active
        ],
        ("PUT", "/api/stacks/42", 1): [_ok({})],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    # Avoid a real 2-second sleep between polls.
    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    snapshot = {
        **_snapshot(),
        "runtime_type": "portainer",
        "endpoint_id": 1,
        "stack_id": 42,
        "stack_name": "prod-stack",
        "stack_type": "standalone",
        "env": [{"name": "LOG_LEVEL", "value": "info"}],
        "stack_file": _STACK_FILE,
        "image_at_capture": "ghcr.io/acme/api:1.0.0",
    }

    result = await adapter.recover_from_snapshot(_target_ref(), snapshot)

    assert result.updated is True
    assert result.new_image == "ghcr.io/acme/api:1.0.0"
    assert "restored from snapshot" in (result.message or "")

    put_calls = [call for call in client.calls if call["method"] == "PUT"]
    assert len(put_calls) == 1
    assert put_calls[0]["json"]["stackFileContent"] == _STACK_FILE
    assert put_calls[0]["json"]["env"] == snapshot["env"]


@pytest.mark.asyncio
async def test_recover_from_snapshot_rejects_invalid_snapshot_before_update():
    # Live stack returns swarm while snapshot claims standalone. The adapter
    # must short-circuit inside validate_snapshot without touching PUT.
    swarm_detail = {**_STACK_DETAIL_ACTIVE, "Type": 1}
    responses = {
        ("GET", "/api/stacks/42", 1): [_ok(swarm_detail)],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    snapshot = {
        **_snapshot(),
        "stack_type": "standalone",
        "endpoint_id": 1,
        "stack_id": 42,
        "stack_file": _STACK_FILE,
        "env": [],
    }

    with pytest.raises(ValueError, match="stack_type mismatch"):
        await adapter.recover_from_snapshot(_target_ref(), snapshot)

    assert not any(call["method"] == "PUT" for call in client.calls)


@pytest.mark.asyncio
async def test_recover_from_snapshot_reuses_runtime_connection_without_new_credential(monkeypatch):
    # Use a local import to prove we have not imported the credentials
    # module into the adapter namespace. If the adapter accidentally added a
    # re-materialization path this test would import the helper and the
    # adapter would call it — we prove it does not by monkeypatching.
    from releasetracker.services import runtime_credentials as rc_module

    calls: list[Any] = []

    def _spy(*args, **kwargs):  # pragma: no cover - defensive only
        calls.append((args, kwargs))
        raise AssertionError(
            "Portainer adapter must reuse the supplied RuntimeConnection; "
            "no new credential materialization is allowed."
        )

    monkeypatch.setattr(
        rc_module,
        "materialize_runtime_connection_credentials",
        _spy,
    )

    responses = {
        **_native_responses(),
        ("GET", "/api/stacks/42", 1): [
            _ok(_STACK_DETAIL_ACTIVE),  # validate_snapshot
            _ok(_STACK_DETAIL_ACTIVE),  # live stack before update
            _ok(_STACK_DETAIL_ACTIVE),  # first poll is already active
        ],
        ("PUT", "/api/stacks/42", 1): [_ok({})],
    }
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    snapshot = {
        **_snapshot(),
        "stack_type": "standalone",
        "endpoint_id": 1,
        "stack_id": 42,
        "stack_name": "prod-stack",
        "env": [{"name": "LOG_LEVEL", "value": "info"}],
        "stack_file": _STACK_FILE,
        "image_at_capture": "ghcr.io/acme/api:1.0.0",
    }

    result = await adapter.recover_from_snapshot(_target_ref(), snapshot)
    assert result.updated is True
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint_id", 2),
        ("stack_id", 43),
        ("stack_name", "other-stack"),
        ("endpoint_id", None),
        ("stack_id", None),
        ("stack_name", None),
        ("runtime_type", "docker"),
        ("env", None),
        ("env", [{"name": "TOKEN"}]),
    ],
)
async def test_recovery_rejects_wrong_or_incomplete_snapshot_without_io(field, value):
    client = _FakePortainerHttpClient({})
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(ValueError, match=f"snapshot.{field}"):
        await adapter.recover_from_snapshot(_target_ref(), {**_snapshot(), field: value})
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("EndpointId", 2),
        ("Id", 43),
        ("Name", "other-stack"),
        ("EndpointId", None),
        ("Id", None),
        ("Name", None),
        ("Type", 1),
    ],
)
@pytest.mark.parametrize("drift_after_validation", [False, True])
async def test_recovery_rechecks_live_identity_before_write(field, value, drift_after_validation):
    changed = _ok({**_STACK_DETAIL_ACTIVE, field: value})
    responses = [_ok(_STACK_DETAIL_ACTIVE), changed] if drift_after_validation else [changed]
    client = _FakePortainerHttpClient({("GET", "/api/stacks/42", 1): responses})
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(ValueError, match="does not match|stack_type mismatch"):
        await adapter.recover_from_snapshot(_target_ref(), _snapshot())
    assert all(call["method"] == "GET" for call in client.calls)


@pytest.mark.asyncio
async def test_recovery_restores_exact_bytes_even_with_deployment_marker_context():
    client = _FakePortainerHttpClient(
        {
            **_native_responses(),
            ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
            ("PUT", "/api/stacks/42", 1): [_ok({})],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    snapshot = {**_snapshot(), "env": [{"name": "TOKEN", "value": "example-secret"}]}
    client._responses[("GET", "/api/stacks/42", 1)] = [
        _ok({**_STACK_DETAIL_ACTIVE, "Env": snapshot["env"]})
    ]
    token = MANAGED_MARKERS.set({"releasetracker.io/managed-by": "different-installation"})
    try:
        await adapter.recover_from_snapshot(_target_ref(), snapshot)
    finally:
        MANAGED_MARKERS.reset(token)
    writes = [call for call in client.calls if call["method"] == "PUT"]
    assert len(writes) == 1
    assert writes[0]["json"]["stackFileContent"] == snapshot["stack_file"]
    assert writes[0]["json"]["env"] == snapshot["env"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("EndpointId", 2), ("Id", 43), ("Name", "other-stack")])
async def test_capture_snapshot_rejects_live_identity_mismatch(field, value):
    client = _FakePortainerHttpClient(
        {
            ("GET", "/api/stacks/42", 1): [_ok({**_STACK_DETAIL_ACTIVE, field: value})],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(ValueError, match="does not match recovery target"):
        await adapter.capture_snapshot(_target_ref(), "")
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_recovery_rejects_runtime_endpoint_change():
    connection = _runtime_connection()
    connection.config["endpoint_id"] = 2
    client = _FakePortainerHttpClient(
        {
            ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
        }
    )
    adapter = PortainerRuntimeAdapter(connection, client=client)
    with pytest.raises(ValueError, match="endpoint does not match runtime connection"):
        await adapter.recover_from_snapshot(_target_ref(), _snapshot())
    assert not any(call["method"] == "PUT" for call in client.calls)


@pytest.mark.asyncio
async def test_recovery_rejects_git_backed_stack_drift_before_write():
    client = _FakePortainerHttpClient(
        {
            ("GET", "/api/stacks/42", 1): [
                _ok(_STACK_DETAIL_ACTIVE),
                _ok(
                    {
                        **_STACK_DETAIL_ACTIVE,
                        "GitConfig": {"URL": "https://git.example.test/team/app"},
                    }
                ),
            ],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(ValueError, match="git-backed"):
        await adapter.recover_from_snapshot(_target_ref(), _snapshot())
    assert not any(call["method"] == "PUT" for call in client.calls)


@pytest.mark.asyncio
async def test_capture_persists_native_evidence():
    client = _FakePortainerHttpClient(
        {
            **_native_responses(),
            ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
            ("GET", "/api/stacks/42/file", 1): [_ok({"StackFileContent": _STACK_FILE})],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    snapshot = await adapter.capture_snapshot(_target_ref(), "")
    assert snapshot["recovery_evidence"] == _evidence()
    assert all(call["method"] == "GET" for call in client.calls)


@pytest.mark.asyncio
async def test_old_snapshot_without_native_evidence_cannot_restore():
    snapshot = _snapshot()
    del snapshot["recovery_evidence"]
    client = _FakePortainerHttpClient({("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)]})
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(ValueError, match="native recovery evidence"):
        await adapter.recover_from_snapshot(_target_ref(), snapshot)
    assert all(call["method"] == "GET" for call in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["engine_changed", "alias_moved", "image_missing"])
async def test_native_preflight_failure_never_writes(failure):
    responses = {**_native_responses(), ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)]}
    prefix = "/api/endpoints/1/docker"
    path = f"{prefix}/images/{quote(_evidence()['services']['api']['image'], safe='')}/json"
    if failure == "engine_changed":
        responses[("GET", f"{prefix}/info", None)] = [_ok({"ID": "different-engine"})]
    elif failure == "alias_moved":
        responses[("GET", path, None)] = [_ok({"Id": "sha256:" + "c" * 64})]
    else:
        responses[("GET", path, None)] = [_FakePortainerHttpResponse(status_code=404)]
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(ValueError, match="identity changed|alias changed|no longer available"):
        await adapter.recover_from_snapshot(_target_ref(), _snapshot())
    assert all(call["method"] == "GET" for call in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "unhealthy",
        "starting",
        "missing_health",
        "healthcheck_removed",
        "stopped",
        "paused",
        "restarting",
        "wrong_image",
        "missing_replica",
        "extra_replica",
    ],
)
async def test_native_probe_rejects_incomplete_or_unhealthy_service(failure):
    from releasetracker.executors import portainer_recovery

    responses = _native_responses()
    worker = deepcopy(_container("worker"))
    if failure in {"unhealthy", "starting"}:
        worker["State"]["Health"]["Status"] = failure
    elif failure == "missing_health":
        worker["State"].pop("Health")
    elif failure == "healthcheck_removed":
        worker["Config"].pop("Healthcheck")
    elif failure == "stopped":
        worker["State"]["Status"] = "exited"
    elif failure in {"paused", "restarting"}:
        worker["State"][failure.capitalize()] = True
    elif failure == "wrong_image":
        worker["Image"] = "sha256:" + "c" * 64
    elif failure == "missing_replica":
        responses[("GET", "/api/endpoints/1/docker/containers/json", None)] = [
            _ok([{"Id": "api-container"}])
        ]
    else:
        extra = deepcopy(worker)
        extra["Id"] = "worker-extra"
        responses[("GET", "/api/endpoints/1/docker/containers/json", None)] = [
            _ok([{"Id": name} for name in ("api-container", "worker-container", "worker-extra")])
        ]
        responses[("GET", "/api/endpoints/1/docker/containers/worker-extra/json", None)] = [
            _ok(extra)
        ]
    responses[("GET", "/api/endpoints/1/docker/containers/worker-container/json", None)] = [
        _ok(worker)
    ]
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    ready, _ = await portainer_recovery.probe(adapter, _target_ref(), _evidence())
    assert ready is False
    assert all(call["method"] == "GET" for call in client.calls)


@pytest.mark.asyncio
async def test_active_stack_with_unhealthy_service_times_out_without_replaying_write(monkeypatch):
    from releasetracker.executors import portainer

    monkeypatch.setattr(portainer, "_PORTAINER_RECOVERY_POLL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(portainer, "_PORTAINER_RECOVERY_POLL_INTERVAL_SECONDS", 0.01)
    worker = deepcopy(_container("worker"))
    worker["State"]["Health"]["Status"] = "unhealthy"
    client = _FakePortainerHttpClient(
        {
            **_native_responses(),
            ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
            ("PUT", "/api/stacks/42", 1): [_ok({})],
            ("GET", "/api/endpoints/1/docker/containers/worker-container/json", None): [
                _ok(worker)
            ],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(RuntimeError, match="native service verification timed out"):
        await adapter.recover_from_snapshot(_target_ref(), _snapshot())
    writes = [call for call in client.calls if call["method"] == "PUT"]
    assert len(writes) == 1
    assert writes[0]["json"]["pullImage"] is False


@pytest.mark.asyncio
async def test_no_healthcheck_proves_runtime_readiness_only():
    from releasetracker.executors import portainer_recovery

    evidence = _evidence()
    responses = _native_responses()
    for name in ("api", "worker"):
        evidence["services"][name]["healthcheck"] = False
        container = deepcopy(_container(name))
        container["Config"].pop("Healthcheck")
        container["State"].pop("Health")
        responses[("GET", f"/api/endpoints/1/docker/containers/{name}-container/json", None)] = [
            _ok(container)
        ]
    adapter = PortainerRuntimeAdapter(
        _runtime_connection(), client=_FakePortainerHttpClient(responses)
    )
    ready, sample = await portainer_recovery.probe(adapter, _target_ref(), evidence)
    assert ready is True and len(sample) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["file", "env"])
async def test_healthy_services_do_not_mask_failed_configuration_restore(monkeypatch, changed):
    async def fast_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    snapshot = {**_snapshot(), "env": _STACK_DETAIL_ACTIVE["Env"]}
    responses = {
        **_native_responses(),
        ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
        ("PUT", "/api/stacks/42", 1): [_ok({})],
    }
    if changed == "file":
        responses[("GET", "/api/stacks/42/file", 1)] = [
            _ok({"StackFileContent": _STACK_FILE + "# changed\n"})
        ]
    else:
        responses[("GET", "/api/stacks/42", 1)] = [_ok({**_STACK_DETAIL_ACTIVE, "Env": []})]
    client = _FakePortainerHttpClient(responses)
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    with pytest.raises(RuntimeError, match="configuration readback"):
        await adapter.recover_from_snapshot(_target_ref(), snapshot)
    assert len([call for call in client.calls if call["method"] == "PUT"]) == 1


@pytest.mark.asyncio
async def test_recovery_waits_for_native_health_and_stable_container_identity(monkeypatch):
    async def fast_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    pending = deepcopy(_container("worker"))
    pending["State"]["Health"]["Status"] = "starting"
    restarted = deepcopy(_container("worker"))
    restarted["RestartCount"] = 1
    client = _FakePortainerHttpClient(
        {
            **_native_responses(),
            ("GET", "/api/stacks/42", 1): [_ok(_STACK_DETAIL_ACTIVE)],
            ("PUT", "/api/stacks/42", 1): [_ok({})],
            ("GET", "/api/endpoints/1/docker/containers/worker-container/json", None): [
                _ok(pending),
                _ok(_container("worker")),
                _ok(restarted),
                _ok(restarted),
            ],
        }
    )
    adapter = PortainerRuntimeAdapter(_runtime_connection(), client=client)
    result = await adapter.recover_from_snapshot(
        _target_ref(), {**_snapshot(), "env": _STACK_DETAIL_ACTIVE["Env"]}
    )
    assert result.updated
    probes = [
        call for call in client.calls if call["path"].endswith("/containers/worker-container/json")
    ]
    assert len(probes) == 4
    assert len([call for call in client.calls if call["method"] == "PUT"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"image": "${IMAGE}"},
        {"profiles": ["optional"]},
        {"build": "."},
        {"pull_policy": "always"},
        {"deploy": {"replicas": 0}},
    ],
)
def test_unsupported_recovery_semantics_fail_closed(change):
    from releasetracker.executors import portainer_recovery

    document = yaml.safe_load(_STACK_FILE)
    document["services"]["api"].update(change)
    with pytest.raises(ValueError):
        portainer_recovery.service_spec(yaml.safe_dump(document))


@pytest.mark.parametrize("image", ["registry.example.test/app:latest", "registry.example.test/app"])
@pytest.mark.parametrize("policy", [None, "missing", "never"])
def test_latest_recovery_requires_explicit_no_pull(image, policy):
    from releasetracker.executors import portainer_recovery

    snapshot = _snapshot()
    document = yaml.safe_load(snapshot["stack_file"])
    document["services"]["api"]["image"] = image
    if policy:
        document["services"]["api"]["pull_policy"] = policy
    snapshot["stack_file"] = yaml.safe_dump(document)
    snapshot["recovery_evidence"]["services"]["api"]["image"] = image
    if policy == "never":
        assert portainer_recovery.validate(snapshot) == snapshot["recovery_evidence"]
    else:
        with pytest.raises(ValueError, match="pull_policy=never"):
            portainer_recovery.validate(snapshot)
