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

Rate limits and transport failures receive only bounded retries. Failed events are not stored in a durable replay queue. A successful test does not guarantee every future delivery.

Disabling a notifier retains configuration and stops sending. Links in messages depend on [BASE URL](../reference/settings.md). For delivery failures, use [Notification troubleshooting](../reference/troubleshooting.md#notifications).
