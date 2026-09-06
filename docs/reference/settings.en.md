---
title: System settings
---

# System settings

Manage normal instance configuration under **System Settings**, not by editing the database or `.env`. This page owns global settings and advanced runtime policy. Source filtering and execution policies belong to [Trackers](../guides/trackers.md) and [Executors](../guides/executors.md).

## Global configuration {#global}

| Setting | Default / range | Effect |
| --- | --- | --- |
| BASE URL | Empty; non-empty values must be canonical absolute HTTPS URLs | Notification links, frontend base path, and OIDC callbacks; include any deployment sub-path |
| Timezone | `UTC`; valid IANA zone such as `Asia/Shanghai` | Date display and maintenance-window time interpretation |
| Log level | `INFO`; `DEBUG/INFO/WARNING/ERROR` | Backend log verbosity |
| Release history count | 20; 1–1000 | Retained entries per source and release channel |
| Snapshot history count | 10; 1–1000 | Per-executor retention target; locked or claimed snapshots are protected, so actual count may exceed it |
| OCI registry redirects | Disabled | Opt-in registry redirects subject to security checks, not unrestricted URL redirects |

See [Reverse proxy and sub-paths](../operations/reverse-proxy.md) for deployment and [Key rotation](../operations/backup-and-upgrade.md#keys) for key operations. Identify records to retain before reducing limits; pruning is not a backup strategy.

## Runtime operation policy (API) {#operation-policy}

Configure `config.operation_policy` through runtime connection APIs; there is no dedicated form yet. This is a `config` fragment, not a complete connection request. Preserve other configuration keys when editing:

```json
{
  "operation_policy": {
    "read_timeout_seconds": 20,
    "write_timeout_seconds": 90,
    "read_retries": 1
  }
}
```

| Field | Default | Valid range |
| --- | --- | --- |
| `read_timeout_seconds` | 20 seconds | Integer 1–600 |
| `write_timeout_seconds` | 90 seconds | Integer 1–600 |
| `read_retries` | 1 additional attempt | Integer 0–3; 0 disables retries |

Do not submit booleans or strings as integers. Actual coverage is:

| Call path | Policy application |
| --- | --- |
| Portainer reads | Read timeout and bounded retries for transient transport failures |
| Portainer stack writes | Write-request timeout; no automatic replay |
| Docker / Podman SDK | Client network timeout uses the write timeout; not a whole-update deadline |
| Compose / Helm commands | Subprocesses use the write budget; no automatic replay |
| Native Kubernetes workload API | Not uniformly integrated with this transport policy; the budget is not a whole-operation guarantee |

This is not a universal maximum update duration. In particular, a timed-out write may already have executed on the server. See [Handling update timeouts](troubleshooting.md#update-timeout).

Implementation references: [Global settings](https://github.com/dalamudx/ReleaseTracker/blob/main/backend/src/releasetracker/storage/sqlite.py), [Runtime policy](https://github.com/dalamudx/ReleaseTracker/blob/main/backend/src/releasetracker/services/runtime_policy.py).
