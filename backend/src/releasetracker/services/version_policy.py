"""Deployment direction gates are independent of automatic update range limits."""

from ..tag_ordering import tag_order
from ..versioning import parsed_version
from .deployment_version_evidence import image_reference


def stable_version(value):
    version = parsed_version(value)
    if version is None or version.is_prerelease or version.is_devrelease:
        return None
    return version


def image_version(image):
    return stable_version(image_reference(image)[1])


def allowed(policy, before, after):
    if before is None or after is None or after < before:
        return False
    return before.major == after.major and (policy == "minor" or before.minor == after.minor)


def comparable_order(tag, channel):
    if not isinstance(tag, str):
        return None
    order = tag_order(tag, channel)
    if order is not None and order.family == "extracted":
        return order
    return order if stable_version(tag) is not None else None


def image_pair(evidence, target, service, current):
    desired = target.get("target") or []
    target_tag = desired[0] if desired else None
    proof = next(
        (
            p
            for p in getattr(evidence, "version_evidence", ())
            if p.get("source_id") == target.get("source_id")
            and p.get("channel_name") == target.get("channel")
            and p.get("service") == service
            and p.get("target") == desired
        ),
        None,
    )
    if proof and proof.get("same_artifact"):
        return None, None, None
    channel = proof.get("channel") if proof else None
    current_tag = image_reference(current)[1]
    aliases = proof.get("aliases") if proof else None
    tags = aliases or [current_tag]
    orders = {order for tag in tags if (order := comparable_order(tag, channel)) is not None}
    after = comparable_order(target_tag, channel)
    if len(orders) != 1 or after is None:
        return "version_unknown_requires_approval", None, None
    before = next(iter(orders))
    if before.family != after.family:
        return "version_unknown_requires_approval", None, None
    if after.value < before.value:
        return "version_downgrade_requires_approval", None, None
    if after.value == before.value:
        return None, None, None
    # Explicit extracted fields express direction, not major/minor/patch ranges.
    if before.family == "extracted":
        return None, None, after
    versions = {stable_version(tag) for tag in tags if stable_version(tag) is not None}
    return None, next(iter(versions)) if len(versions) == 1 else None, stable_version(target_tag)


def version_policy_reason(task, evidence):
    payload = task["payload"]
    policy = payload.get("auto_update_policy", "all")
    targets = payload.get("targets") or []
    if not targets:
        return "version_policy_requires_approval"
    configuration = evidence.configuration
    pairs = []
    for target in targets:
        if evidence.recovery == "helm_revision":
            before = stable_version(configuration.get("current_chart_version"))
            after = stable_version(target.get("chart_version"))
            reason = (
                "version_unknown_requires_approval"
                if before is None or after is None
                else "version_downgrade_requires_approval" if after < before else None
            )
            pairs.append((reason, before, after))
        elif evidence.recovery == "container_config":
            pairs.append(image_pair(evidence, target, None, configuration.get("current_image")))
        else:
            current = configuration.get("current_services") or {}
            services = [
                binding["service"]
                for binding in payload.get("policy_bindings", [])
                if binding.get("tracker_source_id") == target.get("source_id")
                and binding.get("channel_name") == target.get("channel")
            ]
            if not services:
                return "version_policy_requires_approval"
            pairs.extend(
                image_pair(evidence, target, service, current.get(service)) for service in services
            )
    for reason in ("version_downgrade_requires_approval", "version_unknown_requires_approval"):
        if any(pair[0] == reason for pair in pairs):
            return reason
    if payload.get("manual", True) or policy == "all":
        return None
    if policy not in {"minor", "patch"} or not all(
        (before is None and after is None) or allowed(policy, before, after)
        for _, before, after in pairs
    ):
        return "version_policy_requires_approval"
    return None
