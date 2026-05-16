---
title: ReleaseTracker Wiki
---

# ReleaseTracker

<div class="grid cards" markdown>

-   :material-rocket-launch-outline: **快速上手**

    ---

    通过 Docker 或 Docker Compose 完成部署。

    [:octicons-arrow-right-24: 安装部署](getting-started/installation.md)

-   :material-source-branch: **源码仓库**

    ---

    代码托管在 GitHub，欢迎提交 issue 与 PR。

    [:octicons-arrow-right-24: GitHub](https://github.com/dalamudx/ReleaseTracker)

</div>

## 项目定位

ReleaseTracker 是一款轻量级、可配置的版本追踪与更新编排工具。它追踪 GitHub、GitLab、Gitea、Helm Chart 与 OCI 容器镜像仓库中的 release / tag，并将版本变化关联到 Docker、Podman、Portainer、Kubernetes 与 Helm Release 等运行时目标。

## 适用人群

- **运维 / DevOps**：需要跟踪上游依赖版本，并按计划推进到自有环境。
- **自托管服务管理员**：希望在统一面板中管理多套 Docker / Kubernetes / Helm 部署的升级。

!!! info "Wiki 正在建设中"
    已上线：安装部署、系统设置、凭证管理、运行时连接、通知、追踪器、执行器、已知限制。更多章节（运维指南、常见问题等）会陆续补齐。

## 核心能力

- **多源版本追踪**：GitHub、GitLab（含自托管）、Gitea、Helm Chart、Docker Hub、GHCR、私有 OCI Registry。
- **聚合追踪器**：单个追踪器可绑定多个版本源，按发布渠道规则筛选、归并与展示。
- **执行器编排**：为容器、Compose Project、Portainer Stack、Kubernetes Workload、Helm Release 提供目标发现、绑定、手动 / 定时执行、维护窗口与执行历史。
- **快照与回滚（部分执行器）**：完整运行时配置快照 / 恢复用于 Docker / Podman 的破坏性重建目标：单容器与 Compose 分组更新。Portainer Stack、Kubernetes Workload、Helm Release 以声明式更新、版本历史或运行历史为主，不作为 ReleaseTracker 管理的完整运行时配置快照。
- **安全**：本地用户 + JWT + OIDC；敏感数据使用 Fernet 加密；系统密钥可轮换。
- **Web UI 配置**：时区、日志级别、版本历史保留、BASE URL、密钥轮换等运行参数均可在浏览器中完成，无需环境变量。

## 下一步

- 按照 [安装部署](getting-started/installation.md) 完成部署。
- 登录后修改默认管理员密码。
- 根据需要在 **凭证管理** 中录入后续所需的各类密钥与令牌。
