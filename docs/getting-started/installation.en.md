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
- On Kubernetes, use `GET /api/health/ready` for readiness and `GET /api/health/live` for liveness (port 8000). The image includes a Docker HEALTHCHECK. Set Pod resource requests and limits for your workload.
- Only same-origin requests are allowed by default. For a separate frontend, set `RELEASETRACKER_CORS_ORIGINS` to comma-separated explicit HTTP(S) origins (not `*`). `RELEASETRACKER_DB_PATH` overrides the database path; the key file stays beside it.
- Daily maintenance removes up to 500 fetch runs older than 90 days if no release-history or webhook receipt references them. Tasks and trigger keys remain as audit and deduplication evidence; back up the data directory regularly.
- The image still runs as root: existing volumes may be root-owned. Back up and migrate volume permissions before switching to a non-root `runAsUser`; do not change it on existing Pods without this step.

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

## Kubernetes probes and resources {#kubernetes-probes}

Apply this probe and resource baseline to the container in a single-replica Deployment; it is not a complete deployment manifest. Increase the startup allowance for slow migrations and tune resources for fetch and deployment workloads. Keep `replicas: 1` and `strategy.type: Recreate` to avoid two processes sharing the volume during upgrades.

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

Login endpoints allow 10 unsuccessful requests per client address per minute, with a global budget of 100 per minute. Honor `Retry-After` on `429` responses. The limiter uses the ASGI peer, not arbitrary `X-Forwarded-For` headers; configure trusted proxy addresses in Uvicorn when deploying behind a proxy.

Background workers poll every 2 seconds by default. Set `RELEASETRACKER_WORKER_POLL_SECONDS=5` (integer 1–60) to reduce idle polling at the cost of dispatch, readiness, notification, and webhook latency. Tracker check schedules and daily cleanup are unchanged. This does not enable multiple instances or parallel deployment mutations.

Deployments and recovery are serial by default. Set `RELEASETRACKER_DEPLOYMENT_CONCURRENCY=2` for bounded concurrency (1–3, read at startup), sharing slots between both kinds of work. The same target, executor, and unverified identities remain serialized. This does not enable multiple instances sharing SQLite. Back up, verify target scopes, and observe a maintenance window first; return to `1` if resources or contention are a concern.

Browser sessions use HttpOnly cookies instead of JWTs in localStorage or OIDC URLs. HTTPS uses `Secure`, `__Host-`, and `SameSite=Lax`; local HTTP development uses non-Secure cookies. Configure the production HTTPS base URL and trusted proxy settings so the protocol is inferred correctly. Writes require a session-bound CSRF cookie/header. Refresh rotates tokens; tabs serialize renewal using Web Locks where available, otherwise falling back to per-tab single-flight. Upgrades attempt to migrate the old refresh token into cookies and erase local tokens; expired sessions require sign-in. CLI/API `/api/auth/token` and Bearer authentication remain compatible and do not depend on browser cookies. This mode does not support arbitrary cross-site cookie-authenticated frontends.

Image builds verify fixed SHA256 digests for dbmate and Helm. Overriding `DBMATE_VERSION` or `HELM_VERSION` also requires an independently verified `DBMATE_SHA256` or `HELM_SHA256`.

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
