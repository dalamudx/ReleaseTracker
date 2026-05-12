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

## 2. Creating or editing a runtime connection

Open **Executors → Runtime Connections → New Connection** and fill in:

- **Name**: unique within ReleaseTracker. Executors use this name when selecting the runtime they will update.
- **Type**: Docker, Podman, Kubernetes, or Portainer. The selected type controls which connection fields appear.
- **Enabled state**: disabled connections keep their configuration but are not usable for executor updates.
- **Credential**: select a saved credential when the runtime needs one. Local Docker / Podman sockets usually do not; Kubernetes and Portainer usually do.
- **Description**: optional notes about environment, owner, or access scope.

## 3. Docker / Podman

| UI field | Required | Description |
| -------- | -------- | ----------- |
| Socket / API address | Yes | Must start with `unix://` or `tcp://`. Typical values: `unix:///var/run/docker.sock`, `tcp://docker.example.com:2376`. |
| TLS verification | No | Enable for remote TLS endpoints when the client should verify the certificate chain. |
| API version | No | Pins the Docker API version; empty uses the SDK default. |

Local Unix socket access usually does not require a credential. For a mutual-TLS TCP endpoint, create a matching `docker_runtime` or `podman_runtime` credential first, then select it here.

## 4. Kubernetes

| UI field | Required | Description |
| -------- | -------- | ----------- |
| Use in-cluster configuration | No | Uses the ReleaseTracker pod's own ServiceAccount; no kubeconfig credential is needed. |
| kubeconfig context | No | Selects a context within the kubeconfig. |
| Single namespace | No | Limits discovery to one namespace. |
| Namespace list | No | Explicitly lists multiple namespaces that can be scanned. |
| Credential | Required unless in-cluster configuration is enabled | Select a `kubernetes_runtime` credential containing the kubeconfig YAML. |

Discovery semantics:

- If both **Single namespace** and **Namespace list** are set, the list wins for both discovery and namespace authorization.
- If neither is set, ReleaseTracker scans whichever namespaces the kubeconfig can list.
- Use **Executors → Runtime Connection → Discover Namespaces** to preview what the current kubeconfig can reach.

### Minimum ServiceAccount permissions

When using a kubeconfig, the associated ServiceAccount needs at minimum:

- `list` / `get` on Deployments, StatefulSets, DaemonSets.
- `patch` on the above to modify images during updates.
- `list` on Namespaces (optional, for namespace discovery).
- For Helm release executors: access to list Helm's Secrets (Helm 3 stores releases as Secrets).

## 5. Portainer

| UI field | Required | Description |
| -------- | -------- | ----------- |
| Portainer URL | Yes | Portainer HTTP/HTTPS root URL. |
| Endpoint ID | Yes | Portainer endpoint ID (positive integer). |
| Credential | Yes | Select a `portainer_runtime` credential containing the Portainer API key. |

Known limitation: **only `standalone` Portainer stacks are supported.** Swarm stacks are skipped during discovery and cannot be updated from ReleaseTracker. See the Portainer section in [Known Limitations](../limitations.en.md).

## 6. Enable / disable

- Disabling a runtime connection causes executor runs that still reference it to fail with an explanatory message instead of silently skipping work. This helps operators notice the connection is unavailable.
- The configuration is preserved while disabled and can be used again after re-enabling.

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
    - The Endpoint ID does not match the environment the API key belongs to.

!!! failure "Kubernetes: namespace discovery returns an empty list"
    Usually the ServiceAccount cannot `list` Namespaces. Switch to the namespace list and enumerate explicitly.
