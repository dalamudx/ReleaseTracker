---
title: Troubleshooting
---

# Troubleshooting

Record the time, executor / tracker name, run ID, and error category first. Remove tokens, passwords, Webhook URLs, kubeconfigs, and sensitive snapshot fields before sharing logs.

## Login or password unavailable {#login}

Read the first-install bootstrap password from startup logs; existing installations do not generate another one. For forgotten or blocked legacy default passwords, use [Administrator recovery](../operations/accounts-and-oidc.md#password-recovery). Do not delete user tables or key files to force initialization.

## OIDC failure {#oidc}

- `redirect_uri_mismatch`: compare the IdP callback with `{BASE URL}/auth/oidc/{slug}/callback` exactly, including HTTPS, sub-path, and Slug.
- Provider configured but login unavailable: confirm the administrator binding was completed, not just provider creation.
- Binding or callback failure: check HTTPS, browser cookies, system time, and IdP signing configuration. Retain the error category, not the full authorization URL.

Start a fresh login flow after fixing configuration; do not replay old callbacks. See [Administrator and OIDC](../operations/accounts-and-oidc.md#oidc).

## UI works but API / assets return 404 {#proxy}

Compare the [root and sub-path examples](../operations/reverse-proxy.md): preserve paths at the root, strip a sub-path prefix exactly once. Match BASE URL to the external address and ensure APIs return JSON rather than an SPA page. Reload from the external URL after changes.

## Missing or unexpected versions {#versions}

Check in order: source address and credentials → upstream rate limits / request failures → Release and Tag fallback → channels, include and exclude patterns → fetch depth → publish-time / SemVer sorting.

Run a manual check for one source and inspect the returned versions. Rules match tags, not release bodies. See [Version rules](../guides/trackers.md#channels).

## New version but no update {#not-running}

Check executor/runtime enabled states, the specific source and release-channel binding, manual mode, maintenance windows, and whether the target is already current. Inspect skip reasons and active runs in execution history, not just the tracker's displayed version.

See [Execution policies](../guides/executors.md#policies).

## Runtime connection or discovery failure {#connections}

| Target | Check first |
| --- | --- |
| Docker / Podman | `unix://` or `tcp://` prefix, Socket mount, process permissions, listening service |
| TLS connection | CA trust, certificate expiry, hostname; do not disable verification |
| Kubernetes | Context, valid credentials, namespace permissions, ServiceAccount |
| Portainer | API key, Endpoint ID, platform availability, stack type |

See [Credentials and runtimes](../guides/runtime-connections.md) for configuration.

## Health-check failure {#health}

Confirm the target supports the selected pipeline, then check reachability from the instance. HTTP private-address or redirect rejection is a security policy, not a DNS failure. Adjust grace periods and probe windows for slow startup. TCP success only proves a port accepts connections.

Checks never roll back automatically; use [Health checks and rollback](../guides/health-and-rollback.md) to decide next steps.

## Update timeout {#update-timeout}

Pause new automatic executions. Inspect actual images, service state, and platform events in Docker / Portainer / Kubernetes / Helm, then compare execution history. A timed-out write may already have been accepted; do not assume “nothing happened” and immediately repeat it.

Distinguish source-fetch timeouts, control-plane request timeouts, and probe timeouts. [Runtime operation policy](settings.md#operation-policy) is not a deadline for the entire upgrade.

## Missing or invalid snapshot {#snapshots}

Check the [support matrix](support.md#runtimes), whether a snapshot-producing update ran, and whether retention removed it. `legacy_unverified` does not mean corrupted; `invalid` must not be used for recovery. Inspect the [preview API](../guides/health-and-rollback.md#preview) result rather than editing digests to bypass checks.

## Notification not received {#notifications}

Send a test, then check enabled state, event filters, public addressing, approved ports, receiver rate limits, and message format. Failed events have no durable replay queue; see [Webhook notifications](../guides/notifications.md).

## Startup, write, or migration failure {#startup}

1. Read the actual logged error. Check the mount path, write permissions, disk space, and filesystem restrictions; do not broadly loosen permissions recursively.
2. Ensure only one instance is active and its database matches `system-secrets.json`.
3. For migration issues, stop and back up before using the `migrate` entry command. Do not edit live tables manually.
4. For downgrades or inconsistent data, follow [Recovery with matching versions](../operations/backup-and-upgrade.md#restore).

Include the version, deployment method, reproduction steps, and redacted logs in issues, not real databases, key files, or complete snapshots.
