---
title: ReleaseTracker Wiki
---

# ReleaseTracker

追踪上游版本，并按策略将选定版本应用到运行时目标。本文档面向自托管实例的管理员；只做版本追踪时不需要配置执行器。

## 开始使用 {#_4}

首次部署请阅读[安装与首次运行](getting-started/installation.md)，完成部署、登录和第一次版本检查。

## 概念关系 {#_1}

| 对象 | 作用 |
| --- | --- |
| 追踪器 | 组织同一个项目的一个或多个版本来源 |
| 版本来源 | 提供版本的 Git 仓库、Helm Chart 或 OCI 镜像仓库 |
| 发布渠道 | 用发布状态和版本规则筛选来源中的版本 |
| 运行时连接 | 访问 Docker、Podman、Portainer、Kubernetes 或 SSH 主机的连接 |
| 执行器 | 将来源的发布渠道绑定到运行时目标，并执行更新 |
| 通知器 | 把版本和执行事件发送到 Webhook、企业微信、飞书、Telegram 等渠道 |

版本来源 → 发布渠道 → 选定版本 → 执行器 → 运行时目标。

三类“历史”不要混淆：**版本历史**是上游版本记录；**执行器快照**是更新前的目标配置，用于回滚；**实例备份**是 ReleaseTracker 自身数据库与密钥的归档，用于灾难恢复。三者都不包含业务应用数据。

## 按任务查阅 {#_2}

| 我想…… | 文档 |
| --- | --- |
| 筛选版本、配置 Changelog 或仓库 Webhook | [追踪器与版本规则](guides/trackers.md) |
| 接入私有来源、容器平台或 SSH 主机 | [凭证与运行时连接](guides/runtime-connections.md) |
| 配置自动更新、维护窗口和部署确认 | [执行器与更新策略](guides/executors.md) |
| 验证更新结果或回滚 | [健康检查与回滚](guides/health-and-rollback.md) |
| 接收版本和执行事件、自定义消息模板 | [消息通知](guides/notifications.md) |
| 配置 HTTPS、子路径或 SSO | [反向代理](operations/reverse-proxy.md) · [管理员与 OIDC](operations/accounts-and-oidc.md) |
| 备份、恢复、升级或轮换密钥 | [备份、升级与密钥](operations/backup-and-upgrade.md) |
| 查参数、支持边界或错误 | [系统设置](reference/settings.md) · [支持范围](reference/support.md) · [故障排查](reference/troubleshooting.md) |

## 使用边界 {#_3}

按单实例部署。自动更新前先手动验证版本和绑定；更新失败不会自动回滚。各目标的更新、健康检查和恢复能力以[支持范围](reference/support.md)为准。源码与开发入口见 [GitHub 仓库](https://github.com/dalamudx/ReleaseTracker)。
