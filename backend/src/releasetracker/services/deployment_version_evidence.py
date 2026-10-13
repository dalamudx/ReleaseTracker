"""Bind version aliases to the running artifact, never to a mutable tag lookup."""

import re

from ..executors.container_recovery import normalize_image_id
from ..tag_ordering import compile_pattern
from ..versioning import container_channel_eligible


def image_reference(value):
    """Canonical repository, tag and digest; Docker Hub shorthand is equivalent."""
    if not isinstance(value, str) or not value or "://" in value:
        return None, None, None
    name, _, digest = value.partition("@")
    last = name.rsplit("/", 1)[-1]
    tag = last.rsplit(":", 1)[1] if ":" in last else None
    repository = name.rsplit(":", 1)[0] if tag else name
    parts = repository.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        return None, None, None
    if len(parts) == 1 or not ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts.insert(0, "docker.io")
    if parts[0] in {"index.docker.io", "registry-1.docker.io"}:
        parts[0] = "docker.io"
    if parts[0] == "docker.io" and len(parts) == 2:
        parts.insert(1, "library")
    return "/".join(parts), tag, normalize_image_id(digest)


def channel_accepts(tag, channel):
    if not container_channel_eligible(tag, "", channel):
        return False
    try:
        return (
            not channel.get("include_pattern")
            or compile_pattern(channel["include_pattern"]).search(tag) is not None
        ) and (
            not channel.get("exclude_pattern")
            or compile_pattern(channel["exclude_pattern"]).search(tag) is None
        )
    except (ValueError, re.error):
        return False


def snapshot_artifacts(snapshot):
    mode = snapshot.get("mode", "container")
    if mode == "docker_compose":
        result = {}
        for item in snapshot.get("snapshots", []):
            result.setdefault(item.get("compose_service"), []).append(
                (item.get("recovery_evidence") or {}).get("image_id")
            )
        return result
    if mode == "portainer_stack" or "stack_file" in snapshot:
        return {
            service: [item.get("image_id")]
            for service, item in (snapshot.get("recovery_evidence") or {})
            .get("services", {})
            .items()
        }
    if mode == "kubernetes_workload":
        proof = snapshot.get("artifact_evidence") or {}
        if proof.get("status") == "verified":
            return {k: [v] for k, v in proof.get("images", {}).get("containers", {}).items()}
        return {}
    return {None: [(snapshot.get("recovery_evidence") or {}).get("image_id")]}


async def collect_version_evidence(storage, adapter, executor, task, snapshot, images, targets):
    if not task:
        return ()
    artifacts = snapshot_artifacts(snapshot)
    proofs = []
    cache = {}

    async def inspect(reference):
        if reference not in cache:
            try:
                cache[reference] = await adapter.inspect_local_image(executor.target_ref, reference)
            except Exception:
                cache[reference] = None
        return cache[reference]

    for selected in task["payload"].get("targets", []):
        source = (
            await storage.get_tracker_source(selected.get("source_id"))
            if selected.get("source_id")
            else None
        )
        if source is None or source.source_type != "container":
            continue
        channels = [c for c in source.release_channels if c.name == selected.get("channel")]
        if len(channels) != 1:
            continue
        channel = channels[0].model_dump(mode="json")
        services = (
            [None]
            if executor.target_ref.get("mode", "container") == "container"
            else [
                b["service"]
                for b in task["payload"].get("policy_bindings", [])
                if b.get("tracker_source_id") == selected.get("source_id")
                and b.get("channel_name") == selected.get("channel")
            ]
        )
        for service in services:
            current_repo, _, _ = image_reference(images.get(service))
            target_repo, _, _ = image_reference(targets.get(service))
            desired = selected.get("target") or []
            digest = normalize_image_id(desired[1]) if len(desired) > 1 else None
            aliases = None
            same_artifact = bool(digest and current_repo == target_repo and current_repo)
            ids = artifacts.get(service) or [None]
            verified_ids = []
            for identity in ids:
                # Kubernetes evidence is already verified across live pods/platforms.
                repo, _, pinned = image_reference(identity)
                if pinned and repo == current_repo:
                    same_artifact &= pinned == digest
                    aliases = set()
                    verified_ids.append(identity)
                    continue
                identity = normalize_image_id(identity)
                info = await inspect(identity) if identity else None
                if not isinstance(info, dict) or normalize_image_id(info.get("Id")) != identity:
                    same_artifact = False
                    aliases = set()
                    continue
                verified_ids.append(identity)
                tags = {
                    tag
                    for ref in info.get("RepoTags") or []
                    for repository, tag, _ in [image_reference(ref)]
                    if repository == current_repo and tag and channel_accepts(tag, channel)
                }
                aliases = tags if aliases is None else aliases & tags
                digests = {
                    d
                    for ref in info.get("RepoDigests") or []
                    for repository, _, d in [image_reference(ref)]
                    if repository == target_repo and d
                }
                same_artifact &= digest in digests
            # A local tag may have moved since its digest was discovered. Confirm
            # the actual target reference too; image inspect never pulls an image.
            if same_artifact and not all(image_reference(i)[2] for i in verified_ids):
                desired_info = await inspect(targets.get(service))
                desired_id = (
                    normalize_image_id(desired_info.get("Id"))
                    if isinstance(desired_info, dict)
                    else None
                )
                same_artifact = bool(desired_id and all(i == desired_id for i in verified_ids))
            proofs.append(
                {
                    "source_id": selected.get("source_id"),
                    "channel_name": selected.get("channel"),
                    "service": service,
                    "target": desired,
                    "channel": channel,
                    "aliases": sorted(aliases or []),
                    "image_ids": sorted(set(verified_ids)),
                    "same_artifact": bool(same_artifact),
                }
            )
    return tuple(proofs)
