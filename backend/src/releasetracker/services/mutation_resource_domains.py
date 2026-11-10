"""Conservative cross-entry mutation domains from immutable runtime evidence.

Locks cover the entire engine/cluster: Stack submissions can replace unselected
containers and Helm upgrades can change resources outside their current manifest.
"""

import asyncio
import re
from uuid import UUID

from ..executors.adapter_lifetime import runtime_adapter_scope

MUTATION_PREFIX = "deployment-mutations:"
DOMAIN = re.compile(r"(engine|cluster):[0-9a-f]{64}\Z")


def scopes_conflict(left, right):
    """Only different, verified identities in the same family prove independence."""
    if not isinstance(left, str) or not isinstance(right, str):
        return True
    if not DOMAIN.fullmatch(left) or not DOMAIN.fullmatch(right):
        return True
    return left.split(":", 1)[0] != right.split(":", 1)[0] or left == right


def scope_conflict_sql(left, right, *, mutation_keys=False):
    """SQL equivalent; arguments are trusted SQL expressions, each evaluated once.

    Bind values through placeholders, never interpolate them into this expression.
    Unknown/legacy keys remain fences until their next live identity resolution.
    """
    if mutation_keys:
        prefix_length = len(MUTATION_PREFIX)
        normalized = ", ".join(
            f"CASE WHEN substr({name},1,{prefix_length})='{MUTATION_PREFIX}' "
            f"THEN substr({name},{prefix_length + 1}) ELSE '*' END AS {name}"
            for name in ("a", "b")
        )
        source = f"(SELECT {normalized} FROM (SELECT {left} AS a, {right} AS b))"
    else:
        source = f"(SELECT {left} AS a, {right} AS b)"
    independent = []
    for prefix in ("engine:", "cluster:"):
        checks = []
        for name in ("a", "b"):
            checks.append(
                f"length({name})={len(prefix) + 64} "
                f"AND substr({name},1,{len(prefix)})='{prefix}' "
                f"AND substr({name},{len(prefix) + 1}) NOT GLOB '*[^0-9a-f]*'"
            )
        independent.append("(" + " AND ".join(checks) + ")")
    return (
        f"EXISTS (SELECT 1 FROM {source} WHERE NOT COALESCE("
        f"a!=b AND ({' OR '.join(independent)}),0))"
    )


def uncertain_write_sql(alias):
    """needs_attention fences only while a runtime write may have started.

    Admission/preparation is read-only (no attempt, no mutation marker), so a
    stalled or refused admission must not hold a mutation domain forever.
    Legacy rows without a marker stay fenced once an attempt began.
    """
    return f"COALESCE(json_extract({alias}.result,'$.mutation_started'),{alias}.attempts>0)=1"


def docker_scope(info, version=None):
    from ..executors.docker_identity import ID, PODMAN_COMPAT_PREFIX
    from .ssh_compose_ownership import _key

    if not isinstance(info, dict) or "Rootless" in info or "BuildahVersion" in info:
        return "*"
    components = version.get("Components") if isinstance(version, dict) else None
    if not isinstance(components, list):
        return "*"
    names = {item.get("Name") for item in components if isinstance(item, dict)}
    if "Engine" not in names or "Podman Engine" in names:
        return "*"
    daemon = info.get("ID")
    if (
        not isinstance(daemon, str)
        or daemon.startswith(PODMAN_COMPAT_PREFIX)
        or not ID.fullmatch(daemon)
    ):
        return "*"
    # Identical to SSH's verified Docker ownership key, independent of route.
    return "engine:" + _key(["docker", daemon])


def _cluster_scope(adapter):
    from .ssh_compose_ownership import _key

    namespace = adapter._get_core_api().read_namespace("kube-system")
    metadata = namespace.get("metadata", {}) if isinstance(namespace, dict) else namespace.metadata
    uid = metadata.get("uid") if isinstance(metadata, dict) else metadata.uid
    if not isinstance(uid, str):
        return "*"
    uid = str(UUID(uid))
    return "cluster:" + _key(["kubernetes", uid])


@runtime_adapter_scope
async def _resolve_deployment_resource_scope(storage, scheduler, executor):
    """Read actual runtime identities; connection IDs and URLs are never identities."""
    from .deployment_readiness_probes import _adapter, _bounded_thread

    mode = executor.target_ref.get("mode")
    if mode not in {
        "container",
        "docker_compose",
        "portainer_stack",
        "kubernetes_workload",
        "helm_release",
    }:
        return "*"
    adapter = await _adapter(storage, scheduler, executor)
    if mode in {"kubernetes_workload", "helm_release"} and executor.runtime_type == "kubernetes":
        return await _bounded_thread(adapter, "mutation-cluster-identity", _cluster_scope, adapter)
    if mode == "portainer_stack" and executor.runtime_type == "portainer":
        endpoint = executor.target_ref.get("endpoint_id") or adapter._runtime_endpoint_id()
        info = await adapter._request_payload("GET", f"/api/endpoints/{int(endpoint)}/docker/info")
        version = await adapter._request_payload(
            "GET", f"/api/endpoints/{int(endpoint)}/docker/version"
        )
        return docker_scope(info, version)
    if executor.runtime_type == "docker" and mode in {"container", "docker_compose"}:

        def read():
            client = adapter._get_client()
            return docker_scope(client.info(), client.version())

        return await _bounded_thread(adapter, "mutation-engine-identity", read)
    # Native Podman and its Docker compatibility ID lack a portable immutable
    # machine identity here; a route-bound hash would incorrectly separate aliases.
    return "*"


async def resolve_deployment_resource_scope(storage, scheduler, executor):
    try:
        return await _resolve_deployment_resource_scope(storage, scheduler, executor)
    except Exception:
        return "*"


async def resource_scope(storage, executor, scheduler=None):
    try:
        if executor.target_ref.get("mode") == "ssh_compose":
            db = await storage._get_connection()
            row = await (
                await db.execute(
                    "SELECT runtime_key FROM ssh_compose_ownership WHERE executor_id=?",
                    (executor.id,),
                )
            ).fetchone()
            scope = f"engine:{row[0]}" if row and row[0] else "*"
            return scope if DOMAIN.fullmatch(scope) else "*"
        if scheduler is not None:
            return await asyncio.wait_for(
                resolve_deployment_resource_scope(storage, scheduler, executor), timeout=10
            )
    except Exception:
        pass
    return "*"
