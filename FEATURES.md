# 功能截图 / Feature Screenshots

## 登录 / Login

![Login](docs/images/login.png)

## 仪表盘 / Dashboard

![Dashboard](docs/images/dashboard.png)

系统整体状态与近期版本变化概览。

Overview of system state and recent release activity.

## 追踪器 / Trackers

![Trackers](docs/images/trackers.png)
![Release Notes](docs/images/trackers-changelog2.png)

定义版本来源。支持 GitHub、GitLab、Gitea、Helm Chart、OCI 容器镜像仓库，通过发布渠道规则区分 Stable、Pre-Release、Beta、Canary。

Define release sources. Supports GitHub, GitLab, Gitea, Helm charts, and OCI registries; release channel rules separate Stable, Pre-Release, Beta, and Canary streams.

### 编辑/添加追踪器 / Edit & Add Tracker

![Trackers-add](docs/images/trackers-add.png)
![Trackers-add](docs/images/trackers-channels.png)
![Trackers-add](docs/images/trackers-changelog.png)

## 执行器 / Executors

![Executors](docs/images/executors.png)
![Executors](docs/images/executors-snapshot.png)

将追踪器的目标版本绑定到实际运行时目标：Docker 容器、Compose Project、Portainer Stack、Kubernetes Workload、Helm Release 或远程主机上的 SSH Compose 项目。支持手动执行、维护窗口、部署计划确认、快照回滚和执行历史。

Bind tracker target versions to runtime targets: Docker containers, Compose projects, Portainer stacks, Kubernetes workloads, Helm releases, or SSH Compose projects on remote hosts. Supports manual execution, maintenance windows, deployment plan approval, snapshot rollback, and run history.

### 编辑/添加执行器 / Edit & Add Executor

![Executors-add](docs/images/executors-add.png)
![Executors-add](docs/images/executors-binding.png)
![Executors-add](docs/images/executors-policy.png)
![Executors-add](docs/images/executors-confirm.png)

## 运行时连接 / Runtime Connections

![Runtime Connections](docs/images/runtime.png)

接入 Docker、Podman、Portainer、Kubernetes 环境和 SSH 主机。敏感连接信息由凭证模块统一加密管理。

Connect Docker, Podman, Portainer, Kubernetes environments and SSH hosts. Connection secrets are managed and encrypted through the credentials module.

## 版本历史 / Release History

![Release History](docs/images/history.png)

记录追踪器发现过的版本变化（来源、发布渠道、发布时间、版本标识），用于回溯演进和作为执行器的更新依据。

History of discovered versions (source, channel, published time, identity) — used for auditing and as executor update candidates.


## 凭证管理 / Credentials

![Credentials](docs/images/credentials.png)

集中管理 Git 平台 Token、容器镜像仓库账号、运行时连接密钥等敏感信息，入库前加密保存。

Central store for Git tokens, container registry credentials, and runtime connection secrets. Sensitive fields are encrypted before persistence.

## 消息通知 / Notifications

![Notifications](docs/images/notifications.png)

支持通用 Webhook、企业微信、飞书、钉钉、Discord、Slack、Telegram，可按事件订阅并使用自定义消息模板；仓库 Webhook 可在发版或构建成功时即时触发版本拉取。

Generic Webhook, WeCom, Feishu, DingTalk, Discord, Slack and Telegram notifications with per-event subscriptions and custom message templates; repository webhooks trigger immediate refreshes on releases or successful builds.

## 系统设置 / System Settings

![System Settings](docs/images/settings.png)
![System Settings](docs/images/settings-keys.png)
![System Settings](docs/images/settings-oidc.png)

时区、日志级别、历史与快照保留、备份策略、BASE URL、密钥轮换、OIDC 等运行配置，均可在 Web UI 完成；备份页支持在线备份与恢复。

Time zone, log level, history and snapshot retention, backup policy, BASE URL, key rotation and OIDC are all configurable from the Web UI; the backup page supports online backup and restore.