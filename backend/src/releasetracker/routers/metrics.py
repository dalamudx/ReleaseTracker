"""Opt-in scrape endpoint with a dedicated read-only bearer credential."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..services.metrics_credential import get_digest, matches
from ..services.operational_metrics import render_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise HTTPException(503, "Storage unavailable")
    digest = await get_digest(storage)
    if not digest:
        raise HTTPException(404, "Not found")
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not matches(digest, supplied.strip()):
        raise HTTPException(
            401, "Invalid metrics credential", headers={"WWW-Authenticate": "Bearer"}
        )
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
