---
title: Executors
---

# Executors

Executors bind a tracker's current version to a concrete runtime target and apply updates according to the configured mode.

## 1. Binding model

```text
ExecutorConfig
├── runtime_type            # docker / podman / kubernetes / portainer
├── runtime_connection_id   # Reference into Runtime Connections
├── tracker_name            # Aggregate tracker name
├── tracker_source_id       # The specific source under that tracker
├── channel_name            # Release channel to bind
├── enabled                 # Disabling keeps the config but skips execution
├── update_mode             # manual / maintenance_window / immediate
├── target_ref              # Runtime-specific target reference
├── service_bindings[]      # For grouped target modes
├── image_selection_mode    # How the new image is derived
├── image_reference_mode    # Whether the emitted reference uses a tag or a digest
├── maintenance_window      # Required when update_mode=maintenance_window
└── health_check            # Post-update health check profile
```

## 2. Supported target modes

| `target_ref.mode` | Compatible `runtime_type` | Applies to |
| ----------------- | ------------------------- | ---------- |
| `container` | `docker`, `podman` | Single container |
| `docker_compose` | `docker`, `podman` | Compose project |
| `portainer_stack` | `portainer` | Portainer standalone stack |
| `kubernetes_workload` | `kubernetes` | Deployment / StatefulSet / DaemonSet |
| `helm_release` | `kubernetes` | Helm 3 release |

Unsupported `(runtime_type, mode)` combinations are rejected by the Pydantic validator with a 400 response at save time.

## 3. Version source restriction

Only trackers whose source `source_type ∈ { container, helm }` can be bound to executors. This is enforced by the `EXECUTOR_BINDABLE_SOURCE_TYPES` constant. Git-platform sources (GitHub / GitLab / Gitea) do not drive executor updates directly.

## 4. Update modes

| `update_mode` | Behaviour |
| ------------- | --------- |
| `manual` | Not scheduled. Only runs when **Run now** is clicked. |
| `maintenance_window` | Runs on schedule only inside the configured local maintenance window; runs outside the window are skipped with `outside maintenance window` in history. |
| `immediate` | Runs as soon as the tracker produces a higher target version. |

`maintenance_window` interprets times in the timezone configured in System Settings.

## 5. Image selection

- **`image_selection_mode`**
  - `replace_tag_on_current_image` (default): keep the target runtime's current image repository and only replace the tag with the tracker's current version. Example: current image `ghcr.io/owner/app:1.2.0` + tracker version `1.3.0` → new image `ghcr.io/owner/app:1.3.0`.
  - `use_tracker_image_and_tag`: use the tracker source's configured `registry + image` as the new repository and the tracker's current version as the tag.
- **`image_reference_mode`**
  - `digest` (default): emit the image reference with `@sha256:...` digest for reproducible deployments.
  - `tag`: use tag references only. Easier for registries that don't return stable digests, at the cost of content-addressing guarantees.

## 6. Snapshots and rollback

Pre-update snapshots are captured **only** for:

- `container` (Docker / Podman single containers)
- `helm_release`

The other modes (`docker_compose`, `portainer_stack`, `kubernetes_workload`) do **not** capture pre-update snapshots. `POST /api/executors/{id}/rollback` therefore cannot roll these modes back (it returns `404` without an available snapshot). See [Known Limitations](../limitations.en.md).

Snapshot retention is controlled by `executor_snapshot_retention_count` (default `10`).

## 7. Health checks

Each executor can configure a `health_check` profile. Allowed strategies per target mode:

| Mode | Allowed strategies |
| ---- | ------------------ |
| `container` / `docker_compose` / `portainer_stack` / `kubernetes_workload` | `none`, `auto`, `runtime_native`, `manual_http`, `manual_tcp`, `http`, `tcp` |
| `helm_release` | `none`, `auto`, `helm_status`, `runtime_native`, `manual_http`, `manual_tcp`, `http`, `tcp` |

Defaults (`use_default_strategy=true`):

| Mode | Default |
| ---- | ------- |
| `container` / `docker_compose` / `portainer_stack` / `kubernetes_workload` | `auto` |
| `helm_release` | `helm_status` |

The default template uses `grace_period_seconds=15`, `attempt_timeout_seconds=10`, `interval_seconds=5`, `probe_window_seconds=180`, `failure_policy=mark_failed`.

!!! note "Health checks are still evolving"
    The probe framework is in place, but automatic recovery (rollback) only works end-to-end where both snapshots and a runtime-native probe are available. `docker_compose` / `portainer_stack` / `kubernetes_workload` with `failure_policy=mark_failed_and_recover` still cannot roll back because they never captured a snapshot; the run is simply reported as failed.

## 8. Run statuses

- `success`: the update completed and (if configured) the health check passed.
- `failed`: the update itself failed, or the health check failed under `mark_failed`.
- `skipped`: the target was already on the desired version, the executor is disabled, outside a maintenance window, or a precondition was missing.

Grouped targets (compose / stack / workload) aggregate per-service outcomes into the `diagnostics` field to aid troubleshooting.

## 9. Run history

- **Executors → Run history** shows the from/to versions, status, message, and diagnostics JSON for each run.
- **Clear history** on the detail page removes log entries (it does not remove snapshots).

## 10. Common issues

!!! failure "Saving returns `target_ref.mode '...' is only supported when runtime_type is '...'`"
    The runtime type and target mode don't match. Cross-check the matrix in section 2.

!!! failure "A tracker source isn't listed when binding"
    Only `container` / `helm` sources can drive executors. Git-platform sources are not currently supported.

!!! failure "The Rollback button is disabled, or rollback returns 404 for Kubernetes / Compose / Portainer stack executors"
    Those modes do not capture snapshots, so rollback is not possible from ReleaseTracker. Recover using native tooling such as `kubectl rollout undo` or `docker compose up -d`.

!!! failure "A `maintenance_window` executor hasn't run in a long time"
    - Confirm the System Settings timezone matches the operational timezone; windows are interpreted in that timezone.
    - Check the allowed days field — leaving it blank means "all days"; unintended matching usually comes from this.
    - Runs inside / outside the window are logged explicitly as `inside maintenance window` or `outside maintenance window`.
