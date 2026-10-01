---
title: 备份、升级与密钥
---

# 备份、升级与密钥

以下命令在部署目录执行，假设数据挂载为 `./data`。升级前保存当前镜像标签或摘要和部署配置；它们与数据备份共同构成恢复点。

## 在线备份与停机恢复 {#online-backup}

管理员可在 **系统设置 → 备份** 中确认敏感信息提示后创建和下载 ZIP。备份使用 SQLite 在线快照，并与密钥轮换互斥；包含 `releases.db`、`system-secrets.json` 和校验清单。失败时不会清理已有备份。单个数据库上限 2 GiB，快照超时 120 秒；更大的实例使用下面的停机备份。

ZIP **不加密**，包含凭证、会话状态和加密密钥；只通过 HTTPS 下载并私密存储。默认保存在数据库旁的 `backups` 目录，不会自动复制到异机。下载后应使用外部加密存储保留异机副本。备份不包含环境变量、Kubernetes Secrets、挂载在外部的 kubeconfig/SSH 文件及镜像，请另外保存部署配置和镜像摘要。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `RELEASETRACKER_BACKUP_INTERVAL_HOURS` | `0` | 0 关闭，1–168 为自动备份间隔小时数；启动后等待一个周期，不立即执行 |
| `RELEASETRACKER_BACKUP_RETENTION` | `7` | 每次成功后保留最近 1–100 份，手动和自动备份共享保留策略 |
| `RELEASETRACKER_BACKUP_DIR` | 数据库旁的 `backups` | 受保护的备份目录，可挂载独立存储 |

恢复仅提供本地 CLI，不允许通过网页覆盖运行中的数据库。先用备份对应版本的镜像验证，再停止实例并恢复到**尚不存在**的新目录。校验包括 ZIP 成员和大小限制、SHA256、SQLite 完整性、迁移版本完全匹配，以及凭证/快照解密。SHA256 只能发现损坏，不能证明来源可信；不要恢复不可信 ZIP。

```bash
# 在匹配版本的应用环境执行；容器可用 --entrypoint python 调用
python -m releasetracker.cli inspect-backup /backups/BACKUP.zip
# 确认所有实例停止；--confirm-stopped 是操作者确认，不会代替你停止容器
python -m releasetracker.cli restore-backup /backups/BACKUP.zip \
  --destination /restore/new-data --confirm-stopped
```

保留旧卷，将新目录挂载到应用数据路径；自定义 `RELEASETRACKER_DB_PATH` 时应指向新目录中的 `releases.db`。恢复操作不自动启动实例、不撤销外部部署。重新启动前隔离执行器网络或外部运行时凭证，核对待部署任务及实际运行版本，避免旧任务重放；恢复会清空会话与临时 OAuth 状态，避免已注销的旧会话复活；需要重新登录。原 ZIP 不会修改。旧版本数据先用匹配镜像恢复，再走升级迁移，不直接跳过兼容性检查。

## 监控指标 {#metrics}

设置至少 32 字符的随机 `RELEASETRACKER_METRICS_TOKEN` 后开放 `GET /metrics`。未设置或太短时返回 404；请求必须携带独立的 `Authorization: Bearer ...`，普通登录令牌不能代替。令牌只用于读取指标，不具备管理权限。建议通过集群内地址每 60 秒抓取，不将其直接暴露公网。

```yaml
scrape_configs:
  - job_name: releasetracker
    scrape_interval: 60s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/releasetracker-metrics-token
    static_configs:
      - targets: [releasetracker.infra.svc:8000]
```

所有指标为 gauge：`releasetracker_tasks` 按任务类型/状态统计（含隐藏记录），`task_oldest_pending_seconds` 为最早排队时长，`task_attempts_24h` 和 `task_attempt_duration_seconds_24h` 是近 24 小时完成次数与耗时总和，`task_errors_24h` 区分 TLS/安全错误，其余错误归为有限类别。`fetch_runs_24h` 包含成功、部分成功、失败和中断的来源持久化记录；抓取失败可能发生在创建来源记录前，完整抓取失败率应以任务尝试为准。指标名称均带 `releasetracker_` 前缀。

通知指标为 `notification_outbox`、`notification_oldest_pending_seconds`；备份指标为 `backup_last_success_timestamp_seconds`、`backup_failures`（进程内状态，重启归零）。标签不包含 URL、凭证、任务名称或任意 ID。保留策略会降低历史统计值，不应对这些 gauge 使用 `rate()`。

告警示例：`releasetracker_tasks{state="needs_attention"} > 0` 持续 5 分钟；`releasetracker_notification_oldest_pending_seconds > 600` 持续 5 分钟。队列等待包含维护窗口，不能仅凭长时间排队认定故障。抓取平均耗时可用 `task_attempt_duration_seconds_24h / clamp_min(task_attempts_24h, 1)`（两个名称均补全上述前缀）。

## 停机整目录备份 {#backup}

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
