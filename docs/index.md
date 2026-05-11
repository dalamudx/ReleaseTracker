---
title: ReleaseTracker Wiki
---

# ReleaseTracker

<div class="grid cards" markdown>

-   :material-rocket-launch-outline: **快速上手**

    ---

    Docker 单命令启动或使用 Docker Compose 部署。

    [:octicons-arrow-right-24: 安装部署](getting-started/installation.md)

-   :material-source-branch: **源码仓库**

    ---

    代码托管在 GitHub，提 issue 或 PR 欢迎。

    [:octicons-arrow-right-24: GitHub](https://github.com/dalamudx/ReleaseTracker)

</div>

## 这是什么

ReleaseTracker 是一款轻量级、可配置的版本追踪与更新编排工具。它追踪 GitHub、GitLab、Gitea、Helm Chart 与 OCI 容器镜像仓库中的 release / tag，并将版本变化关联到 Docker、Podman、Portainer、Kubernetes、Helm 等运行时目标。

## 面向谁

- **运维 / DevOps**：需要跟踪上游依赖版本并按计划推进到自有环境。
- **自托管服务管理员**：需要一个中心化面板管理多套 Docker / K8s / Helm 部署的升级。
- **平台团队**：需要把版本发现、审批、更新执行、回滚串成一条可审计的流程。

!!! info "本 Wiki 仍在建设中"
    当前先发布「骨架 + 安装部署」一页试水。后续页面会陆续补齐：配置说明、追踪器与执行器细节、运维指南、常见限制与 FAQ。

## 核心能力一览

- **多源版本追踪**：GitHub、GitLab（含自托管）、Gitea、Helm Chart、Docker Hub、GHCR、私有 OCI Registry。
- **聚合追踪器**：一个追踪器绑定多个版本源，按发布渠道规则筛选、归并与展示。
- **执行器编排**：容器、Compose、Portainer Stack、Kubernetes Workload、Helm Release 的目标发现、绑定、手动 / 定时执行、维护窗口与执行历史。
- **快照与回滚**：更新前自动捕获快照，失败时支持回滚与健康检查驱动的自动恢复。
- **安全**：本地用户 + JWT + OIDC；敏感数据 Fernet 加密；系统密钥可轮换。
- **Web UI 配置**：时区、日志级别、版本历史保留、BASE URL、密钥轮换等均可在浏览器完成，无需环境变量。

## 下一步

- 跟着 [安装部署](getting-started/installation.md) 跑起来。
- 登录后立即修改默认管理员密码。
- 阅读后续即将上线的 **配置说明 / 运维指南** 页面。
