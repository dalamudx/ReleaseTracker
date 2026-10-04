---
title: Notifications
---

# Notifications

Notifications report release and execution events; they never trigger or block executor updates. Add notifiers on the **Notifications** page. Times in messages use the time zone from [System settings](../reference/settings.md#global).

## Channel types {#channels}

| Type | Example endpoint | Notes |
| --- | --- | --- |
| Generic Webhook | Any public HTTP(S) URL | JSON with Discord / Slack compatible fields |
| WeCom | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` | Markdown message |
| Feishu / Lark | `https://open.feishu.cn/open-apis/bot/v2/hook/...` | Card message colored by event |
| DingTalk | `https://oapi.dingtalk.com/robot/send?access_token=...` | Markdown message |
| Discord | `https://discord.com/api/webhooks/...` | Embed message |
| Slack | `https://hooks.slack.com/services/...` | Text message |
| Telegram | `https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat_id>` | Add `message_thread_id` to post into a topic |

Each type builds the platform's native payload and checks error codes in the response body. Feishu and DingTalk request signing is not supported; use custom keywords or an IP allowlist instead, and put the keyword in the message template. WeCom, Discord and Telegram messages are capped at about 4096 bytes and truncated beyond that.

## Configure and test {#setup}

1. Add a notifier and choose its channel type, endpoint, message language and message template.
2. Select notification events (see below).
3. Save, click **Send Test**, and confirm the receiver gets and renders the message.

| Notification event | Sent when |
| --- | --- |
| New release detected / Release republished | A release channel gets a new version; upstream content changes under the same tag |
| Deployment completed / incomplete / skipped | An executor run ends |
| Readiness check result | Post-deployment readiness or health verification completes |
| Deployment approval required / Deployment blocked | A deployment plan needs confirmation, or ownership or safety checks block it; see [Deployment plan approval](executors.md#approval) |
| Task processing error | System alerts such as scheduled backup failures and post-deployment watch findings |

Before enabling automatic updates, verify at least once that failure events reach the receiver.

## Message templates {#templates}

Each notifier references a template; the default is the **built-in template**. Create or save a copy in the template editor:

- One Jinja template covers every event, with separate title and body; conditions, loops and macros are allowed.
- Common variables: `release` (version, source, channel, digest, notes, published_at), `services` (name, from_display, to_display, check_label), `health` (outcome_label, ...) and `labels` (localized strings).
- The **translation dictionary** adds or overrides strings as JSON, for example `{"en": {"custom": "Note"}}`, used as `{{ labels.custom }}`.
- **Live preview** renders sanitized sample data per event, language and channel format without sending anything.

Saving validates rendering for every event in both languages; queued messages keep the content they were queued with. Templates referenced by a notifier cannot be deleted. Verification notes and the details link are appended by the system and cannot be removed by a template.

## Destination security {#security}

- Only public HTTP(S) destinations are allowed: `80/8080` for HTTP, `443/8443/9443` for HTTPS.
- Private, loopback, link-local, container-network and cloud metadata addresses are rejected; redirects are not followed.
- Endpoints are stored in plaintext in SQLite and usually contain tokens. Protect the database and backups; never paste full URLs into public logs or issues.
- Custom HTTP headers are not supported.

## Delivery behavior {#delivery}

Events are written to a durable queue and sent in the background, resuming after restarts or interrupted sends. Failures retry after 30 s, 2 min, 10 min and 30 min and are marked failed after 4 attempts; there is no manual replay. Identical event/release/digest entries are deduplicated locally, but a crash after sending and before acknowledging can resend, so receivers should be idempotent. Disabling a notifier or unsubscribing an event discards its pending messages.

New releases are evaluated **per release channel**: when a prerelease is the tracker-wide latest version, a new stable release still notifies separately. Scheduled backup failures alert once per failure streak and reset after a success; alerts never include paths or keys.

Links depend on [BASE URL](../reference/settings.md#global). If messages do not arrive, see [Notification troubleshooting](../reference/troubleshooting.md#notifications).
