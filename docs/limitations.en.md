---
title: Known Limitations
---

# Known Limitations

This page lists constraints worth knowing before running ReleaseTracker in production. The project is still evolving; some items will disappear in future releases. Every entry is grounded in the current code.

## 1. Deployment architecture

- **Single-process, single-instance.** ReleaseTracker is a FastAPI process backed by a local SQLite database in WAL mode. **Horizontal scaling is not supported** (multiple replicas would race on the same SQLite file) and there is no distributed coordination layer. Vertical scaling (better disk I/O) is the only knob.
- **Container architecture.** The official image is published for `linux/amd64` only. Other architectures need to be rebuilt locally.
- **Container runs as root.** The Dockerfile has no `USER` directive. Rootless Docker and SELinux deployments need extra attention on data-directory permissions.
- **Permissive CORS by default.** The backend is configured with `allow_origins=["*"]`. Put a reverse proxy with access control in front before exposing to the internet.

## 2. Snapshot and rollback coverage

Full runtime configuration snapshots and manual rollback work **only** for these destructive recreate targets:

- Docker / Podman single containers
- Docker / Podman Compose grouped updates

These targets capture enough runtime configuration before updates to recover from the executor detail page when a snapshot is available. Compose / Podman pod scenarios do not guarantee stable container IDs or pod IDs; rollback resolves current runtime objects from stable names.

The following targets are not full ReleaseTracker-managed runtime snapshot targets today:

- Portainer stacks: update through the declarative stack-file API and primarily rely on Portainer / stack-file state.
- Kubernetes workloads: patch image fields on Deployment / StatefulSet / DaemonSet objects; use Kubernetes-native rollback.
- Helm releases: use Helm 3 upgrade / release history; use Helm-native rollback.

Rollback calls for these targets return 404 when no snapshot is available. Recover with native tooling (`kubectl rollout undo`, `helm rollback`, Portainer UI, etc.).

## 3. Health check framework

- **Manual HTTP probe** / **Manual TCP probe** support explicit host / port / path probe configuration; Docker / Podman can use runtime-native healthchecks or runtime-state fallback. Kubernetes, Portainer, and Helm grouped update pipelines are still being wired into health checks, so do not assume they support arbitrary host-port probing.
- Update failures and health-check failures only record a failed run; they do not trigger automatic rollback. Operators can manually trigger ReleaseTracker snapshot rollback only when the target has an available snapshot; targets without full snapshots need native recovery tools.
- The default timings (15-second grace period, 10-second attempt timeout, 5-second interval, 180-second total probe duration) suit most workloads, but complex startup sequences need hand-tuning.

## 4. Authentication and accounts

- **Single administrator, not RBAC.** `system.admin_user_id` identifies one stable administrator. All business resources and system-management operations are restricted to that user; existing non-administrator rows can use only self-service authentication endpoints. Registration is disabled.
- **No roles or tenants.** The stable administrator ID is intentionally not a configurable role system. Renaming the administrator does not transfer access; a malformed or dangling reference fails startup closed.
- **Bootstrap administrator password.** Fresh installations generate a random one-time password and record it once at INFO level; existing installations keep their current credentials. Deleting the bootstrap administrator causes subsequent startup to fail instead of generating a new password. Restrict log access and change the password immediately after first login.
- **Explicit OIDC binding.** OIDC accepts one validated issuer + subject bound to the existing administrator and never creates users. Bind and unbind require the current local password, which must be retained as the recovery path.

## 5. Supply-chain checks

The current release workflow includes dependency supply-chain verification, scoped to locked dependencies and audits:

- The frontend runs `npm ci` from `package-lock.json`, runs `npm audit` at high severity, and uploads a CycloneDX SBOM artifact; `frontend/.npmrc` disables dependency install scripts by default to reduce install-time execution risk.
- The backend installs dependencies with uv's locked mode, exports a locked requirements artifact, and scans that requirements file with `pip-audit`.
- GitHub Actions token permissions are least-privilege by job: `contents: read` by default, `packages: write` only for image publishing, and `contents: write` only for GitHub Release creation.

These checks run in CI / release workflows and do not require deployers to edit the SBOM or requirements artifacts. There is currently no image vulnerability scanning step; add one in your own deployment pipeline if you need image-level scanning.

## 6. Portainer

- Only Portainer `standalone` stacks are supported (Swarm stacks are not). Discovery skips non-standalone stacks, and save-time validation rejects them.
- Portainer endpoint health is not pre-flighted; unhealthy endpoints surface Portainer's error verbatim during updates.

## 7. Kubernetes / Helm

- Only Helm 3 is supported. Helm 2 is not.
- Helm release discovery relies on Helm 3's Secret-backed storage. Deployments using a ConfigMap-backed storage driver (rare) will not be recognised.
- Kubernetes workload support covers `Deployment`, `StatefulSet`, and `DaemonSet` only. CronJob, Job, and others are not supported.
- Multi-container workloads require choosing a version source explicitly for each container in the executor service-binding step.

## 8. Notifications

- Webhook is the only supported channel.
- Webhook URLs are stored in SQLite **without encryption**. Anyone with database access can read them in plaintext.
- Webhook delivery performs a small bounded retry for rate limits and transport failures, but failed events are not queued for later replay.
- Webhooks with custom HTTP headers are not supported. Provider credentials may therefore be part of the stored URL; full webhook URLs are intentionally omitted from application logs.
- Webhook and HTTP health-check destinations must resolve entirely to public addresses and use approved HTTP(S) ports. Private/container-network targets and redirects are not supported.

## 9. Trackers

- Release channel names are restricted to `stable` / `prerelease` / `beta` / `canary`. Custom names are not allowed.
- Include / exclude regexes match version tags only. Filtering on release body, author, or other fields is not supported.
- Anonymous GitHub and Docker Hub access is heavily rate-limited. In practice, credentials are required.
- Container source publish-time accuracy depends on the registry; rate-limited registries may force the **First observed time** strategy.

## 10. Database and migrations

- dbmate migrations are **forward-only**. Once a newer version's migrations have run, downgrading the container can fail to start due to schema mismatch; recovery means restoring from backup.
- Database backups must be paired with `system-secrets.json`. Without both, encrypted data cannot be recovered.

## 11. API / UI

- There is no public API versioning strategy. `/api` is implicit v1. Breaking changes are infrequent, but surface through the README roadmap and release notes.
- There is no built-in audit log. Run histories (`ExecutorRunHistory`, `SourceFetchRun`) provide most of the traceability.
- Only zh and en are available in the UI.
- OIDC is used only to obtain a local JWT session for the bound administrator; the API does not accept IdP-issued tokens directly.
- The password policy is minimal (length ≥ 6). For stronger policies, integrate via an OIDC IdP that enforces them.

---

Spot a missing entry or an item that has since been fixed? Open an issue or PR on GitHub. This page is kept up to date alongside each release.
