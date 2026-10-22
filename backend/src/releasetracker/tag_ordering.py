"""Channel-scoped ordering without project-specific tag adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

MAX_PATTERN_LENGTH = 512
MAX_REGEX_DEPTH = 12
MAX_REPEAT_BOUND = 256
_ASCII_CHARS = tuple(chr(i) for i in range(128))
_REPEAT_OPS = {re._parser.MAX_REPEAT, re._parser.MIN_REPEAT}


def python_pattern(pattern: str | None) -> str | None:
    if not isinstance(pattern, str):
        return pattern
    return re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", pattern)


def _untaint_char(ch: str) -> str:
    code = ord(ch)
    if 32 <= code < 127:
        return _ASCII_CHARS[code]
    if code < 32 or 0xD800 <= code <= 0xDFFF or code > 0x10FFFF:
        raise re.error("Invalid character in channel pattern")
    acc = 0
    for bit in range(21):
        if (code >> bit) & 1:
            acc |= 1 << bit
    return chr(acc)


def _contains_unbounded_repeat(subpattern) -> bool:
    for op, av in subpattern:
        if op in _REPEAT_OPS:
            _, max_rep, inner = av
            if max_rep == re._parser.MAXREPEAT or max_rep > 1 or _contains_unbounded_repeat(inner):
                return True
        elif op == re._parser.SUBPATTERN and _contains_unbounded_repeat(av[3]):
            return True
        elif op == re._parser.BRANCH and any(_contains_unbounded_repeat(b) for b in av[1]):
            return True
    return False


def _has_mandatory_fixed_delimiter(subpattern) -> bool:
    for op, av in subpattern:
        if op in (re._parser.LITERAL, re._parser.NOT_LITERAL, re._parser.IN):
            return True
        if (
            op == re._parser.SUBPATTERN
            and not _contains_unbounded_repeat(av[3])
            and av[3].getwidth()[0] > 0
        ):
            return True
    return False


def _validate_safe_regex_ast(subpattern, depth: int = 0, *, in_repeat: bool = False) -> None:
    if depth > MAX_REGEX_DEPTH:
        raise re.error("Channel pattern nesting is too deep")
    for op, av in subpattern:
        if in_repeat and op == re._parser.GROUPREF:
            raise re.error("Backreference inside repeat is not allowed")
        if op in _REPEAT_OPS:
            min_rep, max_rep, inner = av
            if min_rep > MAX_REPEAT_BOUND or (
                max_rep != re._parser.MAXREPEAT and max_rep > MAX_REPEAT_BOUND
            ):
                raise re.error("Channel pattern repetition bound is too large")
            repeating = max_rep == re._parser.MAXREPEAT or max_rep > 1
            if repeating and _contains_unbounded_repeat(inner):
                if not _has_mandatory_fixed_delimiter(inner):
                    raise re.error("Nested repeating quantifier is not allowed")
            _validate_safe_regex_ast(inner, depth + 1, in_repeat=in_repeat or repeating)
        elif op == re._parser.SUBPATTERN:
            _validate_safe_regex_ast(av[3], depth + 1, in_repeat=in_repeat)
        elif op == re._parser.BRANCH:
            for branch in av[1]:
                _validate_safe_regex_ast(branch, depth + 1, in_repeat=in_repeat)


@lru_cache(maxsize=256)
def compile_pattern(pattern: str):
    normalized = python_pattern(pattern)
    if not isinstance(normalized, str) or not (1 <= len(normalized) <= MAX_PATTERN_LENGTH):
        raise re.error("Invalid channel pattern length")
    clean = "".join(_untaint_char(ch) for ch in normalized)
    parsed = re._parser.parse(clean)
    _validate_safe_regex_ast(parsed)
    return re._compiler.compile(parsed)


def channel_pattern(channel):
    return (
        channel.get("include_pattern")
        if isinstance(channel, dict)
        else getattr(channel, "include_pattern", None)
    )


@dataclass(frozen=True)
class TagOrder:
    family: str
    value: tuple


def _is_compact_hash(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{7,64}", value, re.I)) and not value.isdecimal()


def tag_order(tag: str, channel=None, *, image_variants: bool = True) -> TagOrder | None:
    from .versioning import normalize_version, parsed_version

    pattern = channel_pattern(channel)
    if pattern:
        try:
            regex = compile_pattern(pattern)
        except re.error:
            return None
        if "version" in regex.groupindex:
            match = regex.fullmatch(tag)
            if not match:
                return None
            version = match.groupdict().get("version") or ""
            revision = match.groupdict().get("revision") or "0"
            # Explicit numeric fields have no implicit major/minor/patch semantics.
            if not re.fullmatch(r"\d+(?:\.\d+)*", version) or not re.fullmatch(r"\d+", revision):
                return None
            parts = [int(p) for p in version.split(".")]
            while len(parts) > 1 and parts[-1] == 0:
                parts.pop()
            return TagOrder("extracted", (tuple(parts), int(revision)))
    normalized = normalize_version(tag)
    # A compact commit ID can accidentally parse as a PEP-440 alpha/beta version.
    if image_variants and _is_compact_hash(normalized):
        return None
    parsed = parsed_version(tag)
    if parsed is not None:
        family = "integer" if re.fullmatch(r"\d+", normalized) else "version"
        suffix = re.fullmatch(r"\d+(?:\.\d+)+-([A-Za-z][A-Za-z0-9.-]*)(?:\+.*)?", normalized)
        if (
            image_variants
            and suffix
            and not re.match(
                r"^(?:alpha|beta|rc|dev|pre|preview|nightly|canary)(?:[0-9.-]|$)", suffix[1], re.I
            )
        ):
            # Image variants are separate families, never ordered by their names.
            family = "variant:" + suffix[1].lower()
        return TagOrder(family, parsed.key)
    # Natural ordering only within a fixed text skeleton. Mixed hash tokens (a1b2)
    # deliberately have no key: digits inside an identity must never rank releases.
    tokens = re.split(r"([-._/])", normalized)
    if not tokens or any(
        not (t.isdecimal() or re.fullmatch(r"[A-Za-z]+|[-._/]", t)) for t in tokens
    ):
        return None
    numbers = tuple(int(t) for t in tokens if t.isdecimal())
    if not numbers:
        return None
    skeleton = "".join("#" if t.isdecimal() else t for t in tokens)
    # Platform variants of numeric versions need a channel rule; never compare JDKs.
    if tokens[0].isdecimal() and any(re.fullmatch(r"[A-Za-z]+", t) for t in tokens):
        return None
    return TagOrder("natural:" + skeleton, numbers)


def release_tag_order(release, channel=None):
    value = (
        (release.chart_version or release.tag_name)
        if release.tracker_type == "helm"
        else (release.tag_name if release.tracker_type == "container" else release.version)
    )
    return tag_order(value, channel, image_variants=release.tracker_type == "container")


def ranked_candidates(releases, channel=None):
    keyed = [(r, release_tag_order(r, channel)) for r in releases]
    keyed = [(r, k) for r, k in keyed if k is not None]
    # An integer may be a floating major alias or a commit ID. It cannot outrank
    # semantic versions in a mixed repository. Integer-only channels remain usable.
    if any(k.family == "version" for _, k in keyed):
        keyed = [(r, k) for r, k in keyed if k.family != "integer"]
    return keyed


def ordering_issue(releases, channel=None):
    keyed = ranked_candidates(releases, channel)
    if not keyed:
        return "unorderable_version"
    if len({k.family for _, k in keyed}) > 1:
        return "incomparable_versions"
    return None


def ordering_basis(releases, channel=None):
    """Describe the channel's ordering semantics, never a user-selected reference."""
    from .versioning import pinned_alias

    if channel is not None and pinned_alias(channel):
        return "pinned"
    pattern = channel_pattern(channel)
    if pattern:
        try:
            if "version" in compile_pattern(pattern).groupindex:
                return "extracted"
        except re.error:
            return None
    families = {key.family for _, key in ranked_candidates(releases, channel)}
    return (
        "version" if "version" in families else next(iter(families)) if len(families) == 1 else None
    )


def ordering_family_conflicts(releases, channel=None):
    """Show family counts and bounded examples when no common order exists."""
    groups = {}
    for release, key in sorted(
        ranked_candidates(releases, channel), key=lambda pair: pair[0].tag_name
    ):
        group = groups.setdefault(key.family, {"family": key.family, "count": 0, "examples": []})
        group["count"] += 1
        if len(group["examples"]) < 3:
            group["examples"].append(release.tag_name)
    return [groups[family] for family in sorted(groups)] if len(groups) > 1 else []


def ordering_diagnostics(releases, channel=None):
    """Report the tags relevant to the actual failure, without changing selection."""
    from .versioning import pinned_alias

    issue = ordering_issue(releases, channel)
    if (channel is not None and pinned_alias(channel)) or issue is None:
        return []
    if issue == "incomparable_versions":
        ranked = ranked_candidates(releases, channel)
        reference_family = ordering_basis(releases, channel)
        if reference_family is None:
            return []
        return [
            {
                "tag": release.tag_name,
                "reason": "incomparable_family",
                "family": key.family,
                "fields": {},
            }
            for release, key in sorted(ranked, key=lambda pair: pair[0].tag_name)
            if key.family != reference_family
        ]
    rows = []
    pattern = channel_pattern(channel)
    for release in sorted(releases, key=lambda r: r.tag_name):
        fields = {}
        reason = (
            "ambiguous_hash"
            if release.tracker_type == "container" and _is_compact_hash(release.tag_name)
            else "no_ordering_key"
        )
        if pattern:
            try:
                regex = compile_pattern(pattern)
                if "version" in regex.groupindex:
                    match = regex.fullmatch(release.tag_name)
                    if not match:
                        reason = "pattern_not_full_match"
                    else:
                        fields = {
                            name: match.groupdict().get(name)
                            for name in ("version", "revision")
                            if name in regex.groupindex
                        }
                        version = fields.get("version") or ""
                        revision = fields.get("revision") or "0"
                        if not re.fullmatch(r"\d+(?:\.\d+)*", version):
                            reason = "invalid_numeric_version"
                        elif not re.fullmatch(r"\d+", revision):
                            reason = "invalid_numeric_revision"
            except re.error:
                reason = "invalid_pattern"
        rows.append(
            {
                "tag": release.tag_name,
                "reason": reason,
                "family": None,
                "fields": fields,
            }
        )
    return rows


def history_order_key(release, channel=None):
    key = release_tag_order(release, channel)
    # Family order only groups audit rows; it is never used to choose a target.
    return (1, key.family, key.value) if key else (0, "", ())
