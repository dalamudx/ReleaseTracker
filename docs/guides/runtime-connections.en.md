---
title: Credentials and runtimes
---

# Credentials and runtimes

Credentials hold authentication material; runtime connections hold platform addresses and reference credentials. Public version tracking needs neither. Running updates requires a working runtime connection or SSH host connection.

## Source credentials {#credentials}

Create the matching type under **Credentials** and reference it from a source or connection. Secrets are encrypted in the database; backups must include both the [database and system keys](../operations/backup-and-upgrade.md#backup).

| Use | Credential material |
| --- | --- |
| GitHub / GitLab / Gitea | Access token with repository read permission |
| Helm chart repository | Basic Auth username and password |
| Private OCI registry | Username and password / PAT |
| Docker / Podman | TLS CA, client certificate and private key when required |
| Kubernetes | kubeconfig or Token / certificate fields supported by the UI |
| Portainer | API key |
| Host SSH | Private key (optionally with passphrase) or password |

Credentialed GitLab, Gitea, Helm and custom changelog requests require HTTPS and reject cross-origin credential forwarding or HTTP downgrade. OCI redirects are controlled separately in [System settings](../reference/settings.md#global).

## Docker / Podman {#containers}

- Use `unix:///var/run/docker.sock` for a local connection, not the bare path; for Podman use its actual Socket path with the `unix://` prefix.
- When ReleaseTracker runs in a container, mount the Socket into it, for example append `- /var/run/docker.sock:/var/run/docker.sock` to the Compose `volumes`.
- For remote access use `tcp://host:2376` with TLS verification; normally leave the API version blank.
- **Podman mode requires the native `/libpod/` API.** For a Docker-API-only Socket Proxy select **Docker**; use a native Podman service/socket for Pods and native Podman update/recovery.
- Enter the CA, client certificate and private key as PEM credential fields, not paths outside the container. Direct IP endpoints accept certificates without IP SANs while still verifying the CA chain; DNS endpoints require hostname verification.

!!! warning "Runtime access is privileged"
    Docker Socket access is usually equivalent to host administration, and a read-only bind mount does not make the API read-only. Never expose an unauthenticated plaintext Docker TCP endpoint.

## Kubernetes / Helm {#kubernetes}

Outside the cluster select a Kubernetes credential; inside a cluster **In-Cluster** uses the Pod's ServiceAccount. Restrict namespaces and grant only the permissions needed for discovery and updates. Helm releases use a Kubernetes connection, not a separate connection type.

## Portainer {#portainer}

Enter the instance address (prefer HTTPS), select a Portainer credential, and discover the target Endpoint. Targets are standalone stacks, not arbitrary containers managed by Portainer.

## SSH hosts {#ssh}

Use SSH to update Compose-managed projects on a remote host without exposing the Docker API.

1. Under **Credentials → SSH host connections**, enter host, port, user name and authentication (private key or password).
2. Click **Fetch host key**, compare it with the host's real fingerprint, **Confirm fingerprint**, then **Test connection**. A later key change is rejected.
3. If the host is not directly reachable, choose another SSH connection with **Allow as proxy** enabled as a jump host (single hop).

The remote host needs `docker compose`, `docker-compose`, `podman compose` or `podman-compose`, and the SSH user must be able to read and write project files and run Compose. See [SSH Compose targets](executors.md#ssh-compose) for project discovery and write strategies.

## Verify the connection {#verify}

Select the connection while creating an executor and run target discovery. Confirm the target name, namespace or Endpoint before binding a source. For failures follow [connection troubleshooting](../reference/troubleshooting.md#connections); never disable TLS verification to bypass certificate problems. Timeouts and read retries can be tuned via the API, see [Runtime operation policy](../reference/settings.md#operation-policy).

## Public registry read-only probe {#registry-probe}

To investigate slow image version fetching, run from `backend/` in a source checkout:

```bash
.venv/bin/python scripts/probe_registry_manifest.py \
  --registry registry.example.com --image team/app --mode auto
```

The script reads anonymously accessible repositories only; it never reads the database or stored credentials and never deploys. Its JSON output records request counts and durations per stage (defaults: 10 versions, 10 s per request, 240 s overall budget); `--mode first_observed` compares the no-Blob path. A Tag-list timeout means Manifest verification was never reached and is not evidence about Manifest performance.
