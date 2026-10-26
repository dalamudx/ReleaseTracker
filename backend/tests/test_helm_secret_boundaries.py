"""Secret-bearing Helm documents never cross public snapshot/recovery boundaries."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import recovery_diff
from releasetracker.services.snapshot_service import SnapshotRedactor
from test_helm_configuration_proof import snapshot


@pytest.mark.parametrize("runtime", [None, "kubernetes"])
def test_public_helm_snapshot_masks_nested_documents_and_raw_release(runtime):
    stored = snapshot()
    stored["release"] = {
        "config": {"credential-as-key": "password"},
        "manifest": "kind: Secret\nstringData:\n  password: raw-password",
    }
    view, _ = SnapshotRedactor().redact(stored, runtime_type=runtime)
    for secret in ("raw-password", "credential-as-key", "secret-value", "secret-key-in-values"):
        assert secret not in json.dumps(view)
    assert stored["helm_configuration"]["values"]["innocent"] == "secret-value"


async def test_public_helm_recovery_diff_masks_changed_keys_and_manifest(monkeypatch):
    saved = snapshot()
    current = deepcopy(saved)
    current["helm_configuration"]["values"]["secret-credential-in-key"] = "opaque-password"
    current["helm_configuration"][
        "manifest"
    ] += "---\napiVersion: custom.example/v1\nkind: Account\nmetadata: {name: isolated}\nspec: {connection: opaque-secret}\n"
    monkeypatch.setattr(recovery_diff, "capture_current", AsyncMock(return_value=current))
    storage = SimpleNamespace(
        system_key_manager=SimpleNamespace(jwt_secret="isolated"),
        get_runtime_connection=AsyncMock(return_value=None),
    )
    executor = SimpleNamespace(
        runtime_connection_id=1,
        target_ref={"mode": "helm_release"},
        model_dump=lambda **kw: {"id": 1},
    )
    result = await recovery_diff.build_review(
        storage, executor, None, SimpleNamespace(id=1, snapshot_data=saved)
    )
    public = json.dumps(result)
    for secret in (
        "secret-credential-in-key",
        "opaque-password",
        "opaque-secret",
        "secret-key-in-values",
        "secret-value",
    ):
        assert secret not in public
    assert "helm_configuration" in public
