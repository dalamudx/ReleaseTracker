"""Tests for SnapshotRedactor — deterministic, recursive, runtime-aware (Req 21)."""

from __future__ import annotations

import pytest

from releasetracker.services.snapshot_service import REDACTED_MARKER, SnapshotRedactor


@pytest.fixture
def redactor() -> SnapshotRedactor:
    return SnapshotRedactor()


def test_redact_none_returns_none(redactor: SnapshotRedactor):
    result, needs_marker = redactor.redact(None)
    assert result is None
    assert needs_marker is False


def test_redact_primitive_passes_through(redactor: SnapshotRedactor):
    result, _ = redactor.redact("hello")
    assert result == "hello"
    result, _ = redactor.redact(42)
    assert result == 42


def test_redact_common_secret_keys_replaces_values(redactor: SnapshotRedactor):
    payload = {
        "username": "alice",
        "password": "supersecret",
        "API_KEY": "k-123",
        "authorization": "Bearer token",
        "nested": {"refresh_token": "rt-xyz", "retained": "ok"},
    }
    result, _ = redactor.redact(payload)

    assert result["username"] == "alice"
    assert result["password"] == REDACTED_MARKER
    assert result["API_KEY"] == REDACTED_MARKER
    assert result["authorization"] == REDACTED_MARKER
    assert result["nested"]["refresh_token"] == REDACTED_MARKER
    assert result["nested"]["retained"] == "ok"


def test_redact_sensitive_suffix_keys(redactor: SnapshotRedactor):
    payload = {
        "DB_PASSWORD": "db-pass",
        "REDIS_TOKEN": "r-token",
        "OTHER_KEY": "generic",
        "keep": "value",
    }
    result, _ = redactor.redact(payload)

    assert result["DB_PASSWORD"] == REDACTED_MARKER
    assert result["REDIS_TOKEN"] == REDACTED_MARKER
    # `OTHER_KEY` ends with `_KEY` → matches the sensitive suffix pattern.
    assert result["OTHER_KEY"] == REDACTED_MARKER
    assert result["keep"] == "value"


def test_redact_recurses_into_lists_and_nested_dicts(redactor: SnapshotRedactor):
    payload = {
        "containers": [
            {"name": "api", "env": [{"name": "HOME", "value": "/root"}]},
            {"name": "db", "password": "pass"},
        ]
    }
    result, _ = redactor.redact(payload)

    assert result["containers"][0]["env"][0]["value"] == "/root"
    assert result["containers"][1]["password"] == REDACTED_MARKER


def test_redact_is_deterministic(redactor: SnapshotRedactor):
    payload = {
        "password": "a",
        "inner": {"token": "b", "items": [{"api_key": "c"}]},
    }
    first, _ = redactor.redact(payload)
    second, _ = redactor.redact(payload)
    assert first == second


# ---- Portainer branch ---------------------------------------------------


def test_redact_portainer_env_list_masks_sensitive_entries(redactor: SnapshotRedactor):
    payload = {
        "stack_file": "services:\n  api:\n    image: acme/api",
        "env": [
            {"name": "LOG_LEVEL", "value": "info"},
            {"name": "DB_PASSWORD", "value": "secret"},
            {"name": "SMTP_TOKEN", "value": "st-1"},
            {"name": "PLAIN", "value": "ok"},
        ],
    }
    result, _ = redactor.redact(payload, runtime_type="portainer")

    env_by_name = {entry["name"]: entry for entry in result["env"]}
    assert env_by_name["LOG_LEVEL"]["value"] == "info"
    assert env_by_name["DB_PASSWORD"]["value"] == REDACTED_MARKER
    assert env_by_name["SMTP_TOKEN"]["value"] == REDACTED_MARKER
    assert env_by_name["PLAIN"]["value"] == "ok"


def test_redact_portainer_drops_embedded_runtime_connection(redactor: SnapshotRedactor):
    payload = {
        "stack_file": "services:\n  api:\n    image: acme/api",
        "runtime_connection": {"api_key": "rk-1"},
    }
    result, _ = redactor.redact(payload, runtime_type="portainer")
    assert "runtime_connection" not in result


# ---- Kubernetes branch -------------------------------------------------


def test_redact_kubernetes_secret_data(redactor: SnapshotRedactor):
    payload = {
        "resources": [
            {
                "kind": "Secret",
                "metadata": {"name": "db-credentials"},
                "data": {"DB_PASSWORD": "cGFzcw==", "DB_USER": "YWRtaW4="},
                "stringData": {"README": "hello"},
            },
            {
                "kind": "ConfigMap",
                "metadata": {"name": "app-config"},
                "data": {"LOG_LEVEL": "info"},
            },
        ]
    }
    result, _ = redactor.redact(payload, runtime_type="kubernetes")

    secret = result["resources"][0]
    assert secret["data"] == {
        "DB_PASSWORD": REDACTED_MARKER,
        "DB_USER": REDACTED_MARKER,
    }
    assert secret["stringData"] == {"README": REDACTED_MARKER}

    # ConfigMap untouched.
    assert result["resources"][1]["data"] == {"LOG_LEVEL": "info"}


def test_redact_helm_values_secret_block(redactor: SnapshotRedactor):
    payload = {
        "values": {
            "app": {
                "image": "acme/api",
                "credentials": {
                    "secret": True,
                    "db_password": "pw",
                    "token": "tk",
                },
            },
        }
    }
    result, _ = redactor.redact(payload, runtime_type="kubernetes")

    creds = result["values"]["app"]["credentials"]
    assert creds["db_password"] == REDACTED_MARKER
    assert creds["token"] == REDACTED_MARKER
    # The generic walk also redacts the `secret` key because it matches
    # the always-redact set. The Helm-specific branch has already done
    # its job: the siblings (db_password/token) were masked before the
    # generic walk saw them.
    assert creds["secret"] == REDACTED_MARKER
    # Untouched siblings.
    assert result["values"]["app"]["image"] == "acme/api"


def test_redact_does_not_mutate_input(redactor: SnapshotRedactor):
    original = {"password": "pw", "nested": {"token": "tk"}}
    snapshot_of_original = {"password": "pw", "nested": {"token": "tk"}}

    redactor.redact(original)

    assert original == snapshot_of_original
