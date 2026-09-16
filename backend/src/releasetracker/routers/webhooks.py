"""Unified webhook management and public repository event receiver."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError

from ..dependencies import get_current_admin_user, get_storage
from ..services.repository_webhooks import (
    RepositoryWebhookInput,
    delivery_key,
    event_reason,
    normalize_event,
    repository_matches,
    verify_signature,
)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
MAX_BODY_BYTES = 1024 * 1024


class SlidingWindowRateLimiter:
    def __init__(self, limit: int = 120, window: float = 60):
        self.limit = limit
        self.window = window
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


receiver_rate_limiter = SlidingWindowRateLimiter()


async def public_config(row, request: Request, storage):
    config = dict(row["config"])
    base_url = await storage.get_system_base_url()
    endpoint_url = (
        f"{base_url}/api/webhooks/repository/{row['id']}"
        if base_url
        else str(request.url_for("receive_repository_webhook", hook_id=row["id"]))
    )
    return {
        "id": row["id"],
        "tracker_source_id": row["tracker_source_id"],
        "tracker_name": row.get("tracker_name"),
        "source_key": row.get("source_key"),
        "provider": row["provider"],
        "enabled": bool(row["enabled"]),
        "auth_mode": row["auth_mode"],
        "secret_configured": bool(row["secret"]),
        "endpoint_url": endpoint_url,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        **config,
    }


@router.get("/repositories", dependencies=[Depends(get_current_admin_user)])
async def list_repository_webhooks(request: Request, storage=Depends(get_storage)):
    return [await public_config(row, request, storage) for row in await storage.webhooks.list()]


@router.post(
    "/repositories",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin_user)],
)
async def create_repository_webhook(request: Request, storage=Depends(get_storage)):
    try:
        data = RepositoryWebhookInput.model_validate(await request.json())
        row = await storage.webhooks.save(data)
        return await public_config(row, request, storage)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.put("/repositories/{hook_id}", dependencies=[Depends(get_current_admin_user)])
async def update_repository_webhook(hook_id: str, request: Request, storage=Depends(get_storage)):
    try:
        data = RepositoryWebhookInput.model_validate(await request.json())
        row = await storage.webhooks.save(data, hook_id)
        return await public_config(row, request, storage)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(code, detail=str(exc)) from exc


@router.delete(
    "/repositories/{hook_id}", status_code=204, dependencies=[Depends(get_current_admin_user)]
)
async def delete_repository_webhook(hook_id: str, storage=Depends(get_storage)):
    if not await storage.webhooks.get(hook_id):
        raise HTTPException(404, detail="Webhook not found")
    await storage.webhooks.delete(hook_id)
    return Response(status_code=204)


@router.get("/repositories/{hook_id}/deliveries", dependencies=[Depends(get_current_admin_user)])
async def list_repository_webhook_deliveries(hook_id: str, storage=Depends(get_storage)):
    if not await storage.webhooks.get(hook_id):
        raise HTTPException(404, detail="Webhook not found")
    return await storage.webhooks.deliveries(hook_id)


@router.post("/repository/{hook_id}", name="receive_repository_webhook")
async def receive_repository_webhook(hook_id: str, request: Request, storage=Depends(get_storage)):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        raise HTTPException(413, detail="Webhook payload is too large")
    body_buffer = bytearray()
    async for chunk in request.stream():
        body_buffer.extend(chunk)
        if len(body_buffer) > MAX_BODY_BYTES:
            raise HTTPException(413, detail="Webhook payload is too large")
    body = bytes(body_buffer)
    hook = await storage.webhooks.get(hook_id)
    # Use the same response for missing and disabled hooks to avoid endpoint enumeration.
    if not hook or not hook["enabled"]:
        raise HTTPException(404, detail="Webhook not found")
    if not receiver_rate_limiter.allow(hook_id):
        raise HTTPException(
            429, detail="Webhook rate limit exceeded", headers={"Retry-After": "60"}
        )
    try:
        secret = storage._decrypt(hook["secret"])
        verify_signature(hook["provider"], hook["auth_mode"], secret or "", request.headers, body)
    except ValueError as exc:
        raise HTTPException(401, detail="Invalid webhook signature") from exc
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, detail="Invalid JSON payload") from exc

    event = normalize_event(hook["provider"], request.headers, payload)
    context = await storage.webhooks.source(hook["tracker_source_id"])
    if not context or not repository_matches(context[0], hook["provider"], event.repository):
        raise HTTPException(403, detail="Repository does not match this webhook")
    reason = event_reason(event, hook["config"])
    try:
        result = await storage.webhooks.receive(
            hook,
            delivery_key(hook["provider"], request.headers, body),
            hashlib.sha256(body).hexdigest(),
            event,
            reason,
        )
    except OverflowError as exc:
        raise HTTPException(429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except ValueError as exc:
        if "conflict" in str(exc).lower():
            raise HTTPException(409, detail=str(exc)) from exc
        raise HTTPException(503, detail="Webhook configuration changed") from exc
    return Response(
        content=json.dumps(result),
        status_code=200 if result["state"] in {"duplicate", "ignored"} else 202,
        media_type="application/json",
    )
