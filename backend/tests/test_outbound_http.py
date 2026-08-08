"""Security regression tests for the scoped outbound HTTP policy."""

from __future__ import annotations

import asyncio
import socket

import pytest

from releasetracker.services import outbound_http
from releasetracker.services.outbound_http import (
    OutboundHTTPClient,
    OutboundRedirectRejected,
    OutboundTimeout,
    OutboundTimeouts,
    OutboundURLRejected,
    validate_outbound_url,
)


async def _set_dns_answers(monkeypatch, *addresses: str) -> None:
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, *, family, type):
        del host
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                type,
                socket.IPPROTO_TCP,
                "",
                (address, port, 0, 0) if ":" in address else (address, port),
            )
            for address in addresses
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
    ],
)
async def test_outbound_policy_rejects_non_public_ipv4_and_ipv6(
    monkeypatch,
    address: str,
):
    await _set_dns_answers(monkeypatch, address)

    with pytest.raises(OutboundURLRejected, match="prohibited address"):
        await validate_outbound_url("https://service.example/hook")


@pytest.mark.asyncio
async def test_outbound_policy_rejects_mixed_public_private_dns_answers(monkeypatch):
    """Every answer is checked, so a rebindable mixed answer fails closed."""
    await _set_dns_answers(monkeypatch, "93.184.216.34", "10.0.0.8")

    with pytest.raises(OutboundURLRejected, match="prohibited address"):
        await validate_outbound_url("https://service.example/health")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("file:///etc/passwd", "scheme"),
        ("ftp://service.example/hook", "scheme"),
        ("https://user:secret@service.example/hook", "user information"),
        ("https://metadata.google.internal/computeMetadata/v1/", "host"),
        ("https://service.example:22/hook", "port"),
        ("https:///missing-host", "host"),
    ],
)
async def test_outbound_policy_rejects_invalid_destinations(
    monkeypatch,
    url: str,
    message: str,
):
    await _set_dns_answers(monkeypatch, "93.184.216.34")

    with pytest.raises(OutboundURLRejected, match=message):
        await validate_outbound_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "address", "port"),
    [
        ("http://service.example:8080/health", "93.184.216.34", 8080),
        ("https://service.example:9443/hook", "2606:4700:4700::1111", 9443),
    ],
)
async def test_outbound_policy_accepts_allowed_public_destinations(
    monkeypatch,
    url: str,
    address: str,
    port: int,
):
    await _set_dns_answers(monkeypatch, address)

    destination = await validate_outbound_url(url)

    assert destination.addresses == (address,)
    assert destination.port == port


class _MemoryWriter:
    def __init__(self) -> None:
        self.closed = False
        self.request = bytearray()

    def write(self, data: bytes) -> None:
        self.request.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_outbound_client_rejects_redirect_without_following(monkeypatch):
    destination = outbound_http._ValidatedDestination(
        scheme="https",
        hostname="service.example",
        port=443,
        request_target="/hook",
        host_header="service.example",
        addresses=("93.184.216.34",),
    )

    async def fake_validate(url: str):
        del url
        return destination

    connects = 0
    writer = _MemoryWriter()

    async def fake_connect(destination_arg, *, verify_tls):
        nonlocal connects
        del destination_arg, verify_tls
        connects += 1
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://169.254.169.254/latest/meta-data/\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        reader.feed_eof()
        return reader, writer

    client = OutboundHTTPClient()
    monkeypatch.setattr(outbound_http, "validate_outbound_url", fake_validate)
    monkeypatch.setattr(client, "_connect", fake_connect)

    with pytest.raises(OutboundRedirectRejected, match="redirects"):
        await client.request("POST", "https://service.example/hook", json_body={"ok": True})

    assert connects == 1
    assert writer.closed is True


@pytest.mark.asyncio
async def test_outbound_client_caps_streamed_response_body(monkeypatch):
    destination = outbound_http._ValidatedDestination(
        scheme="http",
        hostname="service.example",
        port=80,
        request_target="/health",
        host_header="service.example",
        addresses=("93.184.216.34",),
    )

    async def fake_validate(url: str):
        del url
        return destination

    writer = _MemoryWriter()

    async def fake_connect(destination_arg, *, verify_tls):
        del destination_arg, verify_tls
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 32\r\n\r\n" + b"x" * 32)
        reader.feed_eof()
        return reader, writer

    client = OutboundHTTPClient(max_response_bytes=16)
    monkeypatch.setattr(outbound_http, "validate_outbound_url", fake_validate)
    monkeypatch.setattr(client, "_connect", fake_connect)

    response = await client.request("GET", "http://service.example/health")

    assert response.body == b"x" * 16
    assert response.body_truncated is True
    assert writer.closed is True


@pytest.mark.asyncio
async def test_outbound_client_enforces_read_timeout(monkeypatch):
    destination = outbound_http._ValidatedDestination(
        scheme="http",
        hostname="service.example",
        port=80,
        request_target="/slow",
        host_header="service.example",
        addresses=("93.184.216.34",),
    )

    async def fake_validate(url: str):
        del url
        return destination

    writer = _MemoryWriter()

    async def fake_connect(destination_arg, *, verify_tls):
        del destination_arg, verify_tls
        return asyncio.StreamReader(), writer

    client = OutboundHTTPClient(
        timeouts=OutboundTimeouts(connect=0.1, read=0.01, write=0.1, total=0.2)
    )
    monkeypatch.setattr(outbound_http, "validate_outbound_url", fake_validate)
    monkeypatch.setattr(client, "_connect", fake_connect)

    with pytest.raises(OutboundTimeout, match="read timed out"):
        await client.request("GET", "http://service.example/slow")

    assert writer.closed is True
