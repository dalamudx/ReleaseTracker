"""A floating declaration is compared using the actual running artifact."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services.deployment_plan import plan_fingerprint
from releasetracker.services.deployment_version_evidence import (
    collect_ssh_version_evidence,
    collect_version_evidence,
    image_reference,
)
from releasetracker.services.ssh_compose_deploy import inspect_image, running_image_ids
from releasetracker.services.ssh_compose_plan import SSHComposeTarget
from releasetracker.services.ssh_transport import SSHCommandResult
from releasetracker.services.version_policy import version_policy_reason
from test_version_policy import evidence, task

IMAGE_ID = "sha256:" + "a" * 64
OTHER_ID = "sha256:" + "b" * 64
DIGEST = "sha256:" + "c" * 64
OTHER_DIGEST = "sha256:" + "d" * 64
REPO = "docker.io/jenkins/inbound-agent"
JENKINS = "3386.v353e57a_1b_ea_0-1"
RULE = r"^(?<version>\d+)\.v[\w_]+-(?<revision>\d+)$"


async def proof(
    *,
    tags=(JENKINS, "latest"),
    repo=REPO,
    digest=DIGEST,
    target_digest=DIGEST,
    target_id=IMAGE_ID,
    mode="container",
    replicas=(IMAGE_ID,),
    include=RULE,
    exclude=None,
    after=JENKINS,
    error=False,
):
    channel = SimpleNamespace(
        name="stable",
        model_dump=lambda **kw: {
            "name": "stable",
            "type": None,
            "include_pattern": include,
            "exclude_pattern": exclude,
        },
    )
    storage = SimpleNamespace(
        get_tracker_source=AsyncMock(
            return_value=SimpleNamespace(
                source_type="container",
                release_channels=[channel],
            )
        )
    )
    adapter = SimpleNamespace(inspect_local_image=AsyncMock())
    adapter.inspect_local_image.side_effect = lambda ref, name: (
        {"Id": target_id, "RepoTags": [], "RepoDigests": []}
        if name == f"{REPO}:{after}"
        else {
            "Id": name,
            "RepoTags": [f"{repo}:{t}" for t in tags],
            "RepoDigests": [f"{repo}@{digest}"],
        }
    )
    if error:
        adapter.inspect_local_image.side_effect = RuntimeError("offline")
    executor = SimpleNamespace(target_ref={"mode": mode})
    queued = task("all", after, manual=True)
    queued["payload"]["targets"][0].update(
        source_id=1, channel="stable", target=[after, target_digest]
    )
    service = None if mode == "container" else "agent"
    queued["payload"]["policy_bindings"] = [
        {"service": "agent", "tracker_source_id": 1, "channel_name": "stable"}
    ]
    snapshot = {"mode": mode, "recovery_evidence": {"image_id": replicas[0]}}
    if mode == "docker_compose":
        snapshot["snapshots"] = [
            {"compose_service": "agent", "recovery_evidence": {"image_id": i}} for i in replicas
        ]
    if mode == "portainer_stack":
        snapshot["recovery_evidence"] = {"services": {"agent": {"image_id": replicas[0]}}}
    result = await collect_version_evidence(
        storage,
        adapter,
        executor,
        queued,
        snapshot,
        {service: f"{REPO}:latest"},
        {service: f"{REPO}:{after}"},
    )
    observed = replace(
        evidence("latest"),
        configuration=(
            {"current_image": f"{REPO}:latest"}
            if mode == "container"
            else {"current_services": {"agent": f"{REPO}:latest"}}
        ),
        recovery="container_config" if mode == "container" else "workload_images",
        version_evidence=result,
    )
    return queued, observed, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["container", "docker_compose", "portainer_stack"])
async def test_same_artifact_ref_change_needs_no_version_approval(mode):
    queued, observed, _ = await proof(mode=mode)
    queued["payload"].update(manual=False, auto_update_policy="patch")
    assert observed.version_evidence[0]["same_artifact"]
    assert version_policy_reason(queued, observed) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "foreign_repo",
        "moved_tag",
        "different_digest",
        "missing_digest",
        "offline",
        "replica_disagreement",
    ],
)
async def test_unproven_artifact_cannot_bypass_unknown_direction(fault):
    kwargs = {"include": None, "tags": ("latest",)}
    kwargs.update(
        {
            "foreign_repo": {"repo": "docker.io/foreign/agent"},
            "moved_tag": {"target_id": OTHER_ID},
            "different_digest": {"digest": OTHER_DIGEST},
            "missing_digest": {"target_digest": None},
            "offline": {"error": True},
            "replica_disagreement": {"mode": "docker_compose", "replicas": (IMAGE_ID, OTHER_ID)},
        }[fault]
    )
    queued, observed, _ = await proof(**kwargs)
    assert not observed.version_evidence[0]["same_artifact"]
    assert version_policy_reason(queued, observed) == "version_unknown_requires_approval"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "before,after,policy,manual,reason",
    [
        ("3385.vabc-1", JENKINS, "all", False, None),
        ("3385.vabc-1", JENKINS, "patch", True, None),
        ("3385.vabc-1", JENKINS, "patch", False, "version_policy_requires_approval"),
        ("3385.vabc-1", JENKINS, "minor", False, "version_policy_requires_approval"),
        ("3387.vabc-1", JENKINS, "all", True, "version_downgrade_requires_approval"),
        ("3386.vabc-2", JENKINS, "all", True, "version_downgrade_requires_approval"),
        ("3386.vabc-1", JENKINS, "patch", False, None),
    ],
)
async def test_complex_aliases_reuse_channel_sorting(before, after, policy, manual, reason):
    queued, observed, _ = await proof(tags=(before, "latest"), digest=OTHER_DIGEST, after=after)
    queued["payload"].update(auto_update_policy=policy, manual=manual)
    assert version_policy_reason(queued, observed) == reason


@pytest.mark.asyncio
async def test_conflicting_version_aliases_remain_unknown():
    queued, observed, _ = await proof(tags=("3385.vabc-1", "3387.vabc-1"), digest=OTHER_DIGEST)
    assert version_policy_reason(queued, observed) == "version_unknown_requires_approval"


@pytest.mark.asyncio
async def test_aliases_obey_channel_exclusions():
    queued, observed, _ = await proof(
        tags=("3385.vabc-1", "3387.vabc-1"), digest=OTHER_DIGEST, exclude="^3387"
    )
    assert observed.version_evidence[0]["aliases"] == ["3385.vabc-1"]
    assert version_policy_reason(queued, observed) is None


@pytest.mark.asyncio
async def test_standard_alias_preserves_patch_range():
    queued, observed, _ = await proof(
        tags=("1.2.3", "latest"), include=None, digest=OTHER_DIGEST, after="1.3.0"
    )
    queued["payload"].update(auto_update_policy="patch", manual=False)
    assert version_policy_reason(queued, observed) == "version_policy_requires_approval"
    queued["payload"]["auto_update_policy"] = "minor"
    assert version_policy_reason(queued, observed) is None


@pytest.mark.asyncio
async def test_changed_proof_invalidates_approval_but_not_baseline():
    queued, observed, _ = await proof()
    queued.update(id=1)
    queued["payload"]["config_identity"] = "test-config"
    changed = replace(
        observed, version_evidence=({**observed.version_evidence[0], "same_artifact": False},)
    )
    assert observed.evidence_hash == changed.evidence_hash
    assert plan_fingerprint(queued, observed) != plan_fingerprint(queued, changed)


@pytest.mark.asyncio
async def test_verified_kubernetes_digest_uses_shared_same_artifact_gate():
    queued, observed, adapter = await proof(mode="kubernetes_workload")
    source = SimpleNamespace(
        source_type="container",
        release_channels=[
            SimpleNamespace(name="stable", model_dump=lambda **kw: {"name": "stable", "type": None})
        ],
    )
    storage = SimpleNamespace(get_tracker_source=AsyncMock(return_value=source))
    snapshot = {
        "mode": "kubernetes_workload",
        "artifact_evidence": {
            "status": "verified",
            "images": {"containers": {"agent": f"{REPO}@{DIGEST}"}},
        },
    }
    proofs = await collect_version_evidence(
        storage,
        adapter,
        SimpleNamespace(target_ref={"mode": "kubernetes_workload"}),
        queued,
        snapshot,
        {"agent": f"{REPO}:latest"},
        {"agent": f"{REPO}:{JENKINS}"},
    )
    assert proofs[0]["same_artifact"]
    assert version_policy_reason(queued, replace(observed, version_evidence=proofs)) is None


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("nginx:1.2.3", "docker.io/library/nginx"),
        ("jenkins/inbound-agent:latest", REPO),
        ("registry-1.docker.io/jenkins/inbound-agent:latest", REPO),
        ("registry.test:5000/agent:latest", "registry.test:5000/agent"),
        ("other.test/jenkins/inbound-agent:latest", "other.test/jenkins/inbound-agent"),
    ],
)
def test_repository_identity(ref, expected):
    assert image_reference(ref)[0] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_type", ["docker", "podman"])
async def test_native_image_inspect_reads_local_sdk_without_pulling(runtime_type):
    from unittest.mock import Mock
    from releasetracker.config import RuntimeConnectionConfig
    from releasetracker.executors.docker import DockerRuntimeAdapter
    from releasetracker.executors.podman import PodmanRuntimeAdapter

    image = SimpleNamespace(
        id=IMAGE_ID,
        attrs={"Id": IMAGE_ID, "RepoTags": [f"{REPO}:latest"], "RepoDigests": [f"{REPO}@{DIGEST}"]},
    )
    client = SimpleNamespace(images=SimpleNamespace(get=Mock(return_value=image), pull=Mock()))
    adapter_class = DockerRuntimeAdapter if runtime_type == "docker" else PodmanRuntimeAdapter
    adapter = adapter_class(
        RuntimeConnectionConfig(
            name="isolated",
            type=runtime_type,
            config={"socket": "unix:///isolated.sock"},
            secrets={},
        ),
        client=client,
    )
    result = await adapter.inspect_local_image({"mode": "container"}, IMAGE_ID)
    assert result["Id"] == IMAGE_ID
    assert result["RepoDigests"] == [f"{REPO}@{DIGEST}"]
    client.images.get.assert_called_once_with(IMAGE_ID)
    client.images.pull.assert_not_called()


@pytest.mark.asyncio
async def test_portainer_image_inspect_preserves_repository_path():
    from releasetracker.config import RuntimeConnectionConfig
    from releasetracker.executors.portainer import PortainerRuntimeAdapter

    adapter = PortainerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="isolated",
            type="portainer",
            credential_id=1,
            config={"base_url": "https://isolated.test", "endpoint_id": 1},
            secrets={},
        )
    )
    adapter._request_json = AsyncMock(return_value={"Id": IMAGE_ID})
    assert await adapter.inspect_local_image({"endpoint_id": 1}, f"{REPO}:{JENKINS}") == {
        "Id": IMAGE_ID
    }
    request = adapter._request_json.call_args
    assert request.args[0] == "GET"
    assert "/docker/images/docker.io/jenkins/inbound-agent:" not in request.args[1]
    assert "/docker/images/docker.io/jenkins/inbound-agent%3A" in request.args[1]
    assert "%2F" not in request.args[1]


@pytest.mark.asyncio
async def test_same_artifact_still_requires_first_ownership_approval():
    queued, observed, _ = await proof()
    assert version_policy_reason(queued, observed) is None
    assert observed.ownership_reason("installation", "target") == "marker_missing"


async def ssh_proof(running_tags, *, digest=DIGEST, target_digest=DIGEST, current="latest"):
    """Podman over SSH: one running replica, one exited leftover and the local store."""
    store = {
        IMAGE_ID: {
            "Id": IMAGE_ID.removeprefix("sha256:"),
            "RepoTags": [f"{REPO}:{t}" for t in running_tags],
            "RepoDigests": [f"{REPO}@{digest}"],
        }
    }

    async def run(argv, **_):
        if argv[1] == "ps":
            matched = "label=io.podman.compose.project=jenkins" in argv
            return SSHCommandResult(0, "c1\nc2\n" if matched else "", "")
        if argv[1] == "inspect":
            image = IMAGE_ID if argv[2] == "c1" else OTHER_ID
            container = {
                "Image": image.removeprefix("sha256:"),
                "State": {"Running": argv[2] == "c1"},
            }
            return SSHCommandResult(0, json.dumps([container]), "")
        info = store.get(argv[3])
        return (
            SSHCommandResult(0, json.dumps([info]), "") if info else SSHCommandResult(125, "", "")
        )

    session = SimpleNamespace(run=run)
    target = SSHComposeTarget(
        working_dir="/srv/jenkins",
        project="jenkins",
        config_files=["compose.yml"],
        tool="podman-compose",
    )
    channel = SimpleNamespace(
        name="stable",
        model_dump=lambda **kw: {"name": "stable", "type": None, "include_pattern": RULE},
    )
    storage = SimpleNamespace(
        get_tracker_source=AsyncMock(
            return_value=SimpleNamespace(source_type="container", release_channels=[channel])
        )
    )
    queued = task("all", JENKINS, manual=True)
    queued["payload"]["targets"][0].update(
        source_id=1, channel="stable", target=[JENKINS, target_digest]
    )
    queued["payload"]["policy_bindings"] = [
        {"service": "agent", "tracker_source_id": 1, "channel_name": "stable"}
    ]
    images = {"agent": f"{REPO}:{current}"}
    result = await collect_ssh_version_evidence(
        storage,
        SimpleNamespace(target_ref={"mode": "ssh_compose"}),
        queued,
        images,
        {"agent": f"{REPO}:{JENKINS}"},
        await running_image_ids(session, target, ["agent"]),
        lambda reference: inspect_image(session, target, reference),
    )
    observed = replace(
        evidence(current),
        recovery="ssh_files",
        configuration={"current_services": images},
        version_evidence=result,
    )
    return queued, observed


@pytest.mark.asyncio
async def test_ssh_floating_tag_on_target_artifact_survives_target_pull():
    queued, before = await ssh_proof(["latest"])
    _, after = await ssh_proof(["latest", JENKINS])
    # SSH pulls before its pre-write check; the approved fingerprint must still match.
    assert before.version_evidence == after.version_evidence
    assert before.version_evidence[0]["same_artifact"]
    assert before.version_evidence[0]["image_ids"] == [IMAGE_ID]
    queued["payload"].update(manual=False, auto_update_policy="patch")
    assert version_policy_reason(queued, before) is None


@pytest.mark.asyncio
async def test_ssh_republished_tag_moving_away_on_pull_keeps_the_proof():
    queued, before = await ssh_proof([JENKINS], target_digest=OTHER_DIGEST, current=JENKINS)
    _, after = await ssh_proof([], target_digest=OTHER_DIGEST, current=JENKINS)
    assert before.version_evidence == after.version_evidence
    assert not before.version_evidence[0]["same_artifact"]
    assert version_policy_reason(queued, before) is None


@pytest.mark.asyncio
async def test_ssh_floating_tag_on_another_artifact_still_needs_approval():
    queued, observed = await ssh_proof(["latest"], digest=OTHER_DIGEST)
    assert version_policy_reason(queued, observed) == "version_unknown_requires_approval"
    queued, observed = await ssh_proof(["latest", "3385.vabc-1"], digest=OTHER_DIGEST)
    assert observed.version_evidence[0]["aliases"] == ["3385.vabc-1"]
    assert version_policy_reason(queued, observed) is None
