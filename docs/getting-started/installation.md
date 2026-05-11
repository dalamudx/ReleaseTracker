---
title: 安装部署
---

# 安装部署

本页覆盖 ReleaseTracker 的三种部署方式：Docker、Docker Compose、本地开发模式。生产环境推荐 Docker 或 Docker Compose。

## 1. 前置要求

| 项目 | 要求 | 备注 |
| ---- | ---- | ---- |
| 服务器 | Linux x86_64 | 容器镜像当前仅构建 `linux/amd64` |
| 端口 | `8000` 可对外或经反代 | 前端静态资源和 API 共用该端口 |
| 持久化目录 | 挂载到容器 `/app/backend/data` | SQLite 数据库与系统密钥落在此目录 |
| 出站网络 | 允许访问上游（GitHub/GitLab/…、OCI Registry） | 扫描版本时使用 |

如果需要本地开发或调试，另外需要：Python 3.12+、Node.js 20+、`npm`、`uv`。

!!! warning "请先规划持久化目录"
    不挂载目录也能跑，但容器重建后数据库和 `system-secrets.json` 都会丢失。丢失 `system-secrets.json` 会让所有已加密数据（凭证、OIDC 密钥、运行时连接密钥）**永久不可解密**。

## 2. Docker 部署

=== "单容器"

    ```bash
    mkdir -p ./data

    docker run -d \
      --name releasetracker \
      -p 8000:8000 \
      -v $(pwd)/data:/app/backend/data \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    ```

=== "Docker Compose"

    `docker-compose.yml`:

    ```yaml
    services:
      releasetracker:
        image: ghcr.io/dalamudx/releasetracker:latest
        container_name: releasetracker
        ports:
          - "8000:8000"
        volumes:
          - ./data:/app/backend/data
        restart: unless-stopped
        command: migrate-and-serve
    ```

    启动：

    ```bash
    docker compose up -d
    ```

### 入口命令

Docker 镜像支持三种入口命令：

| 命令 | 说明 | 典型场景 |
| ---- | ---- | ---- |
| `serve` | 只启动应用，不执行迁移 | 确认数据库 schema 已经是最新时使用 |
| `migrate` | 只执行数据库迁移 | 升级前预先迁移，避免应用启动阶段耗时 |
| `migrate-and-serve` | 先迁移后启动 | 默认推荐；新装 / 升级都能用 |

首次启动日志里会看到：

```
SQLite persistent connection established with WAL mode enabled
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 3. 首次登录

部署完成后访问 <http://localhost:8000>（或你的反代地址）。首次启动会自动创建默认管理员：

| 用户名 | 密码 |
| ------ | ---- |
| `admin` | `admin` |

!!! danger "立即修改默认密码"
    登录后第一件事：在右上角用户菜单打开「修改密码」并设置强密码。如果暴露在公网而没改密码，任何人都能接管这个实例。

## 4. 数据目录结构

挂载到容器内 `/app/backend/data` 的目录大致长这样：

```
data/
├── releases.db                 # SQLite 主库
├── releases.db-shm             # SQLite WAL 共享内存
├── releases.db-wal             # SQLite WAL 写前日志
└── system-secrets.json         # JWT 签名密钥 + Fernet 加密密钥
```

备份策略：

- **必须整体备份目录**：SQLite 的 `.db-wal` / `.db-shm` 与主库配套，分开备份可能得到不一致快照。
- **`system-secrets.json` 与数据库必须一起备份**：单独备份数据库无法解密其中任何加密字段。
- 建议在容器停止或 `PRAGMA wal_checkpoint` 后再拷贝；生产环境可先 `docker compose stop`，拷贝，再启动。

## 5. 反向代理（可选但推荐）

生产环境通常在 Nginx / Traefik / Caddy 后面运行 ReleaseTracker，以便提供 HTTPS、访问控制或子路径部署。

=== "Nginx"

    ```nginx
    server {
        listen 443 ssl http2;
        server_name releases.example.com;

        ssl_certificate     /etc/letsencrypt/live/releases.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/releases.example.com/privkey.pem;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host              $host;
            proxy_set_header X-Real-IP         $remote_addr;
            proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 300s;
        }
    }
    ```

=== "Caddy"

    ```caddy
    releases.example.com {
        reverse_proxy 127.0.0.1:8000
    }
    ```

=== "Traefik v2/v3"

    ```yaml
    services:
      releasetracker:
        image: ghcr.io/dalamudx/releasetracker:latest
        restart: unless-stopped
        volumes:
          - ./data:/app/backend/data
        command: migrate-and-serve
        labels:
          - "traefik.enable=true"
          - "traefik.http.routers.rt.rule=Host(`releases.example.com`)"
          - "traefik.http.routers.rt.entrypoints=websecure"
          - "traefik.http.routers.rt.tls.certresolver=letsencrypt"
          - "traefik.http.services.rt.loadbalancer.server.port=8000"
    ```

部署后务必在「系统设置 → 全局配置 → BASE URL」填写与反代匹配的公网地址，例如 `https://releases.example.com`。子路径部署（如 `https://example.com/releasetracker`）时 BASE URL 必须包含完整子路径。BASE URL 决定 OIDC callback：

```text
{BASE URL}/auth/oidc/{provider}/callback
```

BASE URL 配置错误时最明显的症状是 OIDC 登录回跳到错误域名或返回 `redirect_uri_mismatch`。

## 6. 升级

1. 停止当前容器：`docker compose stop`（或 `docker stop releasetracker`）。
2. 备份 `./data/` 目录。
3. 拉取新镜像：`docker compose pull`（或 `docker pull ghcr.io/dalamudx/releasetracker:latest`）。
4. 启动：`docker compose up -d`（`migrate-and-serve` 入口会自动迁移 schema）。
5. 观察日志确认迁移完成：`docker compose logs -f`。

!!! tip "跨版本升级前看一眼变更说明"
    涉及 schema 变更的版本会在 GitHub Release notes 里注明。dbmate 迁移是向前兼容的 —— 一旦迁移完成，降级到旧版本可能导致应用启动失败。

## 7. 本地开发模式

仅供贡献者或调试使用。

```bash
git clone https://github.com/dalamudx/ReleaseTracker.git
cd ReleaseTracker

make install       # 安装前后端依赖（需要 uv + npm）
make dev           # 并行启动后端 + 前端
```

默认端口：

- 前端（Vite）: <http://localhost:5173>
- 后端 API: <http://localhost:8000>
- Swagger UI / ReDoc: <http://localhost:8000/docs>、<http://localhost:8000/redoc>

前端在开发模式下将 `/api` 请求代理到后端，因此直接访问前端端口即可。

## 8. 常见部署问题

!!! failure "启动日志里出现 `system-secrets.json` 权限错误"
    表示容器无法写入数据目录。检查宿主机目录权限；容器内以 UID `1000`（或根据镜像用户）运行，需要该用户对挂载目录有读写权限：
    ```bash
    chown -R 1000:1000 ./data
    ```

!!! failure "OIDC 登录回跳失败（`redirect_uri_mismatch`）"
    - 确认「系统设置 → 全局配置 → BASE URL」的值与 IdP 里注册的 callback 前缀完全一致；
    - 子路径部署必须包含完整子路径；
    - 修改 BASE URL 后需要重新登录才会生效。

!!! failure "反代后访问 UI 能开，但调用 API 返回 404"
    很可能反代未把 `/api/*` 透传给 ReleaseTracker，或者把请求路径前缀剥掉了。ReleaseTracker 把 API 与静态资源挂在同一个 FastAPI 进程，反代规则对 `/` 和 `/api` 使用相同的 `proxy_pass` 即可，不要做路径重写。

!!! failure "升级后容器反复重启"
    先执行一次 `migrate` 入口命令查看迁移日志：
    ```bash
    docker run --rm \
      -v $(pwd)/data:/app/backend/data \
      ghcr.io/dalamudx/releasetracker:latest migrate
    ```
    如果报 schema 冲突，通常是手动改过数据库。恢复备份或从官方 schema 重建。

## 9. 下一步

- 按需配置反向代理与 BASE URL（见第 5 节）。
- 在「凭证管理」添加后续需要的 Token / OIDC Secret / 运行时连接密钥。
- 等待 **配置说明 / 追踪器 / 执行器 / 运维指南** 页面上线后按章节深入。
