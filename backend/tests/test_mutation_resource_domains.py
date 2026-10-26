"""Cross-executor mutation fences require runtime proof, never connection aliases."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from releasetracker.services import mutation_resource_domains as domains
from releasetracker.services import deployment_readiness_probes as probes
from releasetracker.services.ssh_compose_ownership import _key, runtime_identity

ENGINE_A = "engine:" + "a" * 64
ENGINE_B = "engine:" + "b" * 64
CLUSTER_A = "cluster:" + "a" * 64
CLUSTER_B = "cluster:" + "b" * 64
UID_A = "0e8d30e1-c17f-478c-b1e0-f6b86460c07a"
UID_B = "7f18cb3c-f338-4eea-bd54-1591e8471525"
DAEMON = "AAAA:BBBB:CCCC:DDDD:EEEE:FFFF"
DOCKER_VERSION = {"Components": [{"Name": "Engine"}]}


@pytest.mark.parametrize("mutation_keys", [False, True])
def test_sql_and_python_overlap_match_for_unknown_legacy_and_proven_domains(mutation_keys):
    scopes = [
        None,
        "*",
        "",
        "engine:unverified",
        "kubernetes:uid:old-workload",
        "connection:1",
        "engine:" + "g" * 64,
        "engine:" + "a" * 63,
        ENGINE_A,
        ENGINE_B,
        CLUSTER_A,
        CLUSTER_B,
    ]
    sql = domains.scope_conflict_sql("?", "?", mutation_keys=mutation_keys)
    with sqlite3.connect(":memory:") as db:
        for left in scopes:
            for right in scopes:
                values = (left, right)
                if mutation_keys:
                    values = tuple(domains.MUTATION_PREFIX + v if v else v for v in values)
                actual = db.execute(f"SELECT {sql}", values).fetchone()[0]
                assert bool(actual) == domains.scopes_conflict(left, right), (left, right)


def test_correlated_sql_preserves_overlap_across_executor_aliases():
    match = domains.scope_conflict_sql(
        "active.resource_key", "candidate.resource_key", mutation_keys=True
    )
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE tasks(id, resource_key)")
        db.executemany(
            "INSERT INTO tasks VALUES (?,?)",
            [
                (1, domains.MUTATION_PREFIX + ENGINE_A),
                (2, domains.MUTATION_PREFIX + ENGINE_A),
                (3, domains.MUTATION_PREFIX + ENGINE_B),
                (4, domains.MUTATION_PREFIX + CLUSTER_A),
                (5, "deployment-mutations:kubernetes:uid:legacy"),
            ],
        )
        rows = db.execute(
            f"SELECT candidate.id FROM tasks candidate WHERE candidate.id!=1 AND EXISTS "
            f"(SELECT 1 FROM tasks active WHERE active.id=1 AND {match}) ORDER BY candidate.id"
        ).fetchall()
    assert rows == [(2,), (4,), (5,)]


def executor(mode, runtime_type, connection_id=1):
    return SimpleNamespace(
        id=connection_id,
        runtime_connection_id=connection_id,
        runtime_type=runtime_type,
        target_ref={"mode": mode, "endpoint_id": 17},
    )


async def install_adapter(monkeypatch, adapter):
    monkeypatch.setattr(probes, "_adapter", AsyncMock(return_value=adapter))


@pytest.mark.asyncio
async def test_sdk_compose_portainer_and_ssh_aliases_share_daemon_scope(monkeypatch):
    info = {"ID": DAEMON}
    client = SimpleNamespace(info=lambda: info, version=lambda: DOCKER_VERSION)
    adapter = SimpleNamespace(
        _get_client=lambda: client, _request_payload=AsyncMock(side_effect=[info, DOCKER_VERSION])
    )
    await install_adapter(monkeypatch, adapter)
    scopes = []
    for index, (mode, runtime_type) in enumerate(
        [
            ("container", "docker"),
            ("docker_compose", "docker"),
            ("portainer_stack", "portainer"),
        ]
    ):
        scopes.append(
            await domains.resource_scope(None, executor(mode, runtime_type, index + 1), object())
        )
    import json

    session = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(exit_status=0, stdout=json.dumps(info)))
    )
    ssh_key = await runtime_identity(session, "docker")
    assert scopes == ["engine:" + ssh_key] * 3
    assert adapter._request_payload.await_args_list == [
        call("GET", "/api/endpoints/17/docker/info"),
        call("GET", "/api/endpoints/17/docker/version"),
    ]
    assert not domains.scopes_conflict(
        scopes[0], domains.docker_scope({"ID": "OTHER-DAEMON-ID"}, DOCKER_VERSION)
    )


@pytest.mark.parametrize(
    "info",
    [
        {},
        None,
        {"ID": "short"},
        {"ID": DAEMON, "Rootless": False},
        {"ID": DAEMON, "BuildahVersion": "1.0"},
        {"ID": "podman-compat-v1:" + "a" * 64},
    ],
)
def test_missing_or_route_bound_engine_identity_is_global(info):
    assert domains.docker_scope(info, DOCKER_VERSION) == "*"


@pytest.mark.asyncio
async def test_helm_and_workloads_share_cluster_scope_across_connections(monkeypatch):
    current_uid = [UID_A]
    core = SimpleNamespace(read_namespace=lambda name: {"metadata": {"uid": current_uid[0]}})
    await install_adapter(monkeypatch, SimpleNamespace(_get_core_api=lambda: core))
    workload = await domains.resource_scope(
        None, executor("kubernetes_workload", "kubernetes", 1), object()
    )
    helm = await domains.resource_scope(None, executor("helm_release", "kubernetes", 2), object())
    assert workload == helm == "cluster:" + _key(["kubernetes", UID_A])
    assert domains.scopes_conflict(workload, helm)
    current_uid[0] = UID_B
    unrelated = await domains.resource_scope(
        None, executor("helm_release", "kubernetes", 3), object()
    )
    assert not domains.scopes_conflict(helm, unrelated)
    assert domains.scopes_conflict("kubernetes:uid:old-workload", unrelated)


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", [None, "", "name-is-not-a-uid"])
async def test_missing_cluster_identity_keeps_wildcard(monkeypatch, uid):
    core = SimpleNamespace(read_namespace=lambda name: {"metadata": {"uid": uid}})
    await install_adapter(monkeypatch, SimpleNamespace(_get_core_api=lambda: core))
    assert (
        await domains.resource_scope(None, executor("helm_release", "kubernetes"), object()) == "*"
    )


@pytest.mark.asyncio
async def test_cluster_sdk_models_and_permission_denial(monkeypatch):
    core = SimpleNamespace(
        read_namespace=lambda name: SimpleNamespace(metadata=SimpleNamespace(uid=UID_A))
    )
    await install_adapter(monkeypatch, SimpleNamespace(_get_core_api=lambda: core))
    assert await domains.resource_scope(None, executor("helm_release", "kubernetes"), object()) == (
        "cluster:" + _key(["kubernetes", UID_A])
    )

    def denied(name):
        raise PermissionError("credentials must never be serialized")

    core.read_namespace = denied
    assert (
        await domains.resource_scope(None, executor("helm_release", "kubernetes"), object()) == "*"
    )


@pytest.mark.asyncio
async def test_native_podman_and_missing_scheduler_stay_global(monkeypatch):
    await install_adapter(monkeypatch, object())
    assert await domains.resource_scope(None, executor("container", "podman"), object()) == "*"
    assert await domains.resource_scope(None, executor("container", "docker")) == "*"


@pytest.mark.asyncio
async def test_ssh_ownership_uses_shared_verified_engine_key():
    cursor = SimpleNamespace(fetchone=AsyncMock(return_value=(_key(["docker", DAEMON]),)))
    db = SimpleNamespace(execute=AsyncMock(return_value=cursor))
    storage = SimpleNamespace(_get_connection=AsyncMock(return_value=db))
    ssh = executor("ssh_compose", "ssh")
    assert await domains.resource_scope(storage, ssh) == domains.docker_scope(
        {"ID": DAEMON}, DOCKER_VERSION
    )
    cursor.fetchone.return_value = ("unverified-connection-alias",)
    assert await domains.resource_scope(storage, ssh) == "*"
    cursor.fetchone.return_value = None
    assert await domains.resource_scope(storage, ssh) == "*"


@pytest.mark.parametrize(
    "version",
    [
        None,
        {},
        {"Components": []},
        {"Components": [{"Name": "Podman Engine"}]},
        {"Components": [{"Name": "Engine"}, {"Name": "Podman Engine"}]},
    ],
)
def test_random_compat_info_id_never_establishes_docker_identity(version):
    assert domains.docker_scope({"ID": DAEMON}, version) == "*"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_type,mode", [("docker", "container"), ("portainer", "portainer_stack")]
)
async def test_compat_version_blocks_random_daemon_ids_without_info_hints(
    monkeypatch, runtime_type, mode
):
    info = {"ID": DAEMON}
    version = {"Components": [{"Name": "Podman Engine"}]}
    client = SimpleNamespace(info=lambda: info, version=lambda: version)
    adapter = SimpleNamespace(
        _get_client=lambda: client,
        _request_payload=AsyncMock(side_effect=[info, version]),
    )
    await install_adapter(monkeypatch, adapter)
    assert await domains.resource_scope(None, executor(mode, runtime_type), object()) == "*"


async def queue_task(storage, scope, executor_id, *, kind="deploy"):
    key = "deployment-mutations" if scope == "*" else domains.MUTATION_PREFIX + scope
    return await storage.tasks.enqueue(
        kind=kind,
        resource_key=key,
        dedupe_key=f"{kind}:{executor_id}",
        target_label=f"executor-{executor_id}",
        payload={"executor_id": executor_id},
        trigger_mode="manual",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active_kind", ["deploy", "recover"])
@pytest.mark.parametrize(
    "left,right,conflict",
    [
        (ENGINE_A, ENGINE_A, True),
        (ENGINE_A, ENGINE_B, False),
        (CLUSTER_A, CLUSTER_A, True),
        (CLUSTER_A, CLUSTER_B, False),
        (ENGINE_A, CLUSTER_A, True),
        ("*", ENGINE_B, True),
        ("kubernetes:uid:legacy", CLUSTER_B, True),
    ],
)
async def test_actual_claim_and_blockers_use_domain_overlap(
    storage, active_kind, left, right, conflict
):
    first = await queue_task(storage, left, 11, kind=active_kind)
    active = await storage.tasks.claim(active_kind, mutation_capacity=3)
    assert active["id"] == first["id"]
    candidate = await queue_task(storage, right, 22)
    blockers = await storage.tasks.blockers(candidate)
    assert [row["task_id"] for row in blockers] == ([active["id"]] if conflict else [])
    claimed = await storage.tasks.claim("deploy", mutation_capacity=3)
    assert (claimed is None) is conflict
    if claimed:
        assert claimed["id"] == candidate["id"]


@pytest.mark.asyncio
async def test_actual_bind_rechecks_domains_under_lease(storage):
    await queue_task(storage, ENGINE_A, 11)
    active = await storage.tasks.claim("deploy", mutation_capacity=3)
    await queue_task(storage, ENGINE_B, 22)
    candidate = await storage.tasks.claim("deploy", mutation_capacity=3)
    assert await storage.tasks.bind_mutation_scope(candidate, domains.MUTATION_PREFIX + ENGINE_B)
    assert not await storage.tasks.bind_mutation_scope(
        candidate, domains.MUTATION_PREFIX + ENGINE_A
    )
    assert not await storage.tasks.bind_mutation_scope(
        candidate, domains.MUTATION_PREFIX + CLUSTER_A
    )
    assert not await storage.tasks.bind_mutation_scope(candidate, "deployment-mutations")
    persisted = await storage.tasks.get(candidate["id"])
    assert persisted["resource_key"] == domains.MUTATION_PREFIX + ENGINE_B
    assert active["resource_key"] == domains.MUTATION_PREFIX + ENGINE_A


@pytest.mark.asyncio
async def test_same_executor_cannot_escape_scope_with_new_runtime_identity(storage):
    await queue_task(storage, ENGINE_A, 11)
    active = await storage.tasks.claim("deploy", mutation_capacity=3)
    candidate = await queue_task(storage, ENGINE_B, 11)
    assert [row["task_id"] for row in await storage.tasks.blockers(candidate)] == [active["id"]]
    assert await storage.tasks.claim("deploy", mutation_capacity=3) is None
    db = await storage._get_connection()
    await db.execute(
        "UPDATE tasks SET state='running',owner='test-owner',lease_until=? WHERE id=?",
        (active["lease_until"], candidate["id"]),
    )
    await db.commit()
    candidate = await storage.tasks.get(candidate["id"])
    assert not await storage.tasks.bind_mutation_scope(
        candidate, domains.MUTATION_PREFIX + ENGINE_B
    )


@pytest.mark.asyncio
async def test_proven_independence_preserves_capacity_and_default_serialization(storage):
    for index, letter in enumerate("abcd", start=1):
        await queue_task(storage, "engine:" + letter * 64, index)
    first = await storage.tasks.claim("deploy")
    assert first is not None
    assert await storage.tasks.claim("deploy") is None
    assert await storage.tasks.claim("deploy", mutation_capacity=3) is not None
    assert await storage.tasks.claim("deploy", mutation_capacity=3) is not None
    assert await storage.tasks.claim("deploy", mutation_capacity=3) is None


@pytest.mark.asyncio
async def test_uncertain_mutations_block_deploy_but_preserve_recovery_path(storage):
    first = await queue_task(storage, ENGINE_A, 11)
    db = await storage._get_connection()
    await db.execute("UPDATE tasks SET state='needs_attention' WHERE id=?", (first["id"],))
    await db.commit()
    deploy = await queue_task(storage, ENGINE_A, 22)
    assert await storage.tasks.blockers(deploy)
    assert await storage.tasks.claim("deploy", mutation_capacity=3) is None
    recover = await queue_task(storage, ENGINE_A, 33, kind="recover")
    assert await storage.tasks.blockers(recover) == []
    assert (await storage.tasks.claim("recover", mutation_capacity=3))["id"] == recover["id"]
