"""Golden-file tests for SnapshotRedactor (Req 21.1, 21.4, 21.5)."""

from __future__ import annotations

import copy

import pytest

from releasetracker.services.snapshot_service import (
    REDACTED_MARKER,
    SnapshotRedactor,
)


@pytest.fixture
def redactor() -> SnapshotRedactor:
    return SnapshotRedactor()


def _assert_deterministic(redactor: SnapshotRedactor, payload: dict, runtime_type: str) -> dict:
    first, _ = redactor.redact(copy.deepcopy(payload), runtime_type=runtime_type)
    second, _ = redactor.redact(copy.deepcopy(payload), runtime_type=runtime_type)
    assert first == second, "redactor must be deterministic"
    return first


# ---- Generic key-based redaction ----------------------------------------


def test_always_redact_keys_are_masked(redactor: SnapshotRedactor):
    payload = {
        "image": "acme/api:1.0.0",
        "password": "hunter2",
        "token": "abc.def.ghi",
        "api_key": "AKIA...",
        "nested": {"client_secret": "s3cret", "value": "plain"},
    }
    result, needs_marker = redactor.redact(payload)

    assert result["image"] == "acme/api:1.0.0"
    assert result["password"] == REDACTED_MARKER
    assert result["token"] == REDACTED_MARKER
    assert result["api_key"] == REDACTED_MARKER
    assert result["nested"]["client_secret"] == REDACTED_MARKER
    assert result["nested"]["value"] == "plain"
    assert needs_marker is False


def test_sensitive_suffix_keys_match_case_insensitive(redactor: SnapshotRedactor):
    payload = {
        "DB_PASSWORD": "hunter2",
        "redis_token": "xyz",
        "MY_API_KEY": "key",
        "some_value": "keep",
    }
    result, _ = redactor.redact(payload)
    assert result["DB_PASSWORD"] == REDACTED_MARKER
    assert result["redis_token"] == REDACTED_MARKER
    assert result["MY_API_KEY"] == REDACTED_MARKER
    assert result["some_value"] == "keep"


def test_redaction_recurses_into_nested_lists_and_dicts(redactor: SnapshotRedactor):
    payload = {
        "env_vars": [
            {"name": "LOG_LEVEL", "value": "info"},
            {"name": "DB_PASSWORD", "value": "hunter2"},
            [{"token": "inner", "other": "keep"}],
        ],
        "deep": {"a": {"b": {"secret": "x"}}},
    }
    result, _ = redactor.redact(payload)

    # list → dict recursion
    assert result["env_vars"][0] == {"name": "LOG_LEVEL", "value": "info"}
    assert result["env_vars"][1]["name"] == "DB_PASSWORD"
    # The generic redactor only redacts by KEY name, not value-based
    # introspection, so env var name/value pairs are handled by the
    # portainer branch (see below). Here, ``value`` is kept as-is.
    assert result["env_vars"][1]["value"] == "hunter2"
    # nested list of dicts
    assert result["env_vars"][2][0]["token"] == REDACTED_MARKER
    assert result["env_vars"][2][0]["other"] == "keep"
    # deep nesting
    assert result["deep"]["a"]["b"]["secret"] == REDACTED_MARKER


def test_redaction_is_deterministic(redactor: SnapshotRedactor):
    payload = {
        "password": "hunter2",
        "nested": [{"token": "a"}, {"token": "b"}],
    }
    _assert_deterministic(redactor, payload, runtime_type="docker")


# ---- Portainer branch ---------------------------------------------------


def test_portainer_env_entries_redact_by_sensitive_name(redactor: SnapshotRedactor):
    payload = {
        "stack_id": 42,
        "stack_type": "standalone",
        "env": [
            {"name": "LOG_LEVEL", "value": "info"},
            {"name": "DATABASE_PASSWORD", "value": "hunter2"},
            {"name": "redis_token", "value": "xyz"},
            {"name": "HOSTNAME", "value": "prod-01"},
        ],
        "stack_file": "services:\n  api:\n    image: acme/api:1\n",
    }
    result = _assert_deterministic(redactor, payload, runtime_type="portainer")

    assert result["env"][0] == {"name": "LOG_LEVEL", "value": "info"}
    assert result["env"][1]["value"] == REDACTED_MARKER
    assert result["env"][2]["value"] == REDACTED_MARKER
    assert result["env"][3]["value"] == "prod-01"


def test_portainer_strips_embedded_runtime_connection(redactor: SnapshotRedactor):
    payload = {
        "stack_id": 42,
        "stack_type": "standalone",
        "env": [],
        "stack_file": "...",
        "runtime_connection": {"api_key": "DONOTLEAK"},
    }
    result, _ = redactor.redact(payload, runtime_type="portainer")
    assert "runtime_connection" not in result


# ---- Kubernetes / Helm branch -------------------------------------------


def test_kubernetes_secret_data_and_string_data_masked(redactor: SnapshotRedactor):
    payload = {
        "resources": [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "db-creds"},
                "data": {"username": "YWRtaW4=", "password": "aHVudGVyMg=="},
                "stringData": {"token": "plain-token"},
            },
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "ignore-me"},
                "data": {"retain": "value"},
            },
        ]
    }
    result = _assert_deterministic(redactor, payload, runtime_type="kubernetes")

    secret = result["resources"][0]
    assert secret["data"] == {
        "username": REDACTED_MARKER,
        "password": REDACTED_MARKER,
    }
    assert secret["stringData"] == {"token": REDACTED_MARKER}
    # ConfigMap untouched by the Secret rule, though its key-based
    # pattern would still hit ``password``-style keys if present.
    assert result["resources"][1]["data"] == {"retain": "value"}


def test_helm_values_with_secret_flag_redact_sibling_values(redactor: SnapshotRedactor):
    payload = {
        "values": {
            "redis": {
                "secret": True,
                "password": "hunter2",
                "url": "redis://redis:6379",
            },
            "plain": {"value": "keep"},
        }
    }
    result = _assert_deterministic(redactor, payload, runtime_type="kubernetes")
    redis = result["values"]["redis"]
    # The bare ``secret`` key is itself in the always-redact set so its
    # marker value is overwritten with REDACTED_MARKER. That is fine —
    # what matters is that sibling values are masked, not that the flag
    # survives. Callers parsing this payload must not rely on the flag.
    assert redis["password"] == REDACTED_MARKER
    # URL siblings are redacted too because the parent has secret=true.
    assert redis["url"] == REDACTED_MARKER
    assert result["values"]["plain"] == {"value": "keep"}


# ---- Read-time safety net -----------------------------------------------


def test_read_time_redaction_covers_unmarked_keys(redactor: SnapshotRedactor):
    """Even if a write-time path missed a key, the generic walk catches
    the common cases on read so response bodies never leak."""
    payload = {
        "resources": [
            {
                "kind": "Secret",
                "data": {"api_key": "leak-me"},
            }
        ],
        "legacy_payload": {"bearer": "BearerLegacy"},
    }
    result = _assert_deterministic(redactor, payload, runtime_type="kubernetes")
    assert result["resources"][0]["data"] == {"api_key": REDACTED_MARKER}
    assert result["legacy_payload"]["bearer"] == REDACTED_MARKER
