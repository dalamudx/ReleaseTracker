---
title: 支持范围
---

# 支持范围

本页按当前普通执行流程说明产品支持，不把底层适配器方法等同于已对用户完整开放的能力。配置方式见[使用指南](../guides/executors.md)。

## 部署与账号 {#deployment}

| 项目 | 边界 |
| --- | --- |
| 部署 | 单实例、单调度器；SQLite WAL 不是多副本协调机制，无 leader election |
| 官方镜像 | `linux/amd64`；当前容器默认以 root 运行 |
| 管理权限 | 单一管理员，无注册、RBAC 和租户隔离 |
| OIDC | 一个提供商、一个管理员身份绑定；API 使用本地 JWT |
| 网络暴露 | 后端 CORS 默认允许所有来源；生产使用 HTTPS 代理和访问控制，CORS 不能替代认证 |

## 运行时目标 {#runtimes}

| 目标 | 普通更新方式 | 更新前持久化配置快照 | 更新后统一探测流程 | 恢复方式 |
| --- | --- | --- | --- | --- |
| Docker / Podman 单容器 | 按检查到的配置重建 | 支持 | 支持 | 有可用快照时手动回滚 |
| Docker / Podman Compose 分组 | 分组重建 / Compose 更新路径 | 支持 | 尚未接入同一探测流程 | 有可用快照时手动回滚 |
| Portainer standalone Stack | Stack-file API 更新 | 普通更新流程不生成 | 尚未接入同一探测流程 | 由 Portainer / 已保存 Stack 配置恢复 |
| Kubernetes 工作负载 | 修改镜像配置 | 普通更新流程不生成 | 尚未接入同一探测流程 | 使用 Kubernetes 原生历史和恢复机制 |
| Helm Release | Helm upgrade | 普通更新流程不生成 | 尚未接入同一探测流程 | 使用 Helm 历史和 `helm rollback` |

附加限制：

- Portainer 不支持 Swarm、Kubernetes 类型或 Git-backed Stack；Endpoint 不可用时可能在请求阶段失败。
- Kubernetes 工作负载限于 Deployment、StatefulSet、DaemonSet；不包含 Job / CronJob。
- Helm 使用 Helm 3，发现依赖 Secret 存储；ConfigMap 存储驱动不在当前发现支持范围内。
- 所有目标均不会因更新或健康检查失败自动回滚。恢复不等于还原持久卷或应用数据库。
- Portainer 的适配器有快照 / 恢复方法，但普通更新路径没有自动生成快照，不应据此承诺常规 UI 回滚可用。

操作与完整性要求见[健康检查与回滚](../guides/health-and-rollback.md)。

## 来源与通知 {#sources}

| 项目 | 支持 / 限制 |
| --- | --- |
| 版本来源 | GitHub、GitLab、Gitea、Helm Chart、OCI Registry |
| 发布渠道 | `stable`、`prerelease`、`beta`、`canary` |
| 版本筛选 | 包含 / 排除正则匹配标签，不匹配发布正文或作者 |
| 发布时间 | 取决于上游元数据；首次观察时间不能代替真实发布时间 |
| 通知 | 仅 Webhook，不支持自定义请求头或持久化失败重放 |

Webhook 与 HTTP 探针的公网地址限制见[通知安全要求](../guides/notifications.md#security)，不是所有运行时网络访问都采用相同限制。

## 数据与接口 {#data}

数据库迁移后不保证旧应用版本兼容新 schema；按[备份恢复流程](../operations/backup-and-upgrade.md#restore)降级。执行历史提供运行诊断，但不是独立、完整的审计日志系统。API 尚无公开稳定的版本化承诺，接入前查看当前实例 `/docs`。

实现依据：[快照门控](https://github.com/dalamudx/ReleaseTracker/blob/main/backend/src/releasetracker/executor_scheduler_update_safety.py)、[分组执行路径](https://github.com/dalamudx/ReleaseTracker/blob/main/backend/src/releasetracker/executor_scheduler_grouped_runtime.py)、[镜像发布](https://github.com/dalamudx/ReleaseTracker/blob/main/.github/workflows/release.yml)。
