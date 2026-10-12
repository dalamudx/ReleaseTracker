"""Deployment direction gates are independent of automatic update range limits."""

from ..versioning import parsed_version


def stable_version(value):
    version = parsed_version(value)
    if version is None or version.is_prerelease or version.is_devrelease:
        return None
    return version


def image_version(image):
    if not isinstance(image, str):
        return None
    reference = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return stable_version(reference.rsplit(":", 1)[1]) if ":" in reference else None


def allowed(policy, before, after):
    if before is None or after is None or after < before:
        return False
    return before.major == after.major and (policy == "minor" or before.minor == after.minor)


def version_policy_reason(task, evidence):
    payload = task["payload"]
    policy = payload.get("auto_update_policy", "all")
    targets = payload.get("targets") or []
    if not targets:
        return "version_policy_requires_approval"
    configuration = evidence.configuration
    all_pairs = []
    for target in targets:
        if evidence.recovery == "helm_revision":
            pairs = [
                (
                    stable_version(configuration.get("current_chart_version")),
                    stable_version(target.get("chart_version")),
                )
            ]
        elif evidence.recovery == "container_config":
            desired = target.get("target") or []
            pairs = [
                (
                    image_version(configuration.get("current_image")),
                    stable_version(desired[0] if desired else None),
                )
            ]
        else:
            current = configuration.get("current_services") or {}
            services = [
                binding["service"]
                for binding in payload.get("policy_bindings", [])
                if binding.get("tracker_source_id") == target.get("source_id")
                and binding.get("channel_name") == target.get("channel")
            ]
            desired = target.get("target") or []
            after = stable_version(desired[0] if desired else None)
            pairs = [(image_version(current.get(service)), after) for service in services]
        if not pairs:
            return "version_policy_requires_approval"
        all_pairs.extend(pairs)
    # Neither manual initiation nor "all" authorizes a downgrade/unknown direction.
    if any(
        before is not None and after is not None and after < before for before, after in all_pairs
    ):
        return "version_downgrade_requires_approval"
    if any(before is None or after is None for before, after in all_pairs):
        return "version_unknown_requires_approval"
    if payload.get("manual", True) or policy == "all":
        return None
    if policy not in {"minor", "patch"} or not all(
        allowed(policy, before, after) for before, after in all_pairs
    ):
        return "version_policy_requires_approval"
    return None
