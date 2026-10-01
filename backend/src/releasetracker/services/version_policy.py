"""Fail-closed semantic version limits, using observed runtime images/chart versions.

These are approval gates, not version selection rules. Unknown tags, digest-only
images, prereleases and downgrades require explicit approval under a restriction.
"""

import re

STABLE = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z.-]+)?")


def stable_version(value):
    match = STABLE.fullmatch(value) if isinstance(value, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def image_version(image):
    if not isinstance(image, str):
        return None
    # A tag accompanying a digest remains usable; a digest alone says no version.
    reference = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return stable_version(reference.rsplit(":", 1)[1]) if ":" in reference else None


def allowed(policy, before, after):
    if before is None or after is None or after < before:
        return False
    return before[0] == after[0] and (policy == "minor" or before[1] == after[1])


def version_policy_reason(task, evidence):
    payload = task["payload"]
    policy = payload.get("auto_update_policy", "all")
    if payload.get("manual", True) or policy == "all":
        return None
    if policy not in {"minor", "patch"}:
        return "version_policy_requires_approval"
    targets = payload.get("targets") or []
    if not targets:
        return "version_policy_requires_approval"
    configuration = evidence.configuration
    for target in targets:
        if evidence.recovery == "helm_revision":
            before = stable_version(configuration.get("current_chart_version"))
            after = stable_version(target.get("chart_version"))
            pairs = [(before, after)]
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
        if not pairs or not all(allowed(policy, before, after) for before, after in pairs):
            return "version_policy_requires_approval"
    return None
