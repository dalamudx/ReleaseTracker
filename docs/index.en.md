---
title: ReleaseTracker Wiki
---

# ReleaseTracker

Track upstream versions and apply selected versions to runtime targets according to policy. This Wiki is for administrators of self-hosted instances. Tracking versions alone does not require an executor.

## Get started {#next-steps}

Follow [Installation and first run](getting-started/installation.md) to deploy, sign in, and complete your first version check.

## Concepts {#what-it-is}

| Object | Purpose |
| --- | --- |
| Tracker | Groups one or more version sources for a project |
| Version source | A Git repository, Helm chart, or OCI registry providing versions |
| Release channel | Filters source versions by release status and version rules |
| Runtime connection | Access to Docker, Podman, Portainer, Kubernetes or an SSH host |
| Executor | Binds a source's release channel to a runtime target and runs updates |
| Notifier | Sends release and execution events to Webhook, WeCom, Feishu, Telegram and other channels |

Version source → release channel → selected version → executor → runtime target.

Do not confuse three kinds of history: **version history** records upstream releases; **executor snapshots** store target configuration before an update for rollback; **instance backups** archive ReleaseTracker's own database and keys for disaster recovery. None of them contains application data.

## Find a task {#who-it-is-for}

| I want to… | Documentation |
| --- | --- |
| Filter versions, configure a changelog or repository webhooks | [Trackers and version rules](guides/trackers.md) |
| Connect private sources, container platforms or SSH hosts | [Credentials and runtimes](guides/runtime-connections.md) |
| Configure automatic updates, maintenance windows and approvals | [Executors and update policies](guides/executors.md) |
| Validate an update or roll back | [Health checks and rollback](guides/health-and-rollback.md) |
| Receive events or customize message templates | [Notifications](guides/notifications.md) |
| Set up HTTPS, sub-paths, or SSO | [Reverse proxy](operations/reverse-proxy.md) · [Administrator and OIDC](operations/accounts-and-oidc.md) |
| Back up, restore, upgrade, or rotate keys | [Backup, upgrade, and keys](operations/backup-and-upgrade.md) |
| Look up settings, support, or errors | [System settings](reference/settings.md) · [Support scope](reference/support.md) · [Troubleshooting](reference/troubleshooting.md) |

## Before running updates {#core-capabilities}

Deploy a single instance. Verify the selected version and bindings manually before enabling automatic updates; failed updates are never rolled back automatically. See [Support scope](reference/support.md) for update, health-check and recovery coverage. Source code and development entry points are in the [GitHub repository](https://github.com/dalamudx/ReleaseTracker).
