---
title: Support scope
---

# Support scope

This page lists what the regular execution flow offers to users. For configuration see the [user guides](../guides/executors.md).

## Deployment and accounts {#deployment}

| Area | Boundary |
| --- | --- |
| Deployment | Single instance, single scheduler; SQLite WAL is not a multi-replica coordination mechanism |
| Official image | `linux/amd64`; the container runs as root by default |
| Administration | One administrator; no registration, RBAC or tenant isolation |
| OIDC | One provider bound to the one administrator identity |
| Cross-origin | Same-origin only by default; add explicit origins with `RELEASETRACKER_CORS_ORIGINS`. CORS does not replace authentication or HTTPS |

## Runtime targets {#runtimes}

| Target | Update method | Pre-update snapshot | Readiness observation | Recovery |
| --- | --- | --- | --- | --- |
| Docker / Podman container | Recreate from inspected configuration | Yes | Yes | Manual snapshot rollback |
| Docker / Podman Compose group | Group recreate | Yes | Yes | Manual snapshot rollback |
| Portainer standalone stack | Stack-file API update | Yes | Yes | Manual snapshot rollback |
| Kubernetes workload | Change image configuration | Yes | Yes (Deployments include pull grace) | Manual snapshot rollback or native Kubernetes history |
| Helm release | Helm upgrade | Yes | Helm status | Manual snapshot rollback or `helm rollback` |
| SSH Compose project | Rewrite Compose / env / override file, then run Compose | Project files | Yes | Restore configuration files; containers are not rolled back |

Additional limits:

- Portainer does not support Swarm, Kubernetes-type or Git-backed stacks.
- Kubernetes workloads are limited to Deployments, StatefulSets and DaemonSets; no Jobs / CronJobs.
- Helm uses Helm 3; discovery relies on the Secret storage driver.
- SSH Compose needs a Docker or Podman Compose tool on the remote host and refuses complex image expressions or shared variables.
- No target rolls back automatically after a failed update or health check; recovery does not restore persistent volumes or application data.

See [Health checks and rollback](../guides/health-and-rollback.md) for operating requirements.

## Sources and notifications {#sources}

| Area | Support / limits |
| --- | --- |
| Version sources | GitHub, GitLab, Gitea, Helm charts, OCI registries |
| Repository webhooks | Release and successful Actions events from GitHub, GitLab, Gitea and Forgejo; they only trigger refreshes |
| Release channels | `stable`, `prerelease`, `beta`, `canary` |
| Version filters | Include / exclude regexes match tags, not release bodies or authors |
| Publish times | Depend on upstream metadata; first-observed time is not the real publish time |
| Notifications | Generic Webhook, WeCom, Feishu, DingTalk, Discord, Slack, Telegram; no custom headers, Feishu/DingTalk signing, or manual replay after final failure |

Public-address limits for notifications and HTTP probes are described in [Notification security](../guides/notifications.md#security).

## Data and API {#data}

After a migration, older application versions are not guaranteed to read the new schema; downgrade using the [backup recovery process](../operations/backup-and-upgrade.md#restore). Execution history is for diagnostics, not a complete audit log. The API has no stable versioning commitment yet; check the instance's `/docs` before integrating.
