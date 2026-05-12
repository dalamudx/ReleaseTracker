---
title: Executors
---

# Executors

Executors connect a tracked release channel to a concrete runtime target, then update container images or Helm chart versions according to the policy you choose.

## 1. What you choose when creating an executor

In **Executors → New**, configuration is completed step by step:

- **Runtime connection**: choose a Docker, Podman, Portainer, or Kubernetes connection. ReleaseTracker discovers update targets from that connection.
- **Target**: choose a discovered container, Docker Compose project, Portainer stack, Kubernetes workload, or Helm release.
- **Version source and release channel**: choose a tracker image source or Helm chart source, then choose the release channel to follow.
- **Service bindings**: for multi-service targets such as Compose, Portainer stacks, and Kubernetes workloads, bind each service / container to the matching version source and release channel.
- **Execution policy**: choose Manual, Maintenance window, or Immediate automatic execution.
- **Image strategy and target strategy**: decide whether updates keep the current image name or use the tracked image name, and whether ReleaseTracker prefers immutable image references or version tags.
- **Post-update health check**: optional verification before a run is marked successful.

Disabling an executor keeps the configuration but stops automatic runs and prevents manual runs.

## 2. Supported target types

| UI target type | Compatible runtime connections | Applies to |
| -------------- | ------------------------------ | ---------- |
| Container | Docker, Podman | Single container |
| Docker Compose project | Docker, Podman | One or more services in a Compose project |
| Portainer stack | Portainer | Portainer standalone stack |
| Kubernetes workload | Kubernetes | Deployment / StatefulSet / DaemonSet |
| Helm release | Kubernetes | Helm 3 release |

If the target type and runtime connection do not match, saving is rejected; go back to target selection and choose a compatible target.

## 3. Bindable version sources

The executor source selector only shows sources that can directly produce an update target: **container image sources** and **Helm chart sources**. GitHub, GitLab, and Gitea releases / tags can still provide version views and changelog text, but they do not directly drive executor updates today.

## 4. Execution policies

| UI option | Behaviour |
| --------- | --------- |
| Manual | Not scheduled. Runs only when an operator clicks **Run now**. |
| Maintenance window | Checks automatically, but applies updates only inside the configured local maintenance window; triggers outside the window are skipped and recorded in run history. |
| Immediate | Runs automatically as soon as a newer target version is detected. |

Maintenance windows interpret allowed days and start / end times in the timezone configured in **System Settings**.

## 5. Image strategy and target strategy

- **Image strategy**
  - **Keep current image name** (default): keep the target runtime's current image repository and only replace the version with the tracker's current version. Example: current image `ghcr.io/owner/app:1.2.0` + tracker version `1.3.0` → new image `ghcr.io/owner/app:1.3.0`.
  - **Use tracked image name**: use the image name and version from the selected source.
- **Target strategy**
  - **Prefer immutable image** (default): use an image reference with an `@sha256:...` digest when available for more reproducible deployments.
  - **Prefer version tag**: use tag references only. Easier for registries that do not return stable digests, at the cost of content-addressing guarantees.

## 6. Snapshots and rollback

Full runtime configuration snapshots / restores are used only for destructive Docker / Podman recreate targets:

- Docker / Podman single containers
- Docker / Podman Compose grouped updates

These targets persist enough runtime configuration before updates to rebuild containers, networks, volumes, ports, labels, and related settings during manual rollback. Failed updates and failed health checks do not trigger automatic rollback; operators choose whether to restore from an available snapshot. Podman Compose rollback resolves the current pod and runtime objects from stable container / service names; do not assume container IDs or pod IDs remain stable across updates.

Portainer stacks, Kubernetes workloads, and Helm releases are not treated as full ReleaseTracker-managed runtime snapshot targets: Portainer stacks update through the declarative stack-file API, Kubernetes workloads patch image fields on Deployment / StatefulSet / DaemonSet objects, and Helm releases use Helm 3 upgrade flow and release history.

Snapshot retention is controlled by **System Settings → Executor snapshot retention** (default `10`). The snapshot history panel is shown only for destructive snapshot-capable executors; snapshot entries support rollback and deletion with confirmation.

## 7. Health checks

Each executor can configure a post-update health check strategy. Common strategies include:

- **Disabled**: skip post-update probing.
- **Auto (recommended) / Runtime native readiness**: prefer runtime-native health information; Docker / Podman can read container healthchecks or fall back to runtime state.
- **Manual HTTP probe**: probe the configured host, port, path, scheme, method, status codes, body regex, headers, and TLS options.
- **Manual TCP probe**: probe the configured host and port.
- **Helm release status**: the default for Helm releases, based on Helm status checks.

Defaults:

| Target type | Default |
| ----------- | ------- |
| Container / Docker Compose project / Portainer stack / Kubernetes workload | Auto (recommended) |
| Helm release | Helm release status |

The default template uses a 15-second grace period, 10-second attempt timeout, 5-second interval, 180-second total probe duration, and marks the run as failed on health-check failure. Probe window, attempt timeout, and interval values are bounded; manual HTTP / TCP strategies require an explicit host reachable from the ReleaseTracker backend.

!!! note "Health checks are still evolving"
    The Docker / Podman single-container path is wired for post-update health checks and failure policies. Grouped update pipelines (Compose / Portainer stack / Kubernetes workload / Helm release) are still being wired into health checks, so Kubernetes, Portainer, and Helm should not be documented as arbitrary host-port probe targets. Health-check failures mark the run failed or degraded according to policy, but rollback remains a manual UI/API action.

## 8. Run statuses

- **Success**: the update completed and (if configured) the health check passed.
- **Failed**: the update itself failed, or the selected failure policy marked the run as failed after a health-check failure.
- **Skipped**: the target was already on the desired version, the executor is disabled, outside a maintenance window, or a precondition was missing.

Grouped targets (Compose / Stack / Workload) aggregate per-service outcomes into diagnostics details to aid troubleshooting.

## 9. Run history

- **Executors → Run history** shows the starting version, target version, status, message, and diagnostics details for each run.
- **Clear history** on the detail page removes log entries (it does not remove snapshots).

## 10. Common issues

!!! failure "Saving says the target type and runtime do not match"
    The runtime connection type and target type are incompatible. Cross-check the matrix in section 2.

!!! failure "A tracker source isn't listed when binding"
    Only container image sources and Helm chart sources can drive executors. Git-platform sources are not currently supported.

!!! failure "The Rollback button is disabled, or rollback returns 404 for Kubernetes / Portainer stack / Helm release executors"
    These targets are not full runtime snapshot targets, so ReleaseTracker snapshot rollback is not available. Recover using native tooling such as `kubectl rollout undo`, Helm rollback, or the Portainer UI.

!!! info "Container or pod IDs changed after Compose rollback"
    Docker / Podman Compose rollback rebuilds runtime objects from the snapshot, so container IDs or pod IDs may change. Podman Compose resolves the current pod / container from stable names instead of relying on old pod IDs.

!!! failure "A Maintenance window executor hasn't run in a long time"
    - Confirm the System Settings timezone matches the operational timezone; windows are interpreted in that timezone.
    - Check the allowed days field — leaving it blank means "all days"; unintended matching usually comes from this.
    - Run history marks whether a trigger was inside or outside the window.
