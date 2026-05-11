---
title: Notifications
---

# Notifications

ReleaseTracker currently supports webhook notifications only. The webhook payload is both Discord- and Slack-compatible, and messages can be rendered in English or Chinese.

## 1. Data model

```text
Notifier {
  name:        string
  type:        webhook        # webhook is the only supported type today
  url:         string         # Target HTTP/HTTPS URL
  events:      list<string>   # Subscribed events; an empty list is effectively disabled
  enabled:     bool
  language:    en | zh
  description: string | null
}
```

## 2. Supported events

| Event | When it fires |
| ----- | ------------- |
| `new_release` | The aggregate tracker's current-view winner changes to a **different version**. |
| `republish` | The **same version** was observed again (for example, a container tag that was re-pushed to the registry). |
| `executor_run_success` | An executor run completed successfully. |
| `executor_run_failed` | An executor run ended in failure. |
| `executor_run_skipped` | A scheduled trigger was skipped (disabled, outside the maintenance window, target already up to date, etc.). |

`new_release` and `republish` are emitted by the release scheduler. The three `executor_*` events are emitted by the executor scheduler.

## 3. Creating a webhook

**Notifications → New**. Key fields:

- `URL`: webhook target. Must be a complete HTTP/HTTPS address.
- `events`: check the events you want to subscribe to. Unchecked events are not delivered.
- `language`: `en` or `zh`.
- `enabled`: turning it off suppresses delivery even when events fire.

## 4. Test sending

Every notifier has a **Test** button that POSTs a fixed test payload to the target URL:

- English: `This is a test notification from ReleaseTracker`
- Chinese: `这是一条来自 ReleaseTracker 的测试通知`

Timeout: 10 seconds. The UI surfaces non-2xx responses or connection errors verbatim.

## 5. Discord / Slack compatibility

The generated JSON contains both:

- Discord-friendly fields: `content`, `embeds`.
- Slack-friendly fields: `text`, `attachments`.

Paste a Discord "Incoming Webhook" URL or a Slack "Incoming Webhook" URL directly. Custom HTTP receivers can parse the same payload.

## 6. Retries and failure behaviour

- Webhook requests use a 10-second timeout.
- There is **no automatic retry**. Failed deliveries are logged on the server but not queued for replay.
- To compensate for a transient outage, re-trigger the underlying action (e.g. a manual tracker check or executor run) once the target is reachable again.

## 7. Privacy and security notes

- Webhook URLs typically embed a secret path segment (Discord and Slack both work this way). The URL column is stored in SQLite but **is not encrypted**. Anyone with database access can read it in plaintext.
- Avoid sending sensitive release notes or image names to channels outside your team.
- Custom webhooks that require authentication headers (beyond the URL secret) are not supported in this version.
