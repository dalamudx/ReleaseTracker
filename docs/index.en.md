---
title: ReleaseTracker Wiki
---

# ReleaseTracker

Track upstream versions and apply selected versions to runtime targets according to policy. This Wiki is for self-hosted instance administrators. Tracking versions alone does not require an executor.

## Get started {#next-steps}

Follow [Installation and first run](getting-started/installation.md) to deploy, sign in, and complete your first version check.

## Concepts {#what-it-is}

| Object | Purpose |
| --- | --- |
| Tracker | Groups one or more version sources for a project |
| Version source | A Git repository, Helm chart, or OCI registry providing versions |
| Release channel | Filters source versions by release status and version rules |
| Runtime connection | Provides access to Docker, Podman, Portainer, or Kubernetes |
| Executor | Binds a source's release channel to a runtime target and runs updates |

Version source → release channel → selected version → executor → runtime target.

## Find a task {#who-it-is-for}

| I want to… | Documentation |
| --- | --- |
| Filter versions or configure a changelog | [Trackers and version rules](guides/trackers.md) |
| Connect private sources or container platforms | [Credentials and runtimes](guides/runtime-connections.md) |
| Configure automatic updates and maintenance windows | [Executors and update policies](guides/executors.md) |
| Validate an update or roll back | [Health checks and rollback](guides/health-and-rollback.md) |
| Receive version and execution events | [Webhook notifications](guides/notifications.md) |
| Set up HTTPS, sub-paths, or SSO | [Reverse proxy](operations/reverse-proxy.md) · [Administrator and OIDC](operations/accounts-and-oidc.md) |
| Back up, upgrade, or rotate keys | [Backups, upgrades, and keys](operations/backup-and-upgrade.md) |
| Look up settings, support, or errors | [System settings](reference/settings.md) · [Support matrix](reference/support.md) · [Troubleshooting](reference/troubleshooting.md) |

## Before running updates {#core-capabilities}

Deploy a single instance. Verify the selected version and bindings manually before enabling automatic updates. Executor snapshots are not application-data backups. See the [support matrix](reference/support.md) for update, health-check, and recovery coverage.

Source code, development commands, and contribution entry points are in the [GitHub repository](https://github.com/dalamudx/ReleaseTracker).
