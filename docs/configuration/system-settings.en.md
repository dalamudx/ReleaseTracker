---
title: System Settings
---

# System Settings

All runtime configuration is managed from the **System Settings** page. No `.env` files or environment variables are required.

## 1. Available settings

| Setting | Key | Default | Constraints |
| ------- | --- | ------- | ----------- |
| Timezone | `system.timezone` | `UTC` | Any valid IANA timezone (e.g. `Asia/Shanghai`). Invalid names are rejected. |
| Log level | `system.log_level` | `INFO` | One of `DEBUG` / `INFO` / `WARNING` / `ERROR`. Takes effect immediately on save. |
| Release history retention | `system.release_history_retention_count` | `20` | Integer between `1` and `1000`, applied per tracker. |
| Executor snapshot retention | `system.executor_snapshot_retention_count` | `10` | Integer between `1` and `1000`, applied per executor. |
| BASE URL | `system.base_url` | *(empty)* | Absolute `http`/`https` URL with no query or fragment; sub-paths allowed. |

## 2. BASE URL

BASE URL is the public address from which browsers reach ReleaseTracker. It drives:

- The OIDC callback URL generated behind a reverse proxy.
- OIDC post-login redirects.

Configure it under **System Settings → Global Settings → BASE URL**, for example:

- `https://releases.example.com`
- `https://example.com/releasetracker` (sub-path deployments must include the full sub-path)

OIDC callbacks resolve to:

```text
{BASE URL}/auth/oidc/{provider}/callback
```

With an empty BASE URL, ReleaseTracker falls back to the address the browser uses. Behind a reverse proxy, always set it explicitly to avoid OIDC redirects landing on the wrong host.

## 3. Retention policies

**Release history retention** is per tracker. Records beyond the retention count are removed during cleanup. Trigger manually via **System Settings → Maintenance → Cleanup Release History** (the same action runs `PRAGMA optimize` and a WAL checkpoint on the SQLite layer).

**Executor snapshot retention** is per executor. Snapshots beyond the retention count are pruned:

- After every successful `pre_update` / `pre_rollback` snapshot capture.
- When **System Settings → Maintenance → Cleanup Snapshot History** is triggered manually.

!!! note "Snapshot capture is scoped"
    Only Docker / Podman single-container executors and Helm release executors capture pre-update snapshots. Other executor modes do not produce snapshots, so retention has no practical effect on them. See [Known Limitations](../limitations.en.md).

## 4. System key rotation

ReleaseTracker keeps two keys in `data/system-secrets.json`:

| Key | Purpose | Rotation impact |
| --- | ------- | --------------- |
| JWT signing key | Issues / verifies login tokens | Rotation **invalidates all active sessions**; everyone re-authenticates. |
| Fernet encryption key | Encrypts credentials, OIDC client secrets, runtime connection secrets | Rotation re-encrypts every encrypted column. If any row cannot be decrypted with the current key, the entire rotation aborts. |

### Who can rotate

Key rotation endpoints require a login whose username is literally `admin`. This is a hard server-side check (`get_current_admin_user`), not a configurable role. If the default `admin` account is deleted or renamed, key rotation becomes unreachable through the UI.

### Procedure

1. Open **System Settings → Security → Key Management** to inspect current fingerprints and inventory.
2. Click **Rotate** next to the desired key. The server can generate a new value, or you can provide one.
3. Before rotating the **encryption key**, confirm `undecryptable_count` is `0`. Otherwise the rotation aborts with a 400 error without modifying any data.
4. After rotating the **JWT key**, every user must sign in again.

!!! danger "Back up data and keys together"
    Losing `system-secrets.json` makes every encrypted field unrecoverable. Any backup strategy must capture `releases.db` and `system-secrets.json` as a unit. See the data directory layout in [Installation](../getting-started/installation.en.md).

## 5. Log level changes

Log level changes take effect immediately (via `logging.getLogger().setLevel(...)`); no container restart is required. Typical choices:

- `INFO` (default): scheduler lifecycle, tracker scans, executor runs, key rotations.
- `DEBUG`: adds HTTP request logs, scheduler tick details, parsing diagnostics. Use only for troubleshooting.
- `WARNING` / `ERROR`: only exceptional paths. Suitable when shipping logs to external aggregators where volume matters.

## 6. Where the timezone applies

- Maintenance windows: executor `maintenance_window` rules interpret allowed days and time windows in this timezone.
- Cleanup reports and scheduler log timestamps render in this timezone.
- Most UI timestamps also follow this timezone, though a handful of system fields remain in UTC.

After changing the timezone, refresh the frontend to see UI updates.
