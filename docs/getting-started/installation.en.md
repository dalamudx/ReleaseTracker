---
title: Installation
---

# Installation

This page covers the three deployment paths for ReleaseTracker: Docker, Docker Compose, and local development. Docker or Docker Compose is recommended for production.

## 1. Prerequisites

| Item | Requirement | Notes |
| ---- | ----------- | ----- |
| Operating system | Linux x86_64 | The official image is currently published for `linux/amd64` only. |
| Port | `8000` | Frontend assets and the API share this port, optionally fronted by a reverse proxy. |
| Persistent volume | Mounted at `/app/backend/data` | Holds the SQLite database and the system keys. |
| Outbound network | Access to upstream services (GitHub / GitLab / … and OCI registries) | Required during version scans. |

Local development additionally requires Python 3.12+, Node.js 20+, `npm`, and `uv`.

!!! warning "Always configure a persistent volume"
    Running without a volume works, but every container recreation wipes the database and `system-secrets.json`. Losing `system-secrets.json` makes every encrypted credential, OIDC client secret, and runtime connection secret **permanently undecryptable**.

## 2. Docker

=== "Single container"

    ```bash
    mkdir -p ./data

    docker run -d \
      --name releasetracker \
      -p 8000:8000 \
      -v $(pwd)/data:/app/backend/data \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    ```

=== "Docker Compose"

    `docker-compose.yml`:

    ```yaml
    services:
      releasetracker:
        image: ghcr.io/dalamudx/releasetracker:latest
        container_name: releasetracker
        ports:
          - "8000:8000"
        volumes:
          - ./data:/app/backend/data
        restart: unless-stopped
        command: migrate-and-serve
    ```

    Start:

    ```bash
    docker compose up -d
    ```

### Entry commands

The image supports three entry commands:

| Command | Behaviour | When to use |
| ------- | --------- | ----------- |
| `serve` | Starts the application without running migrations. | When the schema is known to be up to date. |
| `migrate` | Runs database migrations only. | To migrate ahead of a cut-over and separate migration cost from startup. |
| `migrate-and-serve` | Runs migrations, then starts the application. | Recommended default for both fresh installs and upgrades. |

Once the application is up, the log should contain entries similar to:

```
SQLite persistent connection established with WAL mode enabled
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 3. First login

Open <http://localhost:8000> (or the address exposed by the reverse proxy). A default administrator account is created on first launch:

| Username | Password |
| -------- | -------- |
| `admin` | `admin` |

!!! danger "Change the default password immediately"
    After signing in, open the **user menu at the bottom-left of the sidebar → User Settings → Change Password** and set a strong password. An instance exposed to the internet with default credentials can be taken over by anyone.

## 4. Quick start

After the service is installed and reachable, use this workflow for the first setup:

1. **Open the web UI and sign in**: Visit the deployed address and sign in with the default administrator account. Change the default password immediately after the first login.
2. **Review System Settings**: Open **System Settings** and confirm that BASE URL, language, log level, retention, and other basic settings match the deployment. When using a reverse proxy or OIDC, BASE URL must match the externally reachable address.
3. **Add notification channels**: Open **Notifications** and configure and test Webhook or other notification channels. Notifications are optional, but recommended before enabling Immediate or Maintenance window execution so failures, skips, and successes are visible.
4. **Add credentials only when needed**: Add credentials under **Credentials** only for private repositories, protected GitHub / GitLab / Gitea projects, private image registries, Kubernetes, Portainer, or similar protected services. Public sources can be tracked without credentials.
5. **Add runtime connections**: If ReleaseTracker will run updates, add Docker, Podman, Kubernetes, or Portainer connections under **Runtime Connections**. Helm release executors use Kubernetes connections. Skip this when you only need version tracking.
6. **Add trackers and release sources**: Add projects, images, or Helm charts under **Trackers**, then configure release channels or source filters as needed.
7. **Create executors**: Create executors from supported version sources, choose the target runtime and update target, then select an execution policy. Start with manual execution to validate the setup before enabling Immediate or Maintenance window execution.
8. **Configure health checks**: Add HTTP, TCP, Helm status, or runtime-native health checks when they are available for the target and the service needs availability verification. A failed health check records a failed run, but it does not trigger automatic rollback.
9. **Run once or wait for scheduling**: Run an executor manually for validation, or wait for trackers and executors to run on their schedules.
10. **Review history, snapshots, and rollback**: Review each run in execution history. Destructive Docker / Podman single-container and Docker / Podman Compose grouped targets keep pre-update snapshots; update or health-check failures do not roll back automatically, so operators must manually trigger rollback after confirming the snapshot is suitable.

## 5. Data directory layout

The volume mounted at `/app/backend/data` typically contains:

```
data/
├── releases.db                 # SQLite main database
├── releases.db-shm             # SQLite WAL shared memory
├── releases.db-wal             # SQLite WAL write-ahead log
└── system-secrets.json         # JWT signing key + Fernet encryption key
```

Backup guidance:

- **Back up the directory as a unit.** `.db-wal` / `.db-shm` and the main database form a set; copying them independently can yield an inconsistent snapshot.
- **`system-secrets.json` and the database must travel together.** Without the keys, the encrypted columns (credentials, OIDC client secrets, runtime connection secrets) cannot be decrypted.
- For production backups, stop the container (`docker compose stop` or `docker stop releasetracker`) before performing file-level copies.

## 6. Reverse proxy (optional but recommended)

ReleaseTracker is typically placed behind Nginx / Traefik / Caddy for HTTPS, access control, or sub-path hosting.

=== "Nginx"

    ```nginx
    server {
        listen 443 ssl http2;
        server_name releases.example.com;

        ssl_certificate     /etc/letsencrypt/live/releases.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/releases.example.com/privkey.pem;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host              $host;
            proxy_set_header X-Real-IP         $remote_addr;
            proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 300s;
        }
    }
    ```

=== "Caddy"

    ```caddy
    releases.example.com {
        reverse_proxy 127.0.0.1:8000
    }
    ```

=== "Traefik v2/v3"

    ```yaml
    services:
      releasetracker:
        image: ghcr.io/dalamudx/releasetracker:latest
        restart: unless-stopped
        volumes:
          - ./data:/app/backend/data
        command: migrate-and-serve
        labels:
          - "traefik.enable=true"
          - "traefik.http.routers.rt.rule=Host(`releases.example.com`)"
          - "traefik.http.routers.rt.entrypoints=websecure"
          - "traefik.http.routers.rt.tls.certresolver=letsencrypt"
          - "traefik.http.services.rt.loadbalancer.server.port=8000"
    ```

After deployment, set **System Settings → Global Settings → BASE URL** to match the public address, for example `https://releases.example.com`. For sub-path deployments (e.g. `https://example.com/releasetracker`), the BASE URL must include the full sub-path. BASE URL drives the OIDC callback:

```text
{BASE URL}/auth/oidc/{provider}/callback
```

A mismatched BASE URL most commonly surfaces as OIDC logins redirecting to the wrong host or failing with `redirect_uri_mismatch`.

## 7. Upgrades

1. Stop the running container: `docker compose stop`, or `docker stop releasetracker`.
2. Back up the `./data/` directory (see section 5).
3. Pull the new image: `docker compose pull`, or `docker pull ghcr.io/dalamudx/releasetracker:latest`.
4. Start again: `docker compose up -d`. The `migrate-and-serve` entry command runs dbmate migrations before the server boots.
5. Follow the logs to confirm the migration completed: `docker compose logs -f`.

!!! note "About downgrades"
    dbmate migrations are forward-only. After a new version's migrations have been applied, reverting to an older container may refuse to start due to schema mismatch; recovery requires restoring from the backup taken in step 2.

## 8. Local development

For contributors and local debugging only.

```bash
git clone https://github.com/dalamudx/ReleaseTracker.git
cd ReleaseTracker

make install       # Install backend + frontend dependencies (requires uv and npm)
make dev           # Run backend + frontend in parallel
```

Default ports:

- Frontend (Vite): <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Swagger UI / ReDoc: <http://localhost:8000/docs>, <http://localhost:8000/redoc>

In development, Vite proxies `/api` to the backend, so only the frontend port needs to be visited from the browser.

## 9. Common deployment issues

!!! failure "Container cannot write to `data/`"
    The container runs as `root` by default, so permission errors are uncommon. If deploying with rootless Docker, SELinux, or another runtime that restricts write access, make sure the mount is writable for the process, for example:
    ```bash
    chmod -R u+rwX ./data
    ```

!!! failure "OIDC login fails with `redirect_uri_mismatch`"
    - Verify that **System Settings → Global Settings → BASE URL** matches the callback prefix registered at the IdP exactly.
    - Sub-path deployments must include the full sub-path.
    - Changes to the BASE URL only take effect after signing out and back in.

!!! failure "The UI loads through the reverse proxy but API calls return 404"
    Usually the proxy is not forwarding `/api/*`, or is stripping a path prefix. ReleaseTracker serves the API and static assets from the same FastAPI process, so `/` and `/api` should use identical `proxy_pass` without path rewriting.

!!! failure "Container keeps restarting after an upgrade"
    Run the `migrate` entry once to inspect the migration output:
    ```bash
    docker run --rm \
      -v $(pwd)/data:/app/backend/data \
      ghcr.io/dalamudx/releasetracker:latest migrate
    ```
    Schema conflicts typically indicate the database was modified by hand; restore from backup.

## 10. Next steps

After installation, see the [Configuration guide](../configuration/flow.md) for the full walkthrough of trackers, credentials, and executors.
