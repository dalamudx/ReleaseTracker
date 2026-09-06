---
title: 系统设置
---

# 系统设置

常规实例配置在 **系统设置** 中管理，不需要编辑数据库或 `.env`。本页只维护全局设置和高级运行时策略；来源筛选与执行策略分别见[追踪器](../guides/trackers.md)和[执行器](../guides/executors.md)。

## 全局配置 {#global}

| 设置 | 默认值 / 范围 | 影响 |
| --- | --- | --- |
| BASE URL | 空；非空时必须为规范的绝对 HTTPS URL | 通知链接、前端基础路径和 OIDC 回调；包含部署子路径 |
| 时区 | `UTC`；有效 IANA 时区，如 `Asia/Shanghai` | 日期展示和维护窗口的时间解释 |
| 日志级别 | `INFO`；`DEBUG/INFO/WARNING/ERROR` | 后端日志详细程度 |
| 版本历史数量 | 20；1–1000 | 每来源、每发布渠道保留的历史条数 |
| 快照历史数量 | 10；1–1000 | 每执行器的保留目标；锁定或占用中的快照受保护，实际数量可能超过目标 |
| OCI Registry 重定向 | 关闭 | 按需允许受安全校验约束的镜像仓库跳转，不等于允许任意 URL 跳转 |

代理配置见[反向代理与子路径](../operations/reverse-proxy.md)，密钥操作见[备份、升级与密钥](../operations/backup-and-upgrade.md#keys)。修改保留数量前确认需要保留的记录，清理不能代替备份。

## 运行时操作策略（API） {#operation-policy}

通过运行时连接 API 的 `config.operation_policy` 配置，目前没有对应的专用表单。以下是 `config` 的片段，不是完整连接请求；编辑连接时保留其他配置键：

```json
{
  "operation_policy": {
    "read_timeout_seconds": 20,
    "write_timeout_seconds": 90,
    "read_retries": 1
  }
}
```

| 字段 | 默认值 | 合法范围 |
| --- | --- | --- |
| `read_timeout_seconds` | 20 秒 | 整数 1–600 |
| `write_timeout_seconds` | 90 秒 | 整数 1–600 |
| `read_retries` | 1 次额外重试 | 整数 0–3；0 表示不重试 |

不要将布尔值或字符串作为整数提交。实际接入范围如下：

| 调用路径 | 策略应用方式 |
| --- | --- |
| Portainer 读取 | 读取超时和传输类瞬态失败的有限重试 |
| Portainer Stack 写入 | 写请求超时；不自动重放 |
| Docker / Podman SDK | 客户端网络超时使用写超时值；不是整个更新流程的总时限 |
| Compose / Helm 命令 | 子进程使用写操作预算；不自动重放 |
| Kubernetes 原生工作负载 API | 尚未统一接入这套传输超时策略，不承诺全程受此预算约束 |

策略不是跨所有运行时的统一“更新总时长”。尤其是写请求超时，服务端仍可能已执行更新；见[超时后的处理](troubleshooting.md#update-timeout)。

实现依据：[全局设置](https://github.com/dalamudx/ReleaseTracker/blob/main/backend/src/releasetracker/storage/sqlite.py)、[运行时策略](https://github.com/dalamudx/ReleaseTracker/blob/main/backend/src/releasetracker/services/runtime_policy.py)。
