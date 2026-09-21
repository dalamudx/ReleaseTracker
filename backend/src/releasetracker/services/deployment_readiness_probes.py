"""Read-only, single-attempt native deployment observations (never poll or mutate).

Persist ``baseline`` before mutation and ``target`` immediately afterwards using
capture_deployment_target. Missing identity evidence is unknown, never healthy.
"""

from __future__ import annotations

import asyncio
import json

# Timed-out read-only SDK calls cannot be killed by asyncio. Keep at most one
# outstanding read per adapter/operation instead of leaking a thread every tick.
_READS = {}


async def _bounded_thread(adapter, operation, callback, *args):
    key = (id(adapter), operation)
    running = _READS.get(key)
    if running is not None and not running.done():
        raise TimeoutError("previous native read still outstanding")
    task = asyncio.create_task(asyncio.to_thread(callback, *args))
    _READS[key] = task

    def finished(done):
        if _READS.get(key) is done:
            _READS.pop(key, None)
        if not done.cancelled():
            done.exception()  # Retrieve errors even when the caller timed out.

    task.add_done_callback(finished)
    return await asyncio.wait_for(asyncio.shield(task), timeout=20)


def _field(obj, camel, snake=None, default=None):
    return obj.get(camel, obj.get(snake, default))


def _result(outcome, services=None, message=None):
    return {"outcome": outcome, "services": services or [], "message": message}


def _service(name, status, message, method="runtime_native"):
    if message == "Running; no healthcheck configured":
        method = "runtime_state"
    return {"service": name, "status": status, "method": method, "message": message}


def _aggregate(rows):
    for status in ("superseded", "unhealthy", "unsupported", "unknown", "pending"):
        if any(row["status"] == status for row in rows):
            return _result(status, rows)
    return _result("healthy" if rows else "unknown", rows)


async def _adapter(storage, scheduler, executor):
    connection = await storage.get_runtime_connection(executor.runtime_connection_id)
    if connection is None or not connection.enabled:
        raise ValueError("runtime connection unavailable")
    from .runtime_credentials import materialize_runtime_connection_credentials

    connection = await materialize_runtime_connection_credentials(storage, connection)
    return scheduler._get_adapter(executor.id, connection)


def _safe_state(state):
    """Never persist native health output/logs (commands may print credentials)."""
    result = {key: state[key] for key in ("Running", "Status", "Dead") if key in state}
    for key in ("Health", "Healthcheck"):
        if isinstance(state.get(key), dict):
            result[key] = {"Status": state[key].get("Status")}
    return result


def _has_healthcheck(attrs):
    check = (attrs.get("Config") or {}).get("Healthcheck") or {}
    test = check.get("Test") or []
    return bool(test and test[0] != "NONE")


def _container_record(container):
    attrs = getattr(container, "attrs", {}) or {}
    return {
        "id": attrs.get("Id") or getattr(container, "id", None),
        "image_id": attrs.get("Image"),
        "image": (attrs.get("Config") or {}).get("Image"),
        "state": _safe_state(attrs.get("State") or {}),
        "restart_count": attrs.get("RestartCount"),
        "healthcheck_configured": _has_healthcheck(attrs),
    }


def _containers(adapter, ref):
    if ref.get("mode") == "docker_compose":
        groups = adapter._find_compose_service_containers(ref["project"])
    else:
        # The saved pre-update container ID may be gone; the stable name locates its replacement.
        ref = dict(ref)
        if ref.get("container_name"):
            ref.pop("container_id", None)
        groups = {"container": [adapter._get_container(ref)]}
    return {name: [_container_record(c) for c in items] for name, items in groups.items()}


async def _portainer_containers(adapter, ref):
    # Observation is one attempt: do not invoke the adapter read retry sleeps.
    from copy import copy
    from dataclasses import replace

    adapter = copy(adapter)
    adapter._operation_policy = replace(adapter._operation_policy, read_retries=0)
    endpoint = ref.get("endpoint_id") or adapter._runtime_endpoint_id()
    stack = await adapter.fetch_stack_detail(endpoint_id=endpoint, stack_id=ref["stack_id"])
    if adapter._resolve_stack_type(stack) != "standalone":
        raise NotImplementedError("only standalone Portainer stacks support native observation")
    project = stack.get("Name") or stack.get("name")
    if not project:
        raise ValueError("stack project identity missing")
    prefix = f"/api/endpoints/{endpoint}/docker"
    payload = await adapter._request_payload(
        "GET",
        f"{prefix}/containers/json",
        params={
            "all": "true",
            "filters": json.dumps({"label": [f"com.docker.compose.project={project}"]}),
        },
    )
    groups = {}
    for item in payload:
        attrs = await adapter._request_payload("GET", f"{prefix}/containers/{item['Id']}/json")
        labels = (attrs.get("Config") or {}).get("Labels") or {}
        name = labels.get("com.docker.compose.service")
        if name and labels.get("com.docker.compose.oneoff", "false").lower() != "true":
            groups.setdefault(name, []).append(
                {
                    "id": attrs.get("Id"),
                    "image_id": attrs.get("Image"),
                    "image": (attrs.get("Config") or {}).get("Image"),
                    "state": _safe_state(attrs.get("State") or {}),
                    "restart_count": attrs.get("RestartCount"),
                    "healthcheck_configured": _has_healthcheck(attrs),
                }
            )
    return groups


async def _capture(storage, scheduler, executor):
    ref = executor.target_ref
    if ref.get("mode") == "ssh_compose":
        return await _capture_ssh(storage, executor)
    adapter = await _adapter(storage, scheduler, executor)
    mode = ref.get("mode")
    if mode in {"kubernetes_workload", "helm_release"}:

        def read():
            adapter._authorize_namespace(ref["namespace"])
            refs = [ref]
            release = None
            if mode == "helm_release":
                raw_release = adapter._get_helm_release(ref)
                release = {
                    "version": raw_release.get("version"),
                    "info": {"status": (raw_release.get("info") or {}).get("status")},
                }
                refs = [
                    w
                    for w in adapter._list_workloads(adapter._get_apps_api(), ref["namespace"])
                    if adapter._get_helm_release_name(w) == ref["release_name"]
                ]
            workloads = {}
            for item in refs:
                w = adapter._get_workload(item["kind"], item["name"], ref["namespace"])
                workloads[f"{item['kind']}/{item['name']}"] = {
                    "kind": w["kind"],
                    "containers": w.get("containers", []),
                    "metadata": {
                        k: (w.get("metadata") or {}).get(k) for k in ("uid", "generation")
                    },
                    "spec": {
                        k: v
                        for k, v in (w.get("spec") or {}).items()
                        if k in {"replicas", "updateStrategy", "update_strategy"}
                    },
                    "status": {
                        k: v
                        for k, v in (w.get("status") or {}).items()
                        if k not in {"collisionCount", "collision_count"}
                    },
                }
            return {"kind": mode, "workloads": workloads, "release": release}

        return await _bounded_thread(adapter, "workloads", read)
    if mode == "portainer_stack":
        return {"kind": mode, "containers": await _portainer_containers(adapter, ref)}
    if mode in {"container", "docker_compose"}:
        return {
            "kind": mode,
            "containers": await _bounded_thread(adapter, "containers", _containers, adapter, ref),
        }
    raise NotImplementedError("native readiness not supported for target mode")


async def capture_deployment_baseline(storage, scheduler, executor) -> dict:
    """Read-only evidence; errors are durable unknown evidence, not permission to pass."""
    try:
        return await asyncio.wait_for(_capture(storage, scheduler, executor), timeout=30)
    except NotImplementedError:
        return {"error": "unsupported"}
    except Exception:
        return {"error": "native identity capture unavailable"}


async def capture_deployment_target(storage, scheduler, executor) -> dict:
    """Call immediately after mutation, before yielding deployment ownership."""
    if executor.target_ref.get("mode") == "kubernetes_workload":
        try:
            adapter = await _adapter(storage, scheduler, executor)
            submitted = getattr(adapter, "_readiness_submission", None) or {}
            ref = executor.target_ref
            metadata = submitted.get("metadata") or {}
            if metadata.get("uid") and all(
                submitted.get(k) == ref.get(k) for k in ("namespace", "kind", "name")
            ):
                # Mutation response is authority, not a later publisher's GET revision.
                key = f"{ref['kind']}/{ref['name']}"
                return {
                    "kind": "kubernetes_workload",
                    "workloads": {
                        key: {"metadata": {k: metadata.get(k) for k in ("uid", "generation")}}
                    },
                    "submitted_images": submitted.get("images") or {},
                }
        except Exception:
            pass
        return {"error": "submitted workload identity unavailable"}
    return await capture_deployment_baseline(storage, scheduler, executor)


def _workload_status(current, target, expected):
    meta, spec, status = (current.get(k) or {} for k in ("metadata", "spec", "status"))
    target_meta = target.get("metadata") or {}
    uid, generation = target_meta.get("uid"), target_meta.get("generation")
    if not uid or not isinstance(generation, int):
        return "unknown", "submitted workload UID/generation missing"
    if meta.get("uid") != uid or meta.get("generation") != generation:
        return "superseded", "workload identity or generation changed after submission"
    images = {c["name"]: c.get("image") for c in current.get("containers", [])}
    if not expected:
        return "unknown", "expected target images missing"
    if any(images.get(name) != image for name, image in expected.items()):
        return "superseded", "workload template no longer matches target images"
    observed = _field(status, "observedGeneration", "observed_generation")
    if not isinstance(observed, int) or observed < generation:
        return "pending", "controller has not observed submitted generation"
    kind = current.get("kind")
    strategy = _field(spec, "updateStrategy", "update_strategy", {}) or {}
    if kind in {"StatefulSet", "DaemonSet"}:
        rolling = _field(strategy, "rollingUpdate", "rolling_update", {}) or {}
        if strategy.get("type", "RollingUpdate") != "RollingUpdate" or rolling.get("partition", 0):
            return "unsupported", "OnDelete or partitioned rollouts require manual verification"
    n = spec.get("replicas", 1)
    ready = _field(status, "readyReplicas", "ready_replicas", 0)
    updated = _field(status, "updatedReplicas", "updated_replicas", 0)
    if kind == "Deployment":
        conditions = status.get("conditions") or []
        if any(
            c.get("type") == "Progressing"
            and c.get("status") == "False"
            and c.get("reason") == "ProgressDeadlineExceeded"
            for c in conditions
        ):
            return "unhealthy", "deployment progress deadline exceeded"
        done = any(
            c.get("type") == "Progressing"
            and c.get("status") == "True"
            and c.get("reason") == "NewReplicaSetAvailable"
            for c in conditions
        )
        ok = (
            n > 0
            and done
            and updated == ready == status.get("replicas") == n
            and _field(status, "availableReplicas", "available_replicas", 0) == n
            and not _field(status, "unavailableReplicas", "unavailable_replicas", 0)
        )
    elif kind == "StatefulSet":
        revision = _field(status, "updateRevision", "update_revision")
        submitted = _field(target.get("status") or {}, "updateRevision", "update_revision")
        # updateRevision can still be the old revision immediately after patch; generation
        # observation and full updated counts are mandatory independently of this value.
        ok = (
            n > 0
            and bool(revision)
            and revision == _field(status, "currentRevision", "current_revision")
            and updated == ready == status.get("replicas") == n
        )
        target_observed = _field(
            target.get("status") or {}, "observedGeneration", "observed_generation", 0
        )
        if submitted and target_observed >= generation and submitted != revision:
            return "superseded", "StatefulSet submitted revision changed"
    elif kind == "DaemonSet":
        n = _field(status, "desiredNumberScheduled", "desired_number_scheduled", 0)
        ok = (
            n > 0
            and _field(status, "updatedNumberScheduled", "updated_number_scheduled", 0) == n
            and _field(status, "numberReady", "number_ready", 0) == n
            and _field(status, "numberAvailable", "number_available", 0) == n
            and not _field(status, "numberUnavailable", "number_unavailable", 0)
            and not _field(status, "numberMisscheduled", "number_misscheduled", 0)
        )
    else:
        return "unsupported", "unsupported workload kind"
    return (
        ("healthy", "submitted rollout complete")
        if ok
        else ("pending", "submitted rollout incomplete")
    )


def _container_status(current, target, expected, count):
    if not target or not expected or not count:
        return "unknown", "submitted container identity or replica count missing"
    if len(current) != count or len(target) != count:
        return "pending", "not all expected replicas present"
    submitted = {c.get("id"): c for c in target}
    if None in submitted or any(not c.get("image_id") for c in target):
        return "unknown", "submitted immutable container/image identity missing"
    for c in current:
        previous = submitted.get(c.get("id"))
        if previous is None or c.get("image_id") != previous.get("image_id"):
            return "superseded", "container identity changed after submission"
        if c.get("image") != expected:
            return "pending", "container is not running the expected image"
        state = c.get("state") or {}
        health = (state.get("Health") or state.get("Healthcheck") or {}).get("Status")
        if health == "unhealthy" or state.get("Dead") or state.get("Status") in {"dead", "exited"}:
            return "unhealthy", "target container unhealthy or stopped"
        if not state.get("Running") or health == "starting":
            return "pending", "target container not ready"
        if health not in {None, "healthy"}:
            return "unknown", "unrecognized native health status"
        if health is None:
            if c.get("healthcheck_configured"):
                return "pending", "configured healthcheck has not reported status"
            before, now = previous.get("restart_count"), c.get("restart_count")
            if isinstance(before, int) and isinstance(now, int) and now > before:
                return "unhealthy", "target container restarted after submission"
    if any(
        not (
            (c.get("state") or {}).get("Health") or (c.get("state") or {}).get("Healthcheck") or {}
        ).get("Status")
        for c in current
    ):
        return "healthy", "Running; no healthcheck configured"
    return "healthy", "all target replicas running with native health"


async def _capture_ssh(storage, executor):
    from .ssh_compose import TOOLS
    from .ssh_compose_deploy import _command, read_state, replica_counts
    from .ssh_compose_ownership import verify_session
    from .ssh_compose_plan import SSHComposeTarget
    from .ssh_transport import open_ssh_session

    target = SSHComposeTarget.model_validate(executor.target_ref)
    connection = await storage.get_runtime_connection(executor.runtime_connection_id)
    groups = {}
    async with open_ssh_session(storage, connection) as session:
        await verify_session(storage, executor, session, target)
        async with session.sftp() as sftp:
            _, _, rendered, _, _ = await read_state(session, sftp, target)
        names = [b.service for b in executor.service_bindings]
        images = {name: rendered.get("services", {}).get(name, {}).get("image") for name in names}
        counts = replica_counts(rendered, names)
        engine = TOOLS[target.tool][1]
        for name in names:
            ids = set()
            for label in ("com.docker.compose.project", "io.podman.compose.project"):
                output = await _command(
                    session,
                    [
                        engine,
                        "ps",
                        "--all",
                        "--no-trunc",
                        "--filter",
                        f"label={label}={target.project}",
                        "--filter",
                        f"label=com.docker.compose.service={name}",
                        "--format",
                        "{{.ID}}",
                    ],
                    target,
                )
                ids.update(output.split())
            if len(ids) > 64 or any(
                not 12 <= len(cid) <= 64 or any(c not in "0123456789abcdef" for c in cid)
                for cid in ids
            ):
                raise ValueError("invalid runtime container identities")
            records = []
            for cid in sorted(ids):
                attrs = json.loads(await _command(session, [engine, "inspect", cid], target))[0]
                records.append(
                    {
                        "id": cid,
                        "image_id": attrs.get("Image"),
                        "image": (attrs.get("Config") or {}).get("Image"),
                        "state": _safe_state(attrs.get("State") or {}),
                        "restart_count": attrs.get("RestartCount"),
                        "healthcheck_configured": _has_healthcheck(attrs),
                    }
                )
            groups[name] = records
    return {"kind": "ssh_compose", "containers": groups, "counts": counts, "images": images}


def _probe_containers(current, verification, expected, *, immutable_images=None):
    target = verification.get("target") or {}
    baseline = verification.get("baseline") or {}
    rows = []
    enrolled = dict(target.get("containers") or {})
    changed = False
    for name, image in expected.items():
        submitted = target.get("containers", {}).get(name, [])
        before = baseline.get("containers", {}).get(name, [])
        now = current.get("containers", {}).get(name, [])
        count = (verification.get("readiness_context") or {}).get("counts", {}).get(name)
        if count is None:
            count = baseline.get("counts", {}).get(name) or len(before)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            rows.append(_service(name, "unknown", "expected replica count missing"))
            continue
        skipped = any(
            s.get("service") == name and s.get("status") == "skipped"
            for s in verification.get("services", [])
        )
        old_ids = {c.get("id") for c in before}
        awaiting_identity = not submitted or (
            not skipped and any(c.get("id") in old_ids for c in submitted)
        )
        if awaiting_identity:
            if (
                not before
                or len(now) != count
                or any(
                    not c.get("id")
                    or c.get("id") in old_ids
                    or not c.get("image_id")
                    or c.get("image") != image
                    for c in now
                )
            ):
                rows.append(
                    _service(name, "pending", "waiting for all new target container identities")
                )
                continue
            if immutable_images and any(
                c.get("image_id") != immutable_images.get(name) for c in now
            ):
                rows.append(_service(name, "pending", "waiting for submitted immutable image"))
                continue
            enrolled[name] = now
            changed = True
            rows.append(
                _service(
                    name, "pending", "target identities enrolled; awaiting durable verification"
                )
            )
            continue
        if immutable_images and any(c.get("image_id") != immutable_images.get(name) for c in now):
            rows.append(_service(name, "superseded", "submitted immutable image changed"))
            continue
        status, message = _container_status(now, submitted, image, count)
        rows.append(_service(name, status, message))
    result = _aggregate(rows)
    if changed:
        result["verification_update"] = {"target": {**target, "containers": enrolled}}
    return result


async def _probe_ssh(storage, executor, verification):
    context = verification.get("readiness_context") or {}
    if not context.get("expected") or not context.get("counts") or not context.get("targets"):
        return _result("unknown", message="SSH immutable image/replica evidence missing")
    current = await _capture_ssh(storage, executor)
    if any(current["images"].get(name) != image for name, image in context["targets"].items()):
        return _result("superseded", message="SSH project target images changed")
    return _probe_containers(
        current, verification, context["targets"], immutable_images=context["expected"]
    )


async def _probe_deployment(storage, scheduler, executor, verification: dict) -> dict:
    """One read-only attempt; caller owns deadline, retry, durability and HTTP checks."""
    try:
        if executor.target_ref.get("mode") == "ssh_compose":
            return await _probe_ssh(storage, executor, verification)
        current = await _capture(storage, scheduler, executor)
        target = verification.get("target") or {}
        baseline = verification.get("baseline") or {}
        expected = {
            s["service"]: s.get("to_version")
            for s in verification.get("services", [])
            if s.get("service") and s.get("to_version")
        }
        if not expected and verification.get("to_version"):
            expected = {"container": verification["to_version"]}
        if any(
            expected.get(name) != image
            for name, image in target.get("submitted_images", {}).items()
        ):
            return _result("superseded", message="submitted image targets differ from finalization")
        rows = []
        if "workloads" in current:
            if current["kind"] == "helm_release":
                release, submitted = current.get("release") or {}, target.get("release") or {}
                if not submitted.get("version"):
                    return _result("unknown", message="submitted Helm revision missing")
                previous_revision = (baseline.get("release") or {}).get("version")
                if previous_revision is None:
                    return _result("unknown", message="pre-update Helm revision missing")
                if int(submitted["version"]) <= int(previous_revision):
                    return _result("pending", message="new Helm revision not observed")
                if int(submitted["version"]) != int(previous_revision) + 1:
                    return _result("superseded", message="intervening Helm revision detected")
                if release.get("version") != submitted["version"]:
                    return _result("superseded", message="Helm revision changed")
                status = (release.get("info") or {}).get("status")
                if status != "deployed":
                    return _result(
                        "unhealthy" if status == "failed" else "pending",
                        message="Helm release not deployed",
                    )
            if not target.get("workloads"):
                return _result("unknown", message="submitted workload identities missing")
            for name, submitted in target["workloads"].items():
                workload = current["workloads"].get(name)
                if workload is None:
                    rows.append(_service(name, "unknown", "submitted workload missing"))
                    continue
                before = baseline.get("workloads", {}).get(name) or {}
                before_meta, submitted_meta = (
                    before.get("metadata") or {},
                    submitted.get("metadata") or {},
                )
                generation = submitted_meta.get("generation")
                if current["kind"] == "kubernetes_workload":
                    if not isinstance(before_meta.get("generation"), int) or not isinstance(
                        generation, int
                    ):
                        rows.append(_service(name, "unknown", "pre-update generation missing"))
                        continue
                    if (
                        before_meta.get("uid") != submitted_meta.get("uid")
                        or generation > before_meta["generation"] + 1
                    ):
                        rows.append(
                            _service(name, "superseded", "intervening workload mutation detected")
                        )
                        continue
                    if generation <= before_meta["generation"]:
                        rows.append(_service(name, "pending", "submitted generation not advanced"))
                        continue
                images = {c["name"]: c["image"] for c in before.get("containers", [])}
                images.update(expected)
                if current["kind"] == "helm_release":
                    images = {c["name"]: c["image"] for c in submitted.get("containers", [])}
                status, message = _workload_status(workload, submitted, images)
                rows.append(_service(name, status, message, "kubernetes_rollout"))
        else:
            return _probe_containers(current, verification, expected)
        return _aggregate(rows)
    except NotImplementedError:
        return _result("unsupported", message="native readiness unsupported for target")
    except Exception:
        # Do not serialize SDK/remote errors: they may contain credentials or file contents.
        return _result("unknown", message="native readiness observation unavailable")


async def probe_deployment(storage, scheduler, executor, verification: dict) -> dict:
    """Bounded one-shot native observation, without polling or mutation."""
    try:
        return await asyncio.wait_for(
            _probe_deployment(storage, scheduler, executor, verification), timeout=30
        )
    except TimeoutError:
        return _result("unknown", message="native readiness observation timed out")


async def resolve_deployment_resource_scope(storage, scheduler, executor) -> str:
    """Only proven immutable workload identity permits independent mutation scopes.

    Helm may own overlapping workloads; Docker/SSH/Portainer cross-entry daemon
    equivalence is not established here. Their caller must use its proven ownership
    identity or conservatively retain the wildcard scope.
    """
    if executor.target_ref.get("mode") != "kubernetes_workload":
        return "*"
    evidence = await capture_deployment_baseline(storage, scheduler, executor)
    workloads = evidence.get("workloads") or {}
    if len(workloads) != 1:
        return "*"
    uid = (next(iter(workloads.values())).get("metadata") or {}).get("uid")
    return f"kubernetes:uid:{uid}" if isinstance(uid, str) and uid.strip() else "*"
