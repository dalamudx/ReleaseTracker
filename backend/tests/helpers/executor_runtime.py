from datetime import datetime, timezone
from typing import Any, Literal

from releasetracker.config import Channel, RuntimeConnectionConfig, TrackerConfig
from releasetracker.models import AggregateTracker, Credential, Release, ReleaseChannel


async def create_runtime_connection(
    storage,
    *,
    name: str = "docker-prod",
    runtime_type: Literal["docker", "podman", "kubernetes"] = "docker",
    description: str = "runtime",
) -> int:
    config = {"socket": "unix:///var/run/docker.sock"}
    secrets = {"token": "runtime-secret"}
    if runtime_type == "kubernetes":
        config = {"namespace": "apps", "in_cluster": True}
        secrets = {}

    return await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name=name,
            type=runtime_type,
            enabled=True,
            config=config,
            secrets=secrets,
            description=description,
        )
    )


async def create_portainer_runtime_connection(
    storage,
    *,
    name: str = "portainer-prod",
    description: str = "portainer runtime",
) -> int:
    credential_id = await storage.create_credential(
        Credential(
            name=f"{name}-credential",
            type="portainer_runtime",
            secrets={"api_key": "portainer-api-key"},
        )
    )
    return await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name=name,
            type="portainer",
            enabled=True,
            config={"base_url": "https://portainer.example", "endpoint_id": 2},
            credential_id=credential_id,
            secrets={},
            description=description,
        )
    )


def _channel_payload(channel: Any) -> dict[str, Any]:
    if isinstance(channel, dict):
        return dict(channel)
    if hasattr(channel, "model_dump"):
        return channel.model_dump()
    return {
        "name": channel.name,
        "type": channel.type,
        "include_pattern": getattr(channel, "include_pattern", None),
        "exclude_pattern": getattr(channel, "exclude_pattern", None),
        "enabled": getattr(channel, "enabled", True),
    }


def make_docker_tracker_config(
    *,
    name: str,
    image: str,
    registry: str = "registry-1.docker.io",
    channels: list[Any] | None = None,
    enabled: bool = True,
    **overrides,
) -> TrackerConfig:
    channel_payloads = (
        [_channel_payload(channel) for channel in channels]
        if channels is not None
        else [Channel(name="stable", enabled=True, type="release").model_dump()]
    )
    return TrackerConfig(
        name=name,
        type="container",
        enabled=enabled,
        image=image,
        registry=registry,
        channels=channel_payloads,
        **overrides,
    )


async def save_docker_tracker_config(
    storage,
    *,
    name: str,
    image: str,
    registry: str = "registry-1.docker.io",
    channels: list[Any] | None = None,
    enabled: bool = True,
    **overrides,
) -> None:
    tracker_config = make_docker_tracker_config(
        name=name,
        image=image,
        registry=registry,
        channels=channels,
        enabled=enabled,
        **overrides,
    )
    await storage.save_tracker_config(tracker_config)

    aggregate_tracker = await storage.get_aggregate_tracker(name)
    if aggregate_tracker is None or not aggregate_tracker.sources:
        return

    runtime_source = aggregate_tracker.sources[0]
    if runtime_source.release_channels:
        return

    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=aggregate_tracker.id,
            name=aggregate_tracker.name,
            enabled=aggregate_tracker.enabled,
            primary_changelog_source_key=aggregate_tracker.primary_changelog_source_key,
            created_at=aggregate_tracker.created_at,
            changelog_policy=aggregate_tracker.changelog_policy,
            description=aggregate_tracker.description,
            sources=[
                (
                    source.model_copy(
                        update={
                            "release_channels": [
                                ReleaseChannel(
                                    release_channel_key=f"{source.source_key}-{channel.name}",
                                    name=channel.name,
                                    type=channel.type,
                                    enabled=channel.enabled,
                                    include_pattern=channel.include_pattern,
                                    exclude_pattern=channel.exclude_pattern,
                                )
                                for channel in tracker_config.channels
                            ]
                        }
                    )
                    if source.id == runtime_source.id
                    else source
                )
                for source in aggregate_tracker.sources
            ],
        )
    )


async def seed_docker_release(
    storage,
    *,
    tracker_name: str,
    version: str,
    prerelease: bool = False,
    published_at: datetime | None = None,
) -> None:
    release = Release(
        tracker_name=tracker_name,
        tracker_type="container",
        name=version,
        tag_name=version,
        version=version,
        published_at=published_at or datetime(2026, 3, 24, tzinfo=timezone.utc),
        url=f"https://example.com/{version}",
        prerelease=prerelease,
    )
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None

    runtime_source = next(
        (
            source
            for source in aggregate_tracker.sources
            if source.source_type == "container" and source.enabled
        ),
        None,
    )
    if runtime_source is None and hasattr(storage, "_select_runtime_source"):
        runtime_source = storage._select_runtime_source(aggregate_tracker)
    assert runtime_source is not None

    await storage.save_source_observations(aggregate_tracker.id, runtime_source, [release])
