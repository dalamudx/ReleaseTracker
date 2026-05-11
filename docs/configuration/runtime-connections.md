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

## 2. 公共字段

```text
RuntimeConnection {
  name:          string         # 全局唯一
  type:          docker | podman | kubernetes | portainer
  enabled:       bool           # 关闭后仍保留配置，但不会被调度器使用
  config:        map<string, any>   # 类型相关，见下文
  credential_id: int | null     # 关联「凭证管理」里的条目
  description:   string | null
}
```

## 3. Docker / Podman

| `config` 字段 | 必填 | 约束 |
| ---- | ---- | ---- |
| `socket` | 是 | 必须以 `unix://` 或 `tcp://` 开头。常见值：`unix:///var/run/docker.sock`、`tcp://docker.example.com:2376`。 |
| `tls_verify` | 否 | 布尔值。启用时要求客户端证书由信任 CA 签发。 |
| `api_version` | 否 | 指定 Docker API 版本；为空时使用 SDK 默认。 |

`credential_id` 在 Docker / Podman 下**通常留空** —— 直接连接本地 Unix 套接字无需鉴权。如果需要通过 TLS 双向认证的 TCP 端点连接远程 Docker，再创建对应 `docker_runtime` / `podman_runtime` 凭证并引用。

## 4. Kubernetes

| `config` 字段 | 必填 | 约束 |
| ---- | ---- | ---- |
| `in_cluster` | 否 | 布尔值。为 `true` 时使用 Pod 自身的 ServiceAccount，无需凭证。 |
| `context` | 否 | 指定 kubeconfig 中的 context 名。 |
| `namespace` | 否 | 单个 namespace，用于所有发现操作。 |
| `namespaces` | 否 | namespace 列表，每项为非空字符串。 |
| *凭证* | `in_cluster` 未开启时必填 | `kubernetes_runtime` 类型凭证，内含 `kubeconfig` YAML。 |

发现规则：

- 同时填写 `namespace` 与 `namespaces` 时，`namespaces` 优先生效，作用于目标发现与 namespace 授权。
- 两者都不填时，只有被 kubeconfig 授权的 namespace 会被扫描。
- 可以通过 **执行器 → 运行时连接 → 发现 Namespace** 按钮预览当前 kubeconfig 能访问到的 namespace 列表。

### ServiceAccount 权限最小集

若使用 kubeconfig，对应 ServiceAccount 至少需要：

- `list`/`get` Deployment、StatefulSet、DaemonSet；
- `patch` 对应工作负载（用于执行更新时修改镜像）；
- `list` Namespace（可选，用于命名空间发现）；
- 若使用 Helm Release 执行器，还需要能列出 Helm 的 Secret（Helm 3 把 release 存储在 Secret 中）。

## 5. Portainer

| `config` 字段 | 必填 | 约束 |
| ---- | ---- | ---- |
| `base_url` | 是 | Portainer 实例的 HTTP/HTTPS 根地址。 |
| `endpoint_id` | 是 | Portainer 内的 Endpoint ID（正整数）。 |
| *凭证* | 是 | `portainer_runtime` 类型，`secrets.token` 为 Portainer API Key。 |

已知限制：**仅支持 Portainer 中 `standalone` 类型的 stack**，不支持 Swarm stack；发现与更新路径均会跳过非 standalone stack。详见 [已知限制](../limitations.md) 的 Portainer 一节。

## 6. 启用 / 禁用

- `enabled=false` 会让执行器运行时跳过此运行时连接（调度中会记录 `runtime connection disabled` 失败），但配置本身保留。
- 关联该运行时连接的执行器若不重新绑定，其本轮运行会失败而非跳过 —— 目的是让操作员尽快察觉。

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
    - `endpoint_id` 与 API Key 所在环境不匹配。

!!! failure "Kubernetes: Namespace 发现返回空列表"
    通常是 ServiceAccount 无权 `list` Namespace。可改用 `config.namespaces` 白名单显式指定需要扫描的 namespace。
