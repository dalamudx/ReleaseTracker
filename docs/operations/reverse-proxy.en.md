---
title: Reverse proxy and sub-paths
---

# Reverse proxy and sub-paths

These Nginx examples assume the proxy shares the application's host, the application listens on `127.0.0.1:8000`, and the certificate files already exist. A containerized proxy must use the application service name on a shared network, not the proxy container's own loopback address.

## Root deployment {#root}

For `https://releases.example.com`, preserve the original request path:

```nginx
server {
    listen 443 ssl;
    server_name releases.example.com;
    ssl_certificate /etc/letsencrypt/live/releases.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/releases.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Save `https://releases.example.com` under **System Settings → BASE URL**.

## Sub-path deployment {#subpath}

For `https://example.com/releasetracker`, backend routes remain `/api`, `/assets`, and so on. Strip the `/releasetracker/` prefix exactly once at the proxy:

```nginx
server {
    listen 443 ssl;
    server_name example.com;
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location = /releasetracker {
        return 308 /releasetracker/;
    }
    location /releasetracker/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The trailing `/` on `proxy_pass` replaces the location prefix. Complete initial login through the local address, set BASE URL to `https://example.com/releasetracker`, then reopen the site at its external URL.

| Browser request | Request forwarded to the backend |
| --- | --- |
| `/releasetracker/` | `/` |
| `/releasetracker/assets/...` | `/assets/...` |
| `/releasetracker/api/...` | `/api/...` |
| `/releasetracker/auth/oidc/provider/callback` | `/auth/oidc/provider/callback` |

BASE URL generates the frontend base path and OIDC URLs. It does not prefix every backend route or replace proxy configuration.

## Verify {#verify}

Run `nginx -t` before reloading the proxy, then check:

1. Both the homepage and direct navigation to `/trackers` work, including the prefix for sub-path deployments.
2. API requests return the expected JSON, not frontend HTML, and assets have no 404 errors.
3. Notification links use the external address. With OIDC, the registered callback exactly matches [OIDC configuration](accounts-and-oidc.md#oidc).

An empty BASE URL supports local access; a non-empty value must be a canonical absolute HTTPS URL. Changing proxy headers alone cannot fix an incorrect BASE URL. See [Proxy troubleshooting](../reference/troubleshooting.md#proxy).
