"""Conservative Pod-backed manifest evidence; never resolves a tag via a registry.

Snapshots keep original configuration. Only the private recovery projection pins
images, and unavailable evidence cannot make an old/mutable snapshot recoverable.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from ..services.deployment_readiness_probes import _workload_status
from .kubernetes_recovery import normalize_spec, validate_strategy

LIMIT = 128
READ_TIMEOUT = 10
PINNED = re.compile(r"[^@\s]+@sha256:[a-f0-9]{64}")


class Unavailable(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Unavailable(reason)


def pinned(image):
    return isinstance(image, str) and PINNED.fullmatch(image) is not None


def repository(image):
    require(
        isinstance(image, str) and bool(image) and not any(c.isspace() for c in image),
        "invalid_image",
    )
    base = image.split("@", 1)[0]
    head, sep, tail = base.rpartition("/")
    return (head + sep if sep else "") + tail.split(":", 1)[0]


def binding(snapshot):
    workload = snapshot["workload"]
    metadata = workload.get("metadata") or {}
    value = {key: snapshot[key] for key in ("namespace", "kind", "name")}
    value.update(
        uid=metadata.get("uid"),
        generation=metadata.get("generation"),
        resource_version=metadata.get("resourceVersion", metadata.get("resource_version")),
        spec=workload.get("api_spec"),
    )
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def groups(snapshot):
    pod = snapshot["workload"].get("api_spec", {}).get("template", {}).get("spec", {})
    result = {}
    names = set()
    for field in ("containers", "initContainers"):
        entries = pod.get(field, [])
        require(isinstance(entries, list), "invalid_template")
        result[field] = {}
        for entry in entries:
            name = entry.get("name")
            require(isinstance(name, str) and name and name not in names, "duplicate_container")
            names.add(name)
            result[field][name] = entry.get("image")
    require(result["containers"] == snapshot["containers"], "image_index_mismatch")
    return result


def owner(obj):
    refs = getattr(obj.metadata, "owner_references", None) or []
    controllers = [ref for ref in refs if ref.controller is True]
    return controllers[0] if len(controllers) == 1 else None


def owned(obj, uid, kind):
    ref = owner(obj)
    return ref is not None and ref.uid == uid and ref.kind == kind


def complete(response):
    require(not getattr(response.metadata, "_continue", None), "truncated_list")
    require(
        isinstance(getattr(response.metadata, "resource_version", None), str)
        and response.metadata.resource_version,
        "list_version_missing",
    )
    require(isinstance(response.items, list) and len(response.items) <= LIMIT, "invalid_list")
    return response.items


def revision_template(revision):
    data = revision.data
    require(isinstance(data, dict), "revision_data_missing")
    template = deepcopy(data.get("spec", {}).get("template"))
    require(isinstance(template, dict), "revision_template_missing")
    directive = template.pop("$patch", "replace")
    require(directive == "replace", "unsupported_revision_patch")
    return normalize_spec({"template": template})["template"]


def controller(adapter, snapshot, selector):
    workload = snapshot["workload"]
    uid, kind, namespace = workload["metadata"]["uid"], snapshot["kind"], snapshot["namespace"]
    apps = adapter._get_apps_api()
    options = {"label_selector": selector, "limit": LIMIT, "_request_timeout": READ_TIMEOUT}
    if kind == "Deployment":
        revision = (workload.get("annotations") or {}).get("deployment.kubernetes.io/revision")
        require(isinstance(revision, str) and revision, "deployment_revision_missing")
        replicasets = [
            rs
            for rs in complete(apps.list_namespaced_replica_set(namespace, **options))
            if owned(rs, uid, kind)
        ]
        matches = [
            rs
            for rs in replicasets
            if (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision") == revision
        ]
        require(len(matches) == 1 and matches[0].metadata.uid, "ambiguous_replicaset")
        rs = matches[0]
        require(
            rs.metadata.namespace == namespace and not rs.metadata.deletion_timestamp,
            "invalid_replicaset",
        )
        template = adapter._readiness_fields(rs.spec.template.spec)
        expected = groups(snapshot)
        for field, sdk_field in (
            ("containers", "containers"),
            ("initContainers", "init_containers"),
        ):
            require(
                {c["name"]: c.get("image") for c in template.get(sdk_field) or []}
                == expected[field],
                "replicaset_images_changed",
            )
        return (
            rs.metadata.uid,
            "ReplicaSet",
            "pod-template-hash",
            (rs.metadata.labels or {}).get("pod-template-hash"),
            {rs.metadata.uid for rs in replicasets},
        )
    if kind == "StatefulSet":
        status = workload["status"]
        name = status.get("updateRevision", status.get("update_revision"))
        require(isinstance(name, str) and name, "stateful_revision_missing")
        revision = apps.read_namespaced_controller_revision(
            name, namespace, _request_timeout=READ_TIMEOUT
        )
        require(
            owned(revision, uid, kind)
            and revision.metadata.namespace == namespace
            and revision.metadata.name == name
            and revision.metadata.uid
            and not revision.metadata.deletion_timestamp,
            "wrong_revision_owner",
        )
        require(
            revision_template(revision) == workload["api_spec"]["template"],
            "revision_template_changed",
        )
        return uid, kind, "controller-revision-hash", name, {uid}
    require(kind == "DaemonSet", "unsupported_kind")
    revisions = [
        r
        for r in complete(apps.list_namespaced_controller_revision(namespace, **options))
        if owned(r, uid, kind)
    ]
    matches = [r for r in revisions if revision_template(r) == workload["api_spec"]["template"]]
    require(len(matches) == 1, "ambiguous_daemon_revision")
    revision = matches[0]
    require(
        revision.metadata.namespace == namespace
        and revision.metadata.uid
        and not revision.metadata.deletion_timestamp,
        "invalid_revision",
    )
    prefix = snapshot["name"] + "-"
    require(
        isinstance(revision.metadata.name, str) and revision.metadata.name.startswith(prefix),
        "invalid_revision_name",
    )
    revision_hash = revision.metadata.name[len(prefix) :]
    labels = revision.metadata.labels or {}
    require(
        revision_hash
        and all(
            labels.get(key, revision_hash) == revision_hash
            for key in ("controller.kubernetes.io/hash", "controller-revision-hash")
        ),
        "revision_hash_conflict",
    )
    return uid, kind, "controller-revision-hash", revision_hash, {uid}


def pod_images(pod, expected, snapshot):
    require(
        pod.metadata.namespace == snapshot["namespace"]
        and pod.metadata.uid
        and pod.metadata.resource_version
        and not pod.metadata.deletion_timestamp,
        "invalid_pod_identity",
    )
    require(
        pod.status.phase == "Running"
        and any(c.type == "Ready" and c.status == "True" for c in pod.status.conditions or []),
        "pod_not_ready",
    )
    resolved = {}
    for field, spec_field, status_field in (
        ("containers", "containers", "container_statuses"),
        ("initContainers", "init_containers", "init_container_statuses"),
    ):
        entries = getattr(pod.spec, spec_field) or []
        statuses = getattr(pod.status, status_field) or []
        require(
            len(entries) == len(expected[field])
            and {e.name: e.image for e in entries} == expected[field],
            "pod_template_images_changed",
        )
        status_by_name = {s.name: s for s in statuses}
        require(
            len(statuses) == len(status_by_name) == len(entries)
            and set(status_by_name) == set(expected[field]),
            "pod_status_incomplete",
        )
        resolved[field] = {}
        for entry in entries:
            status = status_by_name[entry.name]
            running = status.state is not None and status.state.running is not None
            if field == "containers" or getattr(entry, "restart_policy", None) == "Always":
                require(running and status.ready is True, "container_not_ready")
            else:
                terminated = status.state.terminated if status.state else None
                require(terminated is not None and terminated.exit_code == 0, "init_not_complete")
            original = entry.image
            if pinned(original):
                resolved[field][entry.name] = original
                continue
            image_id = status.image_id or ""
            if image_id.startswith("docker-pullable://"):
                image_id = image_id[len("docker-pullable://") :]
            # Bare CRI/config IDs are not proven pullable manifest identities.
            require(
                pinned(image_id)
                and "://" not in image_id
                and repository(image_id) == repository(original),
                "manifest_identity_unproven",
            )
            resolved[field][entry.name] = image_id
    return resolved


def collect(adapter, snapshot):
    try:
        if snapshot.get("recovery_scope") != "workload_spec":
            return {"status": "unavailable", "reason": "legacy_scope"}
        expected = groups(snapshot)
        if all(pinned(image) for group in expected.values() for image in group.values()):
            return {"status": "not_required"}
        workload = snapshot["workload"]
        validate_strategy(workload)
        require(
            _workload_status(workload, workload, snapshot["containers"])[0] == "healthy",
            "rollout_not_converged",
        )
        metadata = workload["metadata"]
        require(
            metadata.get("uid")
            and metadata.get("resourceVersion", metadata.get("resource_version")),
            "workload_identity_missing",
        )
        select = workload["api_spec"].get("selector", {})
        require(
            select.get("matchLabels") and not select.get("matchExpressions"), "unsupported_selector"
        )
        selector = ",".join(f"{k}={v}" for k, v in sorted(select["matchLabels"].items()))
        owner_uid, owner_kind, label, revision, owned_ids = controller(adapter, snapshot, selector)
        require(isinstance(revision, str) and revision, "pod_revision_missing")
        listed = adapter._get_core_api().list_namespaced_pod(
            snapshot["namespace"],
            label_selector=selector,
            limit=LIMIT,
            _request_timeout=READ_TIMEOUT,
        )
        pods = []
        for pod in complete(listed):
            ref = owner(pod)
            if ref is not None and ref.uid in owned_ids:
                require(ref.uid == owner_uid and ref.kind == owner_kind, "old_revision_pod")
                require((pod.metadata.labels or {}).get(label) == revision, "wrong_pod_revision")
                pods.append(pod)
        status = workload["status"]
        n = (
            status.get("desiredNumberScheduled", status.get("desired_number_scheduled"))
            if snapshot["kind"] == "DaemonSet"
            else workload["api_spec"].get("replicas", 1)
        )
        require(type(n) is int and 0 < n <= LIMIT and len(pods) == n, "replicas_incomplete")
        require(len({p.metadata.uid for p in pods}) == n, "duplicate_pods")
        if snapshot["kind"] == "DaemonSet":
            require(
                all(p.spec.node_name for p in pods) and len({p.spec.node_name for p in pods}) == n,
                "daemon_nodes_incomplete",
            )
        if snapshot["kind"] == "StatefulSet":
            start = (workload["api_spec"].get("ordinals") or {}).get("start", 0)
            require(
                type(start) is int
                and start >= 0
                and {p.metadata.name for p in pods}
                == {f"{snapshot['name']}-{i}" for i in range(start, start + n)},
                "stateful_ordinals_incomplete",
            )
        samples = [pod_images(p, expected, snapshot) for p in pods]
        require(all(sample == samples[0] for sample in samples), "replica_artifacts_disagree")
        latest = adapter._get_workload(
            snapshot["kind"], snapshot["name"], snapshot["namespace"], request_timeout=READ_TIMEOUT
        )
        require(
            binding({**snapshot, "workload": latest}) == binding(snapshot),
            "workload_changed_during_capture",
        )
        require(
            _workload_status(latest, workload, snapshot["containers"])[0] == "healthy",
            "rollout_changed_during_capture",
        )
        return {
            "status": "verified",
            "schema": 1,
            "binding": binding(snapshot),
            "images": samples[0],
            "pod_list_resource_version": listed.metadata.resource_version,
            "pods": [
                {
                    "uid": p.metadata.uid,
                    "resource_version": p.metadata.resource_version,
                    "name": p.metadata.name,
                    "revision": revision,
                }
                for p in pods
            ],
        }
    except Unavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except Exception:
        # Optional evidence cannot hide a successful snapshot or leak API credentials.
        return {"status": "unavailable", "reason": "evidence_read_failed"}


def prepare(snapshot):
    """Validate evidence against original snapshot before producing a private projection."""
    evidence = snapshot.get("artifact_evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("Kubernetes Pod artifact evidence is invalid")
    if evidence.get("status") != "verified":
        return snapshot
    try:
        require(
            snapshot.get("recovery_scope") == "workload_spec"
            and type(evidence.get("schema")) is int
            and evidence["schema"] == 1
            and evidence.get("binding") == binding(snapshot),
            "snapshot_binding_mismatch",
        )
        expected = groups(snapshot)
        images = evidence.get("images")
        require(
            isinstance(images, dict) and set(images) == set(expected), "evidence_images_incomplete"
        )
        for field, entries in expected.items():
            require(
                isinstance(images[field], dict) and set(images[field]) == set(entries),
                "evidence_images_incomplete",
            )
            for name, original in entries.items():
                resolved = images[field][name]
                require(
                    pinned(resolved)
                    and repository(resolved) == repository(original)
                    and (not pinned(original) or resolved == original),
                    "evidence_image_mismatch",
                )
        projected = deepcopy(snapshot)
        projected["containers"] = deepcopy(images["containers"])
        pod = projected["workload"]["api_spec"]["template"]["spec"]
        for field in expected:
            for entry in pod.get(field, []):
                entry["image"] = images[field][entry["name"]]
        return projected
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Kubernetes Pod artifact evidence is invalid") from exc
