"""Identity for Portainer's authenticated Docker-compatible endpoint.

Podman's compatibility /info ID is generated per request. Bind that engine to
its Portainer route and host/storage boundary instead, only after /version
explicitly confirms Podman. This is not a daemon-generation identifier: native
container, project, replica and immutable-image evidence remains mandatory.
"""

import hashlib
import json
from pathlib import PurePosixPath

PREFIX = "podman-compat-v1:"


def _text(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("recovery Podman endpoint identity unavailable")
    return value


async def read(adapter, ref, info):
    # These extensions identify a Podman compatibility response, not a Docker
    # version number or an unstable UUID. Never learn/cache identity from drift.
    if "BuildahVersion" not in info and "Rootless" not in info:
        identifier = info.get("ID")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier.startswith(PREFIX)
        ):
            raise ValueError("recovery engine identity unavailable")
        return identifier  # Preserve existing Docker snapshot identities verbatim.

    endpoint_id = ref.get("endpoint_id")
    if (
        type(endpoint_id) is not int
        or endpoint_id < 1
        or endpoint_id != adapter._runtime_endpoint_id()
    ):
        raise ValueError("recovery Podman endpoint identity mismatch")
    prefix = f"/api/endpoints/{endpoint_id}"
    version = await adapter._request_json(
        "GET", prefix + "/docker/version", not_found_message="recovery engine version unavailable"
    )
    components = version.get("Components")
    if not isinstance(components, list) or not any(
        isinstance(item, dict) and item.get("Name") == "Podman Engine" for item in components
    ):
        raise ValueError("recovery Podman engine could not be verified")

    endpoint = await adapter._request_json(
        "GET", prefix, not_found_message="recovery Podman endpoint unavailable"
    )
    if (
        type(endpoint.get("Id")) is not int
        or endpoint["Id"] != endpoint_id
        or type(endpoint.get("Type")) is not int
        or endpoint["Type"] < 1
    ):
        raise ValueError("recovery Podman endpoint identity mismatch")
    root = _text(info.get("DockerRootDir"))
    if not root.startswith("/") or root == "/" or any(p in (".", "..") for p in root.split("/")):
        raise ValueError("recovery Podman storage identity unavailable")
    if type(info.get("Rootless")) is not bool:
        raise ValueError("recovery Podman rootless identity unavailable")
    tls = endpoint.get("TLSConfig")
    if not isinstance(tls, dict) or any(
        type(tls.get(key)) is not bool for key in ("TLS", "TLSSkipVerify")
    ):
        raise ValueError("recovery Podman endpoint TLS identity unavailable")
    edge_id = endpoint.get("EdgeID")
    if edge_id is not None and not isinstance(edge_id, str):
        raise ValueError("recovery Podman endpoint identity unavailable")
    identity = {
        "portainer": adapter._runtime_base_url(),
        "endpoint": {
            "id": endpoint_id,
            "type": endpoint["Type"],
            "url": _text(endpoint.get("URL")),
            "edge_id": edge_id or "",
            "tls": {key: tls[key] for key in ("TLS", "TLSSkipVerify")},
        },
        "host": _text(info.get("Name")),
        "storage_root": str(PurePosixPath(root)),
        "storage_driver": _text(info.get("Driver")),
        "rootless": info["Rootless"],
        "os": _text(info.get("OSType")),
        "architecture": _text(info.get("Architecture")),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PREFIX + digest
