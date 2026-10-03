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
| `RELEASETRACKER_BACKUP_INTERVAL_HOURS` | `0` | 0 关闭，1–168 为自动备份间隔小时数；按最新一份备份的时间续算，重启不会重新计时；没有备份或已过期时，启动约 5 分钟后补做一次 |
| `RELEASETRACKER_BACKUP_RETENTION` | `7` | 每次成功后保留最近 1–100 份，手动和自动备份共享保留策略 |
| `RELEASETRACKER_BACKUP_DAILY_RETENTION` | `0` | 最近 0–90 天每天额外保留最新一份（UTC），叠加最近份数策略 |
| `RELEASETRACKER_BACKUP_WEEKLY_RETENTION` | `0` | 最近 0–52 周每 ISO 周额外保留最新一份（UTC），叠加最近份数策略 |
| `RELEASETRACKER_BACKUP_DIR` | 数据库旁的 `backups` | 受保护的备份目录，可挂载独立存储 |

新 ZIP 先写入受保护的临时目录，重新读取以核对校验和、SQLite 完整性和凭证解密；验证通过后才原子发布到下载列表，最后清理旧归档。校验失败绝不清理旧恢复点；取消请求也会等待工作线程结束再释放备份/密钥轮换锁。另每天校验最新一份已存归档，重启按上次成功校验时间续算，自动备份关闭时也会校验手动归档。存量校验允许旧 schema，恢复仍要求匹配版本。损坏校验会保存失败状态并通知；修复后清除该次校验警告，但不会掩盖备份创建失败。备份页显示最近一次成功时间、连续失败次数与原因；超过两个周期没有成功备份时显示“已过期”。定时备份失败会通过订阅“错误”事件的通知渠道提醒。

恢复仅提供本地 CLI，不允许通过网页覆盖运行中的数据库。先用备份对应版本的镜像验证，再停止实例并恢复到**尚不存在**的新目录。校验包括 ZIP 成员和大小限制、SHA256、SQLite 完整性、外键及任务—运行记录—健康观察的关系一致性、迁移版本完全匹配，以及凭证/快照解密。关系损坏的归档不能作为校验成功的恢复点。SHA256 只能发现损坏，不能证明来源可信；不要恢复不可信 ZIP。

```bash
# 在匹配版本的应用环境执行；容器可用 --entrypoint python 调用
python -m releasetracker.cli inspect-backup /backups/BACKUP.zip
# 确认所有实例停止；--confirm-stopped 是操作者确认，不会代替你停止容器
python -m releasetracker.cli restore-backup /backups/BACKUP.zip \
  --destination /restore/new-data --confirm-stopped
```

保留旧卷，将新目录挂载到应用数据路径；自定义 `RELEASETRACKER_DB_PATH` 时应指向新目录中的 `releases.db`。恢复不启动实例、不撤销外部部署，原 ZIP 不修改。恢复会清空会话和临时 OAuth 状态，需要重新登录；旧的待部署/恢复任务、审批、观察和待发送通知被撤销，旧期望状态及 worker 占用不会重新生效。新部署/恢复的领取及健康巡检被暂停，版本拉取仍可进行。旧版本数据先用匹配镜像恢复，再走正常迁移。

这与普通进程重启不同。核对当前远端版本、执行器配置和恢复期间新排队的目标后，针对新数据库执行：

```bash
# 只读检查；报告关系问题，不自动删除或修复存量数据
RELEASETRACKER_DB_PATH=/restore/new-data/releases.db python -m releasetracker.cli audit-database
# 显式解除新变更的门禁，不复活已撤销的任务与审批
RELEASETRACKER_DB_PATH=/restore/new-data/releases.db python -m releasetracker.cli acknowledge-restore --confirm-reviewed
```

备份列表 API 的 `restore_review_required` 表示仍待确认；目前没有网页确认入口。旧目标若仍需执行，应在确认后重新发起操作，不重放旧任务。外键强制执行仍按存量数据迁移计划处理，本批不直接开启级联删除。曾有成功备份但所有归档丢失时，每日校验会记录 `backup_missing` 并发送去重告警；从未创建备份的实例不因此告警。

## 清理历史孤儿记录 {#prune-orphans}

备份创建时会在**快照副本**中清理已删除追踪器/执行器留下的历史孤儿行，按表统计在清单的 `pruned_orphans` 中；在线数据库不变。其他外键损坏及任务/健康观察关系仍严格校验，不会通过删除任务来绕过错误。

需要修复在线数据库时，先预览，再停止所有实例后执行：

```bash
python -m releasetracker.cli prune-orphans --dry-run
# 确认实例已停止，并保留一份停机整目录备份
python -m releasetracker.cli prune-orphans --confirm-stopped
python -m releasetracker.cli audit-database
```

实际清理前命令会保存**未修改**的数据库及密钥安全副本（`reason: pre_orphan_cleanup`，可能含孤儿数据，不能视为已通过恢复校验的归档）。清理只应用已知历史表的 CASCADE/SET NULL 规则，未知关系损坏会回滚清理。不要在应用运行或密钥轮换时执行。

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

`migrate` / `migrate-and-serve` 在检测到待执行迁移时，会先在备份目录生成一份 `reason: pre_migration` 的一致性归档（数据库 + 密钥），失败则中止迁移；该归档只能用升级前的镜像恢复，并参与保留份数轮换，需要长期保存时请及时下载。确有其他备份机制时可设置 `RELEASETRACKER_PRE_MIGRATION_BACKUP=0` 关闭。数据库 schema 由 dbmate 管理，不建议手工改表。相关错误见[启动与迁移排障](../reference/troubleshooting.md#startup)。
