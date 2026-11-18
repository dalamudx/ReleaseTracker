---
title: System settings
---

# System settings

Manage instance configuration under **System Settings**. Changes apply immediately and are included in database backups; no database or `.env` editing is needed. Source filtering and execution policies belong to [Trackers](../guides/trackers.md) and [Executors](../guides/executors.md).

## Global configuration {#global}

| Setting | Default / range | Effect |
| --- | --- | --- |
| Public Base URL | Empty by default. Use a canonical absolute HTTPS URL | Used for notification links, the frontend base path, OIDC callbacks and repository webhooks. Include the deployment subpath |
| Time zone | `UTC`; valid IANA zone such as `Asia/Shanghai` | Date display, maintenance windows and times in notification messages |
| Log level | `INFO`; `DEBUG/INFO/WARNING/ERROR` | Backend log verbosity |
| Trusted IP ranges | Empty by default. Add IPv4 or IPv6 CIDRs individually. | Use the pool routed by your fake-IP proxy, e.g. `198.18.0.0/15`. HTTPS notifications accept DNS answers in these ranges and verify the certificate against the original hostname. Clear and save to restore the default rules. |
| OCI registry redirects | Disabled | Enable only when a registry serves content through redirects; redirects still pass security checks |
| Readiness defaults | See [Health checks](../guides/health-and-rollback.md#health-checks) | Used when an executor does not override them |
| Release channel history | 20; 1–1000 | Version records kept per release channel |
| Executor runtime snapshots | 10; 1–1000 | Snapshots kept per executor; locked or claimed snapshots are protected, so the actual count may be higher |
| Backup retention | 7; 1–100 | Full backups kept in the data directory's `backups` folder |
| Automatic backup interval | 0 (off); 0–8760 hours | Interval for automatic full backups, see [Online backup](../operations/backup-and-upgrade.md#online-backup) |
| Deployment and recovery concurrency | 1 (1–3) | Both operations share slots. Running operations finish after a reduction |
| Worker polling interval | 2 seconds (1–60) | Time between checks for new work |
| Image pull grace | 1800 seconds (0–3600) | Applies to new Kubernetes readiness observations |
| Runtime health inspection interval | 0 (off) or 300–86400 seconds | Read-only checks after deployment |
| Online restore and pre-migration backup | Online restore enabled on POSIX, pre-migration backup enabled | Controls browser-based restore and backups before migration |
| CORS origins | Empty | Explicit HTTP(S) origins allowed to make cross-origin requests |
| Trusted inbound proxies | `127.0.0.1` | Trust these peers for forwarded client addresses and schemes. `*` trusts every peer |
| Metrics token | Unconfigured | Set, rotate or revoke the dedicated `/metrics` credential |

**Clean now** trims release history or snapshots to the current limit; release history is also pruned daily at 02:00 or after a maintenance window ends. Check which records you need before lowering limits; pruning is not a backup. See [Reverse proxy and sub-paths](../operations/reverse-proxy.md) for proxies and [Backup, upgrade and keys](../operations/backup-and-upgrade.md#keys) for key rotation.

## Runtime operation policy (API) {#operation-policy}

Configure `config.operation_policy` through the runtime connection API; there is no form yet. This is a `config` fragment; keep other keys when editing:

```json
{
  "operation_policy": {
    "read_timeout_seconds": 20,
    "write_timeout_seconds": 90,
    "read_retries": 1
  }
}
```

| Field | Default | Valid range (integer) |
| --- | --- | --- |
| `read_timeout_seconds` | 20 s | 1–600 |
| `write_timeout_seconds` | 90 s | 1–600 |
| `read_retries` | 1 additional attempt | 0–3; 0 disables retries |

| Call path | Policy application |
| --- | --- |
| Portainer reads | Read timeout; bounded retries for transient transport failures |
| Portainer stack writes | Write timeout; no automatic replay |
| Docker / Podman SDK | Client network timeout uses the write timeout |
| Compose / Helm commands | Subprocesses use the write budget; no automatic replay |
| Native Kubernetes workload API | Not yet integrated |

This is not a whole-update deadline. A timed-out write may already have executed on the server; see [Update timeout](troubleshooting.md#update-timeout).
