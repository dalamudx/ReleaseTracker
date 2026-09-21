import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import emoji

from ..services.outbound_http import (
    OutboundHTTPClient,
    OutboundHTTPError,
    OutboundResponse,
    OutboundRedirectRejected,
    OutboundURLRejected,
)
from .base import BaseNotifier

logger = logging.getLogger(__name__)

WEBHOOK_TRANSLATIONS = {
    "en": {
        "executor": "Executor",
        "tracker": "Tracker",
        "runtime": "Runtime",
        "result": "Result",
        "from_version": "From Version",
        "to_version": "To Version",
        "run_id": "Run ID",
        "executor_run_title": "Executor run {status}",
        "no_executor_message": "No executor message",
        "event_footer": "Event: {event}",
        "tag": "Tag",
        "channel": "Channel",
        "published": "Published",
        "no_release_notes": "No release notes",
        "prerelease": "Pre-release",
        "notification_received": "Notification received",
        "event_new_release": "New Release",
        "event_republish": "Republish",
        "status_success": "success",
        "status_failed": "failed",
        "status_skipped": "skipped",
    },
    "zh": {
        "executor": "执行器",
        "tracker": "追踪器",
        "runtime": "运行时",
        "result": "结果",
        "from_version": "原版本",
        "to_version": "目标版本",
        "run_id": "运行 ID",
        "executor_run_title": "执行器运行{status}",
        "no_executor_message": "没有执行器消息",
        "event_footer": "事件：{event}",
        "tag": "标签",
        "channel": "渠道",
        "published": "发布时间",
        "no_release_notes": "暂无发布说明",
        "prerelease": "预发布",
        "notification_received": "收到通知",
        "event_new_release": "新版本发布",
        "event_republish": "重新发布",
        "status_success": "成功",
        "status_failed": "失败",
        "status_skipped": "跳过",
    },
}


def _webhook_labels(language: str) -> dict[str, str]:
    return WEBHOOK_TRANSLATIONS.get(language, WEBHOOK_TRANSLATIONS["en"])


def _translated_event(event: str, labels: dict[str, str]) -> str:
    return labels.get(f"event_{event}", event.replace("_", " ").title())


def _translated_status(status: str, labels: dict[str, str]) -> str:
    if status in {"readiness_recovered", "readiness_recheck_failed"}:
        if labels.get("executor") == "执行器":
            return "重新核验通过" if status == "readiness_recovered" else "重新核验未通过"
        return (
            "Readiness recheck passed"
            if status == "readiness_recovered"
            else "Readiness recheck did not pass"
        )
    return labels.get(f"status_{status}", status)


class WebhookNotifier(BaseNotifier):
    provider_name = "Webhook"

    def __init__(
        self,
        name: str,
        url: str,
        events: list[str] | None = None,
        language: str = "en",
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.url = url
        self.events = events or ["new_release"]
        self.language = language if language in WEBHOOK_TRANSLATIONS else "en"
        self.template = kwargs.get("template")
        self.detail_url = kwargs.get("detail_url")

    async def notify(self, event: str, payload: Any) -> bool:
        if event not in self.events:
            return False

        webhook_payload = await self.prepare(event, payload)
        return await self.send_payload(
            {key: value for key, value in webhook_payload.items() if not key.startswith("_")}
        )

    async def prepare(self, event: str, payload: Any) -> dict:
        if isinstance(payload, dict) and isinstance(payload.get("_prepared_notification"), dict):
            return payload["_prepared_notification"]
        generic = _build_webhook_payload(event, payload, language=self.language)
        if self.template is None:
            return generic
        from .templates import render_notification

        rendered = await render_notification(
            event,
            payload,
            self.language,
            self.template,
            "wecom" if self.provider_name == "WeCom" else "webhook",
            detail_url=self.detail_url,
        )
        generic.update(
            content=rendered["content"], text=rendered["content"], message=rendered["content"]
        )
        embed = dict((generic.get("embeds") or [{}])[0])
        embed.update(title=rendered["title"], description=rendered["body"], fields=[])
        generic["embeds"] = [embed]
        generic["template"] = {
            key: rendered[key] for key in ("template_id", "revision", "locale", "fallback_error")
        }
        generic["_rendered_markdown"] = rendered["content"]
        return generic

    async def send_payload(self, webhook_payload: dict[str, Any]) -> bool:
        """Deliver a prepared payload through the protected outbound policy."""
        client = OutboundHTTPClient()
        for attempt in range(4):
            try:
                response = await client.request("POST", self.url, json_body=webhook_payload)

                if response.status_code == 429:
                    if attempt >= 3:
                        logger.warning(
                            "Webhook 429 Too Many Requests after %s attempts, giving up",
                            attempt + 1,
                        )
                        return False

                    wait_time = 1.0
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        try:
                            wait_time = float(retry_after)
                        except ValueError:
                            pass
                    else:
                        try:
                            data = response.json()
                            if isinstance(data, dict) and "retry_after" in data:
                                raw = float(data["retry_after"])
                                wait_time = raw / 1000.0 if raw > 60 else raw
                        except Exception:
                            pass

                    wait_time = min(wait_time + 0.5, 30.0)
                    logger.warning(
                        "Webhook 429 Too Many Requests (attempt %s/4). "
                        "Waiting %.1fs before retry...",
                        attempt + 1,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code < 200 or response.status_code >= 300:
                    logger.error(
                        "Webhook notification failed with HTTP %s: %s",
                        response.status_code,
                        self.name,
                    )
                    return False

                accepted, rejection_reason = self._validate_success_response(response)
                if not accepted:
                    logger.error(
                        "%s notification rejected for %s: %s",
                        self.provider_name,
                        self.name,
                        rejection_reason or "unknown response",
                    )
                    return False

                logger.info(
                    "%s notification sent successfully: %s (attempt %s)",
                    self.provider_name,
                    self.name,
                    attempt + 1,
                )
                return True
            except (OutboundURLRejected, OutboundRedirectRejected) as exc:
                logger.error("Webhook notification blocked by outbound policy: %s", exc)
                return False
            except OutboundHTTPError as exc:
                if attempt < 3:
                    wait = 2.0**attempt
                    logger.warning(
                        "Webhook notification error (attempt %s/4), retrying in %ss: %s",
                        attempt + 1,
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error("Webhook notification failed after 4 attempts: %s", exc)
                return False
        return False

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        del response
        return True, None


def _build_webhook_payload(
    event: str,
    payload: Any,
    *,
    language: str = "en",
) -> dict[str, Any]:
    labels = _webhook_labels(language)
    if hasattr(payload, "tracker_name") and hasattr(payload, "version"):
        return _build_release_payload(event, payload, labels)

    if isinstance(payload, dict) and payload.get("entity") in {
        "executor_run",
        "executor_health_recheck",
    }:
        return _build_executor_payload(event, payload, labels)

    supplied_message = payload.get("message") if isinstance(payload, dict) else None
    message = (
        str(supplied_message)
        if supplied_message
        else f"[{event}] {labels['notification_received']}"
    )
    return {
        "event": event,
        "message": message,
        "content": message,
        "text": message,
        "data": payload,
    }


def _build_release_payload(
    event: str,
    release: Any,
    labels: dict[str, str],
) -> dict[str, Any]:
    message = f"[{release.tracker_name}] {_translated_event(event, labels)}: {release.version}"
    if release.prerelease:
        message += f" ({labels['prerelease']})"

    return {
        "event": event,
        "tracker": release.tracker_name,
        "version": release.version,
        "content": message,
        "text": message,
        "embeds": [
            {
                "title": f"{release.tracker_name} {release.version}",
                "description": (
                    emoji.emojize(
                        emoji.emojize(release.body[:2000], language="alias"), language="en"
                    )
                    if release.body
                    else labels["no_release_notes"]
                ),
                "url": release.url,
                "color": 15258703 if release.prerelease else 5763719,
                "fields": [
                    {"name": labels["tag"], "value": release.tag_name, "inline": True},
                    {
                        "name": labels["channel"],
                        "value": release.channel_name or "N/A",
                        "inline": True,
                    },
                    {
                        "name": labels["published"],
                        "value": release.published_at.isoformat(),
                        "inline": True,
                    },
                ],
                "footer": {"text": labels["event_footer"].format(event=event)},
                "timestamp": release.published_at.isoformat(),
            }
        ],
    }


def _build_executor_payload(
    event: str,
    payload: dict[str, Any],
    labels: dict[str, str],
) -> dict[str, Any]:
    executor_name = str(payload.get("executor_name") or "unknown executor")
    tracker_name = str(payload.get("tracker_name") or "unknown tracker")
    runtime_type = str(payload.get("runtime_type") or "unknown")
    status = str(payload.get("status") or event.replace("executor_run_", ""))
    status_label = _translated_status(status, labels)
    from ..services.executor_notification_outbox import sanitize_payload

    health = sanitize_payload(payload).get("health_check")
    health_text = ""
    if health:
        is_zh = labels["executor"] == "执行器"
        outcome_labels = (
            {
                "healthy": "就绪",
                "unhealthy": "不健康",
                "timeout": "超时",
                "error": "探测错误",
                "unknown": "未知",
                "unsupported": "不支持",
                "superseded": "已被后续变更取代",
            }
            if is_zh
            else {
                "healthy": "ready",
                "unhealthy": "unhealthy",
                "timeout": "timeout",
                "error": "probe error",
                "unknown": "unknown",
                "unsupported": "unsupported",
                "superseded": "superseded",
            }
        )
        health_label = "健康检查" if is_zh else "Readiness check"
        scope = health["scope"]
        if is_zh:
            scope = (
                "仅运行时就绪；未验证业务健康"
                if health["strategy"] == "runtime_native"
                else "已配置的就绪探测"
            )
        if health.get("native_health_absent"):
            scope += "; " + ("未配置原生健康检查" if is_zh else "native health check absent")
        details = [health["strategy"]]
        if "attempt_count" in health:
            details.append(f"{health['attempt_count']} attempts")
        elapsed = health.get("elapsed_seconds", health.get("duration_seconds"))
        if elapsed is not None:
            details.append(("就绪 " if is_zh else "readiness ") + f"{elapsed}s")
        if "update_duration_seconds" in health:
            details.append(
                ("更新 " if is_zh else "update ") + f"{health['update_duration_seconds']}s"
            )
        health_text = (
            f"\n{health_label}: {outcome_labels[health['outcome']]} "
            f"({', '.join(details)}); {scope}"
        )
        services = health.get("services", [])
        if services:
            # Keep chat notifications concise; the structured payload retains all rows.
            service_text = "; ".join(
                f"{row['service']}: {outcome_labels.get(row['status'], row['status'])} "
                f"({row['method']})"
                for row in services[:5]
            )
            if len(services) > 5:
                service_text += f"; +{len(services) - 5}"
            label = "服务" if is_zh else "Services"
            health_text += f"\n{label} ({health['service_count']}): {service_text}"
    message = f"[Executor:{executor_name}]"
    if health:
        message += f" {status_label}{health_text}"

    fields: list[dict[str, Any]] = [
        {"name": labels["executor"], "value": executor_name, "inline": True},
        {"name": labels["tracker"], "value": tracker_name, "inline": True},
        {"name": labels["runtime"], "value": runtime_type, "inline": True},
        {"name": labels["result"], "value": status_label, "inline": True},
        {
            "name": labels["from_version"],
            "value": str(payload.get("from_version") or "N/A"),
            "inline": False,
        },
        {
            "name": labels["to_version"],
            "value": str(payload.get("to_version") or "N/A"),
            "inline": False,
        },
    ]
    if payload.get("run_id") is not None:
        fields.append({"name": labels["run_id"], "value": str(payload["run_id"]), "inline": True})

    color_map = {
        "success": 5763719,
        "failed": 15548997,
        "skipped": 9807270,
    }
    timestamp = _normalize_webhook_timestamp(
        payload.get("finished_at") or payload.get("started_at")
    )

    return {
        "event": event,
        "entity": "executor_run",
        "executor": {
            "id": payload.get("executor_id"),
            "name": executor_name,
            "tracker": tracker_name,
            "runtime_type": runtime_type,
        },
        "run": {
            "id": payload.get("run_id"),
            "status": status,
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "from_version": payload.get("from_version"),
            "to_version": payload.get("to_version"),
            "message": payload.get("message"),
        },
        "health_check": health,
        "message": message,
        "content": message,
        "text": message,
        "embeds": [
            {
                "title": (
                    (
                        "执行器健康检查结果"
                        if labels["executor"] == "执行器"
                        else "Executor readiness check result"
                    )
                    if event == "executor_health_check_result"
                    else labels["executor_run_title"].format(status=status_label)
                ),
                "description": str(payload.get("message") or labels["no_executor_message"])
                + health_text,
                "color": color_map.get(status, 9807270),
                "fields": fields,
                "footer": {"text": labels["event_footer"].format(event=event)},
                "timestamp": timestamp,
            }
        ],
    }


def _normalize_webhook_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    parse_value = normalized.replace("Z", "+00:00") if normalized.endswith("Z") else normalized
    try:
        timestamp = datetime.fromisoformat(parse_value)
    except ValueError:
        return normalized
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
