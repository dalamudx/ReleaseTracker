"""Real SDK + local mutual-TLS integration; no external runtime or secrets."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.podman import PodmanRuntimeAdapter
from releasetracker.executors.runtime_tls import RuntimeTLSMaterial


def issue(name, issuer_cert=None, issuer_key=None, purpose=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_cert.subject if issuer_cert else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.BasicConstraints(ca=issuer_cert is None, path_length=None), critical=True
        )
    )
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
    )
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer_key or key).public_key()), False
    )
    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=True,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=issuer_cert is None,
            crl_sign=issuer_cert is None,
            encipher_only=False,
            decipher_only=False,
        ),
        True,
    )
    if purpose:
        builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
    if purpose == ExtendedKeyUsageOID.SERVER_AUTH:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
    return builder.sign(issuer_key or key, hashes.SHA256()), key


def pem_cert(cert):
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def pem_key(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def tls_runtime_server(tmp_path):
    ca, ca_key = issue("test-ca")
    server_cert, server_key = issue("internal.test", ca, ca_key, ExtendedKeyUsageOID.SERVER_AUTH)
    client_cert, client_key = issue("runtime-client", ca, ca_key, ExtendedKeyUsageOID.CLIENT_AUTH)
    paths = {}
    for name, value in {
        "ca": pem_cert(ca),
        "cert": pem_cert(server_cert),
        "key": pem_key(server_key),
    }.items():
        paths[name] = tmp_path / name
        paths[name].write_text(value)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = (
                {"ApiVersion": "1.44", "MinAPIVersion": "1.24", "Os": "linux"}
                if self.path == "/version"
                else []
            )
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(paths["cert"], paths["key"])
    context.load_verify_locations(paths["ca"])
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, {
        "ca_cert": pem_cert(ca),
        "client_cert": pem_cert(client_cert),
        "client_key": pem_key(client_key),
    }, requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize(
    "kind,adapter_type", [("docker", DockerRuntimeAdapter), ("podman", PodmanRuntimeAdapter)]
)
async def test_real_sdk_mutual_tls_to_ip_and_file_lifetime(tls_runtime_server, kind, adapter_type):
    port, secrets, requests = tls_runtime_server
    runtime = RuntimeConnectionConfig(
        name="tls",
        type=kind,
        config={"socket": f"tcp://127.0.0.1:{port}", "tls_verify": True},
        secrets=secrets,
    )
    adapter = adapter_type(runtime)
    try:
        assert await adapter.discover_targets() == []
        session = adapter._client.api
        paths = [*session.cert, session.verify]
        for path in paths:
            assert Path(path).is_file()
            assert Path(path).stat().st_mode & 0o777 == 0o600
        assert (
            session.get_adapter(
                f"https://127.0.0.1:{port}/v1/containers/json"
            ).poolmanager.connection_pool_kw["assert_hostname"]
            is False
        )
        assert (
            "assert_hostname"
            not in session.get_adapter(
                "https://different-host.test/"
            ).poolmanager.connection_pool_kw
        )
        assert any("containers/json" in path for path in requests)
        if kind == "podman":
            assert any("/libpod/" in path for path in requests)
    finally:
        await adapter.close()
    assert all(not Path(path).exists() for path in paths)
    await adapter.close()


async def test_docker_failed_initialization_cleans_pem_files(monkeypatch, tmp_path):
    import docker
    import releasetracker.executors.runtime_tls as tls

    original = tls.tempfile.TemporaryDirectory
    monkeypatch.setattr(
        tls.tempfile, "TemporaryDirectory", lambda **kw: original(dir=tmp_path, **kw)
    )

    def fail(**kwargs):
        cert, key = kwargs["tls"].cert
        assert Path(cert).read_text() == "certificate"
        assert Path(key).read_text() == "private-key"
        raise RuntimeError("connection failed")

    monkeypatch.setattr(docker, "DockerClient", fail)
    adapter = DockerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="tls",
            type="docker",
            config={"socket": "tcp://127.0.0.1:2376", "tls_verify": True},
            secrets={"client_cert": "certificate", "client_key": "private-key"},
        )
    )
    with pytest.raises(RuntimeError, match="connection failed"):
        await adapter.discover_targets()
    assert list(tmp_path.iterdir()) == []


def test_tls_pair_must_be_complete():
    with pytest.raises(ValueError, match="provided together"):
        RuntimeTLSMaterial({"client_cert": "certificate"})


@pytest.mark.parametrize(
    "kind,adapter_type", [("docker", DockerRuntimeAdapter), ("podman", PodmanRuntimeAdapter)]
)
async def test_ip_tls_still_rejects_untrusted_ca(tls_runtime_server, kind, adapter_type):
    port, secrets, _ = tls_runtime_server
    secrets = {k: v for k, v in secrets.items() if k != "ca_cert"}
    adapter = adapter_type(
        RuntimeConnectionConfig(
            name="tls",
            type=kind,
            config={"socket": f"tcp://127.0.0.1:{port}", "tls_verify": True},
            secrets=secrets,
        )
    )
    try:
        with pytest.raises(Exception) as caught:
            await adapter.discover_targets()
        from releasetracker.services.task_queue import classify_fetch_error

        assert classify_fetch_error(caught.value).code == "security_validation_failed"
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "kind,adapter_type", [("docker", DockerRuntimeAdapter), ("podman", PodmanRuntimeAdapter)]
)
async def test_dns_endpoint_still_enforces_certificate_hostname(
    tls_runtime_server, kind, adapter_type
):
    port, secrets, _ = tls_runtime_server
    adapter = adapter_type(
        RuntimeConnectionConfig(
            name="tls",
            type=kind,
            config={"socket": f"tcp://localhost:{port}", "tls_verify": True},
            secrets=secrets,
        )
    )
    try:
        with pytest.raises(Exception) as caught:
            await adapter.discover_targets()
        from releasetracker.services.task_queue import classify_fetch_error

        assert classify_fetch_error(caught.value).code == "security_validation_failed"
    finally:
        await adapter.close()
