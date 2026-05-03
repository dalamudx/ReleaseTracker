import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import emoji
import httpx

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
    return labels.get(f"status_{status}", status)


class WebhookNotifier(BaseNotifier):
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

    async def notify(self, event: str, payload: Any):
        if event not in self.events:
            return

        webhook_payload = _build_webhook_payload(event, payload, language=self.language)

        async with httpx.AsyncClient() as client:
            for attempt in range(4):
                try:
                    response = await client.post(
                        self.url,
                        json=webhook_payload,
                        timeout=10.0,
                    )

                    if response.status_code == 429:
                        if attempt >= 3:
                            logger.warning(
                                f"Webhook 429 Too Many Requests after {attempt + 1} attempts, giving up"
                            )
                            return

                        wait_time = 1.0
                        retry_after = response.headers.get("Retry-After")
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
                            f"Webhook 429 Too Many Requests (attempt {attempt + 1}/4). "
                            f"Waiting {wait_time:.1f}s before retry..."
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    logger.debug(
                        f"Webhook notification sent successfully: {self.name} (attempt {attempt + 1})"
                    )
                    return

                except httpx.HTTPStatusError as e:
                    logger.error(
                        f"Webhook notification failed with HTTP {e.response.status_code}: {self.name}"
                    )
                    return
                except Exception as e:
                    if attempt < 3:
                        wait = 2.0**attempt
                        logger.warning(
                            f"Webhook notification error (attempt {attempt + 1}/4), retrying in {wait}s: {e}"
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(f"Webhook notification failed after 4 attempts: {e}")
                    return


def _build_webhook_payload(
    event: str,
    payload: Any,
    *,
    language: str = "en",
) -> dict[str, Any]:
    labels = _webhook_labels(language)
    if hasattr(payload, "tracker_name") and hasattr(payload, "version"):
        return _build_release_payload(event, payload, labels)

    if isinstance(payload, dict) and payload.get("entity") == "executor_run":
        return _build_executor_payload(event, payload, labels)

    message = f"[{event}] {labels['notification_received']}"
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
    message = f"[Executor:{executor_name}]"

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
        "message": message,
        "content": message,
        "text": message,
        "embeds": [
            {
                "title": labels["executor_run_title"].format(status=status_label),
                "description": str(payload.get("message") or labels["no_executor_message"]),
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
