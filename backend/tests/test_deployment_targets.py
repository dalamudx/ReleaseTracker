from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import deployment_targets
from releasetracker.executor_scheduler_target_resolution import ResolvedTrackerTarget


@pytest.mark.asyncio
async def test_manual_chart_targets_resolve_release_once(monkeypatch):
    executor = SimpleNamespace(id=1)
    source = SimpleNamespace(id=2, source_type="helm")
    monkeypatch.setattr(
        deployment_targets,
        "_binding_contexts",
        lambda _: [SimpleNamespace(tracker_source_id=2, channel_name="stable")],
    )
    monkeypatch.setattr(
        deployment_targets,
        "_resolve_tracker_binding_by_source_id_from_storage",
        AsyncMock(return_value=("app", source)),
    )
    resolve = AsyncMock(
        return_value=ResolvedTrackerTarget(
            display_version="2.0.0",
            deploy_alias="0.8.0",
            digest=None,
            chart_version="0.8.0",
            chart_digest="sha256:" + "a" * 64,
        )
    )
    monkeypatch.setattr(
        deployment_targets, "_resolve_tracker_latest_target_details_from_storage", resolve
    )
    targets = await deployment_targets.resolve_deployment_targets(object(), executor, manual=True)
    assert targets == [
        {
            "tracker_name": "app",
            "source_id": 2,
            "channel": "stable",
            "target": ["0.8.0", None],
            "chart_version": "0.8.0",
            "chart_digest": "sha256:" + "a" * 64,
        }
    ]
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_automatic_targets_do_not_resolve_again(monkeypatch):
    storage = SimpleNamespace(
        get_executor_desired_state=AsyncMock(
            return_value=SimpleNamespace(
                desired_state_revision="pinned",
                desired_target={
                    "binding_targets": [
                        {
                            "tracker_name": "app",
                            "tracker_source_id": 2,
                            "channel_name": "stable",
                            "deploy_alias": "0.8.0",
                            "digest": None,
                            "chart_version": "0.8.0",
                            "chart_digest": "sha256:" + "a" * 64,
                        }
                    ]
                },
            )
        )
    )
    resolve = AsyncMock(side_effect=AssertionError("Pinned target must not be re-resolved"))
    monkeypatch.setattr(
        deployment_targets, "_resolve_tracker_latest_target_details_from_storage", resolve
    )
    targets = await deployment_targets.resolve_deployment_targets(
        storage, SimpleNamespace(id=1), manual=False, desired_revision="pinned"
    )
    assert targets[0]["chart_version"] == "0.8.0"
    resolve.assert_not_awaited()
    with pytest.raises(ValueError, match="revision changed"):
        await deployment_targets.resolve_deployment_targets(
            storage, SimpleNamespace(id=1), manual=False, desired_revision="outdated"
        )


@pytest.mark.asyncio
async def test_disabled_or_missing_source_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(
        deployment_targets,
        "_binding_contexts",
        lambda _: [SimpleNamespace(tracker_source_id=2, channel_name="stable")],
    )
    monkeypatch.setattr(
        deployment_targets,
        "_resolve_tracker_binding_by_source_id_from_storage",
        AsyncMock(return_value=None),
    )
    with pytest.raises(ValueError, match="source is unavailable"):
        await deployment_targets.resolve_deployment_targets(
            object(), SimpleNamespace(id=1), manual=True
        )
