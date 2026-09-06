---
title: Installation and first run
---

# Installation and first run {#installation}

Goal: start an instance, sign in, and find a project's versions. Use Docker or Docker Compose for production; see the [README](https://github.com/dalamudx/ReleaseTracker/blob/main/README.en.md#development-commands) for local development.

## Requirements {#1-prerequisites}

- Official image: `linux/amd64`; the application and API share port `8000`.
- Persist `/app/backend/data` in a directory writable by the process.
- Allow outbound access to the upstream services you track.
- Run one instance; do not share its data directory between active replicas.

!!! warning "Keep the database and keys"
    The data directory contains the database and `system-secrets.json`. Reuse it when recreating containers. Losing the key file makes encrypted credentials unreadable.

## Start the container {#2-docker}

These examples bind to the host loopback address for local access or a proxy on the same host. For remote access, configure an [HTTPS proxy](../operations/reverse-proxy.md) rather than exposing the management port directly.

=== "Docker Compose"

    Save as `compose.yml`:

    ```yaml
    services:
      releasetracker:
        image: ghcr.io/dalamudx/releasetracker:latest
        container_name: releasetracker
        ports:
          - "127.0.0.1:8000:8000"
        volumes:
          - ./data:/app/backend/data
        restart: unless-stopped
        command: migrate-and-serve
    ```

    ```bash
    mkdir -p ./data
    docker compose up -d
    ```

=== "Docker run"

    ```bash
    mkdir -p ./data
    docker run -d \
      --name releasetracker \
      -p 127.0.0.1:8000:8000 \
      -v "$(pwd)/data:/app/backend/data" \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    ```

`migrate-and-serve` migrates the database before starting. Other [entry commands](../operations/backup-and-upgrade.md#entry-commands){#entry-commands} are for maintenance. Pin a tested release tag instead of `latest` in production.

## First login {#3-first-login}

1. Open <http://localhost:8000>.
2. Read the one-time `admin` password from the first startup log:

    ```bash
    docker logs releasetracker 2>&1 | grep "one-time bootstrap admin password"
    ```

3. Sign in, then immediately open **User menu → User Settings → Change Password**.

The bootstrap password is logged only during initial setup; restrict log access. Existing installations do not generate another password. For a forgotten or blocked legacy default password, use [local password recovery](../operations/accounts-and-oidc.md#password-recovery).

## First version check {#4-quick-start}

1. Create a **Tracker** with a GitHub source, for example `cli/cli`.
2. Enable the `stable` release channel, select Release, and keep the default fetch settings initially.
3. Save and run a manual check. Confirm that versions, sources, and release notes appear.

Public sources can start with anonymous REST access; add credentials if rate-limited. See [Trackers and version rules](../guides/trackers.md) for filtering and sorting.

## Next steps {#10-next-steps}

- Tracking only: [Webhook notifications](../guides/notifications.md).
- Updating services: [Credentials and runtimes](../guides/runtime-connections.md) → [Executors](../guides/executors.md).
- [Data directory and backups](../operations/backup-and-upgrade.md#backup){#5-data-directory-layout}.
- [Reverse proxy and sub-paths](../operations/reverse-proxy.md){#6-reverse-proxy-optional-but-recommended}.
- [Upgrade the instance](../operations/backup-and-upgrade.md#upgrade){#7-upgrades}.
- [Local development](https://github.com/dalamudx/ReleaseTracker/blob/main/README.en.md#development-commands){#8-local-development}.
- [Deployment troubleshooting](../reference/troubleshooting.md){#9-common-deployment-issues}.
