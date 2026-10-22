"""Version selection is independent of publication and observation timestamps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from packaging.version import InvalidVersion, Version


@total_ordering
@dataclass(frozen=True, eq=False)
class ComparableVersion:
    key: tuple
    major: int
    minor: int
    micro: int
    is_prerelease: bool
    is_devrelease: bool = False

    def __eq__(self, other):
        return isinstance(other, ComparableVersion) and self.key == other.key

    def __lt__(self, other):
        if not isinstance(other, ComparableVersion):
            return NotImplemented
        return self.key < other.key

    def __hash__(self):
        return hash(self.key)


SELECTION_RULES_VERSION = 7


def normalize_version(value: str) -> str:
    value = value.strip()
    for prefix in ("version/", "release/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    if len(value) > 1 and value[0] == "v" and value[1].isdigit():
        value = value[1:]
    return value


def parsed_version(value: str | None) -> ComparableVersion | None:
    if not value:
        return None
    normalized = normalize_version(value)
    # Docker variants and commit-suffixed prereleases are valid SemVer even when
    # packaging.Version rejects them. Build metadata does not affect precedence.
    semver = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        normalized,
    )
    if semver:
        major, minor, micro = (int(semver[i]) for i in (1, 2, 3))
        pre = semver[4]
        identifiers = pre.split(".") if pre else []
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in identifiers):
            return None
        stage = (
            (0, tuple((0, int(part)) if part.isdigit() else (1, part) for part in identifiers))
            if pre
            else (1, 0)
        )
        return ComparableVersion((0, (major, minor, micro), stage), major, minor, micro, bool(pre))
    try:
        # Preserve existing PEP-440 version formats rather than silently dropping them.
        version = Version(normalized)
    except InvalidVersion:
        return None
    release = list(version.release)
    while len(release) > 3 and release[-1] == 0:
        release.pop()
    release.extend([0] * max(0, 3 - len(release)))
    if version.pre or version.dev is not None:
        label, number = version.pre or ("dev", version.dev)
        label = {"a": "alpha", "b": "beta"}.get(label, label)
        stage = (0, ((1, label), (0, number)))
        if version.dev is not None and version.pre:
            stage = (0, (*stage[1], (1, "dev"), (0, version.dev)))
    else:
        stage = (1, version.post or 0)
    return ComparableVersion(
        (version.epoch, tuple(release), stage),
        version.major,
        version.minor,
        version.micro,
        version.is_prerelease,
        version.is_devrelease,
    )


def container_tag_eligible(
    tag: str, version_format: str = "dotted", *, exact_alias: str | None = None
) -> bool:
    # version_format is a retired compatibility argument, not a membership filter.
    from .tag_ordering import tag_order

    return tag == exact_alias if exact_alias is not None else tag_order(tag) is not None


def container_channel_eligible(tag: str, version_format: str, channel) -> bool:
    exact = pinned_alias(channel)
    if exact is not None:
        return tag == exact
    kind = channel.get("type") if isinstance(channel, dict) else channel.type
    prerelease = bool(
        re.search(
            r"(?:^|[-._])(?:alpha|beta|rc|dev|nightly|canary|pre)(?:[0-9._-]|$)", tag, re.IGNORECASE
        )
    )
    # Membership does not depend on whether a version can be ordered.
    return not ((kind == "release" and prerelease) or (kind == "prerelease" and not prerelease))


def release_version(release):
    return parsed_version(
        (release.chart_version or release.tag_name)
        if release.tracker_type == "helm"
        else release.version
    )


def release_order_key(release) -> tuple:
    version = release_version(release)
    return (1, version) if version is not None else (0,)


def pinned_alias(channel) -> str | None:
    """Only an explicit exact tag pattern opts into non-version tracking."""
    pattern = (
        channel.get("include_pattern") if isinstance(channel, dict) else channel.include_pattern
    )
    if not pattern:
        return None
    if not pattern.startswith("^") or not pattern.endswith("$"):
        return None
    literal = pattern[1:-1]
    if not re.fullmatch(r"(?:[A-Za-z0-9_:/-]|\\[.+])+", literal):
        return None
    return literal.replace("\\.", ".").replace("\\+", "+")


def tag_specificity(tag: str | None) -> int:
    """Count the numeric release components a tag spells out (1.17.0 -> 3)."""
    match = re.match(r"\d+(?:\.\d+)*", normalize_version(tag or ""))
    return match[0].count(".") + 1 if match else 0


def select_version_candidate(releases, *, exact_alias=None, channel=None):
    from .tag_ordering import ranked_candidates, ordering_issue

    candidates = list(releases)
    if exact_alias:
        winners = [r for r in candidates if r.tag_name == exact_alias]
    else:
        if ordering_issue(candidates, channel):
            return None
        keyed = ranked_candidates(candidates, channel)
        best_key = max(key.value for _, key in keyed)
        winners = [r for r, key in keyed if key.value == best_key]
    if not winners:
        return None
    by_source = {}
    for release in winners:
        by_source.setdefault(
            (release.selection_source_id or release.tracker_name, release.tracker_type), set()
        ).add(release.artifact_digest or release.commit_sha)
    if any(len(identities) > 1 for identities in by_source.values()):
        return None
    return min(
        winners,
        key=lambda r: (
            r.tracker_type == "container",
            -tag_specificity(r.tag_name),
            r.tag_name,
            r.tracker_type,
            r.name,
        ),
    )
