import json
from unittest.mock import AsyncMock

import pytest

from releasetracker.notifiers.templates import (
    EVENTS,
    TemplateRenderError,
    builtin,
    context_for,
    render_notification,
    render_many,
    sample_payload,
    validate_template,
)
from releasetracker.notifiers import template_store
from releasetracker.notifiers.factory import build_notifier

pytestmark = pytest.mark.asyncio


async def test_builtin_validates_every_event_in_both_languages():
    await validate_template(builtin())
    for locale in ("zh", "en"):
        for event in EVENTS:
            result = await render_notification(event, sample_payload(event), locale)
            assert result["title"] and result["content"]
            assert result["locale"] == locale
            assert result["fallback_error"] is None


@pytest.mark.parametrize(
    "body",
    [
        "{{ subject.__class__ }}",
        "{% include '/etc/passwd' %}",
        "{{ cycler.__init__.__globals__ }}",
        "{{ 'x' * 100000000 }}",
        "{{ missing.field }}",
        "{% for x in services %}{% for y in services %}{{ missing }}{% endfor %}{% endfor %}",
    ],
)
async def test_unsafe_or_invalid_templates_rejected(body):
    template = builtin() | {"body": body}
    with pytest.raises(TemplateRenderError):
        await validate_template(template)


async def test_loops_macros_translations_and_fallback():
    template = builtin() | {
        "body": "{% macro line(s) %}{{ s.name }}{% endmacro %}{% for s in services %}{{ line(s) }}{% endfor %} {{ labels.custom }}",
        "translations": {"zh": {"custom": "自定义"}, "en": {"custom": "Custom"}},
    }
    await validate_template(template)
    result = await render_notification(
        "executor_run_success", sample_payload("executor_run_success"), "zh", template
    )
    assert "自定义" in result["body"] and "service-1" in result["body"]
    fallback = await render_notification("test", {}, "zh", builtin() | {"body": "{{ missing }}"})
    assert fallback["fallback_error"] and "通知测试" in fallback["title"]


async def test_guardrails_and_byte_budget():
    template = builtin() | {"body": "好" * 4000}
    result = await render_notification(
        "executor_run_failed",
        sample_payload("executor_run_failed", "timeout"),
        "zh",
        template,
        "wecom",
    )
    assert len(result["content"].encode()) <= 4096
    assert "未确认就绪" in result["body"] and "不会自动回滚" in result["body"]
    context = context_for("new_release", sample_payload("new_release", "container"), "zh")
    assert not context["release"]["prerelease"]
    absent = await render_notification(
        "executor_run_success", sample_payload("executor_run_success", "no_healthcheck"), "en"
    )
    assert "No native Healthcheck" in absent["body"]


async def test_output_limit_and_no_secret_context():
    context = context_for(
        "error", {"message": "password=fixture-secret", "token": "fixture-secret"}, "en"
    )
    assert "fixture-secret" not in json.dumps(context)
    with pytest.raises(TemplateRenderError):
        await render_many(
            builtin() | {"body": "{% for x in services %}" + "x" * 4000 + "{% endfor %}"},
            [context | {"services": list(range(12))}],
        )


async def test_resource_exhaustion_is_bounded_and_does_not_block_event_loop():
    import asyncio
    import time

    context = context_for("test", {}, "en") | {"services": list(range(12))}
    body = "{% for x in services %}" * 8 + "{% endfor %}" * 8
    task = asyncio.create_task(render_many(builtin() | {"body": body}, [context]))
    ticks = 0
    started = time.monotonic()
    while not task.done():
        await asyncio.sleep(0.02)
        ticks += 1
    with pytest.raises(TemplateRenderError):
        await task
    assert ticks > 3 and time.monotonic() - started < 6
    assert (await render_notification("test", {}, "en"))["fallback_error"] is None


async def test_template_storage_revision_and_reference_protection(storage):
    template = await template_store.save_template(storage, builtin() | {"name": "Example template"})
    notifier = await storage.create_notifier(
        {
            "name": "Example channel",
            "url": "https://example.test/hook",
            "template_id": template["id"],
        }
    )
    assert notifier.template_id == template["id"]
    with pytest.raises(ValueError, match="in_use"):
        await template_store.delete_template(storage, template["id"])
    updated = await template_store.save_template(
        storage, template | {"body": "{{ subject.name }}"}, template["id"]
    )
    assert updated["revision"] == 2
    with pytest.raises(ValueError, match="conflict"):
        await template_store.save_template(storage, template, template["id"])
    await storage.update_notifier(notifier.id, {"template_id": None})
    await template_store.delete_template(storage, template["id"])


async def test_factory_sends_custom_bilingual_content_without_changing_event():
    for channel in ("wecom", "webhook"):
        notifier = build_notifier(
            notifier_type=channel,
            name="Example",
            url="https://example.test/hook",
            events=["new_release"],
            language="en",
            template=builtin() | {"body": "Custom {{ release.version }}"},
        )
        notifier.send_payload = AsyncMock(return_value=True)
        assert await notifier.notify("new_release", sample_payload("new_release"))
        sent = notifier.send_payload.call_args.args[0]
        assert "Custom 1.3.0" in json.dumps(sent)
        assert "查看详情" not in json.dumps(sent, ensure_ascii=False)
        if channel == "webhook":
            assert sent["event"] == "new_release" and sent["version"] == "1.3.0"
