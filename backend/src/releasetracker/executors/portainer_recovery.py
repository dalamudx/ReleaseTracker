"""Read-only Docker evidence for standalone Portainer snapshot recovery.

No engine writes or registry pulls. Recovery restores the original Compose bytes;
if a local mutable alias no longer resolves to the captured image, refuse to
restore instead of silently deploying today's image under yesterday's tag.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

import yaml

_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def service_spec(stack_file: str) -> dict:
    document = yaml.safe_load(stack_file)
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict) or not services:
        raise ValueError("recovery requires explicit Compose services")
    if document.get("include"):
        raise ValueError("recovery cannot verify included Compose services")
    result = {}
    for name, cfg in services.items():
        if not isinstance(name, str) or not isinstance(cfg, dict):
            raise ValueError("recovery service configuration is invalid")
        image = cfg.get("image")
        if not isinstance(image, str) or not image.strip() or "$" in image:
            raise ValueError(f"recovery requires an explicit image for service {name}")
        if any(cfg.get(key) for key in ("build", "extends", "profiles")):
            raise ValueError(f"recovery cannot verify build/extends/profiles for service {name}")
        if cfg.get("pull_policy") not in (None, "missing", "if_not_present", "never"):
            raise ValueError(f"recovery cannot force an image pull for service {name}")
        deploy = cfg.get("deploy") or {}
        if not isinstance(deploy, dict):
            raise ValueError("recovery deploy configuration is invalid")
        count = deploy.get("replicas", cfg.get("scale", 1))
        if type(count) is not int or count < 1:
            raise ValueError(f"recovery requires a positive replica count for service {name}")
        if "scale" in cfg and cfg["scale"] != count:
            raise ValueError(f"recovery replica configuration conflicts for service {name}")
        result[name] = {"image": image, "replicas": count}
    return result


def _healthcheck(attrs: dict) -> bool:
    config = attrs.get("Config") or {}
    test = (config.get("Healthcheck") or {}).get("Test")
    return bool(test and test != ["NONE"])


async def engine_id(adapter, ref: dict) -> str:
    info = await adapter._request_json(
        "GET",
        f"/api/endpoints/{ref['endpoint_id']}/docker/info",
        not_found_message="recovery engine identity unavailable",
    )
    identity = info.get("ID")
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("recovery engine identity unavailable")
    return identity


async def containers(adapter, ref: dict) -> dict:
    prefix = f"/api/endpoints/{ref['endpoint_id']}/docker"
    listing = await adapter._request_payload(
        "GET",
        f"{prefix}/containers/json",
        params={
            "all": "true",
            "filters": json.dumps({"label": [f"com.docker.compose.project={ref['stack_name']}"]}),
        },
    )
    if not isinstance(listing, list):
        raise ValueError("recovery container inventory is invalid")
    groups, seen = {}, set()
    for item in listing:
        container_id = item.get("Id") if isinstance(item, dict) else None
        if not isinstance(container_id, str) or not container_id or container_id in seen:
            raise ValueError("recovery container identity missing or duplicated")
        seen.add(container_id)
        attrs = await adapter._request_json(
            "GET",
            f"{prefix}/containers/{quote(container_id, safe='')}/json",
            not_found_message="recovery container disappeared",
        )
        config = attrs.get("Config") or {}
        labels = config.get("Labels") or {}
        if (
            attrs.get("Id") != container_id
            or labels.get("com.docker.compose.project") != ref["stack_name"]
        ):
            raise ValueError("recovery container project identity mismatch")
        if str(labels.get("com.docker.compose.oneoff", "false")).lower() == "true":
            continue
        service = labels.get("com.docker.compose.service")
        if not isinstance(service, str) or not service:
            raise ValueError("recovery container service identity missing")
        groups.setdefault(service, []).append(
            {
                "id": container_id,
                "image_id": attrs.get("Image"),
                "image": config.get("Image"),
                "healthcheck": _healthcheck(attrs),
                "state": attrs.get("State") or {},
                "restarts": attrs.get("RestartCount"),
            }
        )
    return groups


async def capture(adapter, ref: dict, stack_file: str) -> dict:
    spec = service_spec(stack_file)
    identity = await engine_id(adapter, ref)
    groups = await containers(adapter, ref)
    if set(groups) != set(spec):
        raise ValueError("recovery service inventory does not match Compose configuration")
    evidence = {}
    for name, expected in spec.items():
        rows = groups[name]
        ids = {row["image_id"] for row in rows}
        checks = {row["healthcheck"] for row in rows}
        if (
            len(rows) != expected["replicas"]
            or len(ids) != 1
            or len(checks) != 1
            or any(row["image"] != expected["image"] for row in rows)
        ):
            raise ValueError(f"recovery evidence is ambiguous for service {name}")
        image_id = next(iter(ids))
        if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
            raise ValueError(f"recovery immutable image evidence missing for service {name}")
        evidence[name] = {**expected, "image_id": image_id, "healthcheck": next(iter(checks))}
    if await engine_id(adapter, ref) != identity:
        raise ValueError("recovery engine changed during snapshot capture")
    return {"schema": 1, "engine_id": identity, "services": evidence}


def validate(snapshot: dict) -> dict:
    evidence = snapshot.get("recovery_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema") != 1:
        raise ValueError("snapshot lacks verified native recovery evidence")
    if not isinstance(evidence.get("engine_id"), str) or not evidence["engine_id"].strip():
        raise ValueError("snapshot recovery engine identity missing")
    spec = service_spec(snapshot["stack_file"])
    config = yaml.safe_load(snapshot["stack_file"])["services"]
    for name, expected in spec.items():
        image = expected["image"]
        last = image.rsplit("/", 1)[-1]
        # Compose pulls :latest even under its default "missing" policy.
        # Preserve the file verbatim, but never let rollback refresh that alias.
        if "@" not in image and (":" not in last or last.endswith(":latest")):
            if config[name].get("pull_policy") != "never":
                raise ValueError(f"recovery requires pull_policy=never for latest service {name}")
    services = evidence.get("services")
    if not isinstance(services, dict) or set(spec) != set(services):
        raise ValueError("snapshot recovery service evidence incomplete")
    for name, expected in spec.items():
        item = services[name]
        if (
            not isinstance(item, dict)
            or item.get("image") != expected["image"]
            or type(item.get("replicas")) is not int
            or item["replicas"] != expected["replicas"]
            or not isinstance(item.get("image_id"), str)
            or not _IMAGE_ID.fullmatch(item["image_id"])
            or type(item.get("healthcheck")) is not bool
        ):
            raise ValueError(f"snapshot recovery evidence invalid for service {name}")
    return evidence


async def preflight(adapter, ref: dict, evidence: dict) -> None:
    if await engine_id(adapter, ref) != evidence["engine_id"]:
        raise ValueError("recovery engine identity changed")
    prefix = f"/api/endpoints/{ref['endpoint_id']}/docker"
    for name, expected in evidence["services"].items():
        image = await adapter._request_json(
            "GET",
            f"{prefix}/images/{quote(expected['image'], safe='')}/json",
            not_found_message=f"recovery image is no longer available for service {name}",
        )
        if image.get("Id") != expected["image_id"]:
            raise ValueError(f"recovery image alias changed for service {name}")


async def probe(adapter, ref: dict, evidence: dict) -> tuple[bool, tuple]:
    if await engine_id(adapter, ref) != evidence["engine_id"]:
        raise ValueError("recovery engine identity changed after restore")
    groups = await containers(adapter, ref)
    if set(groups) != set(evidence["services"]):
        return False, ()
    stable = []
    for name, expected in evidence["services"].items():
        rows = groups[name]
        if len(rows) != expected["replicas"]:
            return False, ()
        for row in rows:
            state = row["state"]
            if row["image_id"] != expected["image_id"] or row["image"] != expected["image"]:
                return False, ()
            if (
                state.get("Status") != "running"
                or state.get("Running") is not True
                or state.get("Paused")
                or state.get("Restarting")
                or state.get("Dead")
            ):
                return False, ()
            if row["healthcheck"] != expected["healthcheck"]:
                return False, ()
            if row["healthcheck"] and (state.get("Health") or {}).get("Status") != "healthy":
                return False, ()
            if type(row["restarts"]) is not int or row["restarts"] < 0:
                return False, ()
            stable.append((row["id"], row["image_id"], row["restarts"]))
    return True, tuple(sorted(stable))
