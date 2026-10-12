"""Explicit upstream times. Observation times are never publication fallbacks."""

from datetime import datetime, timezone

TIME_FIELDS = (
    "artifact_created_at",
    "registry_pushed_at",
    "registry_created_at",
    "registry_modified_at",
    "source_created_at",
)


def parse_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def time_payload(release):
    return {
        key: value.isoformat()
        for key in TIME_FIELDS
        if (value := parse_time(getattr(release, key, None))) is not None
    }


def time_fields(payload):
    return {key: parse_time(payload.get(key)) for key in TIME_FIELDS}
