---
title: 系统设置
---

# 系统设置

常规实例配置在 **系统设置** 中管理，保存后立即生效并随数据库备份，不需要编辑数据库或 `.env`。来源筛选与执行策略分别见[追踪器](../guides/trackers.md)和[执行器](../guides/executors.md)。

## 全局配置 {#global}

| 设置 | 默认值 / 范围 | 影响 |
| --- | --- | --- |
| BASE URL | 空；非空时必须为规范的绝对 HTTPS URL | 通知链接、前端基础路径、OIDC 回调和仓库 Webhook 接收地址；包含部署子路径 |
| 时区 | `UTC`；有效 IANA 时区，如 `Asia/Shanghai` | 日期展示、维护窗口和通知消息中的时间 |
| 日志级别 | `INFO`；`DEBUG/INFO/WARNING/ERROR` | 后端日志详细程度 |
| OCI 镜像仓库跳转 | 关闭 | 仅在镜像仓库需要跳转访问时开启；跳转仍受安全校验约束 |
| 就绪观察默认值 | 见[健康检查](../guides/health-and-rollback.md#health-checks) | 执行器未单独覆盖时使用 |
| 发布渠道版本历史 | 20；1–1000 | 每个发布渠道保留的版本记录数 |
| 执行器运行态快照 | 10；1–1000 | 每个执行器保留的快照数；锁定或占用中的快照受保护，实际数量可能更多 |
| 系统备份保留份数 | 7；1–100 | 数据目录 `backups` 下保留的全量备份数 |
| 系统自动备份周期 | 0（关闭）；0–8760 小时 | 自动全量备份间隔，见[在线备份](../operations/backup-and-upgrade.md#online-backup) |

版本历史和快照可点击 **立即清理** 按当前数量裁剪；版本历史也会在每天 02:00 或维护窗口结束后自动清理。调小数量前确认需要保留的记录，清理不能代替备份。代理配置见[反向代理与子路径](../operations/reverse-proxy.md)，密钥轮换见[备份、升级与密钥](../operations/backup-and-upgrade.md#keys)。

## 运行时操作策略（API） {#operation-policy}

通过运行时连接 API 的 `config.operation_policy` 配置，暂无专用表单。以下为 `config` 片段，编辑时保留其他配置键：

```json
{
  "operation_policy": {
    "read_timeout_seconds": 20,
    "write_timeout_seconds": 90,
    "read_retries": 1
  }
}
```

| 字段 | 默认值 | 合法范围（整数） |
| --- | --- | --- |
| `read_timeout_seconds` | 20 秒 | 1–600 |
| `write_timeout_seconds` | 90 秒 | 1–600 |
| `read_retries` | 1 次额外重试 | 0–3；0 表示不重试 |

| 调用路径 | 策略应用方式 |
| --- | --- |
| Portainer 读取 | 读取超时，瞬态传输失败有限重试 |
| Portainer Stack 写入 | 写请求超时，不自动重放 |
| Docker / Podman SDK | 客户端网络超时使用写超时值 |
| Compose / Helm 命令 | 子进程使用写操作预算，不自动重放 |
| Kubernetes 原生工作负载 API | 尚未统一接入 |

这不是整个更新流程的总时限。写请求超时后服务端仍可能已执行更新，见[更新超时](troubleshooting.md#update-timeout)。
