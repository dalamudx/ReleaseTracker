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
- **Mark as failed and roll back** only works end-to-end when the target has both an available snapshot and matching health-probe wiring. Targets without full snapshots cannot be rolled back from ReleaseTracker snapshots even if that UI option is selected.
- The default timings (15-second grace period, 10-second attempt timeout, 5-second interval, 180-second total probe duration) suit most workloads, but complex startup sequences need hand-tuning.

## 4. Authentication and accounts

- **Key rotation is restricted to accounts whose username equals `admin`.** This is a hard-coded check in `get_current_admin_user`, not a configurable role.
  - Deleting or renaming the default `admin` account takes away the ability to rotate keys via the UI; recovery means hand-editing the database.
- **There are no roles or fine-grained permissions.** Every authenticated user sees the same data and can perform the same actions (except key rotation).
- **Default account `admin` / `admin`.** Change the password immediately after the first login. A stock instance on the internet is equivalent to a compromised machine.

## 5. Portainer

- Only Portainer `standalone` stacks are supported (Swarm stacks are not). Discovery skips non-standalone stacks, and save-time validation rejects them.
- Portainer endpoint health is not pre-flighted; unhealthy endpoints surface Portainer's error verbatim during updates.

## 6. Kubernetes / Helm

- Only Helm 3 is supported. Helm 2 is not.
- Helm release discovery relies on Helm 3's Secret-backed storage. Deployments using a ConfigMap-backed storage driver (rare) will not be recognised.
- Kubernetes workload support covers `Deployment`, `StatefulSet`, and `DaemonSet` only. CronJob, Job, and others are not supported.
- Multi-container workloads require choosing a version source explicitly for each container in the executor service-binding step.

## 7. Notifications

- Webhook is the only supported channel.
- Webhook URLs are stored in SQLite **without encryption**. Anyone with database access can read them in plaintext.
- Webhook delivery **does not retry** on failure; failures are logged but not queued for replay.
- Webhooks with custom HTTP headers are not supported — authentication relies on the secret embedded in the URL.

## 8. Trackers

- Release channel names are restricted to `stable` / `prerelease` / `beta` / `canary`. Custom names are not allowed.
- Include / exclude regexes match version tags only. Filtering on release body, author, or other fields is not supported.
- Anonymous GitHub and Docker Hub access is heavily rate-limited. In practice, credentials are required.
- Container source publish-time accuracy depends on the registry; rate-limited registries may force the **First observed time** strategy.

## 9. Database and migrations

- dbmate migrations are **forward-only**. Once a newer version's migrations have run, downgrading the container can fail to start due to schema mismatch; recovery means restoring from backup.
- Database backups must be paired with `system-secrets.json`. Without both, encrypted data cannot be recovered.

## 10. API / UI

- There is no public API versioning strategy. `/api` is implicit v1. Breaking changes are infrequent, but surface through the README roadmap and release notes.
- There is no built-in audit log. Run histories (`ExecutorRunHistory`, `SourceFetchRun`) provide most of the traceability.
- Only zh and en are available in the UI.
- OIDC is used for user sign-in only; the API itself does not accept OIDC-issued tokens (it expects local JWTs).
- The password policy is minimal (length ≥ 6). For stronger policies, integrate via an OIDC IdP that enforces them.

---

Spot a missing entry or an item that has since been fixed? Open an issue or PR on GitHub. This page is kept up to date alongside each release.
