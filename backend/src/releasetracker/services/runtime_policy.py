"""Bounded, retry-safe policy helpers for runtime control planes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

DEFAULT_RUNTIME_READ_TIMEOUT_SECONDS = 20
DEFAULT_RUNTIME_WRITE_TIMEOUT_SECONDS = 90
DEFAULT_RUNTIME_READ_RETRIES = 1
MAX_RUNTIME_TIMEOUT_SECONDS = 600
MAX_RUNTIME_READ_RETRIES = 3

T = TypeVar("T")


@dataclass(frozen=True)
class RuntimeOperationPolicy:
    read_timeout_seconds: int = DEFAULT_RUNTIME_READ_TIMEOUT_SECONDS
    write_timeout_seconds: int = DEFAULT_RUNTIME_WRITE_TIMEOUT_SECONDS
    read_retries: int = DEFAULT_RUNTIME_READ_RETRIES


def runtime_operation_policy(runtime_connection: Any) -> RuntimeOperationPolicy:
    config = getattr(runtime_connection, "config", {})
    raw = config.get("operation_policy", {}) if isinstance(config, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return RuntimeOperationPolicy(
        read_timeout_seconds=_bounded_int(
            raw.get("read_timeout_seconds"), DEFAULT_RUNTIME_READ_TIMEOUT_SECONDS
        ),
        write_timeout_seconds=_bounded_int(
            raw.get("write_timeout_seconds"), DEFAULT_RUNTIME_WRITE_TIMEOUT_SECONDS
        ),
        read_retries=_bounded_int(
            raw.get("read_retries"),
            DEFAULT_RUNTIME_READ_RETRIES,
            minimum=0,
            maximum=MAX_RUNTIME_READ_RETRIES,
        ),
    )


def _bounded_int(
    value: Any,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = MAX_RUNTIME_TIMEOUT_SECONDS,
) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum
        else default
    )


def is_transient_runtime_error(exc: Exception) -> bool:
    """Classify only transport-style errors; authentication and validation never retry."""
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, asyncio.TimeoutError)):
        return True
    name = exc.__class__.__name__.lower()
    return any(token in name for token in ("timeout", "connection", "temporar", "unavailable"))


async def run_read_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RuntimeOperationPolicy,
    operation_name: str,
) -> T:
    """Retry idempotent reads only. Mutations must call their SDK/API exactly once."""
    for attempt in range(policy.read_retries + 1):
        try:
            return await asyncio.wait_for(operation(), timeout=policy.read_timeout_seconds)
        except Exception as exc:
            if attempt >= policy.read_retries or not is_transient_runtime_error(exc):
                raise
            delay_seconds = min(1.0, 0.1 * (2**attempt))
            logger.warning(
                "runtime_read_retry operation=%s attempt=%s/%s cause=%s",
                operation_name,
                attempt + 1,
                policy.read_retries,
                exc.__class__.__name__,
            )
            await asyncio.sleep(delay_seconds)
    raise AssertionError("unreachable")
