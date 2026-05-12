---
title: Credentials
---

# Credentials

Credentials centralize tokens, usernames / passwords, and runtime authentication material. Every sensitive field is encrypted with Fernet (key sourced from `system-secrets.json`) before being written to disk.

## 1. Supported credential types

| Type | Purpose | Typical `secrets` fields |
| ---- | ------- | ----------------------- |
| `github` | GitHub release / tag scans | `token` |
| `gitlab` | GitLab release / tag scans (including self-hosted) | `token` |
| `gitea` | Gitea release / tag scans | `token` |
| `helm` | Private Helm chart repositories | `token` (e.g. `username:password` or Basic auth string) |
| `docker` | OCI registry authentication (GHCR / Docker Hub / private registries) | `token` (`username:password` or registry token) |
| `docker_runtime` | Docker runtime connection metadata (usually requires no secret) | — |
| `podman_runtime` | Podman runtime connection metadata | — |
| `kubernetes_runtime` | Kubernetes runtime connection | `kubeconfig` (full YAML as a string) |
| `portainer_runtime` | Portainer runtime connection | `token` (Portainer API key) |

The strings above are the enum values stored server-side. The UI may localize their display labels.

## 2. Data model

```text
Credential {
  name:        string            # Globally unique
  type:        CredentialType    # See the table above
  token:       string            # Legacy field; mirrored to secrets.token
  secrets:     map<string, any>  # Primary secret container (encrypted)
  description: string | null
}
```

- `token` exists purely for backward compatibility with earlier versions; new entries should put the value under `secrets.token`. The model mirrors the two automatically: writing `token` copies into `secrets.token` and vice versa.
- Additional keys inside `secrets` are also encrypted. They are available for future extensions (e.g. OIDC secrets, custom headers).

## 3. List and detail responses

- List and detail endpoints **mask** every sensitive value. Strings longer than 8 characters are shown as `AAAA...ZZZZ`; shorter values become `****`.
- Plaintext is decrypted server-side only when a scheduler or executor needs it; it is never returned through the API.
- When editing, leaving a secret field empty keeps the existing value. Submit a new value to overwrite.

## 4. Deletion rules

Before a credential can be deleted, the server checks for references:

- Any tracker that points at the credential.
- Any runtime connection that points at the credential.

If references exist, the endpoint returns `409` with the reference list. Remove or rewire the referencing objects first, then retry the delete.

## 5. Suggestions per credential type

### GitHub token

- Form: `ghp_*` personal access tokens or fine-grained PATs.
- Scope: read access to releases / tags on the target repositories. Even for public repos, a token is recommended to avoid anonymous rate limits.
- Applies to both GraphQL and REST fetch paths; the tracker **GitHub Fetch Priority** setting chooses which path is tried first.

### GitLab token

- Form: personal access token or project access token.
- Scope: `read_api` is sufficient.
- Self-hosted instances use the same credential type; the tracker's `instance` field points at your GitLab host.

### OCI registry token

- Docker Hub / GHCR / self-hosted registries all share the `docker` credential type.
- Recommended format: `username:password` or a registry-specific token.
- Without a credential, anonymous requests hit Docker Hub rate limits and tracker scans fail intermittently.

### Portainer API key

- Created under **My Account → Access Tokens** in the Portainer UI.
- Only needs stack read/write permissions for the relevant endpoint.

### Kubernetes kubeconfig

- Put the full kubeconfig YAML into `secrets.kubeconfig`.
- If the runtime connection sets `in_cluster=true`, no credential is required and `kubeconfig` can be omitted.

## 6. Encryption and key rotation

- Every `secrets` value (and the legacy `token` field) is stored as a Fernet-encrypted string in SQLite.
- The encryption key lives in `data/system-secrets.json`; losing that file means the encrypted credentials cannot be recovered.
- Rotating the encryption key re-encrypts every credential in bulk. If any row cannot be decrypted with the current key (usually from hand-edited databases or a mismatch between `system-secrets.json` and the DB), rotation aborts with `400` and no data is changed. See the "System key rotation" section in [System Settings](system-settings.en.md).

## 7. Backup guidance

- Backups must include both `releases.db` and `system-secrets.json`. Neither alone is sufficient.
- For transfers between teams or sites, wrap the pair in a GPG-encrypted archive.
