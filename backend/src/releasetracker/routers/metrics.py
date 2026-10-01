"""Opt-in scrape endpoint with a dedicated read-only bearer credential."""

import asyncio
import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..services.operational_metrics import render_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    token = os.environ.get("RELEASETRACKER_METRICS_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(404, "Not found")
    supplied = request.headers.get("authorization", "")
    if not secrets.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        raise HTTPException(
            401, "Invalid metrics credential", headers={"WWW-Authenticate": "Bearer"}
        )
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise HTTPException(503, "Storage unavailable")
    # Bound expensive scrapes; the request connection is closed by middleware.
    try:
        async with asyncio.timeout(5):
            output = await render_metrics(
                storage, getattr(request.app.state, "instance_backup", None)
            )
    except TimeoutError as exc:
        raise HTTPException(503, "Metrics collection timed out") from exc
    return Response(
        output, media_type="text/plain; version=0.0.4", headers={"Cache-Control": "no-store"}
    )
