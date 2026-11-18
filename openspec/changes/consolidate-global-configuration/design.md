# Design

## Context

`system.base_url` already supplies OIDC, notification links, repository webhooks and HTML base paths. The OIDC handler uses `frontend_url` as a local variable for this stored value. The deployed service sets `FRONTEND_URL` to the same value. Uvicorn consumes `FORWARDED_ALLOW_IPS` before the application sees the connection. The backup CLI runs before schema migration.

## Goals / Non-Goals

Keep saved data and resource locks authoritative during upgrades. Preserve existing timezone and key generation, storage and rotation behavior. Use the current global settings API, page sections and permissions.

## Decisions

1. Keep the public URL under its existing database key. Use public base URL terminology in both locales and the OIDC handler. Check deploy examples and consumer fallbacks. Exclude `FRONTEND_URL` from all import mappings.
2. Apply the current defaults and bounds to persisted policies: shared mutation slots 1 (1–3), scheduler polling 2 s (1–60), pull grace 1800 s (0–3600), health watch disabled (0 or 300–86400 s). Refresh the actual dispatch limit, scheduler intervals and inspection registration after save. Use saved pull grace for new observation records and preserve existing deadlines. Read retention policy during prun...
3. Preserve POSIX capability checks for online restore and finish any journal recovery before enabling new operations. A read-only lookup against the existing database supplies the migration backup preference before dbmate runs. Default to backup for an older schema with no preference. Fail safely on an unreadable existing database. Point dbmate at the same fixed database as the application and check alternate migration invocation paths.
4. Make the persisted inbound proxy policy the sole authority for forwarded headers. Disable the server's own header rewriting explicitly and apply the existing server library's proxy interpretation before rate limiting, origin checks and cookie decisions using the original peer. Preserve explicitly configured ingress trust across upgrade. Keep CORS middleware and session-origin validation in sync. Retain TLS and outbound CIDR validation separately.
5. Expose the metrics token through a secret-specific administrator operation and a configured flag. Reuse protected credential storage and constant-time validation. Exclude the credential from generic settings reads, logs and migration summaries, and preserve access to metrics across rotation and backup/restore.

## Risks / Trade-offs

- A live reduction in slots cannot reclaim a running mutation safely. Enforce the new cap at the next claim and drain existing workers normally.
- Former custom paths may contain the only database, secrets and ZIP archives. Validate paths and mounts offline before creating a database in the new location.
- A failed upgrade import could leave trusted proxy, backup, or metrics policy inconsistent. Validate the complete input, persist atomically and record completion with the import. Preserve existing saved values including empty strings and zero.

## Migration Plan

1. Before upgrading, take and verify a database and system-secrets backup. Record the data mount, effective policy and proxy settings. For custom paths stop the service and expose the original data at the fixed mount, retaining the backups and matching keys. Check original database identity before the first app start.
2. Provide an explicit one-time upgrade path for the old policy values. Read the real concurrency variable `RELEASETRACKER_MUTATION_CONCURRENCY`. Correct the older documented spelling. Exclude obsolete frontend URL configuration. Check pre-migration backup and restore-journal handling against both fresh installs and old schemas.
3. Start the new release using persisted configuration, remove retired declarations and verify actual dispatch, public links, forwarded requests, health watches and backup availability. For rollback stop the new service and restore the matching pre-upgrade data, secrets, image and mounts.
