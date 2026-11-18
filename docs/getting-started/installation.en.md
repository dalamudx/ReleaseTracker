---
title: Installation and first run
---

# Installation and first run {#installation}

Goal: start an instance, sign in, and see versions for a first project. Use Docker or Docker Compose in production; for local development see the [README](https://github.com/dalamudx/ReleaseTracker/blob/main/README.en.md#development-commands).

## Requirements {#1-prerequisites}

- Official image `linux/amd64`; the application and API share port `8000`.
- Persist a writable data directory at `/app/backend/data`; it holds the database, key file and the `backups` directory.
- Run one instance; do not share the data directory between active replicas.
- Allow outbound access to the upstream services you track.
- The image runs as root and existing volumes may be root-owned; back up and migrate volume permissions before switching to non-root.

!!! warning "Keep the database and keys"
    Reuse the data directory when recreating containers. Losing `system-secrets.json` makes encrypted credentials unreadable.

## Start the container {#2-docker}

These examples listen on the host loopback address only, for local access or a reverse proxy on the same host. For remote access configure an [HTTPS proxy](../operations/reverse-proxy.md) first; do not expose the management port directly.

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

## Kubernetes probes and resources {#kubernetes-probes}

Reference container settings for a single-replica Deployment (not a complete manifest). Keep `replicas: 1` with `strategy.type: Recreate` so two processes never use the data volume at once; raise the startup probe budget if migrations are slow.

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: "1"
    memory: 512Mi
startupProbe:
  httpGet:
    path: /api/health/live
    port: 8000
  periodSeconds: 5
  failureThreshold: 60
readinessProbe:
  httpGet:
    path: /api/health/ready
    port: 8000
  periodSeconds: 10
  timeoutSeconds: 3
livenessProbe:
  httpGet:
    path: /api/health/live
    port: 8000
  periodSeconds: 30
  timeoutSeconds: 3
```

## Global settings {#environment}

In **Settings**, configure the public base URL (including any deployment subpath), worker polling, deployment and recovery concurrency, image pull grace, runtime inspections, backups, trusted proxies, CORS origins and the metrics token. New instances use the defaults shown in the form. Before increasing concurrency, back up the instance and check resource usage.

For an existing installation, stop the old service and verify a backup of its database and system secrets. Mount the original data at `/app/backend/data` with the database named `releases.db`, then remove `RELEASETRACKER_DB_PATH`. Run `python -m releasetracker.cli import-legacy-settings` with the old policy environment and the new release before serving traffic. This imports settings once, preserves saved values, and lets you remove the old declarations after validation. Set the public base URL explicitly if required for public links or OIDC. See [Backup and upgrade](../operations/backup-and-upgrade.md).

## Sign-in and session security {#security}

- Sign-in is limited to 10 failed requests per minute per client address (100 per minute globally). On `429`, retry after `Retry-After`. Behind a proxy, enter its address in the global trusted proxies setting to identify individual clients.
- Browser sessions use HttpOnly cookies with CSRF protection; under HTTPS they use `Secure` and the `__Host-` prefix, so production needs a correct BASE URL and trusted proxy configuration.
- CLI/API clients keep using `/api/auth/token` and Bearer tokens without cookies.

## First login {#3-first-login}

1. Open <http://localhost:8000>.
2. Read the one-time `admin` password from the first startup log:

    ```bash
    docker logs releasetracker 2>&1 | grep "one-time bootstrap admin password"
    ```

3. Immediately open **User menu → User settings → Change password**.

The bootstrap password is logged only on first initialization; restrict log access. If you forget the password, use the [local recovery command](../operations/accounts-and-oidc.md#password-recovery).

## First version check {#4-quick-start}

1. Create a **Tracker** with a GitHub source, for example `cli/cli`.
2. Enable the `stable` release channel, select Release, and keep the default fetch settings.
3. Save and run a manual check; confirm that versions, sources and release notes appear.

Public sources work anonymously; add credentials if rate-limited. See [Trackers and version rules](../guides/trackers.md) for filtering.

## Next steps {#10-next-steps}

- Tracking only: [Notifications](../guides/notifications.md).
- Updating services: [Credentials and runtimes](../guides/runtime-connections.md) → [Executors](../guides/executors.md).
- [Data directory and backups](../operations/backup-and-upgrade.md#backup){#5-data-directory-layout}.
- [Reverse proxy and sub-paths](../operations/reverse-proxy.md){#6-reverse-proxy-optional-but-recommended}.
- [Upgrade the instance](../operations/backup-and-upgrade.md#upgrade){#7-upgrades}.
- [Local development](https://github.com/dalamudx/ReleaseTracker/blob/main/README.en.md#development-commands){#8-local-development}.
- [Deployment troubleshooting](../reference/troubleshooting.md){#9-common-deployment-issues}.
