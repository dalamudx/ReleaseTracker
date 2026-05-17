---
title: ReleaseTracker Wiki
---

# ReleaseTracker

<div class="grid cards" markdown>

-   :material-rocket-launch-outline: **Get started**

    ---

    Deploy with Docker or Docker Compose.

    [:octicons-arrow-right-24: Installation](getting-started/installation.en.md)

-   :material-source-branch: **Source**

    ---

    Hosted on GitHub. Issues and pull requests welcome.

    [:octicons-arrow-right-24: GitHub](https://github.com/dalamudx/ReleaseTracker)

</div>

## What it is

ReleaseTracker is a lightweight, configurable release tracking and update orchestration tool. It tracks releases and tags from GitHub, GitLab, Gitea, Helm charts, and OCI container registries, and maps version changes to runtime targets such as Docker, Podman, Portainer, Kubernetes, and Helm releases.

## Who it is for

- **Ops / DevOps**: teams tracking upstream dependency versions and rolling updates into their own environments on a schedule.
- **Self-hosting administrators**: operators who want a single console to manage upgrades across multiple Docker / Kubernetes / Helm deployments.

!!! info "This Wiki is under construction"
    Available: Installation, System Settings, Credentials, Runtime Connections, Notifications, Trackers, Executors, Known Limitations. More chapters (operations, FAQ) will follow.

## Core capabilities

- **Multi-source release tracking**: GitHub, GitLab (including self-hosted instances), Gitea, Helm charts, Docker Hub, GHCR, and private OCI registries.
- **Aggregate trackers**: bind multiple sources under a single tracker; filter, merge, and display releases through release channel rules.
- **Executor orchestration**: target discovery, binding, manual / scheduled execution, maintenance windows, and run history for containers, Compose projects, Portainer stacks, Kubernetes workloads, and Helm releases.
- **Snapshot and rollback (selected executors)**: full runtime configuration snapshots / restores are used for destructive Docker / Podman recreate targets: single containers and Compose grouped updates. Portainer stacks, Kubernetes workloads, and Helm releases rely on declarative updates, version history, or run history rather than full ReleaseTracker-managed runtime snapshots.
- **Security**: local users plus JWT and OIDC; sensitive data is encrypted with Fernet; system keys are rotatable.
- **Web UI configuration**: timezone, log level, release history retention, BASE URL, and key rotation are all handled from the browser without environment variables.

## Next steps

- Follow [Installation](getting-started/installation.en.md) to deploy ReleaseTracker.
- Change the default administrator password after first login.