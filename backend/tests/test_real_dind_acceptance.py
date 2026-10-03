"""Opt-in real DinD mTLS acceptance; owns all workloads and synthetic certificates.

Run with RT_RUN_REAL_DIND_TESTS=1. Requires podman and the cached DinD image.
No host business socket or existing credential/certificate files are accessed.
"""

import asyncio
import os
from pathlib import Path
import subprocess
import time
import uuid

from cryptography.x509.oid import ExtendedKeyUsageOID
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.services.task_queue import classify_fetch_error
from test_runtime_tls import issue, pem_cert, pem_key

pytestmark = pytest.mark.skipif(
    os.environ.get("RT_RUN_REAL_DIND_TESTS") != "1",
    reason="Real privileged runtime acceptance is explicitly opt-in",
)


def podman(*args, timeout=30, check=True):
    return subprocess.run(
        ["podman", *args], capture_output=True, text=True, timeout=timeout, check=check
    )


@pytest.mark.timeout(300)
async def test_real_dind_mutual_tls_deploy_and_immutable_recovery(tmp_path):
    # Generate new synthetic material in memory. Never read existing PEM files.
    ca, ca_key = issue("synthetic-dind-ca")
    server_cert, server_key = issue(
        "rt-owned-dind.test", ca, ca_key, ExtendedKeyUsageOID.SERVER_AUTH
    )
    client_cert, client_key = issue("rt-owned-client", ca, ca_key, ExtendedKeyUsageOID.CLIENT_AUTH)
    tmp_path.chmod(0o700)
    for name, value in {
        "ca.pem": pem_cert(ca),
        "server-cert.pem": pem_cert(server_cert),
        "server-key.pem": pem_key(server_key),
    }.items():
        path = tmp_path / name
        path.write_text(value)
        path.chmod(0o600)
    secrets = {
        "ca_cert": pem_cert(ca),
        "client_cert": pem_cert(client_cert),
        "client_key": pem_key(client_key),
    }
    name = "rt-owned-mtls-" + uuid.uuid4().hex[:10]
    adapter = None
    try:
        podman(
            "run",
            "-d",
            "--name",
            name,
            "--privileged",
            "--entrypoint",
            "dockerd",
            "-e",
            "DOCKER_TLS_CERTDIR=",
            "-v",
            f"{tmp_path}:/testtls:ro,Z",
            "-p",
            "127.0.0.1::2376",
            "data.forgejo.org/oci/docker:dind",
            "--tlsverify",
            "--tlscacert=/testtls/ca.pem",
            "--tlscert=/testtls/server-cert.pem",
            "--tlskey=/testtls/server-key.pem",
            "--host=unix:///var/run/docker.sock",
            "--host=tcp://0.0.0.0:2376",
        )
        port = int(podman("port", name, "2376/tcp").stdout.strip().rsplit(":", 1)[1])
        runtime = RuntimeConnectionConfig(
            name="synthetic-mtls-dind",
            type="docker",
            config={
                "socket": f"tcp://127.0.0.1:{port}",
                "tls_verify": True,
                "operation_policy": {"write_timeout_seconds": 60},
            },
            secrets=secrets,
        )
        adapter = DockerRuntimeAdapter(runtime)
        deadline = time.monotonic() + 60
        while True:
            try:
                assert await adapter.discover_targets() == []
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise AssertionError(podman("logs", "--tail", "30", name).stderr)
                await asyncio.sleep(1)
        client = adapter._get_client()
        assert client.api.verify and client.api.cert
        certificate_paths = [*client.api.cert, client.api.verify]
        assert all(Path(path).stat().st_mode & 0o777 == 0o600 for path in certificate_paths)

        # Security checks still fail closed with a wrong CA or mismatched DNS.
        other_ca, _ = issue("untrusted-test-ca")
        for hostname, ca_text in [
            ("127.0.0.1", pem_cert(other_ca)),
            ("localhost", secrets["ca_cert"]),
        ]:
            invalid = runtime.model_copy(deep=True)
            invalid.config["socket"] = f"tcp://{hostname}:{port}"
            invalid.secrets["ca_cert"] = ca_text
            invalid_adapter = DockerRuntimeAdapter(invalid)
            try:
                with pytest.raises(Exception) as caught:
                    await invalid_adapter.discover_targets()
                assert classify_fetch_error(caught.value).code == "security_validation_failed"
            finally:
                await invalid_adapter.close()

        old = "docker.m.daocloud.io/library/alpine:3.20"
        new = "docker.m.daocloud.io/library/alpine:3.21"
        await asyncio.to_thread(client.images.pull, old)
        await asyncio.to_thread(client.images.pull, new)
        container = await asyncio.to_thread(
            client.containers.run,
            old,
            ["sh", "-c", "while :; do sleep 60; done"],
            name="rt-owned-mtls-workload",
            detach=True,
            environment={"RT_FIXTURE": "original"},
            labels={"rt.followup": "true"},
        )
        ref = {"container_id": container.id, "container_name": container.name}
        snapshot = await adapter.capture_snapshot(ref, await adapter.get_current_image(ref))
        await adapter.validate_snapshot(ref, snapshot)
        updated = await adapter.update_image(ref, new)
        assert updated.updated
        changed = await asyncio.to_thread(client.containers.get, "rt-owned-mtls-workload")
        assert changed.attrs["Config"]["Image"] == new
        recovered = await adapter.recover_from_snapshot(ref, snapshot)
        restored = await asyncio.to_thread(client.containers.get, "rt-owned-mtls-workload")
        assert recovered.updated
        assert restored.attrs["Image"] == snapshot["recovery_evidence"]["image_id"]
        assert "RT_FIXTURE=original" in restored.attrs["Config"]["Env"]
        assert restored.attrs["State"]["Running"] is True
        await adapter.close()
        assert all(not Path(path).exists() for path in certificate_paths)
    finally:
        if adapter is not None:
            await adapter.close()
        podman("rm", "--force", "--time", "0", "--volumes", name, check=False)
        for name in ("ca.pem", "server-cert.pem", "server-key.pem"):
            (tmp_path / name).unlink(missing_ok=True)
