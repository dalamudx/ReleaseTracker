---
title: Runtime Connections
---

# Runtime Connections

Runtime connections describe how ReleaseTracker reaches external container or orchestration environments. Executors use them to discover targets and apply updates.

## 1. Supported runtime types

| Type | Description |
| ---- | ----------- |
| `docker` | Docker Engine API, over a Unix socket or TCP. |
| `podman` | Podman API, over a Unix socket or TCP. |
| `kubernetes` | kubeconfig or in-cluster ServiceAccount. |
| `portainer` | Portainer HTTP API. |

## 2. Common fields

```text
RuntimeConnection {
  name:          string         # Globally unique
  type:          docker | podman | kubernetes | portainer
  enabled:       bool           # Disabling keeps the config but excludes it from scheduling
  config:        map<string, any>  # Type-specific; see below
  credential_id: int | null     # Reference into Credentials
  description:   string | null
}
```

## 3. Docker / Podman

| `config` key | Required | Constraints |
| ------------ | -------- | ----------- |
| `socket` | Yes | Must start with `unix://` or `tcp://`. Typical values: `unix:///var/run/docker.sock`, `tcp://docker.example.com:2376`. |
| `tls_verify` | No | Boolean. When enabled, the client expects a CA-signed certificate. |
| `api_version` | No | Pins the Docker API version; empty uses the SDK default. |

`credential_id` is usually left empty for Docker / Podman, since local Unix sockets do not require authentication. For a mutual-TLS TCP endpoint, create a `docker_runtime` / `podman_runtime` credential and reference it here.

## 4. Kubernetes

| `config` key | Required | Constraints |
| ------------ | -------- | ----------- |
| `in_cluster` | No | Boolean. When `true`, the pod's own ServiceAccount is used and no credential is needed. |
| `context` | No | Selects a context within the kubeconfig. |
| `namespace` | No | Single namespace used for all discovery operations. |
| `namespaces` | No | List of non-empty namespace strings. |
| *Credential* | Required unless `in_cluster=true` | `kubernetes_runtime` credential containing the kubeconfig YAML. |

Discovery semantics:

- If both `namespace` and `namespaces` are set, `namespaces` wins for both discovery and namespace authorization.
- If neither is set, ReleaseTracker scans whichever namespaces the kubeconfig can list.
- Use **Executors → Runtime Connection → Discover Namespaces** to preview what the current kubeconfig can reach.

### Minimum ServiceAccount permissions

When using a kubeconfig, the associated ServiceAccount needs at minimum:

- `list` / `get` on Deployments, StatefulSets, DaemonSets.
- `patch` on the above to modify images during updates.
- `list` on Namespaces (optional, for namespace discovery).
- For Helm release executors: access to list Helm's Secrets (Helm 3 stores releases as Secrets).

## 5. Portainer

| `config` key | Required | Constraints |
| ------------ | -------- | ----------- |
| `base_url` | Yes | Portainer HTTP/HTTPS root URL. |
| `endpoint_id` | Yes | Portainer endpoint ID (positive integer). |
| *Credential* | Yes | `portainer_runtime` credential; `secrets.token` is the Portainer API key. |

Known limitation: **only `standalone` Portainer stacks are supported.** Swarm stacks are skipped during discovery and cannot be updated from ReleaseTracker. See the Portainer section in [Known Limitations](../limitations.en.md).

## 6. Enable / disable

- `enabled=false` causes the executor scheduler to skip the runtime connection (runs log `runtime connection disabled`), but the configuration is preserved.
- Executors still bound to a disabled runtime connection fail rather than skip, so operators notice quickly.

## 7. Delete and rename

- Runtime connections referenced by any executor cannot be deleted directly; rewire the executor first.
- Renaming is allowed, but the UI may cache the old name; refresh to verify.

## 8. Common failures

!!! failure "Docker: `permission denied` on the socket"
    The process inside the container needs read/write access to `docker.sock`. The usual approach is to bind-mount `/var/run/docker.sock` with the correct host permissions. Mounting read-only (`:ro`) breaks any update operation.

!!! failure "Kubernetes: `Unauthorized` / `Forbidden`"
    Verify the kubeconfig user or ServiceAccount has the minimum permissions listed in section 4.

!!! failure "Portainer: `401 Invalid Access Token`"
    - The Portainer API key has expired or been revoked; regenerate and update the credential.
    - The `endpoint_id` does not match the environment the API key belongs to.

!!! failure "Kubernetes: namespace discovery returns an empty list"
    Usually the ServiceAccount cannot `list` Namespaces. Switch to `config.namespaces` and enumerate explicitly.
