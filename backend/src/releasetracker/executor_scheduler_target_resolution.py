from __future__ import annotations

import re
from typing import Any

from .config import EXECUTOR_BINDABLE_SOURCE_TYPES, ExecutorConfig
from .models import Release, TrackerSource
from .storage.sqlite import SQLiteStorage

_DOCKER_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


async def _resolve_tracker_binding_by_source_id_from_storage(
    storage: SQLiteStorage, tracker_source_id: int
) -> tuple[str, TrackerSource] | None:
    binding = await storage.get_executor_binding(tracker_source_id)
    if binding is None:
        return None
    aggregate_tracker, tracker_source = binding
    if tracker_source.source_type in EXECUTOR_BINDABLE_SOURCE_TYPES and tracker_source.enabled:
        return aggregate_tracker.name, tracker_source
    return None


async def _resolve_tracker_binding_from_storage(
    storage: SQLiteStorage, executor_config: ExecutorConfig
) -> tuple[str, TrackerSource] | None:
    if executor_config.tracker_source_id is None:
        return None
    return await _resolve_tracker_binding_by_source_id_from_storage(
        storage,
        executor_config.tracker_source_id,
    )


async def _load_bound_releases_from_storage(
    storage: SQLiteStorage,
    tracker_name: str,
    *,
    tracker_source_id: int | None,
    tracker_source_type: str | None,
) -> list[Release]:
    if tracker_source_id is not None:
        observations = await storage.get_source_release_observations_by_source(tracker_source_id)
        if observations:
            return [
                Release(
                    tracker_name=tracker_name,
                    tracker_type=tracker_source_type or "container",
                    name=observation.name,
                    tag_name=observation.tag_name,
                    version=observation.version,
                    app_version=observation.app_version,
                    chart_version=observation.chart_version,
                    published_at=observation.published_at,
                    url=observation.url,
                    prerelease=observation.prerelease,
                    body=observation.body,
                    commit_sha=observation.commit_sha,
                )
                for observation in observations
            ]

    # Older installations can have an executor/source binding before source
    # observations were backfilled. Preserve its aggregate projection behavior
    # until a source-specific observation becomes available.
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None or aggregate_tracker.id is None:
        return []

    releases = await storage.get_tracker_current_releases(aggregate_tracker.id)
    for release in releases:
        release.tracker_name = tracker_name
    return releases


async def _resolve_tracker_latest_version_from_storage(
    storage: SQLiteStorage,
    tracker_name: str,
    channel_name: str | None,
    *,
    tracker_source_id: int | None = None,
    tracker_source_type: str | None = None,
) -> str | None:
    target = await _resolve_tracker_latest_target_from_storage(
        storage,
        tracker_name,
        channel_name,
        tracker_source_id=tracker_source_id,
        tracker_source_type=tracker_source_type,
    )
    if target is None:
        return None
    version, _ = target
    return version


async def _resolve_tracker_latest_target_from_storage(
    storage: SQLiteStorage,
    tracker_name: str,
    channel_name: str | None,
    *,
    tracker_source_id: int | None = None,
    tracker_source_type: str | None = None,
) -> tuple[str, str | None] | None:
    releases = await _load_bound_releases_from_storage(
        storage,
        tracker_name,
        tracker_source_id=tracker_source_id,
        tracker_source_type=tracker_source_type,
    )
    if not releases:
        return None

    tracker_config = await storage.get_tracker_config(tracker_name)
    sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"

    scoped_channels = tracker_config.channels if tracker_config else []
    if tracker_source_id is not None:
        bound_source = await storage.get_tracker_source(tracker_source_id)
        if bound_source is not None and bound_source.release_channels:
            scoped_channels = bound_source.release_channels

    bound_channels = scoped_channels
    if channel_name:
        bound_channels = [ch for ch in scoped_channels if ch.name == channel_name and ch.enabled]
        if not bound_channels:
            return None

    best_release = storage.select_best_release(releases, bound_channels, sort_mode=sort_mode)
    if best_release is None:
        return None

    if tracker_source_type == "container":
        same_version_releases = [
            release for release in releases if release.version == best_release.version
        ]
        if bound_channels:
            same_version_releases = [
                release
                for release in same_version_releases
                if any(
                    SQLiteStorage._release_matches_channel(release, channel)
                    for channel in bound_channels
                )
            ]

        digest_candidates = [
            release
            for release in same_version_releases
            if _normalize_docker_digest(release.commit_sha) is not None
        ]
        if digest_candidates:
            best_release = max(
                digest_candidates,
                key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
            )

    return best_release.version, _normalize_docker_digest(best_release.commit_sha)


def _normalize_docker_digest(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if _DOCKER_DIGEST_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def _target_identity_key(target_version: str, target_digest: str | None) -> str:
    return f"{target_version}@{_normalize_docker_digest(target_digest) or 'no_digest'}"


def _replace_image_tag_value(image: str, target_version: str) -> str:
    return _build_image_target_value(image, target_version=target_version, target_digest=None)


def _build_image_target_value(
    image: str,
    *,
    target_version: str,
    target_digest: str | None,
) -> str:
    if "@" in image:
        image = image.split("@", 1)[0]
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        image = image[:last_colon]

    if target_digest is not None:
        return f"{image}@{target_digest}"
    return f"{image}:{target_version}"


def _normalize_image_registry_value(registry: str) -> str:
    normalized = registry.strip().rstrip("/")
    if normalized.startswith("https://"):
        normalized = normalized[len("https://") :]
    elif normalized.startswith("http://"):
        normalized = normalized[len("http://") :]
    return normalized.rstrip("/")


def _image_has_explicit_registry(image: str) -> bool:
    first_component = image.split("/", 1)[0]
    return "." in first_component or ":" in first_component or first_component == "localhost"


def _build_tracker_image_base(source_config: dict[str, Any]) -> str:
    base_image = source_config.get("image")
    if not isinstance(base_image, str) or not base_image.strip():
        raise ValueError("tracker source image is required for tracker image selection mode")

    image = base_image.strip().strip("/")
    registry = source_config.get("registry")
    if not isinstance(registry, str) or not registry.strip():
        return image

    normalized_registry = _normalize_image_registry_value(registry)
    if not normalized_registry or _image_has_explicit_registry(image):
        return image
    if normalized_registry in {"registry-1.docker.io", "docker.io"}:
        return f"docker.io/{image}"
    return f"{normalized_registry}/{image}"


def _build_target_image_value(
    *,
    current_image: str,
    target_version: str,
    target_digest: str | None,
    executor_config: ExecutorConfig,
    tracker_source,
    tracker_source_type: str | None,
) -> str:
    resolved_digest = (
        _normalize_docker_digest(target_digest)
        if tracker_source_type == "container" and executor_config.image_reference_mode == "digest"
        else None
    )

    if executor_config.image_selection_mode == "use_tracker_image_and_tag":
        source_config = getattr(tracker_source, "source_config", {}) or {}
        base_image = _build_tracker_image_base(source_config)
        return _build_image_target_value(
            base_image,
            target_version=target_version,
            target_digest=resolved_digest,
        )

    if executor_config.image_selection_mode == "replace_tag_on_current_image":
        return _build_image_target_value(
            current_image,
            target_version=target_version,
            target_digest=resolved_digest,
        )

    raise ValueError(f"unsupported image selection mode: {executor_config.image_selection_mode}")


class ExecutorSchedulerTargetResolution:
    """Resolve tracker releases and render runtime image targets."""

    async def _resolve_tracker_latest_version(
        self,
        tracker_name: str,
        channel_name: str | None,
        *,
        tracker_source_id: int | None = None,
        tracker_source_type: str | None = None,
    ) -> str | None:
        return await _resolve_tracker_latest_version_from_storage(
            self.storage,
            tracker_name,
            channel_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )

    async def _resolve_tracker_latest_target(
        self,
        tracker_name: str,
        channel_name: str | None,
        *,
        tracker_source_id: int | None = None,
        tracker_source_type: str | None = None,
    ) -> tuple[str, str | None] | None:
        return await _resolve_tracker_latest_target_from_storage(
            self.storage,
            tracker_name,
            channel_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )

    async def _resolve_tracker_latest_chart_version(
        self,
        tracker_name: str,
        channel_name: str | None,
        *,
        tracker_source_id: int | None,
        tracker_source_type: str | None,
    ) -> str | None:
        releases = await self._load_bound_releases(
            tracker_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )
        if not releases:
            return None

        tracker_config = await self.storage.get_tracker_config(tracker_name)
        sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"
        scoped_channels = tracker_config.channels if tracker_config else []
        if tracker_source_id is not None:
            bound_source = await self.storage.get_tracker_source(tracker_source_id)
            if bound_source is not None and bound_source.release_channels:
                scoped_channels = bound_source.release_channels

        bound_channels = scoped_channels
        if channel_name and scoped_channels:
            bound_channels = [
                ch for ch in scoped_channels if ch.name == channel_name and ch.enabled
            ]
            if not bound_channels:
                return None

        if bound_channels:
            channel_winners = self.storage.select_best_releases_by_channel(
                releases,
                bound_channels,
                sort_mode=sort_mode,
                channel_source_type=tracker_source_type,
            )
            if not channel_winners:
                return None
            best_release = max(
                channel_winners.values(),
                key=lambda release: self.storage._release_order_key(release, sort_mode),
            )
        else:
            best_release = self.storage.select_best_release(
                releases,
                bound_channels,
                sort_mode=sort_mode,
            )
        if best_release is None:
            return None
        chart_version = best_release.chart_version or best_release.tag_name
        return (
            chart_version.strip()
            if isinstance(chart_version, str) and chart_version.strip()
            else None
        )

    def _build_target_image(
        self,
        *,
        current_image: str,
        target_version: str,
        target_digest: str | None,
        executor_config: ExecutorConfig,
        tracker_source,
        tracker_source_type: str | None,
    ) -> str:
        return _build_target_image_value(
            current_image=current_image,
            target_version=target_version,
            target_digest=target_digest,
            executor_config=executor_config,
            tracker_source=tracker_source,
            tracker_source_type=tracker_source_type,
        )

    async def _resolve_tracker_binding(
        self, executor_config: ExecutorConfig
    ) -> tuple[str, TrackerSource] | None:
        return await _resolve_tracker_binding_from_storage(self.storage, executor_config)

    async def _resolve_tracker_binding_by_source_id(
        self, tracker_source_id: int
    ) -> tuple[str, TrackerSource] | None:
        return await _resolve_tracker_binding_by_source_id_from_storage(
            self.storage,
            tracker_source_id,
        )

    async def _load_bound_releases(
        self,
        tracker_name: str,
        *,
        tracker_source_id: int | None,
        tracker_source_type: str | None,
    ) -> list:
        return await _load_bound_releases_from_storage(
            self.storage,
            tracker_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )

    @staticmethod
    def _replace_image_tag(image: str, target_version: str) -> str:
        return _replace_image_tag_value(image, target_version)
