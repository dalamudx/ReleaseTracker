---
title: 安装部署
---

# 安装部署

本页说明 ReleaseTracker 的三种部署方式：Docker、Docker Compose、本地开发模式。生产环境推荐 Docker 或 Docker Compose。

## 1. 前置要求

| 项目 | 要求 | 说明 |
| ---- | ---- | ---- |
| 操作系统 | Linux x86_64 | 官方容器镜像目前仅提供 `linux/amd64` 构建。 |
| 监听端口 | `8000` | 前端静态资源与 API 共用该端口；经反向代理暴露亦可。 |
| 持久化目录 | 挂载到容器内 `/app/backend/data` | SQLite 数据库与系统密钥均落在此目录。 |
| 出站网络 | 可访问上游（GitHub/GitLab/… 与各 OCI Registry） | 执行版本扫描时使用。 |

本地开发或调试额外需要：Python 3.12+、Node.js 20+、`npm`、`uv`。

!!! warning "务必配置持久化目录"
    不挂载持久化目录也能运行，但容器重建后数据库和 `system-secrets.json` 会全部丢失。丢失 `system-secrets.json` 会使已加密的凭证、OIDC 客户端密钥、运行时连接密钥**永久无法解密**。

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

    `docker-compose.yml`：

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

镜像支持三种入口命令：

| 命令 | 行为 | 建议使用场景 |
| ---- | ---- | ---- |
| `serve` | 仅启动应用，不执行数据库迁移。 | 已经确认 schema 为最新时使用。 |
| `migrate` | 仅执行数据库迁移。 | 升级前预先迁移，将迁移开销与启动过程分离。 |
| `migrate-and-serve` | 先迁移再启动。 | 默认推荐；新装与升级均适用。 |

应用正常启动后，日志中会出现类似如下条目：

```
SQLite persistent connection established with WAL mode enabled
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 3. 首次登录

访问 <http://localhost:8000>（或反向代理的对外地址）。首次启动会自动创建默认管理员账户：

| 用户名 | 密码 |
| ------ | ---- |
| `admin` | `admin` |

!!! danger "请立即修改默认密码"
    登录后第一时间修改密码：打开**左下角用户菜单 → 用户设置 → 修改密码**。如果使用默认凭证将实例暴露在公网，任何人都可直接接管。

## 4. 快速开始

服务安装并可访问后，可以按下面的工作流完成首次配置：

1. **打开 Web UI 并登录**：访问部署地址，使用默认管理员账户登录；首次登录后先修改默认密码。
2. **检查系统设置**：打开 **系统设置**，确认 BASE URL、语言、日志级别、保留策略等基础配置符合部署环境。使用反向代理或 OIDC 时，BASE URL 必须与外部访问地址一致。
3. **添加通知渠道**：打开 **通知**，配置并测试 Webhook 等通知渠道。通知不是必需项，但建议在启用立即或维护窗口执行前先配置并测试，方便及时收到失败、跳过或成功事件。
4. **按需添加凭证**：只有访问私有代码仓库、受保护的 GitHub / GitLab / Gitea 项目、私有镜像仓库、Kubernetes、Portainer 等服务时才需要在 **凭证管理** 中添加凭证。公开来源可以先跳过。
5. **添加运行时连接**：如果计划让 ReleaseTracker 执行更新，在 **运行时连接** 中添加 Docker、Podman、Kubernetes 或 Portainer 连接；Helm Release 执行器使用 Kubernetes 连接。仅做版本追踪时可以不配置。
6. **添加追踪器和发布来源**：在 **追踪器** 中添加需要关注的项目、镜像或 Helm Chart，并按需要设置发布渠道 / 来源筛选。
7. **创建执行器**：从支持执行的版本来源创建执行器，选择目标运行时、更新目标和执行策略。建议先使用手动执行确认配置正确，再启用立即或维护窗口执行。
8. **配置健康检查**：在目标支持且服务需要验证可用性时，添加 HTTP、TCP、Helm 状态或运行时原生健康检查。健康检查失败会记录失败结果，但不会自动回滚。
9. **运行一次或等待调度**：手动运行执行器进行验证，或等待追踪器 / 执行器按计划运行。
10. **查看历史、快照与回滚**：在执行历史中查看每次运行结果。破坏性 Docker / Podman 单容器和 Docker / Podman Compose 分组目标会在更新前保留快照；更新或健康检查失败后不会自动回滚，需要在确认快照可用后由操作员手动触发回滚。

## 5. 数据目录结构

挂载到 `/app/backend/data` 的目录中通常包含：

```
data/
├── releases.db                 # SQLite 主库
├── releases.db-shm             # SQLite WAL 共享内存
├── releases.db-wal             # SQLite WAL 写前日志
└── system-secrets.json         # JWT 签名密钥 + Fernet 加密密钥
```

备份建议：

- **整目录一致备份**：`.db-wal` / `.db-shm` 与主库为一组，分开拷贝可能得到不一致快照。
- **`system-secrets.json` 与数据库必须一同备份**：缺少密钥文件时，数据库中的加密字段（凭证、OIDC 客户端密钥、运行时连接密钥）将无法解密。
- 生产环境建议先 `docker compose stop`（或 `docker stop releasetracker`）后再进行文件级复制。

## 6. 反向代理（可选但推荐）

生产环境通常在 Nginx / Traefik / Caddy 后方运行 ReleaseTracker，用于提供 HTTPS、访问控制或子路径部署。

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

部署完成后，在 **系统设置 → 全局配置 → BASE URL** 填写与反向代理一致的公网地址，例如 `https://releases.example.com`；若部署在子路径下（如 `https://example.com/releasetracker`），BASE URL 必须包含完整子路径。BASE URL 决定 OIDC callback：

```text
{BASE URL}/auth/oidc/{provider}/callback
```

BASE URL 与实际访问地址不一致时，最常见的表现是 OIDC 登录回跳到错误域名或返回 `redirect_uri_mismatch`。

## 7. 升级

1. 停止当前容器：`docker compose stop`，或 `docker stop releasetracker`。
2. 备份 `./data/` 目录（参见第 5 节）。
3. 拉取新镜像：`docker compose pull`，或 `docker pull ghcr.io/dalamudx/releasetracker:latest`。
4. 重新启动：`docker compose up -d`。`migrate-and-serve` 入口会在启动前自动执行 dbmate 迁移。
5. 观察日志确认迁移完成：`docker compose logs -f`。

!!! note "降级说明"
    dbmate 迁移为向前兼容。一旦新版本的迁移已执行，再回退到旧版本容器可能因 schema 不匹配而无法启动，需要从第 2 步的备份恢复。

## 8. 本地开发模式

仅用于贡献代码或在本地调试。

```bash
git clone https://github.com/dalamudx/ReleaseTracker.git
cd ReleaseTracker

make install       # 安装前后端依赖（需要 uv 与 npm）
make dev           # 并行启动后端 + 前端
```

默认端口：

- 前端（Vite）：<http://localhost:5173>
- 后端 API：<http://localhost:8000>
- Swagger UI / ReDoc：<http://localhost:8000/docs>、<http://localhost:8000/redoc>

开发模式下 Vite 会将 `/api` 请求代理到后端，因此浏览器直接访问前端端口即可。

## 9. 常见部署问题

!!! failure "容器无法写入 `data/` 目录"
    容器默认以 `root` 用户运行，通常不会遇到权限问题。若以非 root 或受限运行时（rootless Docker、SELinux 等）部署，确保挂载目录对进程用户可读写，例如：
    ```bash
    chmod -R u+rwX ./data
    ```

!!! failure "OIDC 登录回跳失败（`redirect_uri_mismatch`）"
    - 确认 **系统设置 → 全局配置 → BASE URL** 的值与在 IdP 侧注册的 callback 前缀完全一致；
    - 子路径部署必须包含完整子路径；
    - 修改 BASE URL 后需要退出并重新登录方能生效。

!!! failure "反向代理后访问 UI 正常，但接口返回 404"
    通常是反向代理没有将 `/api/*` 透传到后端，或对路径前缀进行了重写。ReleaseTracker 将前端静态资源与 API 挂在同一个 FastAPI 进程，反向代理对 `/` 与 `/api` 使用相同的 `proxy_pass` 即可，不要做路径重写。

!!! failure "升级后容器反复重启"
    先以 `migrate` 入口单独执行一次迁移并查看输出：
    ```bash
    docker run --rm \
      -v $(pwd)/data:/app/backend/data \
      ghcr.io/dalamudx/releasetracker:latest migrate
    ```
    若提示 schema 冲突，通常是数据库被手动修改过。建议从备份恢复。

## 10. 下一步

- 根据需要配置反向代理并设置 BASE URL（第 6 节）。
- 在 **凭证管理** 中录入后续需要的 Token / OIDC 客户端密钥 / 运行时连接密钥。
- 关注后续发布的 **配置说明 / 追踪器 / 执行器 / 运维** 章节。
