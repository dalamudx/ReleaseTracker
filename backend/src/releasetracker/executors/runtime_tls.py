"""SDK-compatible TLS files, owned for exactly the runtime client's lifetime."""

from __future__ import annotations

import ipaddress
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


class RuntimeTLSMaterial:
    def __init__(self, secrets):
        self._directory = tempfile.TemporaryDirectory(prefix="rt-runtime-tls-")
        try:
            paths = {}
            for field in ("client_cert", "client_key", "ca_cert"):
                value = secrets.get(field)
                if isinstance(value, str) and value.strip():
                    path = Path(self._directory.name) / field
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(value)
                    paths[field] = str(path)
            if bool(paths.get("client_cert")) != bool(paths.get("client_key")):
                raise ValueError("TLS client certificate and private key must be provided together")
            self.client_cert = (
                (paths["client_cert"], paths["client_key"]) if "client_cert" in paths else None
            )
            self.ca_cert = paths.get("ca_cert")
        except BaseException:
            self.cleanup()
            raise

    def cleanup(self):
        self._directory.cleanup()


def _is_ip_endpoint(endpoint):
    try:
        ipaddress.ip_address(urlparse(endpoint or "").hostname or "")
        return True
    except ValueError:
        return False


class _IPTLSAdapter(HTTPAdapter):
    """Keep CA verification, but allow internal certificates without IP SANs."""

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        # Use the same TLS defaults as requests/urllib3, preserving CA checks.
        context = create_urllib3_context()
        context.check_hostname = False
        kwargs.update(ssl_context=context, assert_hostname=False)
        return super().init_poolmanager(connections, maxsize, block=block, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host, kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        # requests 2.32 injects a default SSL context for verify=True.
        kwargs["ssl_context"] = self.poolmanager.connection_pool_kw["ssl_context"]
        kwargs["assert_hostname"] = False
        return host, kwargs


def configure_ip_hostname_verification(session, endpoint):
    if _is_ip_endpoint(endpoint):
        origin = urlparse(endpoint)._replace(
            scheme="https", path="/", params="", query="", fragment=""
        )
        session.mount(origin.geturl(), _IPTLSAdapter())


def docker_tls_config(docker, material, endpoint):
    class TLSConfig(docker.tls.TLSConfig):
        def configure_client(self, client):
            super().configure_client(client)
            configure_ip_hostname_verification(client, endpoint)

    return TLSConfig(client_cert=material.client_cert, ca_cert=material.ca_cert, verify=True)


def close_runtime_client(client, material):
    try:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    finally:
        if material is not None:
            material.cleanup()
