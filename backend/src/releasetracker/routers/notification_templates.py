"""Admin-only template editing and side-effect-free synthetic preview."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from ..dependencies import get_current_admin_user
from ..notifiers import template_store
from ..notifiers.templates import (
    EVENTS,
    TemplateInput,
    TemplateRenderError,
    builtin,
    render_notification,
    sample_payload,
    validate_template,
)
from .notifiers import get_storage

router = APIRouter(
    prefix="/api/notification-templates",
    tags=["notification-templates"],
    dependencies=[Depends(get_current_admin_user)],
)


class PreviewInput(TemplateInput):
    event: str = Field(default="new_release", max_length=60)
    language: Literal["zh", "en"] = "en"
    channel: Literal["wecom", "webhook"] = "wecom"
    scenario: Literal["normal", "timeout", "no_healthcheck", "unchecked", "many", "container"] = (
        "normal"
    )


@router.get("")
async def list_all(request: Request):
    return {
        "items": await template_store.list_templates(get_storage(request)),
        "builtin": builtin(),
        "events": EVENTS,
    }


@router.post("/preview")
async def preview(data: PreviewInput):
    if data.event not in EVENTS:
        raise HTTPException(422, detail="unsupported_notification_event")
    try:
        return await render_notification(
            data.event,
            sample_payload(data.event, data.scenario),
            data.language,
            data.model_dump(),
            data.channel,
            strict=True,
        )
    except TemplateRenderError as exc:
        raise HTTPException(422, detail=str(exc)) from None


async def save(request, data, template_id=None):
    try:
        await validate_template(data.model_dump())
        return await template_store.save_template(
            get_storage(request), data.model_dump(), template_id
        )
    except TemplateRenderError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from None


@router.post("", status_code=201)
async def create(request: Request, data: TemplateInput):
    return await save(request, data)


@router.put("/{template_id}")
async def update(request: Request, template_id: int, data: TemplateInput):
    return await save(request, data, template_id)


@router.delete("/{template_id}")
async def delete(request: Request, template_id: int):
    try:
        await template_store.delete_template(get_storage(request), template_id)
        return {"deleted": True}
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from None
