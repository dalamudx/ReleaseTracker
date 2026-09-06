---
title: 安装与首次运行
---

# 安装与首次运行 {#_1}

目标：启动实例、登录并查到第一个项目的版本。生产部署使用 Docker 或 Docker Compose；本地开发见 [README](https://github.com/dalamudx/ReleaseTracker#开发命令)。

## 部署要求 {#1}

- 官方镜像：`linux/amd64`；应用和 API 共用端口 `8000`。
- 将数据目录持久化到 `/app/backend/data`，允许进程读写。
- 允许实例访问需要追踪的上游服务。
- 仅运行一个实例，不要把同一数据目录挂载给多个活动副本。

!!! warning "保留数据和密钥"
    数据目录包含数据库与 `system-secrets.json`。容器重建时必须复用该目录；丢失密钥会导致加密凭证无法解密。

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

## 首次登录 {#3}

1. 访问 <http://localhost:8000>。
2. 从首次启动日志读取 `admin` 的一次性密码：

    ```bash
    docker logs releasetracker 2>&1 | grep "one-time bootstrap admin password"
    ```

3. 登录后立即打开 **用户菜单 → 用户设置 → 修改密码**。

引导密码仅在首次初始化时记录，请限制日志访问。已有安装不会重新生成密码；忘记密码或旧默认密码被禁用时，使用[本地恢复命令](../operations/accounts-and-oidc.md#password-recovery)。

## 第一次版本检查 {#4}

1. 在 **追踪器** 中新建项目，添加一个 GitHub 来源，例如 `cli/cli`。
2. 启用 `stable` 发布渠道，选择 Release，先保留默认抓取设置。
3. 保存并手动检查，确认能看到版本、来源和发布说明。

公开来源可先使用匿名 REST 访问；遇到限流再添加凭证。完整筛选和排序规则见[追踪器与版本规则](../guides/trackers.md)。

## 下一步 {#10}

- 只追踪版本：[Webhook 通知](../guides/notifications.md)。
- 还要更新服务：[凭证与运行时](../guides/runtime-connections.md) → [执行器](../guides/executors.md)。
- [数据目录与备份](../operations/backup-and-upgrade.md#backup){#5}。
- [反向代理与子路径](../operations/reverse-proxy.md){#6}。
- [升级实例](../operations/backup-and-upgrade.md#upgrade){#7}。
- [本地开发](https://github.com/dalamudx/ReleaseTracker#开发命令){#8}。
- [部署故障排查](../reference/troubleshooting.md){#9}。
