---
title: 执行器
---

# 执行器

执行器把一个已追踪的发布渠道连接到具体运行时目标，并按你选择的策略更新容器镜像或 Helm Chart 版本。

## 1. 创建执行器时需要选择什么

在 **执行器 → 新建** 中，配置会按步骤完成：

- **运行时连接**：选择 Docker、Podman、Portainer 或 Kubernetes 连接，ReleaseTracker 会从该连接发现可更新目标。
- **目标**：选择一个已发现的容器、Docker Compose 项目、Portainer Stack、Kubernetes 工作负载或 Helm Release。
- **版本来源与发布渠道**：选择追踪器中的镜像来源或 Helm Chart 来源，再选择要跟随的发布渠道。
- **服务绑定**：对 Compose、Portainer Stack、Kubernetes 工作负载这类多服务目标，把每个服务 / container 绑定到对应的版本来源与发布渠道。
- **执行策略**：选择手动、维护窗口或立即自动执行。
- **镜像策略与目标策略**：决定更新时保留当前镜像名，还是使用追踪到的镜像名；以及优先使用不可变镜像引用还是版本标签。
- **更新后健康检查**：可选，用来在标记运行成功前验证服务是否健康。

关闭执行器不会删除配置；它只会停止自动运行，也不能被手动触发。

## 2. 支持的目标类型

| 界面目标类型 | 可用运行时连接 | 适用对象 |
| ---- | ---- | ---- |
| 容器 | Docker、Podman | 单个容器 |
| Docker Compose 项目 | Docker、Podman | Compose 项目中的一个或多个服务 |
| Portainer Stack | Portainer | Portainer 上的 standalone stack |
| Kubernetes 工作负载 | Kubernetes | Deployment / StatefulSet / DaemonSet |
| Helm Release | Kubernetes | Helm 3 release |

如果目标类型和运行时连接不匹配，保存时会被拒绝；请回到目标选择步骤重新选择。

## 3. 可绑定的版本来源

执行器来源选择器只会显示能直接产出更新目标的来源：**容器镜像来源**和 **Helm Chart 来源**。GitHub、GitLab、Gitea 的 release / tag 可以作为版本视图与 changelog 来源，但当前不会直接驱动执行器更新。

## 4. 执行策略

| 界面选项 | 行为 |
| ---- | ---- |
| 手动 | 不参与自动调度；仅在操作员点击「立即执行」时运行一次。 |
| 维护窗口 | 自动检查是否需要更新，但只会在配置的本地维护窗口内执行；窗口外的触发会被跳过并在运行历史中记录原因。 |
| 立即 | 一旦检测到更高的目标版本，就自动执行更新。 |

维护窗口使用 **系统设置** 中的时区解释「允许日期」与开始 / 结束时间。

## 5. 镜像策略与目标策略

- **镜像策略**
  - **保留当前镜像名**（默认）：保留目标运行时当前镜像的仓库名，只把版本替换为追踪器当前版本。举例：当前镜像 `ghcr.io/owner/app:1.2.0`、追踪器目标版本 `1.3.0` → 新镜像 `ghcr.io/owner/app:1.3.0`。
  - **使用追踪到的镜像名**：使用所选来源提供的镜像名和版本。
- **目标策略**
  - **优先使用不可变镜像**（默认）：可用时使用带 `@sha256:...` 摘要的镜像引用，可重放性更强。
  - **优先使用版本标签**：只使用 tag 引用。对无法稳定拉取摘要的来源（某些自托管 Registry）更方便，但失去内容寻址的保证。

## 6. 快照与回滚

完整运行时配置快照 / 恢复仅用于 Docker / Podman 的破坏性重建目标：

- Docker / Podman 单容器
- Docker / Podman Compose 分组更新

这些目标在更新前保存用于重建的运行时配置；手动回滚时，会尽量按快照重建容器、网络、卷、端口、标签等配置。更新失败或健康检查失败不会自动回滚，操作员需要自行决定是否从可用快照恢复。Podman Compose 回滚会基于稳定的容器 / service 名称解析当前 pod 与运行时对象，不应假定容器 ID 或 pod ID 在更新后保持不变。

Portainer Stack、Kubernetes 工作负载、Helm Release 不作为 ReleaseTracker 管理的完整运行时配置快照目标：Portainer Stack 通过声明式 stack-file API 更新，Kubernetes 工作负载更新 Deployment / StatefulSet / DaemonSet 的镜像配置，Helm Release 通过 Helm 3 升级流程与版本历史管理。

快照保留数量由 **系统设置 → 执行器快照保留数量** 控制（默认 10）。快照历史面板仅对上述破坏性重建且具备快照能力的执行器显示；历史项支持带确认的回滚与删除。

## 7. 健康检查

每个执行器可配置一个更新后健康检查策略。常用策略包括：

- **关闭**：不执行更新后探测。
- **自动（推荐）/ 运行时原生就绪**：优先使用运行时原生健康信息；Docker / Podman 可读取容器 healthcheck 或回退到运行状态判断。
- **手动 HTTP 探针**：按配置的主机、端口、路径、协议、方法、状态码、响应内容正则、请求头与 TLS 选项执行 HTTP 探测。
- **手动 TCP 探针**：按配置的主机和端口执行 TCP 探测。
- **Helm 发布状态**：Helm Release 的默认策略，用于 Helm 状态检查。

默认策略：

| 目标类型 | 默认策略 |
| ---- | ---- |
| 容器 / Docker Compose 项目 / Portainer Stack / Kubernetes 工作负载 | 自动（推荐） |
| Helm Release | Helm 发布状态 |

默认模板使用 15 秒等待期、10 秒单次超时、5 秒探测间隔、180 秒总探测时长，失败时把运行标记为失败。探测窗口、单次超时与间隔均有上限校验；使用手动 HTTP / TCP 策略时需要显式提供可从 ReleaseTracker 后端访问的主机。

!!! note "健康检查仍在完善中"
    Docker / Podman 单容器路径已接入更新后健康检查与失败策略。分组目标（Compose / Portainer Stack / Kubernetes 工作负载 / Helm Release）的更新流水线与健康检查接线仍在演进，不应把 Kubernetes、Portainer 或 Helm 文档化为已支持任意 host-port 探测的目标。健康检查失败会按策略把运行标记为失败或降级，但回滚始终是手动 UI/API 操作。

## 8. 运行结果状态

- **成功**：更新完成且（若启用）健康检查通过。
- **失败**：更新本身失败，或健康检查按所选失败处理策略把运行标记为失败。
- **跳过**：当前目标已经处于目标版本、执行器被禁用、非维护窗口、或必要条件缺失等。

分组目标（Compose / Stack / Workload）的运行结果会把每个服务的结果合并到诊断详情中，便于排查。

## 9. 运行历史

- **执行器 → 运行历史** 展示单次运行的起始版本、目标版本、状态、消息与诊断详情。
- 历史条目可在详情页「清空历史」一键删除（不影响快照）。

## 10. 常见问题

!!! failure "保存执行器提示目标类型与运行时不匹配"
    运行时连接类型与目标类型不匹配。对照第 2 节的矩阵修正。

!!! failure "绑定追踪器源时不显示某个源"
    只有容器镜像来源和 Helm Chart 来源可绑定。Git 平台的源需要改造目标架构后才能支持，当前未支持。

!!! failure "Kubernetes 工作负载 / Portainer Stack / Helm Release 执行器的「回滚」按钮灰了 / 返回 404"
    这些目标当前不作为完整运行时配置快照目标，因此无法通过 ReleaseTracker 快照回滚。遇到更新失败时需要通过原生工具手动恢复，例如 `kubectl rollout undo`、Helm rollback、Portainer UI 等。

!!! info "Compose 回滚后容器或 pod ID 变化"
    Docker / Podman Compose 回滚会按快照重建运行时对象，容器 ID 或 pod ID 可能变化。Podman Compose 会基于稳定名称解析当前 pod / 容器，避免依赖旧的 pod ID。

!!! failure "维护窗口策略的执行器长期没跑"
    - 确认系统设置里的时区与运营时区一致（维护窗口按该时区解释）。
    - 确认「允许日期」没有无意间留空出现错误匹配；留空代表允许所有日期。
    - 到达时间窗口时会自动扫描一次；运行历史会标注触发是在窗口内还是窗口外。
