"""Helm declaration evidence, with an explicit values-only upgrade invariant.

Stored manifests bind the approved pre-state and subsequent drift checks. They
are not an expected post-upgrade manifest: chart templates may change defaults.
This does not attest live Kubernetes resources against their Helm declaration.
"""

from copy import deepcopy
import json

import yaml

from .snapshot_service import REDACTED_MARKER

_DYNAMIC_METADATA = {
    "uid",
    "resourceVersion",
    "generation",
    "creationTimestamp",
    "deletionTimestamp",
    "deletionGracePeriodSeconds",
    "managedFields",
    "selfLink",
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_configuration(value):
    """Reject non-JSON keys/types instead of coercing distinct declarations."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Helm configuration requires string mapping keys")
        for item in value.values():
            _json_configuration(item)
    elif isinstance(value, list):
        for item in value:
            _json_configuration(item)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("Helm configuration requires JSON values")
    _json(value)


def declared_manifest(text):
    """Canonicalize stored declarations, excluding only root runtime fields."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Helm declared manifest is unavailable")
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        raise ValueError("Helm declared manifest is invalid") from None
    result = {}
    for document in documents:
        if document is None:
            continue
        if not isinstance(document, dict):
            raise ValueError("Helm declared manifest is invalid")
        document = deepcopy(document)
        metadata = document.get("metadata")
        if not isinstance(metadata, dict) or not all(
            isinstance(field, str) and field
            for field in (document.get("apiVersion"), document.get("kind"), metadata.get("name"))
        ):
            raise ValueError("Helm declared resource identity is unavailable")
        namespace = metadata.get("namespace", "")
        if not isinstance(namespace, str):
            raise ValueError("Helm declared resource namespace is invalid")
        identity = _json([document["apiVersion"], document["kind"], namespace, metadata["name"]])
        if identity in result:
            raise ValueError("Helm declared resource identity is ambiguous")
        document.pop("status", None)
        for key in _DYNAMIC_METADATA:
            metadata.pop(key, None)
        _json_configuration(document)
        result[identity] = document
    if not result:
        raise ValueError("Helm declared manifest is unavailable")
    return result


def projection(snapshot, *, required=False):
    """Legacy rollback snapshots retain their original revision-only scope."""
    result = {key: snapshot.get(key) for key in ("chart_name", "chart_version", "revision")}
    configuration = snapshot.get("helm_configuration")
    if configuration is None and not required:
        return result, "helm_revision"
    if (
        not isinstance(configuration, dict)
        or type(configuration.get("schema")) is not int
        or configuration["schema"] != 1
        or not isinstance(configuration.get("values"), dict)
    ):
        raise ValueError("Helm user values and declared manifest are required")
    _json_configuration(configuration["values"])
    result["helm_configuration"] = {
        "schema": 1,
        "values": deepcopy(configuration["values"]),
        "manifest": declared_manifest(configuration.get("manifest")),
    }
    return result, "helm_values_and_declared_manifest"


def upgrade_configuration(value):
    """Protect exact user values, preserving list order and marker-like user keys.

    shortcut: generated manifest/default changes are permitted during upgrades;
    full post-upgrade declaration attestation needs verified target rendering.
    """
    configuration = value["helm_configuration"]
    return {
        "helm_values_schema": configuration["schema"],
        "chart_name": value.get("chart_name"),
        "chart_version": value.get("chart_version"),
        "values": configuration["values"],
    }


def changed_configuration(before, after, changed_lines):
    """Mask complete Helm values/manifests, including potentially secret keys."""
    lines, truncated = changed_lines(
        {key: value for key, value in before.items() if key != "helm_configuration"},
        {key: value for key, value in after.items() if key != "helm_configuration"},
    )
    left, right = before.get("helm_configuration", {}), after.get("helm_configuration", {})
    for field in ("values", "manifest"):
        if _json(left.get(field)) != _json(right.get(field)):
            for sign, source in (("-", left), ("+", right)):
                if field in source:
                    lines.append(
                        {
                            "operation": sign,
                            "path": f"/helm_configuration/{field}",
                            "value": REDACTED_MARKER,
                            "redacted": True,
                        }
                    )
    return lines, truncated
