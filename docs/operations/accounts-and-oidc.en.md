---
title: Administrator and OIDC
---

# Administrator and OIDC

ReleaseTracker has one stable administrator identity, with no registration, multi-tenancy, or RBAC. Renaming the administrator does not transfer privileges. OIDC adds a login method for that same administrator; it never creates users.

For first login, see [Installation and first run](../getting-started/installation.md).

## Local password recovery {#password-recovery}

For a forgotten password or a legacy default password blocked after upgrading, run on a host authorized to access the data directory:

=== "Docker Compose"

    ```bash
    docker compose exec releasetracker python -m releasetracker.cli reset-admin-password
    ```

=== "Docker run"

    ```bash
    docker exec -it releasetracker python -m releasetracker.cli reset-admin-password
    ```

The command reads a new password interactively, uses the mounted data directory directly, and revokes existing sessions. It does not use the HTTP API. Do not put passwords in command-line arguments. It resets the existing administrator, not a deleted account; restore a missing administrator record from a trusted backup.

## Bind OIDC {#oidc}

1. Configure [HTTPS and BASE URL](reverse-proxy.md).
2. Register an application at the IdP with callback `{BASE URL}/auth/oidc/{slug}/callback`, using the same `slug` as below.
3. Create a provider under **System Settings → OIDC**; prefer Discovery.
4. Sign in locally, enter the current local password in the administrator binding action, and complete authorization at the IdP.
5. Confirm the binding, then test signing out and back in through OIDC. Keep the local password for recovery.

| Field | Requirement |
| --- | --- |
| Slug | Lowercase letters, digits, and hyphens; immutable after creation |
| Client ID / Secret | Supplied by the IdP; a blank Secret on edit keeps the stored value |
| Issuer URL | HTTPS Discovery address matching the token issuer |
| Manual endpoints | Configure without Discovery; authorization, Token, JWKS, and related endpoints require HTTPS |
| Scopes | Default `openid email profile`; must include `openid` |

Provider configuration alone does not enable login. One provider and one validated issuer + subject binding are supported, with no automatic email-based account matching. Unbind with local password confirmation before changing or deleting a bound provider.

## API and troubleshooting {#api}

Use the UI for routine operations. Administrator APIs for integrations include:

| Action | Endpoint |
| --- | --- |
| Inspect binding | `GET /api/oidc-providers/admin-binding` |
| Start binding | `POST /api/oidc-providers/{provider_id}/admin-binding/authorize` |
| Remove binding | `POST /api/oidc-providers/admin-binding/unbind` |

Requests require a ReleaseTracker administrator JWT; binding and unbinding also require the current local password. The API does not accept IdP tokens directly. See the instance's `/docs` for request schemas. Never paste JWTs, Client Secrets, or authorization callback URLs into public examples.

For login failures, follow [OIDC troubleshooting](../reference/troubleshooting.md#oidc), rather than disabling signature validation or weakening identity binding.
