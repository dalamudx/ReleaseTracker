---
title: 运行时连接
---

# 运行时连接

运行时连接定义 ReleaseTracker 如何访问外部的容器 / 编排环境。执行器通过运行时连接发现目标并执行更新。

## 1. 支持的运行时类型

| 类型 | 说明 |
| ---- | ---- |
| `docker` | 通过 Docker Engine API（Unix 套接字或 TCP） |
| `podman` | 通过 Podman API（Unix 套接字或 TCP） |
| `kubernetes` | 通过 kubeconfig 或集群内 ServiceAccount |
| `portainer` | 通过 Portainer HTTP API |

## 2. 创建或编辑运行时连接

打开 **执行器 → 运行时连接 → 新建连接**，主要填写：

- **名称**：在 ReleaseTracker 内唯一，后续创建执行器时会用它选择目标运行时。
- **类型**：选择 Docker、Podman、Kubernetes 或 Portainer。不同类型会展示不同的连接字段。
- **启用状态**：关闭后配置会保留，但执行器不会使用该连接执行更新。
- **凭证**：按需选择「凭证管理」中已创建的条目。Docker / Podman 本地套接字通常不需要凭证；Kubernetes 与 Portainer 通常需要。
- **描述**：可选，用于记录环境、负责人或访问范围。

## 3. Docker / Podman

| UI 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| Socket / API 地址 | 是 | 必须以 `unix://` 或 `tcp://` 开头。常见值：`unix:///var/run/docker.sock`、`tcp://docker.example.com:2376`。 |
| TLS 验证 | 否 | 连接远程 TLS 端点时启用，客户端会校验证书链。 |
| API 版本 | 否 | 指定 Docker API 版本；留空时使用 SDK 默认值。 |

Docker / Podman 本地 Unix 套接字通常无需凭证。如果需要通过 TLS 双向认证的 TCP 端点连接远程 Docker 或 Podman，请先创建对应的 `docker_runtime` / `podman_runtime` 凭证，再在此处选择。

## 4. Kubernetes

| UI 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| 使用集群内配置 | 否 | 开启后使用 ReleaseTracker 所在 Pod 的 ServiceAccount，无需选择 kubeconfig 凭证。 |
| kubeconfig context | 否 | 指定 kubeconfig 中的 context 名。 |
| 单个 Namespace | 否 | 限定所有发现操作使用的单个 namespace。 |
| Namespace 列表 | 否 | 显式列出允许扫描的多个 namespace。 |
| 凭证 | 未开启集群内配置时必填 | 选择 `kubernetes_runtime` 类型凭证，其中包含 kubeconfig YAML。 |

发现规则：

- 同时填写「单个 Namespace」与「Namespace 列表」时，列表优先生效，作用于目标发现与 namespace 授权。
- 两者都不填时，只有被 kubeconfig 授权的 namespace 会被扫描。
- 可以通过 **执行器 → 运行时连接 → 发现 Namespace** 按钮预览当前 kubeconfig 能访问到的 namespace 列表。

### ServiceAccount 权限最小集

若使用 kubeconfig，对应 ServiceAccount 至少需要：

- `list`/`get` Deployment、StatefulSet、DaemonSet；
- `patch` 对应工作负载（用于执行更新时修改镜像）；
- `list` Namespace（可选，用于命名空间发现）；
- 若使用 Helm Release 执行器，还需要能列出 Helm 的 Secret（Helm 3 把 release 存储在 Secret 中）。

## 5. Portainer

| UI 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| Portainer 地址 | 是 | Portainer 实例的 HTTP/HTTPS 根地址。 |
| Endpoint ID | 是 | Portainer 内的 Endpoint ID（正整数）。 |
| 凭证 | 是 | 选择 `portainer_runtime` 类型凭证，凭证内容为 Portainer API Key。 |

已知限制：**仅支持 Portainer 中 `standalone` 类型的 stack**，不支持 Swarm stack；发现与更新路径均会跳过非 standalone stack。详见 [已知限制](../limitations.md) 的 Portainer 一节。

## 6. 启用 / 禁用

- 关闭运行时连接会让执行器运行失败并记录原因，而不是静默跳过。这样可以让操作员尽快发现连接不可用。
- 已禁用的连接不会丢失配置；重新启用后可继续使用。

## 7. 删除与重命名

- 被任何执行器引用的运行时连接无法直接删除，先解除引用。
- 改名合法，但 UI 可能缓存旧名称，建议刷新后验证。

## 8. 典型错误排查

!!! failure "Docker: `Permission denied` 打开套接字"
    容器内进程需对 `docker.sock` 有读写权限。常见做法是把 `/var/run/docker.sock` 挂入容器并确保宿主机权限（挂载时使用 `:ro` 会导致所有更新操作失败）。

!!! failure "Kubernetes: `Unauthorized` / `Forbidden`"
    检查 kubeconfig 指向的用户 / ServiceAccount 是否具备第 4 节列出的最小权限。

!!! failure "Portainer: `401 Invalid Access Token`"
    - Portainer API Key 已过期或被删除，重新生成并更新凭证。
    - Endpoint ID 与 API Key 所在环境不匹配。

!!! failure "Kubernetes: Namespace 发现返回空列表"
    通常是 ServiceAccount 无权 `list` Namespace。可改用 Namespace 列表白名单显式指定需要扫描的 namespace。
