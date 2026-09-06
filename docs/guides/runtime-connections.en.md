---
title: Credentials and runtimes
---

# Credentials and runtimes

Credentials hold authentication material; runtime connections hold platform addresses and reference credentials. Public version tracking can work without either. Running updates requires a working runtime connection.

## Source credentials {#credentials}

Create the matching type under **Credentials**, then reference it from a source or runtime connection. Secrets are encrypted in the database; backups must include both the [database and system keys](../operations/backup-and-upgrade.md#backup).

| Use | Credential material |
| --- | --- |
| GitHub / GitLab / Gitea | Access token with repository read permission; example token prefixes are not the only valid formats |
| Helm chart repository | Basic Auth username and password |
| Private OCI registry | Username and password / PAT |
| Docker / Podman | TLS CA, client certificate, and private key when required |
| Kubernetes | kubeconfig or Token / certificate fields supported by the UI |
| Portainer | API key |

Credentialed GitLab, Gitea, Helm, and custom changelog requests require HTTPS and reject cross-origin credential forwarding or HTTP downgrade. OCI redirects have a separate [system setting](../reference/settings.md).

## Docker / Podman {#containers}

- Use `unix:///var/run/docker.sock` for a local connection, not the bare `/var/run/docker.sock` path. For Podman, use its actual Socket path with the `unix://` prefix.
- When ReleaseTracker runs in a container, mount the Socket into it. For example, append this to `volumes` in your existing Compose service:

    ```yaml
    - /var/run/docker.sock:/var/run/docker.sock
    ```

- For remote access, use `tcp://host:2376` with TLS verification and the required certificates. Match the actual server port.
- Normally leave the API version blank; specify one only after checking server compatibility.

!!! warning "Runtime access is privileged"
    Docker Socket access is usually equivalent to host administration. A read-only bind mount does not make the Docker API read-only. Never expose an unauthenticated plaintext Docker TCP endpoint; restrict who can access ReleaseTracker and its network.

## Kubernetes / Helm {#kubernetes}

Outside the cluster, select a Kubernetes credential. Inside a cluster, **In-Cluster** uses the Pod's ServiceAccount. Restrict namespaces and grant only the permissions needed for target discovery and updates.

Helm releases use a Kubernetes connection, not a separate connection type. See the [support matrix](../reference/support.md#runtimes) for workload and Helm limitations.

## Portainer {#portainer}

Enter the instance address, select a Portainer credential, and discover the target Endpoint. Prefer HTTPS. Targets are supported standalone stacks, not arbitrary containers managed by Portainer; see the [support matrix](../reference/support.md#runtimes).

## Verify the connection {#verify}

Select the connection during executor creation and run target discovery. Confirm the target name, namespace, or Endpoint before binding a source. For discovery failures, follow [connection troubleshooting](../reference/troubleshooting.md#connections); do not disable TLS verification to bypass certificate problems.

Advanced timeout and read-retry configuration is currently API-only; see [Runtime operation policy](../reference/settings.md#operation-policy).
