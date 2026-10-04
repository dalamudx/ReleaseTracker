---
title: Health checks and rollback
---

# Health checks and rollback

A successful update command does not mean the application is available. Health checks validate the result; rollback is a separate operator-confirmed action. Neither update nor probe failures roll back automatically.

## Choose a health check {#health-checks}

After deployment, readiness observation runs durably in the background and resumes after restarts.

| Strategy | Use when |
| --- | --- |
| Auto | Prefer runtime capabilities; a good first choice |
| Runtime native | The container has a healthcheck, or a Kubernetes workload's rollout status; without a healthcheck "running" is not application readiness |
| Helm status | The release status of a Helm release |
| Manual HTTP | A public health endpoint whose status code or body must be checked |
| Manual TCP | Only port connectivity matters; the application protocol is not checked |
| Disabled | You verify the service externally |

HTTP hosts must resolve to public addresses; ports are limited to `80/8080` for HTTP and `443/8443/9443` for HTTPS, and redirects are not followed. Prefer explicit expected status codes such as `200,204`; when empty, 200–399 is accepted but redirects are still rejected.

Defaults: 600 s total deadline, 10 s per probe, 5 s interval, 10 s stability period, overridable per executor. Kubernetes observation follows the Pods of this rollout: a replaced Pod or a growing restart count resets the stability timer, and unreadable Pods never count as success. With digest references the actual image digest of every replica is verified; tag references cannot prove content is unchanged after a re-push.

## Failure policy {#failure-policy}

Readiness observation follows the executor's "On failure" setting:

- **Mark failed**: the task fails.
- **Mark degraded**: the update stays applied, the task ends as "Deployment marked degraded", and the run record starts with `degraded:`.

Neither reverts a completed update; a changed or unobservable target goes to manual handling. Probe failures record only a categorized reason (timeout, connection refused, unexpected status, ...), never URLs or response bodies.

## Kubernetes image pull grace {#image-pull}

When new Pods of a Kubernetes Deployment fail to pull an image for a recoverable reason (timeouts, transient disconnects, registry 5xx), the readiness deadline is followed by a read-only grace period: rechecks every 30–120 s, no redeployment, no extra attempts, and restarts do not reset the deadline. If the Pods become ready, the original task turns successful and keeps the delay evidence; otherwise it fails as a timeout. Definite faults such as missing images, authentication failures, CrashLoopBackOff or OOM end immediately.

- `RELEASETRACKER_READINESS_PULL_GRACE_SECONDS`: default 1800, range 0–3600, 0 disables; applies to new tasks after a restart.
- Deployments only; not StatefulSets, DaemonSets, Helm or other runtimes.
- Failed tasks are not replayed automatically; trigger a read-only readiness recheck from the task page.
- With the `Recreate` strategy an unavailable image causes an outage; the grace period is not an availability guarantee, so keep cluster alerting.

## Read-only post-deployment monitoring {#runtime-watch}

With `RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS` set (300–86400 s; default `0` disables), targets whose latest successful deployment passed readiness verification are checked periodically and read-only, including configured HTTP/TCP probes. Three consecutive unhealthy results, drift, unreadable targets or repeated Pod restarts send one deduplicated alert to notifiers subscribed to "Task processing error".

At most 16 due targets are checked per minute, so the interval is a minimum, not a real-time guarantee; targets being updated, awaiting readiness or needing attention are skipped. Monitoring never deploys, rolls back or rewrites history, and does not replace cluster or application monitoring.

## Snapshots and retention {#snapshots}

The configuration needed to recreate a target is captured before updating (coverage in [Support scope](../reference/support.md#runtimes)). Snapshots exclude persistent volumes, application databases and migration results, and do not guarantee the same container / Pod IDs.

Review time and image in the executor's snapshot history. Locked snapshots are excluded from pruning and cannot be deleted directly; snapshots in use by a rollback are protected. Retention is set in [System settings](../reference/settings.md#global); protected snapshots can make the actual count exceed it.

The snapshot API reports integrity status:

| Status | Meaning |
| --- | --- |
| `verified` | Content matches the stored SHA-256 and size |
| `legacy_unverified` | Older snapshot without integrity metadata; not necessarily corrupt |
| `invalid` | Verification failed; cannot be used for recovery |

A snapshot is limited to 2 MiB. SHA-256 is a consistency check, not a signature, and cannot defend against a database writer changing both content and digest.

## Preview a rollback (API) {#preview}

There is no preview button yet; call the API with an administrator token (`RT_BASE` includes any sub-path):

```bash
curl --fail-with-body -X POST \
  "$RT_BASE/api/executors/$EXECUTOR_ID/rollback/preview" \
  -H "Authorization: Bearer $RT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"snapshot_id": 42}'
```

Without a body the latest snapshot is used. Judge the result by `snapshot_valid`, `integrity_status` and `validation_error`; a successful request alone is not a pass. Preview creates no records and does not touch the runtime, nor does it guarantee the image is still pullable or the rollback will succeed.

## Recover a target {#rollback}

1. Pause automatic policies that may trigger new updates and check the actual runtime state.
2. Confirm the application supports downgrade and back up affected application data.
3. Select a usable snapshot and roll back manually from the executor details.
4. Review the rollback run and verify application health independently.

SSH Compose targets use the project recovery flow, see [SSH Compose targets](executors.md#ssh-compose). Restoring the instance database is different from executor rollback; see [Backup recovery](../operations/backup-and-upgrade.md#restore).
