---
title: Credentials
---

# Credentials

Credentials centralize the sensitive values ReleaseTracker needs to access GitHub, GitLab, container registries, Kubernetes, Portainer, and other external systems. Trackers and runtime connections can reference a saved credential instead of repeating tokens or kubeconfigs in multiple places.

Sensitive inputs are encrypted with Fernet before they are written to SQLite. The encryption key is stored in `data/system-secrets.json`.

## 1. Supported credential types

| Credential type | Purpose | What to enter |
| ---- | ------- | ------------- |
| `github` | GitHub release / tag scans | GitHub personal access token or fine-grained PAT. |
| `gitlab` | GitLab release / tag scans, including self-hosted instances | GitLab personal access token or project access token. |
| `gitea` | Gitea release / tag scans | Gitea access token. |
| `helm` | Private Helm chart repositories | Basic Auth string, repository token, or whatever the chart repository requires. |
| `docker` | OCI registry authentication for GHCR, Docker Hub, or private registries | `username:password`, registry token, or the registry's login token. |
| `docker_runtime` | Docker runtime connections | Usually no secret is needed for local Docker API access; use this only for remote or certificate-based setups. |
| `podman_runtime` | Podman runtime connections | Usually no secret is needed for local Podman API access; use this only for remote or certificate-based setups. |
| `kubernetes_runtime` | Kubernetes runtime connections | Full kubeconfig YAML. |
| `portainer_runtime` | Portainer runtime connections | Portainer API key. |

The UI may localize these labels. The values in the table help you recognize the type names shown in logs or API responses.

## 2. Creating or editing a credential

Open **Credentials → New Credential** and fill in:

- **Name**: unique within ReleaseTracker. This is the value you select later from tracker or runtime connection forms. Use names that describe the purpose, such as `github-prod-readonly` or `portainer-main`.
- **Type**: the service or runtime the credential belongs to. The type controls which sensitive inputs are shown in the form.
- **Sensitive value**: the token, API key, kubeconfig, or username/password value required by the selected type.
- **Description**: optional notes such as source, permission scope, expiry date, or owner.

When editing a credential, leaving a sensitive input blank keeps the existing value. Enter a new value only when you want to replace it.

## 3. Where credentials are used

- **Trackers**: GitHub, GitLab, Gitea, Helm, Docker / OCI, and similar sources can use credentials to access private resources or raise public API rate limits.
- **Runtime connections**: Kubernetes, Portainer, and authenticated remote Docker / Podman connections can use runtime credentials.

Before deleting a credential, ReleaseTracker checks whether trackers or runtime connections still reference it. If references exist, deletion is blocked and the UI shows which objects need to be rewired first.

## 4. Display and security behaviour

- Lists and detail pages never show full secret values; they only show masked summaries.
- Plaintext is decrypted temporarily on the server only when a scan, target discovery, or executor run needs it. It is not returned to the frontend API.
- To rotate a token, edit the credential and enter the new value. Trackers and runtime connections that use it will pick up the change on their next operation.

## 5. Suggestions per credential type

### GitHub token

- Form: `ghp_*` personal access tokens or fine-grained PATs.
- Scope: read access to releases / tags on the target repositories. Even for public repos, a token is recommended to avoid anonymous rate limits.
- Applies to both GraphQL and REST fetch paths; the tracker **GitHub Fetch Priority** setting chooses which path is tried first.

### GitLab token

- Form: personal access token or project access token.
- Scope: `read_api` is sufficient.
- Self-hosted instances use the same credential type; enter your GitLab host in the tracker's instance field.

### OCI registry token

- Docker Hub / GHCR / self-hosted registries all share the `docker` credential type.
- Recommended format: `username:password` or a registry-specific token.
- Without a credential, anonymous requests hit Docker Hub rate limits and tracker scans fail intermittently.

### Portainer API key

- Created under **My Account → Access Tokens** in the Portainer UI.
- Only needs stack read/write permissions for the relevant endpoint.

### Kubernetes kubeconfig

- Paste the full kubeconfig YAML.
- If the runtime connection uses in-cluster configuration, no credential is required and kubeconfig can be omitted.

## 6. Encryption and key rotation

- Sensitive credential values are stored in SQLite as Fernet-encrypted strings.
- The encryption key lives in `data/system-secrets.json`; losing that file means the encrypted credentials cannot be recovered.
- Rotating the encryption key re-encrypts every credential in bulk. If any row cannot be decrypted with the current key (usually from hand-edited databases or a mismatch between `system-secrets.json` and the DB), rotation aborts with `400` and no data is changed. See the "System key rotation" section in [System Settings](system-settings.en.md).

## 7. Backup guidance

- Backups must include both `releases.db` and `system-secrets.json`. Neither alone is sufficient.
- For transfers between teams or sites, wrap the pair in a GPG-encrypted archive.
