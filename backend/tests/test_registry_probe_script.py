"""Public probe reports failures honestly, redacts diagnostics, and never opens a DB."""

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def probe_module():
    path = Path(__file__).parents[1] / "scripts" / "probe_registry_manifest.py"
    spec = importlib.util.spec_from_file_location("public_registry_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "missing_digest",
        "short_result",
        "read_timeout",
        "blob_deadline",
        "unexpected_blob",
    ],
)
async def test_probe_reports_only_verified_results(probe_module, monkeypatch, case):
    wire, constructors = [], []

    class Tracker:
        def __init__(self, *args, **kwargs):
            constructors.append(kwargs)

        async def _request_with_redirect_policy(self, client, method, url, **kwargs):
            wire.append(url)
            if case == "read_timeout":
                raise httpx.ReadTimeout("synthetic-private-error-detail")
            return httpx.Response(200)

        async def fetch_all(self, limit):
            await self._request_with_redirect_policy(
                None,
                "GET",
                "https://registry.test/v2/team/app/tags/list?token=synthetic-private",
                timeout=10,
            )
            if case in {"blob_deadline", "unexpected_blob"}:
                await self._request_with_redirect_policy(
                    None,
                    "GET",
                    "https://registry.test/v2/team/app/blobs/sha256:test",
                    timeout=6 if case == "blob_deadline" else 5,
                )
            if case == "short_result":
                return []
            return [SimpleNamespace(commit_sha=None if case == "missing_digest" else "sha256:test")]

    monkeypatch.setattr(probe_module, "DockerTracker", Tracker)
    result = await probe_module.probe(
        registry="registry.test", image="team/app", mode="first_observed", limit=1
    )
    assert result["status"] == ("passed" if case == "success" else "failed")
    assert result["request_timeout"] == 10
    assert constructors == [
        {"registry": "registry.test", "published_at_mode": "first_observed", "timeout": 10}
    ]
    assert "synthetic-private" not in json.dumps(result)
    assert result["request_counts"]["tags:GET"] == 1
    if case == "blob_deadline":
        assert len(wire) == 1, "reject widened Blob budget before network I/O"
    if case == "read_timeout":
        assert result["error_type"] == "ReadTimeout"
        assert result["events"][0]["error_type"] == "ReadTimeout"


@pytest.mark.parametrize("value", ["0", "-2"])
def test_cli_rejects_nonpositive_budgets(probe_module, value):
    with pytest.raises(argparse.ArgumentTypeError):
        probe_module.positive_int(value)
