"""Portainer adapter snapshot / recover tests (Phase B).

Uses an in-memory ``FakePortainerHttpClient`` that records requests and
returns scripted responses. The tests never touch a real Portainer server
and never re-materialize credentials — the adapter is instantiated with the
already-materialized ``RuntimeConnectionConfig`` used elsewhere in the
executor tests.
"""

from __future__ import annotations

import asyncio
import textwrap
from typing import Any, Sequence

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.portainer import PortainerRuntimeAdapter


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
        endpoint_id = (
            normalized_params.get("endpointId") if normalized_params is not None else None
        )
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


_STACK_FILE = textwrap.dedent(
    """\
    services:
      api:
        image: ghcr.io/acme/api:1.0.0
      worker:
        image: ghcr.io/acme/worker:1.0.0
    """
)

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


def _ok(payload: Any) -> _FakePortainerHttpResponse:
    return _FakePortainerHttpResponse(status_code=200, payload=payload)


# ---- capture_snapshot ----------------------------------------------------


@pytest.mark.asyncio
async def test_capture_snapshot_records_stack_payload():
    responses = {
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
    # module into the adapter namespace. If Phase B accidentally added a
    # re-materialization path this test would import the helper and the
    # adapter would call it — we prove it does not by monkeypatching.
    from releasetracker.services import runtime_credentials as rc_module

    calls: list[Any] = []

    def _spy(*args, **kwargs):  # pragma: no cover - defensive only
        calls.append((args, kwargs))
        raise AssertionError(
            "Phase B adapter must reuse the supplied RuntimeConnection; "
            "no new credential materialization is allowed."
        )

    monkeypatch.setattr(
        rc_module,
        "materialize_runtime_connection_credentials",
        _spy,
    )

    responses = {
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
        "stack_type": "standalone",
        "endpoint_id": 1,
        "stack_id": 42,
        "stack_name": "prod-stack",
        "env": [],
        "stack_file": _STACK_FILE,
        "image_at_capture": "ghcr.io/acme/api:1.0.0",
    }

    result = await adapter.recover_from_snapshot(_target_ref(), snapshot)
    assert result.updated is True
    assert calls == []
