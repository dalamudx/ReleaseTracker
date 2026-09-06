# ReleaseTracker

[中文](README.md) | [English](README.en.md) · [Wiki](https://dalamudx.github.io/ReleaseTracker/en/)

Lightweight version tracking and update orchestration. Track GitHub, GitLab, Gitea, Helm charts, and OCI images, then apply selected versions to supported Docker, Podman, Portainer, Kubernetes, and Helm targets.

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![React](https://img.shields.io/badge/React-19-61dafb)
![License](https://img.shields.io/badge/License-GPL%20v3-blue)

## Features

- Aggregate version sources, filter through channels and rules, and retain history and release notes.
- Bind runtime targets for manual, immediate, or maintenance-window updates with execution diagnostics.
- Configuration snapshots, post-update health checks, and manual rollback for supported targets.
- Webhook events, one administrator with explicit OIDC binding, encrypted credentials, and key rotation.
- React Web UI with Chinese/English, dark mode, and browser-managed configuration.

Runtime capabilities differ; see the [Wiki support matrix](https://dalamudx.github.io/ReleaseTracker/en/reference/support/). Failed updates never roll back automatically. Executor snapshots are not application-data backups.

## Feature screenshots

![Trackers and version lists](docs/images/trackers.png)

See [FEATURES.md](FEATURES.md) for more screens. The gallery shows the UI; procedures are maintained in the Wiki.

## Quick start

Use Docker / Docker Compose for production. See [Installation and first run](https://dalamudx.github.io/ReleaseTracker/en/getting-started/installation/) for commands, persistence, and first login.

| Task | Documentation |
| --- | --- |
| Configure sources, version rules, and changelogs | [Trackers](https://dalamudx.github.io/ReleaseTracker/en/guides/trackers/) |
| Connect platforms and update services | [Runtimes](https://dalamudx.github.io/ReleaseTracker/en/guides/runtime-connections/) · [Executors](https://dalamudx.github.io/ReleaseTracker/en/guides/executors/) |
| Configure a proxy or SSO | [Reverse proxy](https://dalamudx.github.io/ReleaseTracker/en/operations/reverse-proxy/) · [Administrator and OIDC](https://dalamudx.github.io/ReleaseTracker/en/operations/accounts-and-oidc/) |
| Back up, upgrade, or recover | [Operations](https://dalamudx.github.io/ReleaseTracker/en/operations/backup-and-upgrade/) · [Troubleshooting](https://dalamudx.github.io/ReleaseTracker/en/reference/troubleshooting/) |

## Architecture

In production, one FastAPI process hosts static frontend assets, APIs, and schedulers. SQLite stores configuration and run records; `system-secrets.json` holds system keys. Source APIs and runtime control planes are external dependencies. Deploy a single instance.

## Development commands

Requires Python 3.12+, Node.js 20+, uv, and npm:

```bash
git clone https://github.com/dalamudx/ReleaseTracker.git
cd ReleaseTracker
make install
make dev
```

Open the frontend at `http://localhost:5173`. API and Swagger run at `http://localhost:8000` and `/docs`. Vite proxies development requests to the backend.

```bash
uv --directory backend run pytest -q
npm --prefix frontend run test
make lint
make build
```

Use `make dbmate-migrate` for migrations and `make version VERSION=x.y.z` to synchronize version metadata and the backend lockfile. Run `make help` for the full command list.

### Documentation maintenance

Maintain Chinese `.md` and English `.en.md` together, registering new pages in `mkdocs.yml`. Give each fact one primary home, retain legacy anchors, and use screenshots only where text is insufficient.

```bash
python -m pip install -r docs-requirements.txt
python -m unittest discover -s scripts/tests -p 'test_check_docs.py'
mkdocs build --strict --site-dir site
python scripts/check_docs.py --site-dir site
```

Checks cover translation pairs, navigation, internal links, images, and legacy anchors. Behavior changes still need support-matrix and example review; a passing build is not proof of accuracy. See [CI](.github/workflows/ci.yml) and the [release workflow](.github/workflows/release.yml) for dependency audits, SBOMs, and publishing.

## Roadmap

- More notification channels.
- Consult release notes and the Wiki support matrix for subsequent capabilities.

## Special thanks

[![LINUX.DO](https://img.shields.io/badge/LINUX.DO-Community-blue)](https://linux.do)

## License

GPL-3.0 License
