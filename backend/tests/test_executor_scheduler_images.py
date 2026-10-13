"""Image repository/tag selection through the scheduler's public run API."""

from unittest.mock import AsyncMock
import pytest

from helpers.executor_adapters import MutableFakeRuntimeAdapter
from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)
from releasetracker.config import ExecutorConfig, RuntimeConnectionConfig
from releasetracker.executor_scheduler import ExecutorScheduler

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("selection_mode", "current_image", "tracker_image", "registry", "tag", "expected_image"),
    [
        pytest.param(
            "replace_tag_on_current_image",
            "docker.io/library/nginx:1.27.2",
            "nginx",
            "registry-1.docker.io",
            "1.27.3",
            "docker.io/library/nginx:1.27.3",
            id="keep-current-repository",
        ),
        pytest.param(
            "replace_tag_on_current_image",
            "localhost:5000/myapp@sha256:abc",
            "myapp",
            "registry-1.docker.io",
            "2.0.0",
            "localhost:5000/myapp:2.0.0",
            id="replace-digest-with-tag",
        ),
        pytest.param(
            "replace_tag_on_current_image",
            "myorg/myapp",
            "myorg/myapp",
            "registry-1.docker.io",
            "3.0.0",
            "myorg/myapp:3.0.0",
            id="add-missing-tag",
        ),
        pytest.param(
            "use_tracker_image_and_tag",
            "docker.io/library/old:1.0",
            "ghcr.io/acme/target",
            "registry-1.docker.io",
            "5.0.0",
            "ghcr.io/acme/target:5.0.0",
            id="qualified-tracker-image",
        ),
        pytest.param(
            "use_tracker_image_and_tag",
            "docker.io/library/old:1.0",
            "acme/target",
            "ghcr.io",
            "5.0.0",
            "ghcr.io/acme/target:5.0.0",
            id="join-tracker-registry",
        ),
        pytest.param(
            "use_tracker_image_and_tag",
            "docker.io/library/old:1.0",
            "acme/target",
            "https://ghcr.io/",
            "5.0.0",
            "ghcr.io/acme/target:5.0.0",
            id="normalize-registry-url",
        ),
        pytest.param(
            "use_tracker_image_and_tag",
            "docker.io/library/nginx:alpine",
            "library/nginx",
            "registry-1.docker.io",
            "1.30.0-trixie",
            "docker.io/library/nginx:1.30.0-trixie",
            id="docker-hub-official-image",
        ),
        pytest.param(
            "use_tracker_image_and_tag",
            "docker.io/library/nginx:alpine",
            "library/nginx",
            "docker.io",
            "1.30.0-trixie",
            "docker.io/library/nginx:1.30.0-trixie",
            id="docker-hub-alias",
        ),
    ],
)
async def test_scheduler_image_selection(
    storage, selection_mode, current_image, tracker_image, registry, tag, expected_image
):
    runtime_id = await create_runtime_connection(storage, name="prod-docker")
    await save_docker_tracker_config(storage, name="target", image=tracker_image, registry=registry)
    await seed_docker_release(storage, tracker_name="target", version=tag)
    tracker = await storage.get_aggregate_tracker("target")
    assert tracker is not None
    source = next(
        source for source in tracker.sources if source.source_type == "container" and source.enabled
    )
    assert source.id is not None
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="image-selection",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="target",
            tracker_source_id=source.id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            image_selection_mode=selection_mode,
            image_reference_mode="tag",
            target_ref={"mode": "container", "container_name": "app"},
        )
    )
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    adapter = MutableFakeRuntimeAdapter(
        runtime, current_image=current_image, include_runtime_type_in_snapshot=True
    )
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[executor_id] = adapter
    # Formatting is independent of version discovery and channel policy.
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=(tag, None))
    try:
        outcome = await scheduler.run_executor_now(executor_id)
        assert outcome.status == "success", outcome.message
        assert adapter.update_calls == [expected_image]
    finally:
        await scheduler.shutdown()
