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
    当前首发「骨架 + 安装部署」章节。后续计划补齐：配置说明、追踪器与执行器操作指南、运维指南、已知限制与常见问题。

## 核心能力

- **多源版本追踪**：GitHub、GitLab（含自托管）、Gitea、Helm Chart、Docker Hub、GHCR、私有 OCI Registry。
- **聚合追踪器**：单个追踪器可绑定多个版本源，按发布渠道规则筛选、归并与展示。
- **执行器编排**：为容器、Compose Project、Portainer Stack、Kubernetes Workload、Helm Release 提供目标发现、绑定、手动 / 定时执行、维护窗口与执行历史。
- **快照与回滚（部分执行器）**：当前仅 Docker / Podman 单容器与 Helm Release 执行器在更新前自动捕获快照并支持手动回滚；Docker Compose、Portainer Stack、Kubernetes Workload 暂不生成更新前快照，也无法从快照回滚。
- **安全**：本地用户 + JWT + OIDC；敏感数据使用 Fernet 加密；系统密钥可轮换。
- **Web UI 配置**：时区、日志级别、版本历史保留、BASE URL、密钥轮换等运行参数均可在浏览器中完成，无需环境变量。

## 下一步

- 按照 [安装部署](getting-started/installation.md) 完成部署。
- 登录后修改默认管理员密码。
- 根据需要在 **凭证管理** 中录入后续所需的各类密钥与令牌。
