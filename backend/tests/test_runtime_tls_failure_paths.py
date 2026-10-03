"""TLS failures reject clients and leave no synthetic PEM material behind."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.podman import PodmanRuntimeAdapter
from releasetracker.executors import runtime_tls
from releasetracker.services.task_queue import classify_fetch_error
from test_runtime_tls import tls_runtime_server as _tls_runtime_server


@pytest.fixture
def local_tls_runtime_server(tmp_path):
    yield from _tls_runtime_server.__wrapped__(tmp_path)


def temporary_material_directory(monkeypatch, tmp_path):
    original = runtime_tls.tempfile.TemporaryDirectory
    monkeypatch.setattr(
        runtime_tls.tempfile, "TemporaryDirectory", lambda **kw: original(dir=tmp_path, **kw)
    )


@pytest.mark.parametrize(
    "kind,adapter_type", [("docker", DockerRuntimeAdapter), ("podman", PodmanRuntimeAdapter)]
)
@pytest.mark.parametrize("failure", ["missing_client_certificate", "malformed_client_certificate"])
async def test_real_sdk_tls_failure_sends_no_http_and_cleans_files(
    local_tls_runtime_server, monkeypatch, tmp_path, kind, adapter_type, failure
):
    port, secrets, requests = local_tls_runtime_server
    secrets = dict(secrets)
    if failure == "missing_client_certificate":
        secrets.pop("client_cert")
        secrets.pop("client_key")
    else:
        secrets["client_cert"] = "synthetic invalid PEM"
    owned = tmp_path / "owned"
    owned.mkdir()
    temporary_material_directory(monkeypatch, owned)
    adapter = adapter_type(
        RuntimeConnectionConfig(
            name="tls-failure",
            type=kind,
            config={"socket": f"tcp://127.0.0.1:{port}", "tls_verify": True},
            secrets=secrets,
        )
    )
    try:
        with pytest.raises(Exception) as caught:
            await adapter.discover_targets()
        # Missing client identity can surface as a transport EOF; malformed PEM
        # is an ordinary TLS setup failure, not server identity verification.
        result = classify_fetch_error(caught.value)
        assert result.state == "failed"
        assert result.code in {"fetch_failed", "upstream_tls_failed"}
        assert requests == [], "TLS must fail before an HTTP request reaches the runtime"
    finally:
        await adapter.close()
    assert list(owned.iterdir()) == []
    await adapter.close()


@pytest.mark.parametrize("field", ["client_cert", "client_key"])
def test_incomplete_pair_cleans_all_written_files(monkeypatch, tmp_path, field):
    temporary_material_directory(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="provided together"):
        runtime_tls.RuntimeTLSMaterial({field: "synthetic partial PEM", "ca_cert": "synthetic CA"})
    assert list(tmp_path.iterdir()) == []


def test_partial_write_failure_removes_certificate_already_written(monkeypatch, tmp_path):
    temporary_material_directory(monkeypatch, tmp_path)
    original = runtime_tls.os.open

    def fail_key(path, flags, mode=0o777, **kwargs):
        if Path(path).name == "client_key":
            raise OSError("synthetic write failure")
        return original(path, flags, mode, **kwargs)

    monkeypatch.setattr(runtime_tls.os, "open", fail_key)
    with pytest.raises(OSError, match="synthetic write failure"):
        runtime_tls.RuntimeTLSMaterial(
            {"client_cert": "synthetic certificate", "client_key": "synthetic key"}
        )
    assert list(tmp_path.iterdir()) == []


def test_client_close_failure_still_removes_pem_material(monkeypatch, tmp_path):
    temporary_material_directory(monkeypatch, tmp_path)
    material = runtime_tls.RuntimeTLSMaterial(
        {"client_cert": "synthetic certificate", "client_key": "synthetic key"}
    )
    client = SimpleNamespace(close=Mock(side_effect=RuntimeError("synthetic close failure")))
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        runtime_tls.close_runtime_client(client, material)
    assert list(tmp_path.iterdir()) == []
    material.cleanup()


async def test_podman_failed_tls_configuration_closes_client_and_removes_pem(monkeypatch, tmp_path):
    import podman

    temporary_material_directory(monkeypatch, tmp_path)

    class InvalidSession:
        @property
        def base_url(self):
            raise RuntimeError("synthetic TLS setup failure")

    client = SimpleNamespace(api=InvalidSession(), close=Mock())
    monkeypatch.setattr(podman, "PodmanClient", lambda **kwargs: client)
    adapter = PodmanRuntimeAdapter(
        RuntimeConnectionConfig(
            name="tls",
            type="podman",
            config={"socket": "tcp://127.0.0.1:2376", "tls_verify": True},
            secrets={"client_cert": "synthetic certificate", "client_key": "synthetic key"},
        )
    )
    with pytest.raises(RuntimeError, match="synthetic TLS setup failure"):
        await adapter.discover_targets()
    client.close.assert_called_once()
    assert list(tmp_path.iterdir()) == []
    await adapter.close()
