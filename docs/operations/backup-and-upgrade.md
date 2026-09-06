---
title: 备份、升级与密钥
---

# 备份、升级与密钥

以下命令在部署目录执行，假设数据挂载为 `./data`。升级前保存当前镜像标签或摘要和部署配置；它们与数据备份共同构成恢复点。

## 一致备份 {#backup}

1. 停止实例：Compose 使用 `docker compose stop`，单容器使用 `docker stop releasetracker`。
2. 确认实例已停止，再备份整个目录：

    ```bash
    mkdir -p ./backups
    tar -czf "./backups/releasetracker-$(date +%Y%m%d-%H%M%S).tar.gz" ./data
    ```

3. 检查归档可读取，并至少包含 `data/releases.db` 和 `data/system-secrets.json`；将备份存放到受保护的位置。

不要在线逐个复制 `.db`、`.db-wal`、`.db-shm`。停机复制整目录可以避免 WAL 状态不一致；密钥与数据库必须来自同一恢复点。备份包含凭证、令牌和可能未脱敏的快照，应按敏感数据保护。

仅做备份时，完成后使用 `docker compose start` 或 `docker start releasetracker` 恢复运行。

## 升级 {#upgrade}

先完成上述停机备份，再执行对应流程。生产环境将示例 `latest` 替换为已验证的新版本标签，并记录旧版本。

=== "Docker Compose"

    修改 `compose.yml` 中的镜像版本（如使用固定标签），然后执行：

    ```bash
    docker compose pull
    docker compose up -d
    docker compose logs --tail=100 releasetracker
    ```

=== "Docker run"

    先拉取新镜像，再移除已停止的旧容器并重建。以下参数对应[安装示例](../getting-started/installation.md)；原实例若有额外 Socket 挂载、网络或安全参数，必须一并保留。

    ```bash
    docker pull ghcr.io/dalamudx/releasetracker:latest
    docker rm releasetracker
    docker run -d \
      --name releasetracker \
      -p 127.0.0.1:8000:8000 \
      -v "$(pwd)/data:/app/backend/data" \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    docker logs --tail=100 releasetracker
    ```

确认迁移和启动完成，登录后检查配置、手动版本检查与运行历史。仅 `docker pull` 或 `docker start` 不会让旧容器切换到新镜像。

## 恢复实例 {#restore}

1. 停止新版本实例，保留当前数据目录用于排查，不要覆盖唯一备份。
2. 将选定备份解压到空的恢复目录，确认数据库和密钥成对且权限正确。
3. 使用备份对应的镜像版本和部署配置，将恢复的 `data` 挂载为 `/app/backend/data`。
4. 确保没有另一个实例使用该目录，再启动并验证登录、凭证解密及追踪器配置。

新迁移应用后，不保证旧版本能读取新 schema。降级应恢复旧数据和匹配的镜像，而不是只修改镜像标签。实例恢复不会撤销已对外部应用执行的更新；应用恢复见[健康检查与回滚](../guides/health-and-rollback.md)。

## 轮换密钥 {#keys}

| 密钥 | 操作影响 |
| --- | --- |
| 会话签名密钥 | 现有 JWT 会话失效，需要重新登录 |
| 数据加密密钥 | 对已存储的加密字段重新加密；无法解密的数据会阻止轮换 |

在 **系统设置 → 安全密钥** 操作前先备份，完成后再次保存一致备份。若轮换中断，保留原数据和密钥文件，按错误日志排查；不要手工删除恢复用的密钥字段。

## 镜像入口命令 {#entry-commands}

| 命令 | 行为 |
| --- | --- |
| `migrate-and-serve` | 先迁移再启动，安装和升级推荐 |
| `migrate` | 只迁移；排查前先停止其他实例并备份 |
| `serve` | 只启动；要求 schema 已就绪 |

数据库 schema 由 dbmate 管理，不建议手工改表。相关错误见[启动与迁移排障](../reference/troubleshooting.md#startup)。
