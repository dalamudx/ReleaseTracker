from __future__ import annotations

import logging

from .config import TrackerConfig
from .models import Credential
from .trackers import DockerTracker, GiteaTracker, GitHubTracker, GitLabTracker, HelmTracker
from .trackers.base import BaseTracker

logger = logging.getLogger(__name__)


def _credential_secret_string(credential: Credential, key: str) -> str:
    value = credential.secrets.get(key)
    return value.strip() if isinstance(value, str) else ""


def _container_registry_auth_token(credential: Credential) -> str:
    username = _credential_secret_string(credential, "username")
    password = _credential_secret_string(credential, "password") or credential.token.strip()
    if username and password:
        return f"{username}:{password}"
    return credential.token


class ReleaseSchedulerTrackerFactory:
    """Construct source-specific release tracker clients from persisted config."""

    async def _create_tracker(self, config: TrackerConfig) -> BaseTracker:
        """Create a tracker instance"""
        credential = None
        token = None
        if config.credential_name:
            credential = await self.storage.get_credential_by_name(config.credential_name)
            if credential:
                token = credential.token
            else:
                logger.warning(
                    f"Credential '{config.credential_name}' referenced by tracker {config.name} not found, using anonymous access"
                )

        legacy_filter = {}

        if config.type == "github":
            return GitHubTracker(
                name=config.name,
                repo=config.repo or "",
                token=token,
                fetch_mode=config.github_fetch_mode,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "gitlab":
            return GitLabTracker(
                name=config.name,
                project=config.project or "",
                instance=config.instance or "https://gitlab.com",
                token=token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "gitea":
            return GiteaTracker(
                name=config.name,
                repo=config.repo or "",
                instance=config.instance or "https://gitea.com",
                token=token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "helm":
            return HelmTracker(
                name=config.name,
                repo=config.repo or "",
                chart=config.chart or "",
                token=token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "container":
            allow_registry_redirects = await self.storage.get_oci_registry_redirects_enabled()
            return DockerTracker(
                name=config.name,
                image=config.image or "",
                registry=config.registry,
                token=_container_registry_auth_token(credential) if credential else token,
                published_at_mode=config.published_at_mode,
                allow_registry_redirects=allow_registry_redirects,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        else:
            raise ValueError(f"Unsupported tracker type: {config.type}")
