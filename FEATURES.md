# 功能截图与场景说明 / Feature Screenshots

以界面截图串起 ReleaseTracker 的核心模块：版本追踪 → 运行时连接 → 执行器更新编排。

UI screenshots walking through the core flow: release tracking → runtime connections → executor-based update orchestration.

## 仪表盘 / Dashboard

![Dashboard](images/dashboard.png)

系统整体状态与近期版本变化概览。

Overview of system state and recent release activity.

## 追踪器 / Trackers

![Trackers](images/tracker.png)

定义版本来源。支持 GitHub、GitLab、Gitea、Helm Chart、OCI 容器镜像仓库，通过发布渠道规则区分 Stable、Pre-Release、Beta、Canary。

Define release sources. Supports GitHub, GitLab, Gitea, Helm charts, and OCI registries; release channel rules separate Stable, Pre-Release, Beta, and Canary streams.

## 版本历史 / Release History

![Release History](images/history.png)

记录追踪器发现过的版本变化（来源、发布渠道、发布时间、版本标识），用于回溯演进和作为执行器的更新依据。

History of discovered versions (source, channel, published time, identity) — used for auditing and as executor update candidates.

## Release Notes

![Release Notes](images/releasenote.png)

展示单个版本的详细发布说明、来源与渠道。GitHub / GitLab / Gitea 等来源会直接呈现上游 release notes，便于执行更新前评估变更。

Detailed notes, source, and channel for a specific version. For GitHub / GitLab / Gitea sources it renders upstream release notes directly, useful for pre-update review.

## 执行器 / Executors

![Executors](images/executor.png)

将追踪器的目标版本绑定到实际运行时目标：Docker 容器、Compose Project、Portainer Stack、Kubernetes Workload、Helm Release。支持手动执行、计划执行、维护窗口、执行历史。

Bind tracker target versions to runtime targets: Docker containers, Compose projects, Portainer stacks, Kubernetes workloads, or Helm releases. Supports manual / scheduled execution, maintenance windows, and run history.

## 运行时连接 / Runtime Connections

![Runtime Connections](images/runtime.png)

接入 Docker、Podman、Portainer、Kubernetes 环境。敏感连接信息由凭证模块统一加密管理。

Connect Docker, Podman, Portainer, and Kubernetes environments. Connection secrets are managed and encrypted through the credentials module.

## 凭证管理 / Credentials

![Credentials](images/credentials.png)

集中管理 Git 平台 Token、容器镜像仓库账号、运行时连接密钥等敏感信息，入库前加密保存。

Central store for Git tokens, container registry credentials, and runtime connection secrets. Sensitive fields are encrypted before persistence.

## 系统设置 / System Settings

![System Settings](images/settings.png)

时区、日志级别、版本历史保留数量、BASE URL、系统密钥与加密密钥轮换等运行配置，均可在 Web UI 完成，无需环境变量。

Timezone, log level, release history retention, BASE URL, session key rotation, encryption key rotation — all configurable from the Web UI, no environment variables required.

## 推荐使用流程 / Recommended Workflow

1. 在「凭证管理」添加 Git / 镜像仓库 / 运行时 / OIDC 凭证。
2. 在「运行时连接」接入 Docker / Podman / Portainer / Kubernetes。
3. 创建追踪器，配置版本来源与发布渠道规则。
4. 在执行器中发现运行时目标，绑定追踪器与发布渠道。
5. 在追踪器与版本历史中观察版本变化，查看 Release Notes。
6. 手动或按计划执行更新。

Steps: add credentials → connect runtimes → create trackers with channel rules → bind executors to runtime targets → monitor release activity → execute updates manually or on schedule.
