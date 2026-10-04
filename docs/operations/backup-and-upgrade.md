---
title: 备份、升级与密钥
---

# 备份、升级与密钥

以下命令在部署目录执行，假设数据挂载为 `./data`。升级前同时记录当前镜像标签（或摘要）和部署配置，它们与数据备份共同构成恢复点。

## 在线备份 {#online-backup}

在 **系统设置 → 备份** 中确认敏感信息提示后创建和下载 ZIP。备份使用 SQLite 在线快照，包含 `releases.db`、`system-secrets.json` 和校验清单；与密钥轮换互斥。数据库超过 2 GiB 或快照超过 120 秒时改用[停机备份](#backup)。

!!! warning "备份包含敏感数据"
    ZIP **不加密**，包含凭证、会话状态和加密密钥。只通过 HTTPS 下载，并在异机以加密方式保存。备份不包含环境变量、Kubernetes Secrets、外部挂载的 kubeconfig/SSH 文件和镜像。

归档自动存放在数据目录下的 `backups`（容器默认 `/app/backend/data/backups`），路径跟随数据库位置，无需单独配置。保留份数和自动备份周期在 **系统设置 → 全局配置** 中设置（见[系统设置](../reference/settings.md#global)）：

- **保留份数**（默认 7）：手动、自动和恢复前安全备份共用，新备份校验通过后才清理旧归档。
- **自动备份周期**（默认 0 关闭）：按最新归档时间续算，重启不重置；无归档或已逾期时约 5 分钟后补做。

旧版本的备份目录、份数和间隔环境变量已不再读取，升级后请在全局配置中重新设置。以下环境变量仍然有效：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `RELEASETRACKER_BACKUP_DAILY_RETENTION` | `0` | 最近 0–90 天每天额外保留最新一份（UTC） |
| `RELEASETRACKER_BACKUP_WEEKLY_RETENTION` | `0` | 最近 0–52 周每周额外保留最新一份（UTC） |
| `RELEASETRACKER_ONLINE_RESTORE` | POSIX 为 `1`，其他平台 `0` | 是否启用[页面在线恢复](#managed-restore-assessment) |
| `RELEASETRACKER_PRE_MIGRATION_BACKUP` | `1` | 迁移前自动备份，见[入口命令](#entry-commands) |

新归档先写入临时目录，重新核对校验和、SQLite 完整性和凭证解密后才出现在列表中；失败时不动已有归档。系统每天校验最新一份归档。备份页显示最近成功时间、连续失败次数与原因，超过两个周期没有成功备份时显示“已过期”。定时备份失败、归档损坏或曾有的归档全部丢失时，向订阅“任务处理异常”的通知渠道发送去重告警。

## 备份生命周期管理 {#backup-lifecycle}

备份页显示归档数量、总占用和当前保留策略。自动清理只在新备份成功后执行，不会为腾出空间先删旧备份。

管理员可在页面上删除指定归档（需输入归档名确认，不可撤销），但必须至少保留一份。正在下载的归档不能删除，自动清理会暂时跳过它。这些协调只在单个应用实例内有效，不要绕过页面手工删除文件。

## 页面在线恢复 {#managed-restore-assessment}

在 **备份 → 选定归档 → 在线恢复** 执行，无需手动停机。恢复期间应用短暂进入维护状态，不会停止或回滚已部署的业务容器。

1. **预检**：严格校验归档（大小、SHA256、SQLite 与关系一致性、schema 版本、密钥解密），展示时间点、应用版本和指纹。计划 10 分钟内有效，期间归档被锁定。
2. **确认**：输入完整文件名并确认数据将被替换。存在进行中的部署/恢复、下载或待人工处理的任务时拒绝恢复。
3. **维护排空**：新的写请求返回 503，等待在途请求和后台任务结束；超时则放弃切换，数据保持不变。
4. **切换**：先保存一份当前数据的“恢复前安全副本”，再替换数据库与密钥并重建服务。失败会回退到原数据；进程中途退出时，下次启动会先自动还原（`migrate-and-serve` 入口已包含此步骤）。
5. **重新登录并复核**：使用备份内的账号登录。旧会话、待执行任务、审批和待发通知已撤销，自动调度与通知保持暂停；核对现场后点击 **复核恢复状态** 恢复新操作，旧操作不会重放。

“恢复前安全副本”可在备份页下载，不计入常规保留，下次恢复时被替换，建议异机保存。需预留归档、暂存库和原数据的磁盘空间。

限制：仅支持 Linux/POSIX 文件系统；同一数据目录只能有一个应用进程（通过文件锁保证），需要多 worker 时设 `RELEASETRACKER_ONLINE_RESTORE=0` 禁用此功能；数据库和密钥不能是符号链接。只恢复可信且版本匹配的归档，不支持上传。应用无法启动或跨版本恢复时使用[命令行恢复](#restore)。

## 停机整目录备份 {#backup}

1. 停止实例：`docker compose stop` 或 `docker stop releasetracker`。
2. 备份整个数据目录：

    ```bash
    mkdir -p ./backups
    tar -czf "./backups/releasetracker-$(date +%Y%m%d-%H%M%S).tar.gz" ./data
    ```

3. 确认归档至少包含 `data/releases.db` 和 `data/system-secrets.json`，存放到受保护的位置，然后 `docker compose start` 或 `docker start releasetracker`。

不要在线逐个复制 `.db`、`.db-wal`、`.db-shm`；密钥与数据库必须来自同一恢复点。

## 升级 {#upgrade}

先完成备份，生产环境将示例 `latest` 替换为已验证的版本标签，并记录旧版本。

=== "Docker Compose"

    修改 `compose.yml` 中的镜像版本后执行：

    ```bash
    docker compose pull
    docker compose up -d
    docker compose logs --tail=100 releasetracker
    ```

=== "Docker run"

    拉取新镜像后重建容器，保留原有的 Socket 挂载、网络和安全参数：

    ```bash
    docker pull ghcr.io/dalamudx/releasetracker:latest
    docker stop releasetracker && docker rm releasetracker
    docker run -d \
      --name releasetracker \
      -p 127.0.0.1:8000:8000 \
      -v "$(pwd)/data:/app/backend/data" \
      --restart unless-stopped \
      ghcr.io/dalamudx/releasetracker:latest migrate-and-serve
    docker logs --tail=100 releasetracker
    ```

启动后登录检查配置、手动版本检查和运行历史。仅 `docker pull` 不会让旧容器切换到新镜像。

## 恢复实例 {#restore}

迁移后不保证旧版本能读取新 schema：降级要恢复旧数据并使用匹配的镜像，不能只改镜像标签。实例恢复不会撤销已对外部应用执行的更新，应用回滚见[健康检查与回滚](../guides/health-and-rollback.md)。

**从停机备份恢复**：停止当前实例并保留其数据目录以便排查；将备份解压到空目录，用备份对应的镜像版本挂载为 `/app/backend/data` 启动，确认没有其他实例使用该目录。

**从在线备份 ZIP 恢复（命令行）**：用于应用无法启动、跨版本或离线灾难恢复。在与备份版本匹配的镜像中执行（容器可用 `--entrypoint python`），并恢复到**尚不存在**的新目录：

```bash
python -m releasetracker.cli inspect-backup /backups/BACKUP.zip
# 先确认所有实例已停止；--confirm-stopped 只是确认，不会替你停止容器
python -m releasetracker.cli restore-backup /backups/BACKUP.zip \
  --destination /restore/new-data --confirm-stopped
```

校验覆盖 ZIP 大小、SHA256、SQLite 与关系完整性、迁移版本和密钥解密。SHA256 只能发现损坏，不能证明来源可信，不要恢复不可信的 ZIP。将新目录挂载到数据路径（自定义 `RELEASETRACKER_DB_PATH` 时指向新目录中的 `releases.db`），保留旧卷。

恢复后需要重新登录；旧的待执行任务、审批和待发通知被撤销，自动调度与通知暂停。核对远端版本和执行器配置后，在备份页点击 **复核恢复状态**，或对新数据库执行：

```bash
# 只读检查，报告关系问题，不自动修复
RELEASETRACKER_DB_PATH=/restore/new-data/releases.db python -m releasetracker.cli audit-database
# 解除新操作的门禁，完成后重启服务
RELEASETRACKER_DB_PATH=/restore/new-data/releases.db python -m releasetracker.cli acknowledge-restore --confirm-reviewed
```

仍需执行的旧目标应重新发起，旧任务不会重放。

## 轮换密钥 {#keys}

| 密钥 | 操作影响 |
| --- | --- |
| 会话签名密钥 | 现有会话失效，需要重新登录 |
| 数据加密密钥 | 重新加密已存储的加密字段；存在无法解密的数据时拒绝轮换 |

在 **系统设置 → 安全密钥** 操作，前后各保存一份备份。轮换中断时保留原数据和密钥文件并查看日志，不要手工删除密钥字段。

## 镜像入口命令 {#entry-commands}

| 命令 | 行为 |
| --- | --- |
| `migrate-and-serve` | 先迁移再启动，安装和升级推荐 |
| `migrate` | 只迁移；执行前停止其他实例 |
| `serve` | 只启动；要求 schema 已就绪 |

存在待执行迁移时，`migrate` / `migrate-and-serve` 会先在备份目录生成 `reason: pre_migration` 归档，失败则中止迁移。该归档只能用升级前的镜像恢复，并参与保留轮换，需要长期保存时及时下载；已有其他备份机制时可设 `RELEASETRACKER_PRE_MIGRATION_BACKUP=0` 关闭。数据库 schema 由 dbmate 管理，不要手工改表；相关错误见[启动与迁移排障](../reference/troubleshooting.md#startup)。

## 清理历史孤儿记录 {#prune-orphans}

创建备份时，已删除追踪器/执行器遗留的孤儿行只在**归档副本**中清理（统计于清单 `pruned_orphans`），在线数据库不变。需要修复在线数据库时，先预览，再停止所有实例后执行：

```bash
python -m releasetracker.cli prune-orphans --dry-run
python -m releasetracker.cli prune-orphans --confirm-stopped
python -m releasetracker.cli audit-database
```

清理前会自动保存未修改的数据库与密钥副本（`reason: pre_orphan_cleanup`）。只处理已知的历史关系，遇到未知损坏会回滚。不要在应用运行或密钥轮换时执行。

## 监控指标 {#metrics}

设置至少 32 字符的随机 `RELEASETRACKER_METRICS_TOKEN` 后开放 `GET /metrics`（未设置时返回 404）。请求须携带 `Authorization: Bearer <token>`，登录令牌不能代替；该令牌只能读取指标。建议通过内网地址每 60 秒抓取：

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

指标均为 gauge、带 `releasetracker_` 前缀，标签不含 URL、凭证或 ID，不要对其使用 `rate()`：

| 指标 | 含义 |
| --- | --- |
| `tasks` | 按任务类型/状态统计 |
| `task_oldest_pending_seconds` | 最早排队任务的等待时长 |
| `task_attempts_24h` / `task_attempt_duration_seconds_24h` | 近 24 小时完成次数与耗时总和 |
| `task_errors_24h` | 近 24 小时错误，按有限类别（含 TLS/安全） |
| `fetch_runs_24h` | 近 24 小时来源抓取结果 |
| `notification_outbox` / `notification_oldest_pending_seconds` | 通知队列状态 |
| `backup_last_success_timestamp_seconds` / `backup_failures` | 备份状态（失败计数重启归零） |

告警示例：`releasetracker_tasks{state="needs_attention"} > 0` 或 `releasetracker_notification_oldest_pending_seconds > 600` 持续 5 分钟。队列等待包含维护窗口，长时间排队不一定是故障。
