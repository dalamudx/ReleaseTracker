"""Versioned, bilingual notification templates with bounded process isolation."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)
EVENTS = (
    "new_release",
    "republish",
    "executor_run_success",
    "executor_run_failed",
    "executor_run_skipped",
    "executor_health_check_result",
    "error",
    "test",
)
TITLES = {
    "zh": (
        "发现新版本",
        "版本重新发布",
        "部署完成",
        "部署未完成",
        "已跳过部署",
        "服务核验结果",
        "任务处理异常",
        "通知测试",
    ),
    "en": (
        "New release detected",
        "Release republished",
        "Deployment completed",
        "Deployment incomplete",
        "Deployment skipped",
        "Readiness check result",
        "Task processing error",
        "Test notification",
    ),
}
WORDS = {
    "zh": {
        "version": "版本",
        "source": "来源",
        "channel": "渠道",
        "published": "发布时间",
        "service_changes": "服务变更",
        "verification": "服务核验",
        "view_details": "查看详情",
        "runtime": "运行时",
        "result": "结果",
        "from_version": "原版本",
        "to_version": "目标版本",
        "notes": "发布说明",
        "test_message": "这是一条来自 ReleaseTracker 的测试通知。",
        "unknown": "未知",
        "healthy": "核验通过",
        "unhealthy": "核验未通过",
        "pending": "等待就绪",
        "timeout": "在规定时间内未确认就绪",
        "error": "探测错误",
        "unsupported": "不支持核验",
        "superseded": "已被后续变更取代",
        "skipped": "已跳过",
        "success": "成功",
        "failed": "失败",
        "runtime_native": "原生核验",
        "runtime_state": "容器运行状态",
        "kubernetes_rollout": "负载滚动更新",
        "helm_status": "Helm 状态",
        "http": "HTTP",
        "tcp": "TCP",
        "elapsed": "核验耗时",
        "seconds": "秒",
        "not_checked": "未执行健康检查",
        "scope": "仅验证运行时就绪，未验证业务健康。",
        "absent": "未配置原生 Healthcheck，仅验证容器运行状态。",
        "attention": "请查看执行记录确认远端状态；不会自动回滚或重新部署。",
        "recheck": "只读重新核验",
        "prerelease": "预发布",
        "more": "其余服务请查看执行记录",
    },
    "en": {
        "version": "Version",
        "source": "Source",
        "channel": "Channel",
        "published": "Published",
        "service_changes": "Service changes",
        "verification": "Readiness check",
        "view_details": "View details",
        "runtime": "Runtime",
        "result": "Result",
        "from_version": "From version",
        "to_version": "Target version",
        "notes": "Release notes",
        "test_message": "This is a test notification from ReleaseTracker.",
        "unknown": "Unknown",
        "healthy": "Verification passed",
        "unhealthy": "Verification failed",
        "pending": "Waiting for readiness",
        "timeout": "Readiness not confirmed before the deadline",
        "error": "Probe error",
        "unsupported": "Verification unsupported",
        "superseded": "Superseded by a later change",
        "skipped": "Skipped",
        "success": "Success",
        "failed": "Failed",
        "runtime_native": "Native verification",
        "runtime_state": "Container state",
        "kubernetes_rollout": "Workload rollout",
        "helm_status": "Helm status",
        "http": "HTTP",
        "tcp": "TCP",
        "elapsed": "Verification duration",
        "seconds": "s",
        "not_checked": "Health check not performed",
        "scope": "Runtime readiness only; business health has not been verified.",
        "absent": "No native Healthcheck configured; only container running state verified.",
        "attention": "Check the execution record and remote state. No automatic rollback or redeployment.",
        "recheck": "Read-only readiness recheck",
        "prerelease": "Pre-release",
        "more": "See the execution record for remaining services",
    },
}
DEFAULT_TITLE = "{{ labels.events[event] }} · {{ subject.name }}"
DEFAULT_BODY = """{% if release %}
{{ labels.version }}: {{ release.version }}{% if release.prerelease %} ({{ labels.prerelease }}){% endif %}

{{ labels.source }}: {{ release.source }}
{% if release.channel %}{{ labels.channel }}: {{ release.channel }}
{% endif %}{{ labels.published }}: {{ release.published_at }}
{% if release.digest %}Digest: {{ release.digest }}
{% endif %}{% if release.notes %}
{{ labels.notes }}
{{ release.notes }}
{% endif %}{% endif %}
{% if runtime %}{{ labels.runtime }}: {{ runtime }}
{% endif %}{% if services %}
{{ labels.service_changes }}
{% for service in services %}
• {{ service.name }}{% if service.from_display or service.to_display %}: {{ service.from_display }} → {{ service.to_display }}{% endif %}

{% if service.check_label %}  {{ service.check_label }} ({{ service.method_label }})
{% endif %}{% endfor %}
{% if service_count > services|length %}{{ labels.more }} ({{ service_count }})
{% endif %}{% elif from_version or to_version %}
{{ labels.from_version }}: {{ from_version }}
{{ labels.to_version }}: {{ to_version }}
{% endif %}{% if health %}
{{ labels.verification }}: {{ health.outcome_label }}
{% if health.elapsed_seconds is not none %}{{ labels.elapsed }}: {{ health.elapsed_seconds }} {{ labels.seconds }}
{% endif %}{% endif %}
{% if reason %}{{ reason }}
{% endif %}{% if timestamp %}{{ timestamp }}
{% endif %}{% if category == 'test' %}{{ labels.test_message }}{% endif %}
"""


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    title: str = Field(default=DEFAULT_TITLE, min_length=1, max_length=2048)
    body: str = Field(default=DEFAULT_BODY, min_length=1, max_length=12000)
    translations: dict[str, dict[str, str]] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("name_required")
        return value.strip()

    @field_validator("translations")
    @classmethod
    def valid_translations(cls, value):
        if set(value) - {"zh", "en"}:
            raise ValueError("unsupported_language")
        for entries in value.values():
            if len(entries) > 80 or any(len(k) > 80 or len(v) > 500 for k, v in entries.items()):
                raise ValueError("translations_too_large")
        return value


def builtin():
    return {"id": None, **TemplateInput(name="ReleaseTracker").model_dump()}


class TemplateRenderError(ValueError):
    pass


_slots = asyncio.Semaphore(2)


async def render_many(template: dict, contexts: list[dict]) -> list[dict]:
    request = json.dumps(
        {"title": template["title"], "body": template["body"], "contexts": contexts},
        ensure_ascii=False,
    ).encode()
    if len(request) > 512000:
        raise TemplateRenderError("template_input_too_large")
    async with _slots:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).with_name("template_worker.py")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(request), timeout=4)
            try:
                result = json.loads(output)
            except ValueError:
                raise TemplateRenderError("template_resource_limit") from None
            if process.returncode or "error" in result:
                raise TemplateRenderError(
                    f"{result.get('error', 'template_resource_limit')} (line {result.get('line') or '?'})"
                )
            return result["results"]
        except TimeoutError:
            raise TemplateRenderError("template_timeout") from None
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()


def safe_text(value, limit=500):
    from ..services.executor_notification_outbox import _safe_text

    text = _safe_text(str(value or "")) or ""
    # Prevent payload values from introducing Markdown links or HTML/mentions.
    return text[:limit].replace("<", "‹").replace(">", "›").replace("[", "［").replace("]", "］")


def safe_url(value):
    try:
        parsed = urlsplit(str(value or ""))
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and len(str(value).encode("utf-8")) <= 1024
            and not any(character.isspace() or character in '<>"' for character in str(value))
        ):
            return str(value).replace("(", "%28").replace(")", "%29").replace("\n", "")
    except ValueError:
        pass
    return None


def context_for(event, payload, language, template=None):
    locale = language if language in WORDS else "en"
    words = WORDS[locale]
    labels = {**words, **(template or {}).get("translations", {}).get(locale, {})}
    labels["events"] = dict(zip(EVENTS, TITLES[locale], strict=True))
    event = event if event in EVENTS else "error"
    category = (
        "release"
        if event in EVENTS[:2]
        else (
            "health"
            if event == "executor_health_check_result"
            else "deployment" if event.startswith("executor_run_") else event
        )
    )
    context = {
        "schema_version": 1,
        "event": event,
        "category": category,
        "locale": locale,
        "labels": labels,
        "subject": {"name": "ReleaseTracker"},
        "result": {"status": "", "label": ""},
        "release": None,
        "services": [],
        "service_count": 0,
        "health": None,
        "runtime": "",
        "from_version": "",
        "to_version": "",
        "links": {"detail": None},
        "safety": [],
    }
    context["reason"] = ""
    context["timestamp"] = ""
    if isinstance(payload, dict):
        context["timestamp"] = safe_text(payload.get("finished_at") or payload.get("timestamp"))
        if category == "error":
            context["reason"] = safe_text(payload.get("error") or payload.get("message"))
    if hasattr(payload, "tracker_name") and hasattr(payload, "version"):
        context["subject"]["name"] = safe_text(payload.tracker_name)
        context["release"] = {
            "version": safe_text(payload.version),
            "source": safe_text(payload.tracker_type),
            "channel": safe_text(payload.channel_name),
            "published_at": payload.published_at.isoformat(),
            "digest": safe_text(payload.artifact_digest),
            "notes": safe_text(payload.body, 1500),
            "prerelease": payload.tracker_type in {"github", "gitlab", "gitea", "forgejo"}
            and payload.prerelease,
        }
        context["links"]["detail"] = safe_url(payload.url)
    elif isinstance(payload, dict):
        from ..services.executor_notification_outbox import sanitize_payload

        safe = sanitize_payload(payload)
        context["subject"]["name"] = safe_text(
            payload.get("executor_name")
            or payload.get("tracker_name")
            or payload.get("name")
            or "ReleaseTracker"
        )
        status = safe.get("status", "unknown")
        context["result"] = {"status": status, "label": words.get(status, words["unknown"])}
        context["runtime"] = safe_text(safe.get("runtime_type"))
        context["from_version"] = safe_text(safe.get("from_version"))
        context["to_version"] = safe_text(safe.get("to_version"))
        health = safe.get("health_check")
        if health:
            context["health"] = {
                **health,
                "outcome_label": words.get(health["outcome"], words["unknown"]),
                "scope_label": (
                    words["absent"] if health.get("native_health_absent") else words["scope"]
                ),
                "elapsed_seconds": health.get("elapsed_seconds", health.get("duration_seconds")),
            }
            context["safety"].append(context["health"]["scope_label"])
        elif category == "deployment":
            context["safety"].append(words["not_checked"])
        changes = safe.get("services") or []
        checks = (health or {}).get("services") or []
        check_map = {row.get("service"): row for row in checks}
        rows = [{**row, **check_map.get(row.get("service"), {})} for row in changes]
        names = {row.get("service") for row in changes}
        rows.extend(row for row in checks if row.get("service") not in names)
        rows = sorted(rows, key=lambda r: r.get("status") in {"healthy", "success", "skipped"})
        context["service_count"] = len(rows)
        for row in rows[:12]:
            context["services"].append(
                {
                    "name": safe_text(row.get("service")),
                    "from_display": safe_text(row.get("from_version")),
                    "to_display": safe_text(row.get("to_version")),
                    "check_label": words.get(row.get("status"), words["unknown"]),
                    "method_label": words.get(row.get("method"), words["unknown"]),
                }
            )
        if category in {"deployment", "health"}:
            if status == "readiness_recovered":
                context["result"]["label"] = words["healthy"]
            elif status == "readiness_recheck_failed":
                context["result"]["label"] = words["unhealthy"]
            context["safety"].insert(0, words["result"] + ": " + context["result"]["label"])
            if health:
                context["safety"].insert(
                    1, words["verification"] + ": " + context["health"]["outcome_label"]
                )
            if status in {"failed", "readiness_recheck_failed"} or (
                health and health["outcome"] != "healthy"
            ):
                context["safety"].append(words["attention"])
            if payload.get("entity") == "executor_health_recheck":
                context["safety"].insert(0, words["recheck"])
    return context


def sample_payload(event, scenario="normal"):
    if event in EVENTS[:2]:
        from ..models import Release

        return Release(
            tracker_name="Sample tracker",
            name="Sample release",
            version="1.3.0",
            tag_name="v1.3.0",
            tracker_type="container" if scenario == "container" else "github",
            prerelease=scenario == "container",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            url="https://example.test/releases/1.3.0",
            body="Sample release notes",
            artifact_digest="sha256:" + "a" * 64,
        )
    if event == "test":
        return {"name": "Sample channel"}
    if event == "error":
        return {"tracker_name": "Sample tracker", "error": "Upstream request failed"}
    outcome = (
        "timeout"
        if scenario == "timeout"
        else "unhealthy" if event == "executor_run_failed" else "healthy"
    )
    payload = {
        "entity": "executor_run",
        "executor_name": "Sample executor",
        "status": (
            "failed"
            if event == "executor_run_failed"
            else "skipped" if event == "executor_run_skipped" else "success"
        ),
        "runtime_type": "ssh",
        "from_version": "1.2.0",
        "to_version": "1.3.0",
    }
    if event != "executor_run_skipped":
        payload["services"] = [
            {
                "service": f"service-{index}",
                "from_version": "registry.example.test/team/service-a:1.2.0",
                "to_version": "registry.example.test/team/service-a:1.3.0",
                "status": payload["status"],
            }
            for index in range(1, 21 if scenario == "many" else 3)
        ]
    if (
        event.startswith("executor_")
        and event != "executor_run_skipped"
        and scenario != "unchecked"
    ):
        payload["health_check"] = {
            "strategy": "runtime_native",
            "performed": True,
            "outcome": outcome,
            "elapsed_seconds": 38,
            "native_health_absent": scenario == "no_healthcheck",
            "services": [
                {
                    "service": f"service-{i}",
                    "status": outcome,
                    "method": "runtime_state" if scenario == "no_healthcheck" else "runtime_native",
                }
                for i in range(1, 21 if scenario == "many" else 3)
            ],
        }
    return payload


async def validate_template(template):
    contexts = [
        context_for(event, sample_payload(event), locale, template)
        for locale in WORDS
        for event in EVENTS
    ]
    contexts += [
        context_for(
            "executor_health_check_result",
            sample_payload("executor_health_check_result", scenario),
            locale,
            template,
        )
        for locale in WORDS
        for scenario in ("timeout", "no_healthcheck", "unchecked", "many")
    ]
    await render_many(template, contexts)


def format_message(rendered, context, channel):
    # Mandatory system facts remain outside the user-editable template.
    safety = "\n".join(context["safety"])
    link = context["links"]["detail"]
    tail = ("\n\n" + safety if safety else "") + (
        f"\n\n[{WORDS[context['locale']]['view_details']}]({link})" if link else ""
    )
    title = rendered["title"].replace("\n", " ")[:160]
    body = rendered["body"]
    budget = 4096 if channel == "wecom" else 20000
    head = f"### {title}\n\n"
    available = budget - len((head + tail).encode())
    if len(body.encode()) > available:
        body = body.encode()[: max(0, available - 3)].decode("utf-8", "ignore") + "…"
    return {"title": title, "body": body + tail, "content": head + body + tail}


async def render_notification(
    event, payload, language, template=None, channel="webhook", *, strict=False, detail_url=None
):
    template = template or builtin()
    context = context_for(event, payload, language, template)
    if detail_url:
        context["links"]["detail"] = safe_url(detail_url)
    error = None
    try:
        rendered = (await render_many(template, [context]))[0]
    except TemplateRenderError as exc:
        if strict:
            raise
        error = str(exc)
        logger.warning("Notification template rendering failed; using built-in template: %s", error)
        template = builtin()
        context = context_for(event, payload, language, template)
        if detail_url:
            context["links"]["detail"] = safe_url(detail_url)
        rendered = (await render_many(template, [context]))[0]
    return {
        **format_message(rendered, context, channel),
        "locale": context["locale"],
        "template_id": template.get("id"),
        "revision": template.get("revision", 1),
        "fallback_error": error,
    }
