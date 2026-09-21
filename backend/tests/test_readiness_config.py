import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from releasetracker.config import HealthCheckProfile, READINESS_DEFAULTS, READINESS_BOUNDS
from releasetracker.routers.settings import _normalize_setting_value, get_settings
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_readiness_defaults_independent_of_strategy():
    profile = HealthCheckProfile(strategy="none")
    assert profile.readiness_enabled
    assert profile.use_system_readiness_defaults
    assert not profile.notify_result
    for field, value in READINESS_DEFAULTS.items():
        assert getattr(profile, field) == value


def test_legacy_health_profiles_migrate_to_one_readiness_switch():
    disabled = HealthCheckProfile.model_validate(
        {
            "strategy": "none",
            "use_default_strategy": False,
            "failure_policy": "mark_failed",
            "grace_period_seconds": 0,
            "attempt_timeout_seconds": 0,
            "interval_seconds": 0,
            "probe_window_seconds": 0,
            "services": None,
            "http": None,
            "tcp": None,
        }
    )
    enabled = HealthCheckProfile.model_validate(
        {
            "strategy": "runtime_native",
            "use_default_strategy": False,
            "failure_policy": "mark_failed",
            "grace_period_seconds": 15,
            "attempt_timeout_seconds": 10,
            "interval_seconds": 5,
            "probe_window_seconds": 180,
        }
    )
    assert not disabled.readiness_enabled
    assert enabled.readiness_enabled


def test_disabled_readiness_clears_a_retained_legacy_strategy():
    profile = HealthCheckProfile(
        readiness_enabled=False,
        strategy="runtime_native",
        use_default_strategy=True,
        notify_result=True,
        attempt_timeout_seconds=10,
        interval_seconds=5,
        probe_window_seconds=30,
    )
    assert profile.strategy == "none"
    assert not profile.use_default_strategy
    assert profile.notify_result


@pytest.mark.parametrize("enabled", [True, False])
def test_readiness_roundtrip(enabled):
    profile = HealthCheckProfile(
        strategy="none",
        readiness_enabled=enabled,
        notify_result=enabled,
        use_system_readiness_defaults=enabled,
        readiness_timeout_seconds=120,
        readiness_interval_seconds=2,
        readiness_attempt_timeout_seconds=3,
        readiness_stable_seconds=0,
    )
    assert HealthCheckProfile.model_validate_json(profile.model_dump_json()) == profile


@pytest.mark.parametrize("field", list(READINESS_BOUNDS))
def test_readiness_bounds_and_setting_validation(field):
    lower, upper = READINESS_BOUNDS[field]
    for value in [lower, upper]:
        assert getattr(HealthCheckProfile(**{field: value}), field) == value
        assert _normalize_setting_value(f"system.{field}", str(value)) == str(value)
    for value in [lower - 1, upper + 1, 1.5, True, "5"]:
        with pytest.raises(ValidationError):
            HealthCheckProfile(**{field: value})
    for value in [str(lower - 1), str(upper + 1), "1.5", "true", ""]:
        with pytest.raises(HTTPException):
            _normalize_setting_value(f"system.{field}", value)


@pytest.mark.asyncio
async def test_unseeded_settings_return_readiness_defaults():
    storage = SimpleNamespace(get_all_settings_with_updated_at=AsyncMock(return_value={}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(storage=storage)))
    result = {item.key: item.value for item in await get_settings(request, None)}
    for field, value in READINESS_DEFAULTS.items():
        assert result[f"system.{field}"] == str(value)
