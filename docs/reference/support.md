---
title: 支持范围
---

# 支持范围

本页说明普通执行流程下已对用户开放的能力。配置方式见[使用指南](../guides/executors.md)。

## 部署与账号 {#deployment}

| 项目 | 边界 |
| --- | --- |
| 部署 | 单实例、单调度器；SQLite WAL 不是多副本协调机制 |
| 官方镜像 | `linux/amd64`；容器默认以 root 运行 |
| 管理权限 | 单一管理员，无注册、RBAC 和租户隔离 |
| OIDC | 一个提供商、一个管理员身份绑定 |
| 跨域 | 默认仅允许同源；可用 `RELEASETRACKER_CORS_ORIGINS` 增加明确来源，CORS 不能替代认证和 HTTPS |

## 运行时目标 {#runtimes}

| 目标 | 更新方式 | 更新前快照 | 就绪观察 | 恢复方式 |
| --- | --- | --- | --- | --- |
| Docker / Podman 单容器 | 按检查到的配置重建 | 支持 | 支持 | 快照手动回滚 |
| Docker / Podman Compose 分组 | 分组重建 | 支持 | 支持 | 快照手动回滚 |
| Portainer standalone Stack | Stack-file API 更新 | 支持 | 支持 | 快照手动回滚 |
| Kubernetes 工作负载 | 修改镜像配置 | 支持 | 支持（Deployment 含拉取宽限） | 快照手动回滚或 Kubernetes 原生历史 |
| Helm Release | Helm upgrade | 支持 | Helm 状态 | 快照手动回滚或 `helm rollback` |
| SSH Compose 项目 | 改写 Compose / 环境 / 覆盖文件后执行 Compose | 项目文件 | 支持 | 恢复配置文件，不回滚容器 |

附加限制：

- Portainer 不支持 Swarm、Kubernetes 类型或 Git-backed Stack。
- Kubernetes 工作负载限于 Deployment、StatefulSet、DaemonSet，不含 Job / CronJob。
- Helm 使用 Helm 3，发现依赖 Secret 存储驱动。
- SSH Compose 要求远程主机有 Docker 或 Podman 的 Compose 工具，镜像表达式复杂或变量共享时拒绝更新。
- 所有目标均不会因更新或健康检查失败自动回滚；恢复不等于还原持久卷或应用数据。

操作要求见[健康检查与回滚](../guides/health-and-rollback.md)。

## 来源与通知 {#sources}

| 项目 | 支持 / 限制 |
| --- | --- |
| 版本来源 | GitHub、GitLab、Gitea、Helm Chart、OCI Registry |
| 仓库 Webhook | GitHub、GitLab、Gitea、Forgejo 的版本发布与 Actions 成功事件，只触发拉取 |
| 发布渠道 | `stable`、`prerelease`、`beta`、`canary` |
| 版本筛选 | 包含 / 排除正则匹配标签，不匹配发布正文或作者 |
| 发布时间 | 取决于上游元数据；首次观察时间不能代替真实发布时间 |
| 通知 | 通用 Webhook、企业微信、飞书、钉钉、Discord、Slack、Telegram；不支持自定义请求头、飞书/钉钉加签或最终失败后的手动重放 |

通知与 HTTP 探针的公网地址限制见[通知安全要求](../guides/notifications.md#security)。

## 数据与接口 {#data}

数据库迁移后不保证旧应用版本兼容新 schema，降级按[备份恢复流程](../operations/backup-and-upgrade.md#restore)。执行历史用于运行诊断，不是完整的审计日志。API 尚无稳定的版本化承诺，接入前查看实例 `/docs`。
