"""Unit tests for core data models."""

from datetime import datetime

import pytest

from releasetracker.models import ExecutorSnapshot


class TestExecutorSnapshot:
    def test_defaults(self):
        snapshot = ExecutorSnapshot(executor_id=1)
        assert snapshot.id is None
        assert snapshot.executor_id == 1
        assert snapshot.snapshot_data == {}
        assert snapshot.trigger == "pre_update"
        assert snapshot.image_at_capture is None
        assert snapshot.executor_run_id is None
        assert snapshot.unredacted_persisted is False
        assert isinstance(snapshot.created_at, datetime)
        assert isinstance(snapshot.updated_at, datetime)

    def test_full_payload_round_trip(self):
        now = datetime(2026, 5, 8, 12, 0, 0)
        snapshot = ExecutorSnapshot(
            id=42,
            executor_id=7,
            snapshot_data={"runtime_type": "docker", "image": "nginx:1.25"},
            trigger="pre_rollback",
            image_at_capture="nginx:1.25",
            executor_run_id=1001,
            unredacted_persisted=True,
            created_at=now,
            updated_at=now,
        )
        dumped = snapshot.model_dump()
        rebuilt = ExecutorSnapshot(**dumped)
        assert rebuilt == snapshot

    @pytest.mark.parametrize("trigger", ["pre_update", "manual", "pre_rollback"])
    def test_accepts_all_trigger_literals(self, trigger: str):
        snapshot = ExecutorSnapshot(executor_id=1, trigger=trigger)  # type: ignore[arg-type]
        assert snapshot.trigger == trigger

    def test_rejects_unknown_trigger(self):
        with pytest.raises(ValueError):
            ExecutorSnapshot(executor_id=1, trigger="invalid")  # type: ignore[arg-type]

    def test_image_at_capture_accepts_none(self):
        snapshot = ExecutorSnapshot(executor_id=1, image_at_capture=None)
        assert snapshot.image_at_capture is None
