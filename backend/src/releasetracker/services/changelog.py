"""Custom changelog fetching and extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from ..models import Release, TrackerReleaseNotesConfig, TrackerSource

SUPPORTED_CHANGELOG_SOURCE_TYPES = {"github", "gitlab", "gitea"}
_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:[-+][0-9A-Za-z.-]+)?$"
)
_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class ChangelogExtractionResult:
    body: str
    path: str


def _parse_version_parts(release: Release) -> dict[str, str]:
    tag = release.tag_name.strip()
    version = release.version.strip() or tag.lstrip("v")
    candidate = version or tag
    match = _VERSION_RE.match(candidate) or _VERSION_RE.match(tag)
    major = match.group("major") if match else ""
    minor = match.group("minor") if match else ""
    patch = match.group("patch") if match and match.group("patch") is not None else ""
    return {
        "tag": tag,
        "version": version,
        "major": major,
        "minor": minor,
        "patch": patch,
    }


def render_changelog_template(template: str, release: Release) -> str:
    parts = _parse_version_parts(release)
    try:
        return template.format(**parts)
    except KeyError as exc:
        raise ValueError(f"Unsupported changelog placeholder: {{{exc.args[0]}}}") from exc


def _strip_version_prefix(value: str) -> str:
    return value[1:] if value.startswith("v") else value


def _normalize_heading_title(value: str) -> str:
    title = value.strip()
    title = re.sub(r"^\[(?P<version>[^\]]+)\].*$", r"\g<version>", title)
    title = re.split(r"\s+-\s+|\s+\(|\s+–\s+", title, maxsplit=1)[0]
    return title.strip().strip("[]")


def _heading_matches(title: str, release: Release, template: str | None) -> bool:
    if template:
        expected = render_changelog_template(template, release).strip()
        expected_match = _HEADING_RE.match(expected)
        expected_title = expected_match.group("title") if expected_match else expected
        # Normalize both sides: strip markdown link syntax such as [text](url) or [text]
        # so that templates like "# [{tag}]" and headings like "# [v1.2.3](url)" both
        # reduce to the bare version token before comparison.
        return _normalize_heading_title(title) == _normalize_heading_title(expected_title)

    normalized_title = _normalize_heading_title(title)
    candidates = {
        release.tag_name.strip(),
        release.version.strip(),
        _strip_version_prefix(release.tag_name.strip()),
        f"v{release.version.strip()}" if release.version.strip() else "",
    }
    candidates.discard("")
    return normalized_title in candidates or title.strip() in candidates


def _heading_level(line: str) -> int | None:
    match = _HEADING_RE.match(line)
    return len(match.group("marks")) if match else None


def _extract_section(content: str, release: Release, config: TrackerReleaseNotesConfig) -> str:
    lines = content.splitlines()
    start_index: int | None = None
    start_level: int | None = None

    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        if _heading_matches(match.group("title"), release, config.version_heading_template):
            start_index = index
            start_level = len(match.group("marks"))
            break

    if start_index is None or start_level is None:
        raise ValueError(f"No changelog section matched {release.tag_name or release.version}")

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        level = _heading_level(lines[index])
        if level is not None and level <= start_level:
            end_index = index
            break

    section_lines = lines[start_index:end_index]
    if config.extraction_mode == "version_section_from_subheading":
        prefix = (config.subheading_prefix or "").strip().lower()
        subheading_index = None
        for index, line in enumerate(section_lines):
            match = _HEADING_RE.match(line)
            if match and match.group("title").strip().lower().startswith(prefix):
                subheading_index = index
                break
        if subheading_index is None:
            raise ValueError(f"No changelog subheading matched {config.subheading_prefix}")
        section_lines = section_lines[subheading_index:]

    extracted = "\n".join(section_lines).strip()
    if not extracted:
        raise ValueError(
            f"Matched changelog section for {release.tag_name or release.version} is empty"
        )
    return extracted


def extract_changelog_content(
    content: str,
    release: Release,
    config: TrackerReleaseNotesConfig,
) -> str:
    if config.extraction_mode == "whole_file":
        extracted = content.strip()
        if not extracted:
            raise ValueError("Changelog file is empty")
        return extracted
    return _extract_section(content, release, config)


class RepositoryChangelogFetcher:
    def __init__(self, *, token: str | None = None, timeout: int = 15):
        self.token = token
        self.timeout = timeout

    async def fetch_file(
        self,
        source: TrackerSource,
        path: str,
        ref_strategy: str,
        release: Release,
        configured_ref: str | None,
    ) -> str:
        if source.source_type not in SUPPORTED_CHANGELOG_SOURCE_TYPES:
            raise ValueError("Custom changelog requires a GitHub, GitLab, or Gitea source")

        ref = self._resolve_ref(ref_strategy, release, configured_ref)
        if source.source_type == "github":
            return await self._fetch_github(source, path, ref)
        if source.source_type == "gitlab":
            return await self._fetch_gitlab(source, path, ref)
        if source.source_type == "gitea":
            return await self._fetch_gitea(source, path, ref)
        raise ValueError(f"Unsupported changelog source type: {source.source_type}")

    @staticmethod
    def _resolve_ref(ref_strategy: str, release: Release, configured_ref: str | None) -> str | None:
        if ref_strategy == "release_tag":
            return release.tag_name
        if ref_strategy == "configured_ref":
            return configured_ref
        return None

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _gitlab_headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token} if self.token else {}

    def _gitea_headers(self) -> dict[str, str]:
        headers = {"Accept": "text/plain"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    async def _fetch_github(self, source: TrackerSource, path: str, ref: str | None) -> str:
        repo = str(source.source_config.get("repo") or "").strip()
        if not repo:
            raise ValueError("GitHub repository is required for custom changelog")
        url = f"https://api.github.com/repos/{repo}/contents/{quote(path, safe='/')}"
        params = {"ref": ref} if ref else None
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self._github_headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text

    async def _fetch_gitlab(self, source: TrackerSource, path: str, ref: str | None) -> str:
        project = str(source.source_config.get("project") or "").strip()
        if not project:
            raise ValueError("GitLab project is required for custom changelog")
        instance = str(source.source_config.get("instance") or "https://gitlab.com").rstrip("/")
        project_id = quote(project, safe="")
        encoded_path = quote(path, safe="")
        url = f"{instance}/api/v4/projects/{project_id}/repository/files/{encoded_path}/raw"
        params = {"ref": ref or "HEAD"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self._gitlab_headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text

    async def _fetch_gitea(self, source: TrackerSource, path: str, ref: str | None) -> str:
        repo = str(source.source_config.get("repo") or "").strip()
        if not repo:
            raise ValueError("Gitea repository is required for custom changelog")
        instance = str(source.source_config.get("instance") or "https://gitea.com").rstrip("/")
        url = f"{instance}/api/v1/repos/{repo}/raw/{quote(path, safe='/')}"
        params = {"ref": ref} if ref else None
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self._gitea_headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text


async def fetch_and_extract_changelog(
    *,
    source: TrackerSource,
    release: Release,
    config: TrackerReleaseNotesConfig,
    token: str | None = None,
    timeout: int = 15,
) -> ChangelogExtractionResult:
    path = render_changelog_template(config.path_template, release)
    fetcher = RepositoryChangelogFetcher(token=token, timeout=timeout)
    content = await fetcher.fetch_file(source, path, config.ref_strategy, release, config.ref)
    body = extract_changelog_content(content, release, config)
    return ChangelogExtractionResult(body=body, path=path)
