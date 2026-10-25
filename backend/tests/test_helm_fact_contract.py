"""Every Helm reader must agree, and only one place may parse Helm documents."""

import json
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
import helm_outputs

pytestmark = pytest.mark.asyncio


def adapter_with(state, *, metadata=True, listing=True):
    adapter = KubernetesRuntimeAdapter(
        RuntimeConnectionConfig(
            name="isolated",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        )
    )
    adapter._run_helm_command = lambda args: helm_outputs.respond(
        args, state, name="demo", metadata=metadata, listing=listing
    )
    return adapter


REF = {"mode": "helm_release", "namespace": "apps", "release_name": "demo"}


async def test_every_reader_reports_the_same_deployed_facts():
    state = {"chart": "demo", "chart_version": "5.9.17", "revision": 4, "app_version": "2.528.1"}
    adapter = adapter_with(state)
    snapshot = await adapter.capture_helm_release_snapshot(REF)
    metadata = adapter._get_helm_release_chart_metadata("apps", "demo")
    assert await adapter.get_helm_release_version(REF) == "5.9.17"
    assert snapshot["chart_name"] == "demo"
    assert snapshot["chart_version"] == "5.9.17"
    assert snapshot["app_version"] == "2.528.1"
    assert snapshot["revision"] == 4
    assert metadata == {
        "chart_name": "demo",
        "chart_version": "5.9.17",
        "app_version": "2.528.1",
    }


async def test_status_without_chart_still_yields_version_and_revision():
    # Helm 3 `status -o json` has no chart block; metadata carries both facts.
    state = {"chart": "demo", "chart_version": "1.2.3", "revision": 7, "app_version": None}
    adapter = adapter_with(state)
    assert await adapter.get_helm_release_version(REF) == "1.2.3"
    assert (await adapter.capture_helm_release_snapshot(REF))["revision"] == 7


async def test_list_is_only_a_fallback_and_never_invents_a_version():
    state = {"chart": "demo", "chart_version": "9.9.9", "revision": 2, "app_version": None}
    adapter = adapter_with(state, metadata=False)
    assert await adapter.get_helm_release_version(REF) == "9.9.9"
    empty = adapter_with(state, metadata=False, listing=False)
    assert await empty.get_helm_release_version(REF) is None
    assert empty._get_helm_release_chart_metadata("apps", "demo") == {}


async def test_digest_requires_the_revision_receipt():
    state = {"chart": "demo", "chart_version": "1.0.0", "revision": 5, "app_version": None}
    adapter = adapter_with(state)
    receipt = REF | {
        "chart_version": "1.0.0",
        "chart_digest": "a" * 64,
        "chart_revision": 5,
    }
    assert await adapter.get_helm_release_digest(receipt) == "sha256:" + "a" * 64
    assert await adapter.get_helm_release_digest(receipt | {"chart_revision": 4}) is None


@pytest.mark.parametrize(
    "version", ["1.2.3", "1.2.3-rc.1", "1.2.3-beta-2+build.45", "v1.2.3+abc", "2026.8.5"]
)
async def test_list_fallback_preserves_complete_chart_version(version):
    state = {"chart": "my-demo-chart", "chart_version": version, "revision": 4}
    adapter = adapter_with(state, metadata=False)
    assert await adapter.get_helm_release_version(REF) == version
    snap = await adapter.capture_helm_release_snapshot(REF)
    assert snap["chart_name"] == "my-demo-chart" and snap["revision"] == 4


async def test_list_ambiguous_chart_name_requires_explicit_name():
    # Both demo / 1.0.0-test-2.0.0 and demo-1.0.0-test / 2.0.0 are valid splits.
    state = {"chart": "demo-1.0.0-test", "chart_version": "2.0.0", "revision": 4}
    adapter = adapter_with(state, metadata=False)
    assert await adapter.get_helm_release_version(REF) is None
    assert await adapter.get_helm_release_version(REF | {"chart_name": state["chart"]}) == "2.0.0"
    with pytest.raises(ValueError, match="cannot capture a snapshot"):
        await adapter.capture_helm_release_snapshot(REF)


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", 8),
        ("revision", None),
        ("revision", True),
        ("revision", "abc"),
        ("name", "elsewhere"),
        ("namespace", "elsewhere"),
        ("chart", "other-chart"),
        ("status", "failed"),
    ],
)
async def test_conflicting_records_never_prove_an_artifact(field, value):
    state = {"chart": "demo", "chart_version": "1.0.0", "revision": 7}
    adapter = adapter_with(state)
    run = adapter._run_helm_command

    def command(args):
        document = run(args)
        if args[:2] == ["get", "metadata"]:
            metadata = json.loads(document)
            metadata[field] = value
            return json.dumps(metadata)
        return document

    adapter._run_helm_command = command
    receipt = REF | {
        "chart_name": "demo",
        "chart_version": "1.0.0",
        "chart_revision": 7,
        "chart_digest": "a" * 64,
    }
    with pytest.raises(ValueError, match="retry the read"):
        await adapter.get_helm_release_digest(receipt)
    with pytest.raises(ValueError, match="retry the read"):
        await adapter.capture_helm_release_snapshot(receipt)
    if field != "chart":
        assert adapter._get_helm_release_chart_metadata("apps", "demo") == {}


async def test_numeric_chart_version_is_not_a_revision():
    state = {"chart": "demo", "chart_version": "2026", "revision": 7}
    adapter = adapter_with(state)
    assert (await adapter.capture_helm_release_snapshot(REF))["revision"] == 7


async def test_metadata_permission_error_is_not_hidden_by_a_fallback():
    state = {"chart": "demo", "chart_version": "1.0.0", "revision": 7}
    adapter = adapter_with(state)
    run = adapter._run_helm_command

    def denied(args):
        if args[:2] == ["get", "metadata"]:
            raise RuntimeError("forbidden")
        assert args[0] != "list"
        return run(args)

    adapter._run_helm_command = denied
    with pytest.raises(RuntimeError, match="forbidden"):
        await adapter.get_helm_release_version(REF | {"chart_version": "0.1.0"})


async def test_unknown_live_version_cannot_skip_upgrade_or_capture_snapshot():
    state = {"chart": "demo", "chart_version": "1.0.0", "revision": 7}
    adapter = adapter_with(state, metadata=False, listing=False)
    saved = REF | {"chart_version": "1.0.0", "chart_revision": 7, "chart_digest": "a" * 64}
    assert await adapter.get_helm_release_version(saved) is None
    assert await adapter.get_helm_release_digest(saved) is None
    with pytest.raises(ValueError, match="cannot capture a snapshot"):
        await adapter.capture_helm_release_snapshot(saved)


async def test_discovery_prefers_release_metadata_to_stale_workload_labels():
    state = {"chart": "demo", "chart_version": "1.2.3-rc.1", "revision": 7, "app_version": "2.0.0"}
    adapter = adapter_with(state)
    workload = {
        "kind": "Deployment",
        "name": "demo-server",
        "labels": {
            "app.kubernetes.io/managed-by": "Helm",
            "helm.sh/chart": "demo-0.1.0",
            "app.kubernetes.io/version": "0.1.0",
        },
        "annotations": {
            "meta.helm.sh/release-name": "demo",
            "meta.helm.sh/release-namespace": "apps",
        },
    }
    target = adapter._build_helm_release_target("apps", [workload])
    assert target.target_ref["chart_version"] == "1.2.3-rc.1"
    assert target.target_ref["app_version"] == "2.0.0"
    assert (
        adapter._get_helm_chart_version({"labels": {"helm.sh/chart": "demo-1.2.3-rc.1"}})
        == "1.2.3-rc.1"
    )


async def test_list_fallback_keeps_failed_releases_visible():
    state = {"chart": "demo", "chart_version": "1.2.3", "revision": 7, "status": "failed"}
    adapter = adapter_with(state, metadata=False)
    run = adapter._run_helm_command
    calls = []

    def command(args):
        calls.append(args)
        return run(args)

    adapter._run_helm_command = command
    assert await adapter.get_helm_release_version(REF) == "1.2.3"
    assert adapter._helm_release_facts(REF).status == "failed"
    listed = next(args for args in calls if args[0] == "list")
    assert "--all" in listed
