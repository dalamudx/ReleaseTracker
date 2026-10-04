---
title: 安装与首次运行
---

# 安装与首次运行 {#_1}

目标：启动实例、登录并查到第一个项目的版本。生产部署使用 Docker 或 Docker Compose；本地开发见 [README](https://github.com/dalamudx/ReleaseTracker#开发命令)。

## 部署要求 {#1}

- 官方镜像 `linux/amd64`，应用和 API 共用端口 `8000`。
- 持久化并保持可写的数据目录 `/app/backend/data`：其中包含数据库、密钥文件和 `backups` 备份目录。
- 只运行一个实例，不要把同一数据目录挂载给多个活动副本。
- 允许实例访问需要追踪的上游服务。
- 镜像以 root 运行，已有数据卷可能属于 root；改为非 root 运行前先备份并迁移卷权限。

!!! warning "保留数据和密钥"
    重建容器时必须复用数据目录。丢失 `system-secrets.json` 会导致已加密的凭证无法解密。

## 启动容器 {#2-docker}

以下示例只监听宿主机回环地址，适合本机访问或同机反向代理。远程访问请先配置 [HTTPS 代理](../operations/reverse-proxy.md)，不要直接开放管理端口。

=== "Docker Compose"

    保存为 `compose.yml`：

    ```yaml
    services:
      releasetracker:
        image: ghcr.io/dalamudx/releasetracker:latest
        container_name: releasetracker
        ports:
          - "127.0.0.1:8000:8000"
        volumes:
          - ./data:/app/backend/data
        restart: unless-stopped
        command: migrate-and-serve
    ```

    ```bash
    mkdir -p ./data
    docker compose up -d
    ```

=== "Docker run"

    ```bash
    mkdir -p ./data
    docker run -d \
      --name releasetracker \
      -p 127.0.0.1:8000:8000 \
      -v "$(pwd)/data:/app/backend/data" \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    ```

`migrate-and-serve` 先执行数据库迁移再启动；其他[入口命令](../operations/backup-and-upgrade.md#entry-commands){#_2}用于维护。生产环境建议将 `latest` 换成已验证的版本标签。

## Kubernetes 探针与资源 {#kubernetes-probes}

单副本 Deployment 的容器参考配置（非完整清单）。保持 `replicas: 1` 并使用 `strategy.type: Recreate`，避免升级时两个进程同时访问数据卷；迁移较慢时增大启动探针等待时间。

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: "1"
    memory: 512Mi
startupProbe:
  httpGet:
    path: /api/health/live
    port: 8000
  periodSeconds: 5
  failureThreshold: 60
readinessProbe:
  httpGet:
    path: /api/health/ready
    port: 8000
  periodSeconds: 10
  timeoutSeconds: 3
livenessProbe:
  httpGet:
    path: /api/health/live
    port: 8000
  periodSeconds: 30
  timeoutSeconds: 3
```

## 可选环境变量 {#environment}

大部分配置在界面中完成，以下变量用于部署层面的调整，启动时生效：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `RELEASETRACKER_DB_PATH` | `data/releases.db` | 数据库路径；密钥文件和 `backups` 目录自动位于其同目录 |
| `RELEASETRACKER_CORS_ORIGINS` | 空（仅同源） | 逗号分隔的完整 HTTP(S) 来源，不可使用 `*` |
| `RELEASETRACKER_WORKER_POLL_SECONDS` | `2` | 后台任务轮询间隔（1–60）；调大可降低空闲占用，但会增加部署、通知等延迟 |
| `RELEASETRACKER_DEPLOYMENT_CONCURRENCY` | `1` | 部署与恢复共享的并发名额（1–3）；同一目标或同一执行器仍串行 |
| `RELEASETRACKER_READINESS_PULL_GRACE_SECONDS` | `1800` | Kubernetes 镜像拉取宽限，见[健康检查](../guides/health-and-rollback.md#image-pull) |
| `RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS` | `0` | 部署后巡检，见[只读巡检](../guides/health-and-rollback.md#runtime-watch) |
| `RELEASETRACKER_METRICS_TOKEN` | 空 | 开启 `/metrics`，见[监控指标](../operations/backup-and-upgrade.md#metrics) |

备份相关变量见[在线备份](../operations/backup-and-upgrade.md#online-backup)。调高并发前先完成备份并观察资源占用，并发设置不会带来多实例支持。

## 登录与会话安全 {#security}

- 登录接口按客户端地址限制为每分钟 10 次失败请求（全局每分钟 100 次），返回 `429` 时按 `Retry-After` 重试。经代理部署时为 Uvicorn 配置可信代理地址，否则无法区分客户端。
- 浏览器会话使用 HttpOnly Cookie 和 CSRF 校验；HTTPS 下启用 `Secure` 与 `__Host-` 前缀，因此生产环境需要正确的 BASE URL 和可信代理配置。
- CLI/API 继续使用 `/api/auth/token` 与 Bearer 令牌，不依赖 Cookie。

## 首次登录 {#3}

1. 访问 <http://localhost:8000>。
2. 从首次启动日志读取 `admin` 的一次性密码：

    ```bash
    docker logs releasetracker 2>&1 | grep "one-time bootstrap admin password"
    ```

3. 登录后立即打开 **用户菜单 → 用户设置 → 修改密码**。

引导密码仅在首次初始化时记录，请限制日志访问。忘记密码时使用[本地恢复命令](../operations/accounts-and-oidc.md#password-recovery)。

## 第一次版本检查 {#4}

1. 在 **追踪器** 中新建项目，添加一个 GitHub 来源，例如 `cli/cli`。
2. 启用 `stable` 发布渠道，选择 Release，先保留默认抓取设置。
3. 保存并手动检查，确认能看到版本、来源和发布说明。

公开来源可先匿名访问，遇到限流再添加凭证。完整筛选规则见[追踪器与版本规则](../guides/trackers.md)。

## 下一步 {#10}

- 只追踪版本：[消息通知](../guides/notifications.md)。
- 还要更新服务：[凭证与运行时](../guides/runtime-connections.md) → [执行器](../guides/executors.md)。
- [数据目录与备份](../operations/backup-and-upgrade.md#backup){#5}。
- [反向代理与子路径](../operations/reverse-proxy.md){#6}。
- [升级实例](../operations/backup-and-upgrade.md#upgrade){#7}。
- [本地开发](https://github.com/dalamudx/ReleaseTracker#开发命令){#8}。
- [部署故障排查](../reference/troubleshooting.md){#9}。
