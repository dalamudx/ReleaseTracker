---
title: 凭证与运行时连接
---

# 凭证与运行时连接

凭证保存鉴权材料，运行时连接保存平台地址并引用凭证。只追踪公开版本时，两者都可以不配置；执行更新时必须有可用的运行时连接。

## 来源凭证 {#credentials}

在 **凭证管理** 中选择对应类型，再在来源或运行时连接中引用。密钥加密存入数据库；备份必须同时保留[数据库与系统密钥](../operations/backup-and-upgrade.md#backup)。

| 用途 | 凭证内容 |
| --- | --- |
| GitHub / GitLab / Gitea | 有目标仓库读取权限的访问令牌；不要把示例令牌前缀当成唯一合法格式 |
| Helm Chart 仓库 | Basic Auth 用户名与密码 |
| 私有 OCI Registry | 用户名和密码 / PAT |
| Docker / Podman | 按需提供 TLS 的 CA、客户端证书与私钥 |
| Kubernetes | kubeconfig 或界面支持的 Token / 证书配置 |
| Portainer | API 密钥 |

带凭证的 GitLab、Gitea、Helm 和自定义 Changelog 请求要求 HTTPS；不允许携带凭证跨源跳转或降级到 HTTP。OCI 重定向有单独的[系统设置](../reference/settings.md)。

## Docker / Podman {#containers}

- 本地连接使用 `unix:///var/run/docker.sock`，不是裸路径 `/var/run/docker.sock`。Podman 使用实际 Socket 路径并保留 `unix://` 前缀。
- ReleaseTracker 在容器内运行时，Socket 必须挂载进该容器。例如在已有 Compose 配置的 `volumes` 中追加：

    ```yaml
    - /var/run/docker.sock:/var/run/docker.sock
    ```

- 远程连接使用 `tcp://host:2376`，配置 TLS 验证和所需证书；端口以实际服务配置为准。
- API 版本一般留空；确认服务端兼容性后再指定。

!!! warning "运行时连接具有高权限"
    Docker Socket 通常等价于宿主机管理权限，挂载为只读并不能把 Docker API 变成只读。不要开放未认证的明文 Docker TCP 端口；限制 ReleaseTracker 的访问者和网络范围。

## Kubernetes / Helm {#kubernetes}

集群外选择 Kubernetes 凭证；集群内可启用 **In-Cluster**，使用 Pod 的 ServiceAccount。按需限定命名空间，并授予目标发现和更新所需的最小权限。

Helm Release 使用 Kubernetes 连接，不是单独的连接类型。支持的工作负载和 Helm 限制见[支持范围](../reference/support.md#runtimes)。

## Portainer {#portainer}

填写实例地址，选择 Portainer 凭证，并通过 Endpoint 发现选择目标环境。优先使用 HTTPS。当前目标是受支持的 standalone Stack，不是 Portainer 下的任意容器；具体限制见[支持范围](../reference/support.md#runtimes)。

## 验证连接 {#verify}

在创建执行器时选择连接并执行目标发现。确认目标名称、命名空间或 Endpoint 正确后再绑定来源。发现失败先排查[连接错误](../reference/troubleshooting.md#connections)，不要通过关闭 TLS 验证绕过证书问题。

高级超时和读取重试配置目前通过 API 提供，见[运行时操作策略](../reference/settings.md#operation-policy)。
