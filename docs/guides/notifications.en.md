---
title: Webhook notifications
---

# Webhook notifications

Webhook is currently the only channel. Messages include Discord / Slack compatible fields. Notifications report events; they do not trigger or block executor updates.

## Configure and test {#setup}

1. Under **Notifications**, add a Webhook destination and select the message language.
2. Select events: new release, republish, execution success, failure, or skip.
3. Save, use **Send Test**, and confirm the receiver actually receives and renders the message correctly.

Republish means upstream content changed under the same version tag. Before enabling automatic updates, at least verify that failure notifications can reach the recipient.

## Destination security {#security}

- Only public HTTP(S) destinations are allowed: `80/8080` for HTTP and `443/8443/9443` for HTTPS.
- Private, loopback, link-local, container-network, and cloud metadata addresses are rejected. Redirects are not followed.
- Webhook URLs are currently stored in plaintext in SQLite and may contain provider tokens. Protect the database and backups; never paste full URLs into public logs or issues.
- Custom HTTP headers are not supported. Services requiring a special authentication format need a compatible receiver.

## Delivery behavior {#delivery}

New-release, republish, executor result and scheduled-backup failure notifications are written to a durable queue before background delivery. Delivery resumes after restarts or interrupted sends and retries with 30 s, 2 min, 10 min and 30 min backoff, failing after 4 attempts. A notifier receives each release (including digest) only once. Queued messages are discarded if the notifier is disabled or unsubscribed. A successful test does not guarantee every future delivery.

New-release notifications are evaluated **per channel**: when a newer prerelease is the tracker-wide winner, a new stable release still notifies on its own. Scheduled backup failures use the “error” event, alerting once per failure streak (a success resets it); alerts contain only an error category, never paths or keys.

Disabling a notifier retains configuration and stops sending. Links in messages depend on [BASE URL](../reference/settings.md). For delivery failures, use [Notification troubleshooting](../reference/troubleshooting.md#notifications).
