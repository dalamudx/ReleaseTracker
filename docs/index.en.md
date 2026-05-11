---
title: ReleaseTracker Wiki
---

# ReleaseTracker

<div class="grid cards" markdown>

-   :material-rocket-launch-outline: **Get Started**

    ---

    Launch with a single Docker command or via Docker Compose.

    [:octicons-arrow-right-24: Installation](getting-started/installation.en.md)

-   :material-source-branch: **Source**

    ---

    Hosted on GitHub. Issues and PRs welcome.

    [:octicons-arrow-right-24: GitHub](https://github.com/dalamudx/ReleaseTracker)

</div>

## What it is

ReleaseTracker is a lightweight, configurable release tracking and update orchestration tool. It tracks releases and tags from GitHub, GitLab, Gitea, Helm charts, and OCI container registries, and maps version changes to runtime targets such as Docker, Podman, Portainer, Kubernetes, and Helm.

## Who it's for

- **Ops / DevOps**: track upstream dependency versions and roll updates into your environments on a schedule.
- **Self-hosters**: a single panel to manage upgrades across multiple Docker / K8s / Helm deployments.
- **Platform teams**: stitch version discovery, approval, execution, and rollback into an auditable flow.

!!! info "This Wiki is a work in progress"
    Only the skeleton and the Installation page are live for now. Configuration details, tracker / executor deep-dives, operations guides, and FAQs will follow.

## Capabilities at a glance

- **Multi-source tracking**: GitHub, GitLab (incl. self-hosted), Gitea, Helm charts, Docker Hub, GHCR, private OCI registries.
- **Aggregate trackers**: bind multiple sources under one tracker; filter, merge, and display via release channel rules.
- **Executor orchestration**: discover, bind, and run updates against containers, Compose projects, Portainer stacks, Kubernetes workloads, and Helm releases — manually, on schedule, or in maintenance windows, with full run history.
- **Snapshot & rollback**: pre-update snapshots with rollback and health-check-driven recovery.
- **Security**: local users + JWT + OIDC; Fernet-encrypted secrets; rotatable system keys.
- **Web UI configuration**: timezone, log level, history retention, BASE URL, key rotation — all from the browser; no environment variables required.

## Next steps

- Follow the [Installation](getting-started/installation.en.md) guide to bring it up.
- Change the default admin password immediately after first login.
- Watch for upcoming **Configuration / Operations** pages.
