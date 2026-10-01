---
title: Backups, upgrades, and keys
---

# Backups, upgrades, and keys

Run these commands from the deployment directory, assuming data is mounted from `./data`. Before upgrading, record the current image tag or digest and deployment configuration. They form a recovery point together with the data backup.

## Online backups and offline restoration {#online-backup}

Administrators can create and download ZIP archives under **System Settings → Backups** after confirming the sensitive-data warning. Backups use SQLite's online snapshot API and exclude concurrent key rotation. Each archive includes `releases.db`, `system-secrets.json`, and a checksum manifest. Failed backups never prune previous archives. Databases are limited to 2 GiB and snapshots to 120 seconds; larger instances should use the stopped-directory procedure below.

ZIP files are **not encrypted** and include credentials, session state, and encryption keys. Download only over HTTPS and store privately. Archives default to `backups` beside the database, not off-host storage. Keep an externally encrypted off-host copy. Environment variables, Kubernetes Secrets, external kubeconfig/SSH files, and container images are not included; retain deployment configuration and image digests separately.

| Variable | Default | Behavior |
| --- | --- | --- |
| `RELEASETRACKER_BACKUP_INTERVAL_HOURS` | `0` | Disabled at 0; 1–168 hours otherwise. Scheduling resumes from the newest archive, so restarts do not reset it; with no backup or an overdue one, a backup runs about 5 minutes after startup |
| `RELEASETRACKER_BACKUP_RETENTION` | `7` | Keep the newest 1–100 archives after each success; manual and automatic backups share retention |
| `RELEASETRACKER_BACKUP_DIR` | `backups` beside the database | Protected directory; mount separate storage if desired |

Every archive is read back after writing to verify checksums, SQLite integrity and credential decryption; corruption fails the backup. The Backups page shows the last successful backup, consecutive failures with their category, and an “overdue” warning when no backup succeeded within two intervals. Scheduled backup failures alert notifiers subscribed to the “error” event.

Restoration is local CLI only and never overwrites a running database. Validate using the matching application image, stop all instances, then restore to a **nonexistent** directory. Validation checks ZIP members and size limits, SHA256, SQLite integrity, exact migration compatibility, and credential/snapshot decryption. Checksums detect corruption, not authenticity: restore only trusted archives.

```bash
# Run in the matching application environment; containers can use --entrypoint python
python -m releasetracker.cli inspect-backup /backups/BACKUP.zip
# Stop all instances first; --confirm-stopped acknowledges this, it does not stop them
python -m releasetracker.cli restore-backup /backups/BACKUP.zip \
  --destination /restore/new-data --confirm-stopped
```

Keep the old volume and mount the new directory as application data. A custom `RELEASETRACKER_DB_PATH` must point to `releases.db` inside it. Restoration neither starts the instance nor undoes external deployments. Before restarting, isolate executor network access or runtime credentials and review pending work against actual running versions to avoid replaying old tasks. Restoration clears sessions and transient OAuth states so revoked sessions cannot be resurrected; sign in again. The original ZIP is not modified. Restore older data with its matching image first, then upgrade through normal migrations.

## Operational metrics {#metrics}

Set a random `RELEASETRACKER_METRICS_TOKEN` of at least 32 characters to enable `GET /metrics`. Missing/short configuration returns 404. Scrapes require the dedicated `Authorization: Bearer ...` token; ordinary login credentials do not substitute. This token provides only read access to metrics, not administration. Prefer internal-cluster access and a 60-second scrape interval rather than public exposure.

```yaml
scrape_configs:
  - job_name: releasetracker
    scrape_interval: 60s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/releasetracker-metrics-token
    static_configs:
      - targets: [releasetracker.infra.svc:8000]
```

All metrics are gauges and use the `releasetracker_` prefix. `tasks` groups retained tasks (including dismissed ones) by kind/state; `task_oldest_pending_seconds` measures oldest queue age. `task_attempts_24h` and `task_attempt_duration_seconds_24h` give completed attempts and total duration over 24 hours; `task_errors_24h` distinguishes TLS/security errors and groups unknown errors. `fetch_runs_24h` covers successful, partial, failed, and interrupted persisted source runs. Some fetch errors occur before a source run is created: use task attempts for complete failure accounting.

`notification_outbox` and `notification_oldest_pending_seconds` describe notification queues. `backup_last_success_timestamp_seconds` and `backup_failures` are process-local and reset on restart. Labels contain no URLs, credentials, resource names, or arbitrary IDs. Retention can decrease historical values; do not apply `rate()` to these gauges.

Example alerts: `releasetracker_tasks{state="needs_attention"} > 0` for 5 minutes; `releasetracker_notification_oldest_pending_seconds > 600` for 5 minutes. Queue age includes intentional maintenance waits, not just faults. Average attempt duration is `task_attempt_duration_seconds_24h / clamp_min(task_attempts_24h, 1)` with both metric names fully prefixed.

## Stopped-directory backup {#backup}

1. Stop the instance: `docker compose stop` for Compose, or `docker stop releasetracker` for a single container.
2. Confirm it has stopped, then archive the entire directory:

    ```bash
    mkdir -p ./backups
    tar -czf "./backups/releasetracker-$(date +%Y%m%d-%H%M%S).tar.gz" ./data
    ```

3. Verify the archive is readable and includes at least `data/releases.db` and `data/system-secrets.json`. Store it in a protected location.

Do not copy live `.db`, `.db-wal`, and `.db-shm` files separately. Copying the whole directory while stopped avoids inconsistent WAL state; the keys and database must belong to the same recovery point. Backups contain credentials, tokens, and possibly unredacted snapshots; treat them as sensitive data.

For backup-only maintenance, resume with `docker compose start` or `docker start releasetracker` afterwards.

## Upgrade {#upgrade}

Complete the stopped-instance backup first, then follow the matching workflow. In production, replace `latest` with a tested new release tag and record the old version.

=== "Docker Compose"

    Update the image version in `compose.yml` if using a fixed tag, then run:

    ```bash
    docker compose pull
    docker compose up -d
    docker compose logs --tail=100 releasetracker
    ```

=== "Docker run"

    Pull the new image before removing the stopped old container and recreating it. These options match the [installation example](../getting-started/installation.md); preserve any extra Socket mounts, networking, or security options from your original deployment.

    ```bash
    docker pull ghcr.io/dalamudx/releasetracker:latest
    docker rm releasetracker
    docker run -d \
      --name releasetracker \
      -p 127.0.0.1:8000:8000 \
      -v "$(pwd)/data:/app/backend/data" \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    docker logs --tail=100 releasetracker
    ```

Confirm migrations and startup, then verify configuration, a manual version check, and run history after login. `docker pull` or `docker start` alone does not switch an existing container to the new image.

## Restore an instance {#restore}

1. Stop the new instance. Preserve its data directory for diagnosis; never overwrite the only backup.
2. Extract the selected backup into an empty recovery directory. Confirm the database/key pair and filesystem permissions.
3. Use the image version and deployment configuration matching the backup, mounting its recovered `data` as `/app/backend/data`.
4. Ensure no other instance uses that directory, then start and verify login, credential decryption, and tracker configuration.

After migrations, an older application may not understand the new schema. Downgrade by restoring old data and the matching image, not merely changing the image tag. Instance recovery does not undo updates already applied to external applications; see [Health checks and rollback](../guides/health-and-rollback.md).

## Rotate keys {#keys}

| Key | Effect |
| --- | --- |
| Session signing key | Invalidates existing JWT sessions; sign in again |
| Data encryption key | Re-encrypts stored encrypted fields; unreadable data blocks rotation |

Back up before using **System Settings → Security Keys**, then take another consistent backup afterwards. If rotation is interrupted, retain the original data and key files and inspect the error logs; do not manually remove recovery key fields.

## Image entry commands {#entry-commands}

| Command | Behavior |
| --- | --- |
| `migrate-and-serve` | Migrate, then start; recommended for installation and upgrades |
| `migrate` | Migrate only; stop other instances and back up before diagnosis |
| `serve` | Start only; requires a ready schema |

When migrations are pending, `migrate` / `migrate-and-serve` first writes a consistent `reason: pre_migration` archive (database + keys) to the backup directory and aborts the migration if that fails. Restore it only with the pre-upgrade image; it shares the retention count, so download it if it must be kept long term. Set `RELEASETRACKER_PRE_MIGRATION_BACKUP=0` only if another mechanism already provides this. Database schema is managed by dbmate; avoid manual table edits. See [Startup and migration troubleshooting](../reference/troubleshooting.md#startup).
