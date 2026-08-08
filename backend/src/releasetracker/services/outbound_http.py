"""Fail-closed outbound HTTP client for administrator-configured destinations.

This module is intentionally scoped to webhooks and HTTP health probes. It resolves and
validates every address, then connects to one of those validated addresses directly so a
second DNS lookup cannot redirect the connection to an internal network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

ALLOWED_OUTBOUND_PORTS: dict[str, frozenset[int]] = {
    "http": frozenset({80, 8080}),
    "https": frozenset({443, 8443, 9443}),
}
DEFAULT_MAX_RESPONSE_BYTES = 65_536
_MAX_HEADER_BYTES = 32_768
_MAX_HEADER_LINE_BYTES = 8_192
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("168.63.129.16"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }
)


class OutboundHTTPError(RuntimeError):
    """Base error whose message is safe to log without exposing a destination URL."""


class OutboundURLRejected(OutboundHTTPError):
    """The destination violates the configured outbound policy."""


class OutboundDNSFailure(OutboundHTTPError):
    """The destination hostname could not be safely resolved."""


class OutboundConnectError(OutboundHTTPError):
    """A connection to a validated address failed."""


class OutboundTLSFailure(OutboundConnectError):
    """TLS negotiation or certificate verification failed."""


class OutboundTimeout(OutboundHTTPError):
    """An outbound deadline elapsed."""


class OutboundProtocolError(OutboundHTTPError):
    """The peer returned a malformed or unsupported HTTP response."""


class OutboundRedirectRejected(OutboundHTTPError):
    """Redirects are never followed by protected outbound requests."""


@dataclass(frozen=True)
class OutboundTimeouts:
    connect: float = 5.0
    read: float = 5.0
    write: float = 5.0
    total: float = 10.0


@dataclass(frozen=True)
class OutboundResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    body_truncated: bool = False

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(frozen=True)
class _ValidatedDestination:
    scheme: str
    hostname: str
    port: int
    request_target: str
    host_header: str
    addresses: tuple[str, ...]


@dataclass
class OutboundHTTPClient:
    """HTTP/1.1 client pinned to validated DNS results.

    Connections are opened directly and never consult proxy environment variables (the
    equivalent of ``trust_env=False`` for higher-level clients).
    """

    timeouts: OutboundTimeouts = field(default_factory=OutboundTimeouts)
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        verify_tls: bool = True,
        max_response_bytes: int | None = None,
    ) -> OutboundResponse:
        try:
            async with asyncio.timeout(self.timeouts.total):
                destination = await validate_outbound_url(url)
                return await self._request_validated(
                    method=method,
                    destination=destination,
                    headers=headers,
                    json_body=json_body,
                    verify_tls=verify_tls,
                    max_response_bytes=(
                        self.max_response_bytes
                        if max_response_bytes is None
                        else max_response_bytes
                    ),
                )
        except TimeoutError as exc:
            raise OutboundTimeout("outbound request exceeded its deadline") from exc

    async def _request_validated(
        self,
        *,
        method: str,
        destination: _ValidatedDestination,
        headers: Mapping[str, str] | None,
        json_body: Any | None,
        verify_tls: bool,
        max_response_bytes: int,
    ) -> OutboundResponse:
        normalized_method = method.upper()
        if normalized_method not in {"GET", "HEAD", "POST"}:
            raise OutboundURLRejected("outbound HTTP method is not allowed")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")

        body = b""
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        request_headers = _prepare_headers(
            headers,
            host=destination.host_header,
            body_length=len(body),
            has_json_body=json_body is not None,
        )
        request_head = (
            f"{normalized_method} {destination.request_target} HTTP/1.1\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in request_headers.items())
            + "\r\n"
        ).encode("iso-8859-1")

        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await self._connect(destination, verify_tls=verify_tls)
            writer.write(request_head)
            if body:
                writer.write(body)
            await asyncio.wait_for(writer.drain(), timeout=self.timeouts.write)
            response = await _read_response(
                reader,
                method=normalized_method,
                read_timeout=self.timeouts.read,
                max_body_bytes=max_response_bytes,
            )
            if 300 <= response.status_code < 400:
                raise OutboundRedirectRejected("outbound redirects are not allowed")
            return response
        except ssl.SSLError as exc:
            raise OutboundTLSFailure("TLS connection failed") from exc
        except TimeoutError as exc:
            raise OutboundTimeout("outbound response read timed out") from exc
        except (ConnectionError, OSError) as exc:
            raise OutboundConnectError("connection to validated destination failed") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _connect(
        self,
        destination: _ValidatedDestination,
        *,
        verify_tls: bool,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        address = destination.addresses[0]
        ssl_context: ssl.SSLContext | bool | None = None
        server_hostname: str | None = None
        if destination.scheme == "https":
            ssl_context = (
                ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
            )
            server_hostname = destination.hostname

        try:
            return await asyncio.wait_for(
                asyncio.open_connection(
                    host=address,
                    port=destination.port,
                    ssl=ssl_context,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=self.timeouts.connect if ssl_context else None,
                ),
                timeout=self.timeouts.connect,
            )
        except ssl.SSLError as exc:
            raise OutboundTLSFailure("TLS connection failed") from exc
        except TimeoutError as exc:
            raise OutboundTimeout("connection to validated destination timed out") from exc
        except OSError as exc:
            raise OutboundConnectError("connection to validated destination failed") from exc


async def validate_outbound_url(url: str) -> _ValidatedDestination:
    """Validate a URL, all DNS answers, and return a connection-pinned destination."""
    if not isinstance(url, str) or not url or len(url) > 4_096:
        raise OutboundURLRejected("outbound URL is missing or too long")
    if any(character in url for character in ("\r", "\n", "\x00")):
        raise OutboundURLRejected("outbound URL contains invalid characters")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise OutboundURLRejected("outbound URL is malformed") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_OUTBOUND_PORTS:
        raise OutboundURLRejected("outbound URL scheme is not allowed")
    if parsed.username is not None or parsed.password is not None:
        raise OutboundURLRejected("outbound URL user information is not allowed")
    if parsed.fragment:
        raise OutboundURLRejected("outbound URL fragments are not allowed")
    if not parsed.hostname:
        raise OutboundURLRejected("outbound URL host is required")

    hostname = parsed.hostname.rstrip(".").lower()
    if not hostname or "%" in hostname or hostname in _METADATA_HOSTNAMES:
        raise OutboundURLRejected("outbound URL host is not allowed")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundURLRejected("outbound URL host is invalid") from exc

    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in ALLOWED_OUTBOUND_PORTS[scheme]:
        raise OutboundURLRejected("outbound URL port is not allowed")

    addresses = await _resolve_public_addresses(hostname, effective_port)
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&?/:@!$'()*+,;%-._~")
    request_target = f"{path}?{query}" if query else path
    default_port = 443 if scheme == "https" else 80
    bracketed_host = f"[{hostname}]" if ":" in hostname else hostname
    host_header = (
        bracketed_host if effective_port == default_port else f"{bracketed_host}:{effective_port}"
    )
    return _ValidatedDestination(
        scheme=scheme,
        hostname=hostname,
        port=effective_port,
        request_target=request_target,
        host_header=host_header,
        addresses=addresses,
    )


async def _resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise OutboundDNSFailure("outbound destination DNS resolution failed") from exc

    addresses: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        raw_address = sockaddr[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise OutboundDNSFailure("outbound DNS returned an invalid address") from exc
        if not _is_allowed_address(address):
            raise OutboundURLRejected("outbound destination resolves to a prohibited address")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)

    if not addresses:
        raise OutboundDNSFailure("outbound destination DNS returned no addresses")
    return tuple(addresses)


def _is_allowed_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address in _METADATA_ADDRESSES:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_allowed_address(address.ipv4_mapped)
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    return address.is_global


def _prepare_headers(
    headers: Mapping[str, str] | None,
    *,
    host: str,
    body_length: int,
    has_json_body: bool,
) -> dict[str, str]:
    protected = {"host", "content-length", "transfer-encoding", "connection", "accept-encoding"}
    prepared: dict[str, str] = {}
    for raw_name, raw_value in (headers or {}).items():
        name = str(raw_name)
        value = str(raw_value)
        if not _HEADER_NAME.fullmatch(name) or name.lower() in protected:
            raise OutboundURLRejected("outbound request contains a prohibited header")
        if any(character in value for character in ("\r", "\n", "\x00")):
            raise OutboundURLRejected("outbound request contains an invalid header value")
        try:
            value.encode("iso-8859-1")
        except UnicodeEncodeError as exc:
            raise OutboundURLRejected("outbound request contains an invalid header value") from exc
        prepared[name] = value

    prepared["Host"] = host
    prepared["Connection"] = "close"
    prepared["Accept-Encoding"] = "identity"
    if has_json_body and not any(name.lower() == "content-type" for name in prepared):
        prepared["Content-Type"] = "application/json"
    prepared["Content-Length"] = str(body_length)
    return prepared


async def _read_response(
    reader: asyncio.StreamReader,
    *,
    method: str,
    read_timeout: float,
    max_body_bytes: int,
) -> OutboundResponse:
    status_line = await _readline(reader, read_timeout)
    try:
        version, raw_status, _reason = status_line.decode("iso-8859-1").rstrip("\r\n").split(" ", 2)
        status_code = int(raw_status)
    except (UnicodeDecodeError, ValueError) as exc:
        raise OutboundProtocolError("outbound peer returned an invalid status line") from exc
    if version not in {"HTTP/1.0", "HTTP/1.1"} or not 100 <= status_code <= 599:
        raise OutboundProtocolError("outbound peer returned an unsupported HTTP response")

    headers: dict[str, str] = {}
    header_bytes = len(status_line)
    while True:
        line = await _readline(reader, read_timeout)
        header_bytes += len(line)
        if header_bytes > _MAX_HEADER_BYTES:
            raise OutboundProtocolError("outbound response headers are too large")
        if line in {b"\r\n", b"\n"}:
            break
        try:
            raw_name, raw_value = line.decode("iso-8859-1").split(":", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise OutboundProtocolError("outbound peer returned an invalid header") from exc
        name = raw_name.strip().lower()
        value = raw_value.strip()
        if not _HEADER_NAME.fullmatch(name):
            raise OutboundProtocolError("outbound peer returned an invalid header name")
        headers[name] = f"{headers[name]}, {value}" if name in headers else value

    if method == "HEAD" or status_code in {204, 304} or 100 <= status_code < 200:
        return OutboundResponse(status_code=status_code, headers=headers, body=b"")

    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if transfer_encoding:
        if transfer_encoding != "chunked":
            raise OutboundProtocolError("outbound response transfer encoding is unsupported")
        body, truncated = await _read_chunked_body(
            reader,
            read_timeout=read_timeout,
            max_body_bytes=max_body_bytes,
        )
    elif "content-length" in headers:
        try:
            content_length = int(headers["content-length"])
        except ValueError as exc:
            raise OutboundProtocolError("outbound response content length is invalid") from exc
        if content_length < 0:
            raise OutboundProtocolError("outbound response content length is invalid")
        to_read = min(content_length, max_body_bytes)
        body = await _readexactly(reader, to_read, read_timeout) if to_read else b""
        truncated = content_length > max_body_bytes
    else:
        body, truncated = await _read_until_eof(
            reader,
            read_timeout=read_timeout,
            max_body_bytes=max_body_bytes,
        )

    return OutboundResponse(
        status_code=status_code,
        headers=headers,
        body=body,
        body_truncated=truncated,
    )


async def _read_chunked_body(
    reader: asyncio.StreamReader,
    *,
    read_timeout: float,
    max_body_bytes: int,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        size_line = await _readline(reader, read_timeout)
        try:
            chunk_size = int(size_line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise OutboundProtocolError("outbound peer returned an invalid chunk size") from exc
        if chunk_size == 0:
            while await _readline(reader, read_timeout) not in {b"\r\n", b"\n"}:
                pass
            break
        remaining = max_body_bytes - total
        keep = min(chunk_size, max(0, remaining))
        if keep:
            chunks.append(await _readexactly(reader, keep, read_timeout))
            total += keep
        if chunk_size > keep:
            truncated = True
            await _discard_exactly(reader, chunk_size - keep, read_timeout)
        if await _readexactly(reader, 2, read_timeout) != b"\r\n":
            raise OutboundProtocolError("outbound peer returned an invalid chunk terminator")
    return b"".join(chunks), truncated


async def _read_until_eof(
    reader: asyncio.StreamReader,
    *,
    read_timeout: float,
    max_body_bytes: int,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while total <= max_body_bytes:
        chunk = await asyncio.wait_for(
            reader.read(min(16_384, max_body_bytes + 1 - total)),
            timeout=read_timeout,
        )
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    body = b"".join(chunks)
    return body[:max_body_bytes], len(body) > max_body_bytes


async def _discard_exactly(
    reader: asyncio.StreamReader,
    length: int,
    read_timeout: float,
) -> None:
    remaining = length
    while remaining:
        chunk = await asyncio.wait_for(reader.read(min(16_384, remaining)), timeout=read_timeout)
        if not chunk:
            raise OutboundProtocolError("outbound response body ended unexpectedly")
        remaining -= len(chunk)


async def _readline(reader: asyncio.StreamReader, read_timeout: float) -> bytes:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=read_timeout)
    except ValueError as exc:
        raise OutboundProtocolError("outbound response line is too large") from exc
    if not line:
        raise OutboundProtocolError("outbound peer closed the response unexpectedly")
    if len(line) > _MAX_HEADER_LINE_BYTES:
        raise OutboundProtocolError("outbound response line is too large")
    return line


async def _readexactly(
    reader: asyncio.StreamReader,
    length: int,
    read_timeout: float,
) -> bytes:
    try:
        return await asyncio.wait_for(reader.readexactly(length), timeout=read_timeout)
    except asyncio.IncompleteReadError as exc:
        raise OutboundProtocolError("outbound response body ended unexpectedly") from exc


__all__ = [
    "ALLOWED_OUTBOUND_PORTS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "OutboundConnectError",
    "OutboundDNSFailure",
    "OutboundHTTPClient",
    "OutboundHTTPError",
    "OutboundProtocolError",
    "OutboundRedirectRejected",
    "OutboundResponse",
    "OutboundTLSFailure",
    "OutboundTimeout",
    "OutboundTimeouts",
    "OutboundURLRejected",
    "validate_outbound_url",
]
