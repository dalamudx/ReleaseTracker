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

旧版本的备份目录、份数和间隔环境变量已不再读取，升级后请在全局配置中重新设置。以下策略同样只由持久化设置控制：

| 设置项 | 默认值 | 含义 |
| --- | --- | --- |
| `system.online_restore` | POSIX 为 `true`，其他平台 `false` | 是否启用[页面在线恢复](#managed-restore-assessment)；保存后重启服务生效，非 POSIX 平台不能启用 |
| `system.pre_migration_backup` | `true` | 迁移前自动备份，见[入口命令](#entry-commands)；在该命令下次执行时生效 |

升级时可一次性导入旧的 `RELEASETRACKER_ONLINE_RESTORE` 和 `RELEASETRACKER_PRE_MIGRATION_BACKUP` 取值：

```bash
docker compose run --rm releasetracker import-legacy-settings
```

导入是显式且可重复执行的：只补写尚未保存的设置项，已保存的值（包括显式 `false` 和 `0`）保持不变；非法取值整体放弃，不写入任何设置。导入后删除这些旧变量并重启服务，之后只读取持久化设置。

新归档先写入临时目录，重新核对校验和、SQLite 完整性和凭证解密后才出现在列表中；失败时不动已有归档。系统每天校验最新一份归档。备份页显示最近成功时间、连续失败次数与原因，超过两个周期没有成功备份时显示“已过期”。定时备份失败、归档损坏或曾有的归档全部丢失时，向订阅“任务处理异常”的通知渠道发送去重告警。

## 备份生命周期管理 {#backup-lifecycle}

备份页显示归档数量、总占用和当前保留策略。自动清理只在新备份成功后执行，不会为腾出空间先删旧备份。

管理员可在页面上删除指定归档（需输入归档名确认，不可撤销），但必须至少保留一份。正在下载的归档不能删除，自动清理会暂时跳过它。这些协调只在单个应用实例内有效，不要绕过页面手工删除文件。

## 页面在线导入 {#online-import}

在 **系统设置 → 实例备份** 选择可信的 ReleaseTracker ZIP 文件并点击 **在线导入**。上传后会核对归档成员、大小、校验和、数据库完整性、schema 兼容性及配对密钥的解密能力，通过后才加入列表。导入不替换当前数据库或密钥，也不会自动恢复；需要应用该备份时，再使用列表中的 **在线恢复**。

导入保留归档内的原备份时间，使用新的服务端文件名，不覆盖已有归档，并遵循常规保留策略。数据库大小上限为 2 GiB；经反向代理访问时，还需允许相应大小的 ZIP 请求。上传或校验失败时会清理暂存文件。

## 页面在线恢复 {#managed-restore-assessment}

在 **备份 → 选定归档 → 在线恢复** 执行，无需手动停机。恢复期间应用短暂进入维护状态，不会停止或回滚已部署的业务容器。

1. **预检**：严格校验归档（大小、SHA256、SQLite 与关系一致性、schema 版本、密钥解密），展示时间点、应用版本和指纹。计划 10 分钟内有效，期间归档被锁定。
2. **确认**：输入完整文件名并确认数据将被替换。存在进行中的部署/恢复、下载或待人工处理的任务时拒绝恢复。
3. **维护排空**：新的写请求返回 503，等待在途请求和后台任务结束；超时则放弃切换，数据保持不变。
4. **切换**：先保存一份当前数据的“恢复前安全副本”，再替换数据库与密钥并重建服务。失败会回退到原数据；进程中途退出时，下次启动会先自动还原，无论在线恢复当前是否启用。
5. **重新登录并复核**：使用备份内的账号登录。旧会话、待执行任务、审批和待发通知已撤销，自动调度与通知保持暂停；核对现场后点击 **复核恢复状态** 恢复新操作，旧操作不会重放。

在备份列表下方的 **上传恢复** 区域选择 ZIP 并点击 **在线导入**。校验通过后，从列表中找到导入的归档，点击 **在线恢复** 并确认。切换前自动生成的安全副本独立于常规保留策略，下次恢复时会被替换。

限制：仅支持 Linux/POSIX 文件系统；同一数据目录只能有一个应用进程（通过文件锁保证），需要多 worker 时在全局配置关闭 **在线恢复** 并重启服务（非 POSIX 平台始终关闭）；数据库和密钥不能是符号链接。即使在关闭状态下，启动仍会先还原被中断的恢复过程。只恢复可信且版本匹配的归档，上传的归档需先通过导入校验。应用无法启动或跨版本恢复时使用[命令行恢复](#restore)。

## 从自定义数据路径迁移 {#custom-path}

旧版本可用 `RELEASETRACKER_DB_PATH` 指定数据库位置。该变量已移除：服务、CLI、备份和 dbmate 只使用固定的 `backend/data`（容器内 `/app/backend/data`），密钥文件和 `backups` 目录位于同一目录。

1. 停止旧服务，确认数据库与 `system-secrets.json` 的备份可用，并记下原数据挂载点。
2. 关闭所有数据库连接，保留原库旁的 `-wal` 文件。将命令中的路径替换为原数据库的实际路径，运行 `sqlite3 'file:/path/to/original.db?mode=rw' 'PRAGMA wal_checkpoint(TRUNCATE);'`。只有命令成功且结果的第一列为 `0` 时才继续。把完成检查点的数据库复制到空的固定数据目录，命名为 `releases.db`，再复制配对的 `system-secrets.json` 和 `backups` 目录。保留原文件以便回退。
3. 确认固定目录中的数据库与密钥来自原实例，再移除 `RELEASETRACKER_DB_PATH`。只要该变量仍指向其他路径，服务和迁移就会拒绝启动。
4. 启动新版本，验证登录、追踪器和备份列表。

回退：停止服务，恢复原镜像与原挂载即可回到旧数据；新的固定目录保留不动。

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

校验覆盖 ZIP 大小、SHA256、SQLite 与关系完整性、迁移版本和密钥解密。SHA256 只能发现损坏，不能证明来源可信，不要恢复不可信的 ZIP。将新目录挂载到固定数据路径（容器内 `/app/backend/data`），保留旧卷。

恢复后需要重新登录；旧的待执行任务、审批和待发通知被撤销，自动调度与通知暂停。核对远端版本和执行器配置后，在备份页点击 **复核恢复状态**，或对新数据执行（先挂载到固定数据路径，CLI 与服务的数据库位置一致）：

```bash
# 只读检查，报告关系问题，不自动修复
python -m releasetracker.cli audit-database
# 解除新操作的门禁，完成后重启服务
python -m releasetracker.cli acknowledge-restore --confirm-reviewed
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
| `import-legacy-settings` | 一次性导入旧策略变量；数据库和密钥必须已存在，缺失时报错退出 |

存在待执行迁移时，`migrate` / `migrate-and-serve` 会先在备份目录生成 `reason: pre_migration` 归档，失败则中止迁移。该归档只能用升级前的镜像恢复，并参与保留轮换，需要长期保存时及时下载；已有其他备份机制时可在全局配置关闭 **迁移前备份**（`system.pre_migration_backup=false`），该设置在下一次迁移命令执行时生效。数据库 schema 由 dbmate 管理，不要手工改表；相关错误见[启动与迁移排障](../reference/troubleshooting.md#startup)。

`make dbmate-migrate` 也复用上述入口，在迁移前备份数据库及密钥。时间语义迁移 `20261012000001` 会在**同一事务**中按既有白名单自动清理已删除父记录遗留的 `CASCADE` / `SET NULL` 关系，然后重建表；无需提前手动执行 `prune-orphans`。此次迁移的安全归档保留**清理前原始记录**，不会提前清理备份副本。清理统计写入设置 `internal.release_time_pruned_orphans`，任务队列不变；未知外键损坏或部署观察关系异常会导致整次迁移（含清理）回滚。执行前仍须停止其他写库实例。直接调用 dbmate 时，应自行先备份数据库及密钥；含孤儿记录的原样安全归档须离线处理后才能通过普通恢复校验。

## 清理历史孤儿记录 {#prune-orphans}

创建备份时，已删除追踪器/执行器遗留的孤儿行只在**归档副本**中清理（统计于清单 `pruned_orphans`），在线数据库不变。需要修复在线数据库时，先预览，再停止所有实例后执行：

```bash
python -m releasetracker.cli prune-orphans --dry-run
python -m releasetracker.cli prune-orphans --confirm-stopped
python -m releasetracker.cli audit-database
```

清理前会自动保存未修改的数据库与密钥副本（`reason: pre_orphan_cleanup`）。只处理已知的历史关系，遇到未知损坏会回滚。不要在应用运行或密钥轮换时执行。

## 监控指标 {#metrics}

在**全局配置 → 网络配置**中生成 16 位令牌，点击“保存”后即可使用 `GET /metrics`。管理员可随时返回此页复制令牌。请求使用 `Authorization: Bearer <token>`。撤销令牌并保存后，接口返回 404。建议通过内网地址每 60 秒抓取一次。

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
