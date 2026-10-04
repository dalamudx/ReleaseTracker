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

## Optional environment variables {#environment}

Most configuration happens in the UI. These variables tune the deployment and apply at startup:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELEASETRACKER_DB_PATH` | `data/releases.db` | Database path; the key file and `backups` directory live beside it automatically |
| `RELEASETRACKER_CORS_ORIGINS` | Empty (same-origin only) | Comma-separated explicit HTTP(S) origins; `*` is not allowed |
| `RELEASETRACKER_WORKER_POLL_SECONDS` | `2` | Background task poll interval (1–60); higher values save idle resources but delay deployments, notifications, etc. |
| `RELEASETRACKER_DEPLOYMENT_CONCURRENCY` | `1` | Concurrency slots shared by deployments and restores (1–3); the same target or executor still runs serially |
| `RELEASETRACKER_READINESS_PULL_GRACE_SECONDS` | `1800` | Kubernetes image pull grace, see [Health checks](../guides/health-and-rollback.md#image-pull) |
| `RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS` | `0` | Post-deployment monitoring, see [Read-only monitoring](../guides/health-and-rollback.md#runtime-watch) |
| `RELEASETRACKER_METRICS_TOKEN` | Empty | Enables `/metrics`, see [Metrics](../operations/backup-and-upgrade.md#metrics) |

For backup variables see [Online backup](../operations/backup-and-upgrade.md#online-backup). Back up and watch resource usage before raising concurrency; it does not add multi-instance support.

## Sign-in and session security {#security}

- Sign-in is limited to 10 failed requests per minute per client address (100 per minute globally); on `429` retry after `Retry-After`. Behind a proxy, configure Uvicorn's trusted proxy addresses so clients can be told apart.
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
