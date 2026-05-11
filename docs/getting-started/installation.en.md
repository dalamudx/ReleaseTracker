---
title: Installation
---

# Installation

This page covers the three ways to deploy ReleaseTracker: Docker, Docker Compose, and local development. Docker or Docker Compose is recommended for production.

## 1. Prerequisites

| Item | Requirement | Notes |
| ---- | ----------- | ----- |
| Server | Linux x86_64 | The container image currently ships for `linux/amd64` only. |
| Port | `8000` reachable directly or via a reverse proxy | Frontend assets and the API share this port. |
| Persistent volume | Mounted at `/app/backend/data` | SQLite DB and system keys live here. |
| Outbound network | Access to upstream services (GitHub/GitLab/… and OCI registries) | Needed when scanning versions. |

For local development you'll also need Python 3.12+, Node.js 20+, `npm`, and `uv`.

!!! warning "Plan the persistent volume first"
    It will run without a mount, but everything — the database and `system-secrets.json` — is lost when the container is recreated. Losing `system-secrets.json` makes every encrypted value (credentials, OIDC client secrets, runtime connection secrets) **permanently undecryptable**.

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

| Command | Description | When to use |
| ------- | ----------- | ----------- |
| `serve` | Starts the app without running migrations. | When you know the schema is already up to date. |
| `migrate` | Runs database migrations only. | Migrate before a cut-over so startup is fast. |
| `migrate-and-serve` | Migrates, then starts the app. | Recommended default for fresh installs and upgrades. |

You should see something like this on first boot:

```
SQLite persistent connection established with WAL mode enabled
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 3. First login

Open <http://localhost:8000> (or your reverse proxy address). A default administrator is created automatically:

| Username | Password |
| -------- | -------- |
| `admin` | `admin` |

!!! danger "Change the default password immediately"
    First thing after login: open the user menu and set a strong password. Exposing a stock `admin/admin` instance on the internet means anyone can take it over.

## 4. Data directory layout

The volume mounted at `/app/backend/data` holds roughly:

```
data/
├── releases.db                 # SQLite main DB
├── releases.db-shm             # SQLite WAL shared memory
├── releases.db-wal             # SQLite WAL write-ahead log
└── system-secrets.json         # JWT signing key + Fernet encryption key
```

Backup strategy:

- **Back up the whole directory**: the `.db-wal` / `.db-shm` files pair with the main DB; backing up only one of them can yield an inconsistent snapshot.
- **`system-secrets.json` and the DB must travel together**: the DB alone can't decrypt any encrypted column.
- For safest snapshots, stop the container (or force a WAL checkpoint) before copying. In production: `docker compose stop`, copy, then start.

## 5. Reverse proxy (optional but recommended)

Most production deployments run behind Nginx / Traefik / Caddy for HTTPS, access control, or sub-path hosting.

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

After deploying, set the matching public address in `System Settings → Global Settings → BASE URL`, for example `https://releases.example.com`. For sub-path deployments (e.g. `https://example.com/releasetracker`), the BASE URL must include the full sub-path. BASE URL drives the OIDC callback:

```text
{BASE URL}/auth/oidc/{provider}/callback
```

A misconfigured BASE URL typically shows up as OIDC login redirecting to the wrong host or returning `redirect_uri_mismatch`.

## 6. Upgrading

1. Stop the container: `docker compose stop` (or `docker stop releasetracker`).
2. Back up the `./data/` directory.
3. Pull the new image: `docker compose pull` (or `docker pull ghcr.io/dalamudx/releasetracker:latest`).
4. Start: `docker compose up -d` (the `migrate-and-serve` entrypoint auto-applies schema migrations).
5. Watch the logs: `docker compose logs -f`.

!!! tip "Check the release notes before cross-version upgrades"
    Releases that change the schema call it out in the GitHub release notes. dbmate migrations are forward-only — once applied, downgrading to an older version may refuse to start.

## 7. Local development

For contributors and debugging only.

```bash
git clone https://github.com/dalamudx/ReleaseTracker.git
cd ReleaseTracker

make install       # Install backend + frontend deps (needs uv + npm)
make dev           # Run backend + frontend in parallel
```

Default ports:

- Frontend (Vite): <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Swagger UI / ReDoc: <http://localhost:8000/docs>, <http://localhost:8000/redoc>

Vite proxies `/api` to the backend, so you only need to visit the frontend port during development.

## 8. Common deployment issues

!!! failure "Permission error on `system-secrets.json` at startup"
    The container can't write to the data directory. Check host permissions — the container runs as a non-root user (typically UID `1000` depending on the image). Grant it read/write access:
    ```bash
    chown -R 1000:1000 ./data
    ```

!!! failure "OIDC login fails with `redirect_uri_mismatch`"
    - The value of `System Settings → Global Settings → BASE URL` must exactly match the callback prefix registered at your IdP.
    - Sub-path deployments must include the full sub-path.
    - After changing the BASE URL, sign out and back in for it to take effect.

!!! failure "UI loads through the reverse proxy but API calls return 404"
    Usually the proxy isn't forwarding `/api/*`, or it's stripping the path prefix. ReleaseTracker serves the API and static assets from the same FastAPI process — both `/` and `/api` should reach the same upstream with no rewriting.

!!! failure "Container keeps restarting after an upgrade"
    Run the `migrate` entrypoint once to inspect the migration log:
    ```bash
    docker run --rm \
      -v $(pwd)/data:/app/backend/data \
      ghcr.io/dalamudx/releasetracker:latest migrate
    ```
    Schema conflicts usually mean the DB was modified by hand. Restore from backup or rebuild from the official schema.

## 9. Next steps

- Put a reverse proxy in front and set the BASE URL (see section 5).
- Add tokens, OIDC client secrets, and runtime connection secrets under **Credentials**.
- The **Configuration / Trackers / Executors / Operations** pages will follow — check back as they land.
