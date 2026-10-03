"""Ordered application cleanup that cannot be abandoned by caller cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
import logging

from ..executors.adapter_lifetime import wait_for_runtime_worker

logger = logging.getLogger(__name__)


async def shutdown_services(steps: Iterable[tuple[str, Callable[[], Awaitable[None]]]]) -> None:
    # Do not let cancellation reach a service's shutdown halfway through its own
    # worker drain. Wait for the complete chain before propagating cancellation.
    cleanup = asyncio.create_task(_shutdown_services(tuple(steps)))
    failure = await wait_for_runtime_worker(cleanup)
    if failure is not None:
        raise failure


async def _shutdown_services(steps) -> BaseException | None:
    failure: BaseException | None = None
    for name, close in steps:
        try:
            await close()
        except (Exception, asyncio.CancelledError) as exc:
            # Remote failures may contain tokens, URLs or credential material.
            logger.error(
                "service_shutdown_failed service=%s error_type=%s", name, type(exc).__name__
            )
            if failure is None or isinstance(exc, asyncio.CancelledError):
                failure = exc.with_traceback(None)
    return failure
