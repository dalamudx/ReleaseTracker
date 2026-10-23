"""Live update review. Only HMAC state identity and masked changed lines leave here."""

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import json

from ..executor_trigger import _binding_contexts
from ..executor_scheduler_target_resolution import QUEUED_TARGETS
from .deployment_plan import MANAGED_MARKERS
from .recovery_diff import _identity, _normalize, capture_current, changed_lines, project

INSPECTED_UPDATE_STATE = ContextVar("inspected_update_state", default=None)
EXPECTED_UPDATE_STATE = ContextVar("expected_update_state", default=None)
UPDATE_REFUSAL = ContextVar("update_refusal", default=None)


@contextmanager
def frozen_targets(task):
    token = QUEUED_TARGETS.set(task["payload"].get("targets", []) if task else None)
    marker_token = MANAGED_MARKERS.set(None)  # Inspection must not simulate the write's labels.
    try:
        yield
    finally:
        MANAGED_MARKERS.reset(marker_token)
        QUEUED_TARGETS.reset(token)


def protected_state(storage, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hmac.new(
        storage.system_key_manager.jwt_secret.encode(),
        ("deployment-state-v1:" + raw).encode(),
        hashlib.sha256,
    ).hexdigest()


def _declaration(value, key=""):
    """Ignore only our write-time markers; user labels remain reviewed configuration."""
    from .deployment_plan import MARKER_KEYS, PREFIX

    marker_keys = {*MARKER_KEYS, PREFIX + "deployment-id"}
    if key.lower() in {"labels", "annotations"}:
        if isinstance(value, list) and all(isinstance(v, str) and "=" in v for v in value):
            value = dict(v.split("=", 1) for v in value)
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in marker_keys}
    if isinstance(value, dict):
        result = {k: _declaration(v, k) for k, v in value.items()}
        return {
            k: v
            for k, v in result.items()
            if not (k.lower() in {"labels", "annotations", "metadata"} and not v)
        }
    if isinstance(value, list):
        return [_declaration(v) for v in value]
    return value


def configuration_state(storage, value):
    return protected_state(
        storage,
        _declaration({k: v for k, v in value.items() if k not in {"revision", "chart_digest"}}),
    )


async def _portainer_overrides(adapter, target_ref, inspected, native, service):
    """Separate image defaults from live overrides, without trusting a mutable tag."""
    from ..executors.container_recovery import normalize_image_id

    image_id = normalize_image_id(inspected.get("Image"))
    if not image_id:
        raise ValueError("deployment native image identity unavailable")
    image = await adapter.inspect_local_image(target_ref, image_id)
    if normalize_image_id(image.get("Id") or image.get("ID")) != image_id:
        raise ValueError("deployment native image identity changed")
    defaults = _normalize(image.get("Config") or {})
    result = deepcopy(native)
    config = result["config"]
    config.pop("Image", None)
    if config.get("Hostname") in {inspected["Id"], inspected["Id"][:12]}:
        config.pop("Hostname", None)
    inherited = {
        "Env": "environment",
        "Cmd": "command",
        "Entrypoint": "entrypoint",
        "User": "user",
        "WorkingDir": "working_dir",
        "Healthcheck": "healthcheck",
        "Labels": "labels",
        "ExposedPorts": "expose",
        "Volumes": "volumes",
        "StopSignal": "stop_signal",
    }
    for key, compose_key in inherited.items():
        actual, base = config.get(key), defaults.get(key)
        if key in {"Env", "Labels"} and isinstance(actual, dict):
            explicit = service.get(compose_key) or {}
            if isinstance(explicit, list):
                explicit = {v.split("=", 1)[0]: None for v in explicit if isinstance(v, str)}
            config[key] = {
                k: v
                for k, v in actual.items()
                if k in explicit or k not in (base or {}) or v != base[k]
            }
            if key == "Labels":
                for generated in ("com.docker.compose.config-hash", "com.docker.compose.image"):
                    config[key].pop(generated, None)
            if not config[key]:
                config.pop(key)
        elif compose_key not in service and actual == base:
            config.pop(key, None)
    return _declaration(result)


async def image_targets(storage, scheduler, executor, task, images):
    result = {}
    if not task:
        return result
    for binding in _binding_contexts(executor):
        selected = next(
            (
                v
                for v in task["payload"].get("targets", [])
                if v.get("source_id") == binding.tracker_source_id
                and v.get("channel") == binding.channel_name
                and v.get("deploy_alias") == getattr(binding, "deploy_alias", None)
            ),
            None,
        )
        if not selected or not selected.get("target"):
            continue
        source = await storage.get_tracker_source(binding.tracker_source_id)
        if source is None or source.source_type != "container":
            continue
        service = getattr(binding, "service", None)
        current = images.get(service)
        if not current:
            raise ValueError("deployment service is missing")
        version, digest = selected["target"]
        result[service] = scheduler._build_target_image(
            current_image=current,
            target_version=version,
            target_digest=digest,
            executor_config=executor,
            tracker_source=source,
            tracker_source_type=source.source_type,
        )
    return result


def live_state(snapshot, observed):
    def artifacts(item):
        return {
            "recovery": item.get("recovery_evidence"),
            "preserved_host_config": item.get("host_config"),
            "pod_relation": item.get("pod_relation_payload"),
            "images": (item.get("artifact_evidence") or {}).get("images"),
            "members": [artifacts(s) for s in item.get("snapshots", [])],
        }

    return {
        "configuration": observed,
        "identity": _identity(snapshot),
        "artifacts": artifacts(snapshot),
    }


async def observed_state(adapter, target_ref, snapshot, observed, *, configuration_projection=None):
    state = live_state(snapshot, observed)
    if "stack_file" in snapshot:
        from urllib.parse import quote
        from ..executors import portainer_recovery

        # Stack declarations alone do not prove the actually running native config.
        groups = await portainer_recovery.containers(adapter, target_ref)
        configurations = {}
        for service, rows in sorted(groups.items()):
            for row in rows:
                inspected = await adapter._request_json(
                    "GET",
                    f"/api/endpoints/{target_ref['endpoint_id']}/docker/containers/{quote(row['id'], safe='')}/json",
                    not_found_message="deployment container disappeared",
                )
                if inspected.get("Id") != row["id"]:
                    raise ValueError("deployment container identity changed")
                if not isinstance(inspected.get("Config"), dict) or not isinstance(
                    inspected.get("HostConfig"), dict
                ):
                    raise ValueError("deployment native configuration unavailable")
                configurations[row["id"]] = _normalize(
                    {
                        "service": service,
                        "config": inspected.get("Config"),
                        "host_config": inspected.get("HostConfig"),
                        "mounts": inspected.get("Mounts"),
                        "networks": {
                            name: {
                                "aliases": sorted(
                                    a
                                    for a in endpoint.get("Aliases") or []
                                    if a not in {row["id"], row["id"][:12]}
                                ),
                                "ipam": endpoint.get("IPAMConfig"),
                                "links": endpoint.get("Links"),
                            }
                            for name, endpoint in (
                                (inspected.get("NetworkSettings") or {}).get("Networks") or {}
                            ).items()
                        },
                    }
                )
                if configuration_projection is not None:
                    projected = await _portainer_overrides(
                        adapter,
                        target_ref,
                        inspected,
                        configurations[row["id"]],
                        observed["compose"]["services"][service],
                    )
                    configuration_projection.setdefault(service, []).append(projected)
        if configuration_projection is not None:
            for members in configuration_projection.values():
                members.sort(key=lambda member: json.dumps(member, sort_keys=True))
        state["native_configuration"] = configurations
    return state


async def verify_update_state(adapter, target_ref):
    try:
        return await _verify_update_state(adapter, target_ref)
    except Exception:
        refusal = UPDATE_REFUSAL.get()
        if refusal is not None:
            refusal["blocked"] = True
        raise ValueError("deployment_configuration_changed") from None


async def _verify_update_state(adapter, target_ref):
    """Second native read after pulling and directly before the destructive write."""
    expected = EXPECTED_UPDATE_STATE.get()
    if expected is None:
        return None
    marker_token = MANAGED_MARKERS.set(None)
    try:
        current = await capture_current(adapter, target_ref)
    finally:
        MANAGED_MARKERS.reset(marker_token)
    if current is None:
        raise ValueError("deployment_configuration_changed")
    observed, _ = project(current, adapter=adapter)
    mode = target_ref.get("mode", "container")
    if mode == "container" and "create_config" in observed:
        observed["create_config"]["image"] = current.get("image")
    elif mode == "docker_compose":
        for item in current.get("snapshots", []):
            observed["containers"][item["container_name"]]["create_config"]["image"] = item.get(
                "image"
            )
    if await observed_state(adapter, target_ref, current, observed) != expected:
        raise ValueError("deployment_configuration_changed")
    return current


async def native_review(
    storage,
    scheduler,
    executor,
    adapter,
    task,
    *,
    inspection_only=False,
    version_evidence=None,
    pre_rollout_states=None,
    configuration_hashes=None,
):
    snapshot = await capture_current(adapter, executor.target_ref, inspection_only=inspection_only)
    if snapshot is None:
        raise ValueError("deployment target is missing")
    mode = executor.target_ref.get("mode", "container")
    if mode == "container" and not isinstance(snapshot.get("create_config"), dict):
        raise ValueError("complete runtime configuration is unavailable")
    artifact = snapshot.get("artifact_evidence") or {}
    if (
        mode == "kubernetes_workload"
        and artifact.get("status") == "unavailable"
        and artifact.get("reason") != "legacy_scope"
    ):
        raise ValueError("deployment_artifact_evidence_unavailable")
    observed, scope = project(snapshot, adapter=adapter)
    mode = executor.target_ref.get("mode", "container")
    images = {}
    if mode == "container":
        images[None] = snapshot.get("image")
        if "create_config" in observed:
            observed["create_config"]["image"] = images[None]
    elif mode == "docker_compose":
        for item in snapshot.get("snapshots", []):
            service = item.get("compose_service")
            images[service] = item.get("image")
            observed["containers"][item["container_name"]]["create_config"]["image"] = item.get(
                "image"
            )
    elif mode == "portainer_stack":
        images = {
            name: value.get("image")
            for name, value in observed["compose"].get("services", {}).items()
        }
    elif mode == "kubernetes_workload":
        images = dict(snapshot.get("containers") or {})
    if mode == "helm_release":
        observed["chart_digest"] = await adapter.get_helm_release_digest(executor.target_ref)
    desired = deepcopy(observed)
    targets = await image_targets(storage, scheduler, executor, task, images)
    proofs = ()
    if version_evidence is not None:
        from .deployment_version_evidence import collect_version_evidence

        proofs = await collect_version_evidence(
            storage, adapter, executor, task, snapshot, images, targets
        )
        version_evidence.extend(proofs)
    if mode == "container" and None in targets:
        key = desired.get("create_config", desired)
        key["image"] = targets[None]
    elif mode == "docker_compose":
        for item in snapshot["snapshots"]:
            if item.get("compose_service") in targets:
                desired["containers"][item["container_name"]]["create_config"]["image"] = targets[
                    item["compose_service"]
                ]
    elif mode == "portainer_stack":
        # Use the same format-preserving rewrite as the native adapter (then compare semantics).
        if targets:
            import yaml

            patched, _ = adapter._patch_stack_file_service_images(snapshot["stack_file"], targets)
            desired["compose"] = _normalize(yaml.safe_load(patched))
    elif mode == "kubernetes_workload":
        containers = (
            desired["spec"]["template"]["spec"]["containers"]
            if "spec" in desired
            else desired["images"]
        )
        for service, image in targets.items():
            if "spec" in desired:
                containers[service]["image"] = image
                if (
                    image == images[service]
                    and "@" not in image
                    and not any(
                        p.get("service") == service and p.get("same_artifact") for p in proofs
                    )
                ):
                    # A recreated Pod must resolve a republished tag instead of reusing the node cache.
                    containers[service]["imagePullPolicy"] = "Always"
            else:
                containers[service] = image
    elif mode == "helm_release" and task:
        selected = next(iter(task["payload"].get("targets", [])), {})
        if selected.get("chart_version"):
            desired["chart_version"] = selected["chart_version"]
        if selected.get("chart_digest"):
            from .deployment_baseline import chart_digest

            desired["chart_digest"] = chart_digest(selected["chart_digest"])
    lines, truncated = changed_lines(observed, desired)
    diff = {"scope": scope, "lines": lines, "truncated": truncated}
    if inspection_only:
        # A draft reads declarations; it must not publish evidence for a later write.
        return protected_state(storage, {"inspection": observed}), diff
    native_configuration = {} if configuration_hashes is not None else None
    state = await observed_state(
        adapter,
        executor.target_ref,
        snapshot,
        observed,
        configuration_projection=native_configuration,
    )
    if configuration_hashes is not None:
        configuration_hashes.update(
            current=configuration_state(
                storage, {**observed, "native_configuration": native_configuration}
            ),
            desired=configuration_state(
                storage, {**desired, "native_configuration": native_configuration}
            ),
        )
    if (
        pre_rollout_states is not None
        and mode == "kubernetes_workload"
        and (snapshot.get("artifact_evidence") or {}).get("status") == "verified"
    ):
        incomplete = deepcopy(state)
        incomplete["artifacts"]["images"] = None
        pre_rollout_states.append(protected_state(storage, incomplete))
    INSPECTED_UPDATE_STATE.set(state)
    return protected_state(storage, state), diff


async def ssh_review(
    storage,
    executor,
    connection,
    target,
    targets,
    *,
    task=None,
    version_evidence=None,
    current_services=None,
    configuration_hashes=None,
):
    from .ssh_compose_deploy import inspect_image, make_plan, running_image_ids
    from .ssh_transport import open_ssh_session

    async with open_ssh_session(storage, connection) as session:
        async with session.sftp() as sftp:
            plan, _ = await make_plan(session, sftp, target, targets)
        images = {s: plan.rendered.get("services", {}).get(s, {}).get("image") for s in targets}
        if current_services is not None:
            current_services.update(images)
        if version_evidence is not None and task:
            from .deployment_version_evidence import collect_ssh_version_evidence

            running = await running_image_ids(session, target, targets)
            version_evidence.extend(
                await collect_ssh_version_evidence(
                    storage,
                    executor,
                    task,
                    images,
                    targets,
                    running,
                    lambda reference: inspect_image(session, target, reference),
                )
            )
    observed = _normalize({"compose": plan.rendered})
    desired = deepcopy(observed)
    for service, image in plan.targets.items():
        desired["compose"]["services"][service]["image"] = image
    if configuration_hashes is not None:
        # Revision and runtime identities are verified separately; only planned config changes belong here.
        configuration_hashes.update(
            current=configuration_state(storage, observed),
            desired=configuration_state(storage, desired),
        )
    lines, truncated = changed_lines(observed, desired)
    return protected_state(
        storage,
        {"files": plan.files, "configuration": observed, "target": target.model_dump(mode="json")},
    ), {"scope": "ssh_files", "lines": lines, "truncated": truncated}
