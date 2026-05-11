---
title: 执行器
---

# 执行器

执行器把追踪器的当前版本绑定到一个具体的运行时目标，并按指定模式执行更新。

## 1. 绑定模型

```text
ExecutorConfig
├── runtime_type            # docker / podman / kubernetes / portainer
├── runtime_connection_id   # 关联「运行时连接」
├── tracker_name            # 聚合追踪器名称
├── tracker_source_id       # 绑定的具体版本源
├── channel_name            # 绑定的发布渠道
├── enabled                 # 关闭后配置保留但不执行
├── update_mode             # manual / maintenance_window / immediate
├── target_ref              # 运行时目标引用（按 mode 区分）
├── service_bindings[]      # 用于分组目标模式
├── image_selection_mode    # 新镜像如何由当前镜像 + 目标版本合成
├── image_reference_mode    # 发出的引用使用 tag 还是 digest
├── maintenance_window      # update_mode=maintenance_window 时必填
└── health_check            # 更新后健康检查配置
```

## 2. 支持的目标模式

| `target_ref.mode` | 支持的 `runtime_type` | 适用对象 |
| ---- | ---- | ---- |
| `container` | `docker`、`podman` | 单个容器 |
| `docker_compose` | `docker`、`podman` | Compose 项目 |
| `portainer_stack` | `portainer` | Portainer 上的 standalone stack |
| `kubernetes_workload` | `kubernetes` | Deployment / StatefulSet / DaemonSet |
| `helm_release` | `kubernetes` | Helm 3 release |

不满足组合的 `(runtime_type, mode)` 在保存时被 Pydantic 校验拦截，直接返回 400。

## 3. 版本来源限制

只有 `source_type ∈ { container, helm }` 的追踪器源可以绑定为执行器来源 —— 这是由代码里的 `EXECUTOR_BINDABLE_SOURCE_TYPES` 约束的。Git 平台（GitHub / GitLab / Gitea）的 release / tag 不会直接驱动执行器更新。

## 4. 更新模式

| `update_mode` | 行为 |
| ---- | ---- |
| `manual` | 不参与调度，只能通过「运行一次」按钮触发。 |
| `maintenance_window` | 在所配置的本地维护窗口内自动运行，窗口外会被跳过并在历史中记录 `outside maintenance window`。 |
| `immediate` | 只要追踪器产出更高目标版本就立即执行更新。 |

`maintenance_window` 使用「系统设置」中的时区解释时间，例如允许日期 + 时间范围。

## 5. 镜像策略

- **`image_selection_mode`**
  - `replace_tag_on_current_image`（默认）：保留目标运行时当前镜像的 repo，仅替换 tag 为追踪器当前版本。举例：当前镜像 `ghcr.io/owner/app:1.2.0`、追踪器目标版本 `1.3.0` → 新镜像 `ghcr.io/owner/app:1.3.0`。
  - `use_tracker_image_and_tag`：直接使用追踪器源里配置的 `registry + image` 作为新镜像 repo，tag 取自追踪器当前版本。
- **`image_reference_mode`**
  - `digest`（默认）：发出的镜像引用带 `@sha256:...` 摘要，可重放性强。
  - `tag`：仅使用 tag 引用。对无法稳定拉取 digest 的来源（某些自托管 Registry）更方便，但失去内容寻址的保证。

## 6. 快照与回滚

仅以下模式会在**更新前**自动捕获快照：

- `container`（Docker / Podman 单容器）
- `helm_release`

其余模式 (`docker_compose`、`portainer_stack`、`kubernetes_workload`) 执行更新时**不生成快照**，`POST /api/executors/{id}/rollback` 无法对它们生效（缺少可用快照会返回 404）。详见 [已知限制](../limitations.md)。

快照保留数量由系统设置的 `executor_snapshot_retention_count`（默认 10）控制。

## 7. 健康检查

每个执行器可配置一个 `health_check` 策略。按目标模式允许的策略：

| 模式 | 可选策略 |
| ---- | ---- |
| `container` / `docker_compose` / `portainer_stack` / `kubernetes_workload` | `none`、`auto`、`runtime_native`、`manual_http`、`manual_tcp`、`http`、`tcp` |
| `helm_release` | `none`、`auto`、`helm_status`、`runtime_native`、`manual_http`、`manual_tcp`、`http`、`tcp` |

默认值（`use_default_strategy=true` 时）：

| 模式 | 默认策略 |
| ---- | ---- |
| `container` / `docker_compose` / `portainer_stack` / `kubernetes_workload` | `auto` |
| `helm_release` | `helm_status` |

默认模板使用：`grace_period_seconds=15`、`attempt_timeout_seconds=10`、`interval_seconds=5`、`probe_window_seconds=180`、`failure_policy=mark_failed`。

!!! note "健康检查仍在完善中"
    健康检查框架已上线，但恢复挂钩（自动回滚）仅在那些既支持快照又支持运行时原生探测的目标上完整工作。`docker_compose` / `portainer_stack` / `kubernetes_workload` 即使启用 `failure_policy=mark_failed_and_recover`，也会因为没有快照而无法回滚，此时仅表现为运行结果被标记为失败。

## 8. 运行结果状态

- `success`：更新完成且（若启用）健康检查通过。
- `failed`：更新本身失败，或健康检查在 `mark_failed` 策略下失败。
- `skipped`：当前目标已经处于目标版本、执行器被禁用、非维护窗口、或必要条件缺失等。

分组目标（compose / stack / workload）的运行结果会把每个 service 的结果合并到 `diagnostics` 字段中，便于排查。

## 9. 运行历史

- **执行器 → 运行历史** 展示单次运行的 from / to 版本、状态、消息与诊断 JSON。
- 历史条目可在详情页「清空历史」一键删除（不影响快照）。

## 10. 常见问题

!!! failure "保存执行器返回 `target_ref.mode '...' is only supported when runtime_type is '...'`"
    运行时类型与目标模式不匹配。对照第 2 节的矩阵修正。

!!! failure "绑定追踪器源时不显示某个源"
    只有 `container` / `helm` 两类源可绑定。Git 平台的源需要改造目标架构后才能支持，当前未支持。

!!! failure "Kubernetes 工作负载 / Compose / Portainer Stack 执行器的「回滚」按钮灰了 / 返回 404"
    这些模式当前不会生成快照，因此无法回滚。遇到更新失败时需要通过原生工具手动恢复，例如 `kubectl rollout undo`、`docker compose up -d` 等。

!!! failure "维护窗口模式的执行器长期没跑"
    - 确认系统设置里的时区与运营时区一致（维护窗口按该时区解释）。
    - 确认「允许日期」没有无意间留空出现错误匹配；留空代表允许所有日期。
    - 到达时间窗口时会自动扫描一次；运行日志里会记录 `inside maintenance window` 或 `outside maintenance window`。
