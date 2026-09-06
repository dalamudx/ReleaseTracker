---
title: Backups, upgrades, and keys
---

# Backups, upgrades, and keys

Run these commands from the deployment directory, assuming data is mounted from `./data`. Before upgrading, record the current image tag or digest and deployment configuration. They form a recovery point together with the data backup.

## Consistent backup {#backup}

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

Database schema is managed by dbmate; avoid manual table edits. See [Startup and migration troubleshooting](../reference/troubleshooting.md#startup).
