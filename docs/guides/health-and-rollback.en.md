---
title: Health checks and rollback
---

# Health checks and rollback

A successful update command does not prove application availability. Health checks validate the result. Rollback is a separate operator-confirmed action; failed updates or probes never trigger automatic rollback.

## Choose a health check {#health-checks}

Post-update probing in the normal update flow currently covers Docker / Podman single containers. Probe configuration or adapter methods alone do not mean a grouped target has a fully integrated pipeline; see the [support matrix](../reference/support.md#runtimes).

| Strategy | Use when |
| --- | --- |
| Auto | Start with the runtime's available capabilities |
| Runtime native | The container has a healthcheck; running-state fallback is not proof of business readiness |
| Manual HTTP | A public health endpoint exposes status codes or response content |
| Manual TCP | A host/port connection is sufficient; no application protocol is checked |
| Disabled | Validate externally rather than using probes to determine run status |

HTTP hosts must resolve to public addresses. Allowed ports are `80/8080` for HTTP and `443/8443/9443` for HTTPS. Redirects are rejected. Without explicit status codes, the matcher accepts 200–399, but redirect responses are still blocked; prefer explicit values such as `200,204`.

Default automatic checks use a 15-second grace period, 10-second attempt timeout, 5-second interval, and 180-second probe window. Adjust the grace period and window for slow startup; the window excludes the initial grace period. Failure can mark a run failed or degraded, but does not undo the update.

## Read-only observation after Kubernetes image pull failures {#image-pull}

For newly submitted Kubernetes Deployments, ReleaseTracker reads waiting reasons on Pods matching the submitted workload and image. Confirmed transient pull timeouts, connection interruptions, or registry 5xx responses are checked **again at the original readiness deadline**; only a still-retrying pull receives a read-only grace period of up to one hour. No deployment is retried and no mutation attempt is spent. Checks run every 30–120 seconds during grace, with a deadline preserved across ReleaseTracker restarts. Missing/unauthorized images, container configuration failures, CrashLoopBackOff, and OOM are terminal; unknown or inaccessible evidence never extends the deadline. Raw Pod error messages may contain private registry URLs, so only categorized reason codes are saved.

`RELEASETRACKER_READINESS_PULL_GRACE_SECONDS` defaults to 1800 seconds and accepts 0–3600 (for example, `3600` is one hour; 0 disables). A restart affects **newly submitted** work only. Previously failed tasks are not replayed; use the task page's read-only readiness recheck. At the deadline, current evidence is checked again. Executor identity and a workload template fingerprint prevent crediting someone else's update as this deployment, while pure scaling is not mistaken for a different template. Full new-replica availability can supersede a stale Kubernetes `ProgressDeadlineExceeded` condition. Immutable `@sha256:` references also compare actual Pod manifest digests across all target replicas; missing Pod RBAC or unrecognizable ImageIDs cannot be reported as success. Mutable tags cannot prove that a republished tag still refers to the originally selected build; use digest references when exact provenance matters.

If the rollout becomes ready during grace, the original task, executor and run are marked successful while preserving initial delay/failure evidence. Only expiration without readiness produces a final timeout. Manual recheck is read-only and reconciles a failed run only if it is still the latest, unreplaced run. An unavailable image under a Deployment `Recreate` strategy can cause an outage; the diagnostics panel shows a prominent warning. Pair this with cluster alerting rather than treating grace as an availability guarantee. Automatic grace currently applies only to new Kubernetes Deployments, not StatefulSets, DaemonSets, Helm or other runtimes. Operators should assess live replicas and application state before considering rollback.

## Snapshots and retention {#snapshots}

Supported destructive updates capture reconstruction configuration before mutation; see [support coverage](../reference/support.md#runtimes). Snapshots do not guarantee stable container / Pod IDs and do not contain persistent volumes, business databases, or application migration results.

Select a snapshot in executor history and check its time and image. Locked snapshots are excluded from pruning and cannot be deleted directly. Active rollbacks protect snapshots in use. Configure retention in [System settings](../reference/settings.md).

The list and detail **APIs** expose integrity status:

| Status | Meaning |
| --- | --- |
| `verified` | Content matches persisted format version, SHA-256, and size metadata |
| `legacy_unverified` | An older snapshot lacks metadata; neither proof of corruption nor verification |
| `invalid` | Validation failed; it cannot be used for recovery |

New snapshots have a **2 MiB** canonical JSON limit. SHA-256 is a consistency check, not a digital signature; it does not protect against a database writer changing both data and digest.

## Preview a rollback (API) {#preview}

There is currently no dedicated preview button. Call with an administrator JWT. `RT_BASE` is the external instance URL including any sub-path; replace `EXECUTOR_ID` and the snapshot ID with actual values:

```bash
curl --fail-with-body -X POST \
  "$RT_BASE/api/executors/$EXECUTOR_ID/rollback/preview" \
  -H "Authorization: Bearer $RT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"snapshot_id": 42}'
```

Omit the body to select the latest snapshot. Inspect `snapshot_valid`, `integrity_status`, and `validation_error`; HTTP success alone does not mean validation passed. Preview creates no run record, claims no snapshot, and performs no runtime mutation.

Preview checks integrity and adapter compatibility. It does not simulate recovery or guarantee that images remain available, application data is compatible, or a later rollback will succeed.

## Recover a target {#rollback}

1. Pause automatic policies that could trigger another update, and inspect runtime state.
2. Confirm that the application supports downgrades and back up affected application data.
3. Select a usable snapshot, preview it, then trigger manual rollback in executor details.
4. Inspect the rollback run and independently verify application health.

If no snapshot exists, use the platform recovery path in the [support matrix](../reference/support.md#runtimes) rather than repeating rollback requests. Restoring the ReleaseTracker instance database is different from executor rollback; see [Backup recovery](../operations/backup-and-upgrade.md#restore).
