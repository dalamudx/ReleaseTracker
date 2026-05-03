from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_retired_active_authority_paths_remain_retired() -> None:
    scheduler_text = _read("backend/src/releasetracker/scheduler.py")
    executors_router_text = _read("backend/src/releasetracker/routers/executors.py")
    executor_scheduler_text = _read("backend/src/releasetracker/executor_scheduler.py")
    trackers_router_text = _read("backend/src/releasetracker/routers/trackers.py")
    frontend_client_text = _read("frontend/src/api/client.ts")
    aggregate_helpers_text = _read("backend/src/releasetracker/storage/sqlite_aggregate_trackers.py")

    # Scheduler must stay aggregate-only for live checks.
    assert "aggregate_tracker = self._require_aggregate_tracker_for_live_check(" in scheduler_text
    assert "result = await self._process_aggregate_tracker_check(" in scheduler_text
    assert "await self._process_tracker_check(" not in scheduler_text

    # Legacy scheduler helper can exist for passive/backward code organization,
    # but it must not become a live execution path again.
    assert scheduler_text.count("_process_tracker_check(") == 1

    # Executor API/scheduler binding must not infer from tracker name.
    assert "if tracker_source_id_value is None:" in executors_router_text
    assert "必须显式指定 tracker_source_id" in executors_router_text
    assert "if executor_config.tracker_source_id is None:" in executor_scheduler_text
    assert "return None" in executor_scheduler_text

    # Active tracker API shims must not reintroduce legacy fields.
    assert "primary_changelog_channel_key" not in trackers_router_text
    assert "tracker_channels" not in trackers_router_text

    # Frontend runtime contract normalization must stay redesign-first.
    assert "function normalizeTracker(" not in frontend_client_text
    assert "function resolveTrackerChannels(" not in frontend_client_text
    assert "function normalizeTrackerChangelogPolicy(" not in frontend_client_text

    # Source-channel fallback must not regress to top-level runtime channels.
    assert "runtime_config.channels" not in aggregate_helpers_text
    assert "tracker.channels" not in aggregate_helpers_text
