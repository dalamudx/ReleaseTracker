"""Tracker base module"""

from abc import ABC, abstractmethod

from ..models import Release
import logging

logger = logging.getLogger(__name__)


class BaseTracker(ABC):
    """Tracker abstract base class"""

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.config = kwargs
        self.timeout = kwargs.get("timeout", 15)

    @abstractmethod
    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        """Fetch latest release"""
        pass

    @abstractmethod
    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        """Fetch all releases with an optional limit."""
        pass

    def _should_include(self, release: Release) -> bool:
        """Determine whether this release should be included based on filter rules"""
        # Prefer channel configuration
        channels_data = self.config.get("channels", [])
        if channels_data:
            for ch in channels_data:
                # Support Pydantic objects and dictionaries
                if hasattr(ch, "enabled") and not ch.enabled:
                    continue
                if isinstance(ch, dict) and not ch.get("enabled", True):
                    continue
                if self.should_include_in_channel(release, ch):
                    return True
            return False

        filter_config = self.config.get("filter", {})

        if release.tracker_type != "container" and not filter_config.get("include_prerelease", False):
            if release.prerelease:
                return False

            version_lower = release.version.lower()
            prerelease_keywords = ["alpha", "beta", "rc", "pre", "dev", "snapshot"]

            for keyword in prerelease_keywords:
                if keyword in version_lower:
                    return False

        # Step 2: include pattern filtering (include_pattern)
        # If an include pattern is defined, the version must match to pass
        include_pattern = filter_config.get("include_pattern")
        if include_pattern:
            import re

            try:
                # Use search matching to allow partial matches
                if not re.search(include_pattern, release.tag_name):
                    return False
            except re.error as e:
                # On regex errors, log and skip this rule
                logger.error(f"Invalid include_pattern regex: {include_pattern}, error: {e}")
                pass

        # Step 3: exclude pattern filtering (exclude_pattern)
        # If an exclude pattern is defined, matching versions are excluded; takes precedence over include
        exclude_pattern = filter_config.get("exclude_pattern")
        if exclude_pattern:
            import re

            try:
                # Exclude immediately if the exclude pattern matches
                if any(
                    re.search(exclude_pattern, candidate)
                    for candidate in self._exclude_match_candidates(release)
                ):
                    return False
            except re.error as e:
                # On regex errors, log and skip this rule
                logger.error(f"Invalid exclude_pattern regex: {exclude_pattern}, error: {e}")
                pass

        return True

    def filter_by_channels(self, releases: list[Release]) -> dict[str, list[Release]]:
        """
        Filter releases by channel

        Args:
            releases: all available releases

        Returns:
            Dictionary keyed by channel identifier (name or type), with filtered releases as values
        """
        from ..config import Channel

        channels = self.config.get("channels", [])
        result = {}

        for channel in channels:
            if isinstance(channel, dict):
                channel = Channel(**channel)

            if not channel.enabled:
                continue

            filtered = []
            for release in releases:
                if self.should_include_in_channel(release, channel):
                    filtered.append(release)

            # Use channel name or type as the key
            channel_key = channel.name or channel.type
            result[channel_key] = filtered

        return result

    def should_include_in_channel(self, release: Release, channel) -> bool:
        """
        Return whether the release belongs to the channel.

        Args:
            release: Release metadata.
            channel: Channel config object or dict.

        Returns:
            True when the release belongs to the channel, otherwise False.
        """
        from ..config import Channel
        import re

        if isinstance(channel, dict):
            channel = Channel(**channel)

        if channel.type is not None and self._supports_release_type_filter(release):
            if channel.type == "release":
                if release.prerelease:
                    return False
            elif channel.type == "prerelease":
                if not release.prerelease:
                    return False

        if channel.include_pattern:
            try:
                if not re.search(channel.include_pattern, release.tag_name):
                    return False
            except re.error as e:
                logger.error(
                    f"Invalid include_pattern regex for channel '{channel.name}': {channel.include_pattern}, error: {e}"
                )
                pass

        if channel.exclude_pattern:
            try:
                if any(
                    re.search(channel.exclude_pattern, candidate)
                    for candidate in self._exclude_match_candidates(release)
                ):
                    return False
            except re.error as e:
                logger.error(
                    f"Invalid exclude_pattern regex for channel '{channel.name}': {channel.exclude_pattern}, error: {e}"
                )
                pass

        return True

    @staticmethod
    def _supports_release_type_filter(release: Release) -> bool:
        return release.tracker_type in {"github", "gitlab", "gitea"}

    @staticmethod
    def _exclude_match_candidates(release: Release) -> list[str]:
        return [release.tag_name]
