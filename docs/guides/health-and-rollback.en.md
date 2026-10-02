---
title: Health checks and rollback
---

# Health checks and rollback

A successful update command does not prove application availability. Health checks validate the result. Rollback is a separate operator-confirmed action; failed updates or probes never trigger automatic rollback.

## Choose a health check {#health-checks}

Production queued deployments use persistent readiness observation; see the [support matrix](../reference/support.md#runtimes) for runtime capabilities and limits. Legacy probe profiles map to the readiness switch. Explicitly disabling readiness clears the legacy strategy, rather than silently running inline checks. The inline runner remains for compatibility callers.

| Strategy | Use when |
| --- | --- |
| Auto | Start with the runtime's available capabilities |
| Runtime native | The container has a healthcheck; running-state fallback is not proof of business readiness |
| Manual HTTP | A public health endpoint exposes status codes or response content |
| Manual TCP | A host/port connection is sufficient; no application protocol is checked |
| Disabled | Validate externally rather than using probes to determine run status |

HTTP hosts must resolve to public addresses. Allowed ports are `80/8080` for HTTP and `443/8443/9443` for HTTPS. Redirects are rejected. Without explicit status codes, the matcher accepts 200–399, but redirect responses are still blocked; prefer explicit values such as `200,204`.

Default readiness observation uses a 600-second overall deadline, 10-second attempt timeout, 5-second interval and 10-second stable window; executors may inherit system defaults or override them. Failure marks a run failed or degraded without undoing the update. New Kubernetes observations also track target Pod UIDs and restart counts: replacement or increased restarts reset the stable window, and inaccessible Pods cannot prove stable readiness. Pull-grace deadline rechecks must also pass any configured HTTP/TCP application probe.

## Failure policy {#failure-policy}

Readiness observation honors the executor failure policy. `Mark failed` fails the task; `Mark degraded` keeps the update applied and finishes the task as “Deployment marked degraded”, with a `degraded:` run message so it is distinguishable from a hard failure (target changes or unobservable targets still require review). Failed HTTP/TCP application probes keep a categorized reason (timeout, connection refused, unexpected status, …) and the HTTP status code, never URLs or response bodies.

## Read-only observation after Kubernetes image pull failures {#image-pull}

For newly submitted Kubernetes Deployments, ReleaseTracker reads waiting reasons on Pods matching the submitted workload and image. Confirmed transient pull timeouts, connection interruptions, or registry 5xx responses are checked **again at the original readiness deadline**; only a still-retrying pull receives a read-only grace period of up to one hour. No deployment is retried and no mutation attempt is spent. Checks run every 30–120 seconds during grace, with a deadline preserved across ReleaseTracker restarts. Missing/unauthorized images, container configuration failures, CrashLoopBackOff, and OOM are terminal; unknown or inaccessible evidence never extends the deadline. Raw Pod error messages may contain private registry URLs, so only categorized reason codes are saved.

`RELEASETRACKER_READINESS_PULL_GRACE_SECONDS` defaults to 1800 seconds and accepts 0–3600 (for example, `3600` is one hour; 0 disables). A restart affects **newly submitted** work only. Previously failed tasks are not replayed; use the task page's read-only readiness recheck. At the deadline, current evidence is checked again. Executor identity and a workload template fingerprint prevent crediting someone else's update as this deployment, while pure scaling is not mistaken for a different template. Full new-replica availability can supersede a stale Kubernetes `ProgressDeadlineExceeded` condition. Immutable `@sha256:` references also compare actual Pod manifest digests across all target replicas; Pods must belong to the controller; Deployments also verify the ReplicaSet ownership chain, not merely matching labels. Multi-platform image indexes may legitimately run a child platform manifest. Membership is verified through digest-pinned manifests using source authentication/network policy and body SHA256. Unavailable index evidence, incomplete Pod/ReplicaSet RBAC, or unrecognizable ImageIDs cannot be reported as success. DaemonSets use desired scheduled node counts; listings over 100 Pods/ReplicaSets or incomplete pagination remain unknown. Mutable tags cannot prove that a republished tag still refers to the originally selected build; use digest references when exact provenance matters.

If the rollout becomes ready during grace, the original task, executor and run are marked successful while preserving initial delay/failure evidence. Only expiration without readiness produces a final timeout. Manual recheck is read-only and reconciles a failed run only if it is still the latest, unreplaced run. An unavailable image under a Deployment `Recreate` strategy can cause an outage; the diagnostics panel shows a prominent warning. Pair this with cluster alerting rather than treating grace as an availability guarantee. Automatic grace currently applies only to new Kubernetes Deployments, not StatefulSets, DaemonSets, Helm or other runtimes. Operators should assess live replicas and application state before considering rollback.

## Read-only post-deployment monitoring {#runtime-watch}

Set `RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS` to opt in: default `0` disables, enabled values are 300–86400 seconds. Only the latest non-skipped successful run with persisted target evidence is eligible, with an enabled, unchanged executor. Later no-change checks do not retire that health baseline. Executors without verified readiness, with readiness disabled, or legacy data lacking target evidence are skipped. Scheduling runs every minute with at most 16 due targets per pass; the interval is a minimum, not a real-time SLA. Active deployments/recovery, waiting observations, operator-blocked shared targets and unreviewed backup restores are skipped. Configuration and run identity are rechecked before and after network I/O.

Monitoring includes configured HTTP/TCP probes. Three consecutive unavailable, drifted, unhealthy, or restarting/replaced-Pod checks enqueue a durable notice to error-event subscribers. An incident is deduplicated; a healthy check resets the streak and permits a later incident to alert. State survives restarts. Monitoring never deploys, rolls back, repairs, or rewrites a previously successful deployment as failed. There is no dedicated monitoring UI yet, and this is not a replacement for cluster/application monitoring.

Automatic maintenance windows constrain the **first remote write**: if admission/network reads outlast the window, the first mutation is prevented. Already-started multi-step updates and read-only observation may finish beyond the window rather than leaving a workload half-modified. Manual deployment retains its window override.

## Snapshots and retention {#snapshots}

Supported destructive updates capture reconstruction configuration before mutation; see [support coverage](../reference/support.md#runtimes). Snapshots do not guarantee stable container / Pod IDs and do not contain persistent volumes, business databases, or application migration results.

Select a snapshot in executor history and check its time and image. Locked snapshots are excluded from pruning and cannot be deleted directly. Active rollbacks protect snapshots in use. Clearing/pruning run history retains active runs, the latest actual deployment, and records referenced by tasks, readiness, notifications or snapshots; the list may not become empty. Legacy observations with missing runs require operator attention instead of endlessly retrying finalization. Rollback finalization commits the run, executor summary and notification intent together. Configure retention in [System settings](../reference/settings.md).

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
