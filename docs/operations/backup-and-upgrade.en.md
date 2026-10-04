---
title: Backup, upgrade, and keys
---

# Backup, upgrade, and keys

Commands run in the deployment directory and assume data is mounted at `./data`. Before upgrading, also record the current image tag (or digest) and deployment configuration; together with the data backup they form the recovery point.

## Online backup {#online-backup}

Under **System Settings → Backups**, acknowledge the sensitive-data notice, then create and download a ZIP. Backups use SQLite online snapshots, contain `releases.db`, `system-secrets.json` and a checksum manifest, and are mutually exclusive with key rotation. Use an [offline backup](#backup) for databases over 2 GiB or snapshots taking longer than 120 seconds.

!!! warning "Backups contain sensitive data"
    The ZIP is **not encrypted** and contains credentials, session state and encryption keys. Download only over HTTPS and keep an encrypted off-host copy. Backups exclude environment variables, Kubernetes Secrets, externally mounted kubeconfig/SSH files and images.

Archives are stored automatically in `backups` under the data directory (`/app/backend/data/backups` in the container); the path follows the database location and needs no configuration. Retention and the automatic interval are set under **System Settings → Global** (see [System settings](../reference/settings.md#global)):

- **Retention** (default 7): shared by manual, automatic and pre-restore safety backups; old archives are pruned only after a new backup passes verification.
- **Automatic interval** (default 0, off): continues from the latest archive and survives restarts; with no archive or when overdue, a backup runs after about 5 minutes.

Older environment variables for the backup directory, retention and interval are no longer read; set them again in global configuration after upgrading. These environment variables still apply:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `RELEASETRACKER_BACKUP_DAILY_RETENTION` | `0` | Additionally keep the newest backup per day for the last 0–90 days (UTC) |
| `RELEASETRACKER_BACKUP_WEEKLY_RETENTION` | `0` | Additionally keep the newest backup per ISO week for the last 0–52 weeks (UTC) |
| `RELEASETRACKER_ONLINE_RESTORE` | `1` on POSIX, `0` elsewhere | Enables [in-app online restore](#managed-restore-assessment) |
| `RELEASETRACKER_PRE_MIGRATION_BACKUP` | `1` | Backup before migrations, see [Entry commands](#entry-commands) |

New archives are written to a temporary directory and appear in the list only after checksum, SQLite integrity and credential decryption are re-verified; failures never touch existing archives. The newest archive is re-verified daily. The backup page shows the last success time, consecutive failures and reason, and marks backups as stale after two intervals without success. Scheduled backup failures, corrupted archives, or losing all previously existing archives send a deduplicated alert to notifiers subscribed to "Task processing error".

## Backup lifecycle {#backup-lifecycle}

The backup page shows archive count, total size and the current retention policy. Automatic pruning runs only after a successful new backup; old backups are never deleted first to free space.

Administrators can delete a specific archive (confirmed by typing its name, irreversible), but at least one archive must remain. An archive being downloaded cannot be deleted and is skipped by pruning. This coordination works within a single application instance only; do not delete files manually behind the page's back.

## In-app online restore {#managed-restore-assessment}

Run it from **Backups → selected archive → Online restore** without stopping the instance manually. The application briefly enters maintenance; deployed workloads are neither stopped nor rolled back.

1. **Preflight**: the archive is strictly verified (size, SHA256, SQLite and relational consistency, schema version, key decryption) and its time, application version and fingerprint are shown. The plan is valid for 10 minutes and locks the archive.
2. **Confirm**: type the full file name and acknowledge that data will be replaced. Restore is refused while deployments/restores, downloads or tasks needing attention are active.
3. **Drain**: new writes return 503 while in-flight requests and background work finish; on timeout the switch is abandoned and data stays unchanged.
4. **Switch**: a "pre-restore safety copy" of the current data is saved first, then database and keys are replaced and services rebuilt. Failures revert to the original data; if the process exits midway, the next start restores it automatically (`migrate-and-serve` includes this step).
5. **Sign in again and review**: use the account from the backup. Old sessions, pending tasks, approvals and queued notifications are revoked, and scheduling and notifications stay paused; after checking the environment click **Review restore state** to resume new operations. Old operations are never replayed.

The pre-restore safety copy can be downloaded from the backup page, is outside normal retention, and is replaced by the next restore; keep it off-host. Reserve disk space for the archive, a staging database and the original data.

Limits: Linux/POSIX file systems only; one application process per data directory (enforced by a file lock), so set `RELEASETRACKER_ONLINE_RESTORE=0` if you need multiple workers; database and keys must not be symlinks. Only trusted archives of the matching version can be restored, and uploads are not supported. Use [command-line restore](#restore) when the app cannot start or across versions.

## Offline directory backup {#backup}

1. Stop the instance: `docker compose stop` or `docker stop releasetracker`.
2. Archive the whole data directory:

    ```bash
    mkdir -p ./backups
    tar -czf "./backups/releasetracker-$(date +%Y%m%d-%H%M%S).tar.gz" ./data
    ```

3. Check the archive contains at least `data/releases.db` and `data/system-secrets.json`, store it securely, then `docker compose start` or `docker start releasetracker`.

Never copy `.db`, `.db-wal` and `.db-shm` individually while running; keys and database must come from the same recovery point.

## Upgrade {#upgrade}

Back up first. In production replace `latest` with a verified version tag and record the previous version.

=== "Docker Compose"

    After changing the image version in `compose.yml`, run:

    ```bash
    docker compose pull
    docker compose up -d
    docker compose logs --tail=100 releasetracker
    ```

=== "Docker run"

    Pull the new image and recreate the container, keeping any Socket mounts, network and security options:

    ```bash
    docker pull ghcr.io/dalamudx/releasetracker:latest
    docker stop releasetracker && docker rm releasetracker
    docker run -d \
      --name releasetracker \
      -p 127.0.0.1:8000:8000 \
      -v "$(pwd)/data:/app/backend/data" \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    docker logs --tail=100 releasetracker
    ```

After startup, sign in and check configuration, a manual version check and run history. `docker pull` alone does not switch an existing container to the new image.

## Restore an instance {#restore}

Older versions are not guaranteed to read a migrated schema: downgrade by restoring old data with the matching image, not by changing the tag only. Restoring the instance does not undo updates already applied to external workloads; see [Health checks and rollback](../guides/health-and-rollback.md).

**From an offline backup**: stop the current instance and keep its data directory for investigation; extract the backup into an empty directory and start the matching image version with it mounted at `/app/backend/data`, ensuring no other instance uses it.

**From an online backup ZIP (command line)**: for an app that cannot start, cross-version or offline disaster recovery. Run inside an image matching the backup version (containers can use `--entrypoint python`) and restore into a directory that **does not exist yet**:

```bash
python -m releasetracker.cli inspect-backup /backups/BACKUP.zip
# Make sure all instances are stopped; --confirm-stopped is only a confirmation
python -m releasetracker.cli restore-backup /backups/BACKUP.zip \
  --destination /restore/new-data --confirm-stopped
```

Verification covers ZIP size, SHA256, SQLite and relational integrity, migration version and key decryption. SHA256 detects corruption but does not prove origin; never restore untrusted ZIPs. Mount the new directory at the data path (with a custom `RELEASETRACKER_DB_PATH`, point it at `releases.db` in the new directory) and keep the old volume.

After restoring you must sign in again; old pending tasks, approvals and queued notifications are revoked and scheduling and notifications are paused. After checking remote versions and executor configuration, click **Review restore state** on the backup page, or run against the new database:

```bash
# Read-only check; reports relational problems without fixing them
RELEASETRACKER_DB_PATH=/restore/new-data/releases.db python -m releasetracker.cli audit-database
# Lift the gate for new operations, then restart the service
RELEASETRACKER_DB_PATH=/restore/new-data/releases.db python -m releasetracker.cli acknowledge-restore --confirm-reviewed
```

Re-trigger any old targets that still need work; old tasks are never replayed.

## Rotate keys {#keys}

| Key | Effect |
| --- | --- |
| Session signing key | Existing sessions become invalid and users must sign in again |
| Data encryption key | Re-encrypts stored encrypted fields; rotation is refused if any data cannot be decrypted |

Use **System Settings → Security Keys** and take a backup before and after. If rotation is interrupted, keep the original data and key files and check the logs; never delete key fields manually.

## Image entry commands {#entry-commands}

| Command | Behavior |
| --- | --- |
| `migrate-and-serve` | Migrate, then start; recommended for install and upgrade |
| `migrate` | Migrate only; stop other instances first |
| `serve` | Start only; requires an up-to-date schema |

When migrations are pending, `migrate` / `migrate-and-serve` first write a `reason: pre_migration` archive to the backup directory and abort if that fails. The archive can only be restored with the pre-upgrade image and counts toward retention, so download it if you need it long term; set `RELEASETRACKER_PRE_MIGRATION_BACKUP=0` if another backup mechanism is in place. The schema is managed by dbmate; never edit tables manually. See [Startup and migration troubleshooting](../reference/troubleshooting.md#startup).

## Prune historical orphans {#prune-orphans}

When a backup is created, orphan rows left by deleted trackers/executors are pruned only in the **archive copy** (counted in the manifest's `pruned_orphans`); the live database is unchanged. To repair the live database, preview first, then stop all instances and run:

```bash
python -m releasetracker.cli prune-orphans --dry-run
python -m releasetracker.cli prune-orphans --confirm-stopped
python -m releasetracker.cli audit-database
```

A copy of the unmodified database and keys (`reason: pre_orphan_cleanup`) is saved first. Only known historical relations are handled; unknown damage rolls the cleanup back. Never run it while the application is running or keys are rotating.

## Metrics {#metrics}

Set a random `RELEASETRACKER_METRICS_TOKEN` of at least 32 characters to expose `GET /metrics` (404 otherwise). Requests need `Authorization: Bearer <token>`; login tokens do not work, and this token can only read metrics. Scrape every 60 seconds over an internal address:

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

All metrics are gauges prefixed with `releasetracker_`; labels never contain URLs, credentials or IDs. Do not apply `rate()` to them.

| Metric | Meaning |
| --- | --- |
| `tasks` | Tasks by type and state |
| `task_oldest_pending_seconds` | Wait time of the oldest queued task |
| `task_attempts_24h` / `task_attempt_duration_seconds_24h` | Completed attempts and total duration in the last 24 h |
| `task_errors_24h` | Errors in the last 24 h by bounded category (including TLS/security) |
| `fetch_runs_24h` | Source fetch results in the last 24 h |
| `notification_outbox` / `notification_oldest_pending_seconds` | Notification queue state |
| `backup_last_success_timestamp_seconds` / `backup_failures` | Backup state (failure count resets on restart) |

Example alerts: `releasetracker_tasks{state="needs_attention"} > 0` or `releasetracker_notification_oldest_pending_seconds > 600` for 5 minutes. Queue waits include maintenance windows, so long waits are not necessarily failures.
