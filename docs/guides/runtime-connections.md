---
title: 凭证与运行时连接
---

# 凭证与运行时连接

凭证保存鉴权材料，运行时连接保存平台地址并引用凭证。只追踪公开版本时两者都可以不配置；执行更新时必须有可用的运行时连接或 SSH 主机连接。

## 来源凭证 {#credentials}

在 **凭证** 中选择对应类型，再在来源或连接中引用。密钥加密存入数据库，备份必须同时保留[数据库与系统密钥](../operations/backup-and-upgrade.md#backup)。

| 用途 | 凭证内容 |
| --- | --- |
| GitHub / GitLab / Gitea | 有目标仓库读取权限的访问令牌 |
| Helm Chart 仓库 | Basic Auth 用户名与密码 |
| 私有 OCI Registry | 用户名和密码 / PAT |
| Docker / Podman | 按需提供 TLS 的 CA、客户端证书与私钥 |
| Kubernetes | kubeconfig 或界面支持的 Token / 证书配置 |
| Portainer | API 密钥 |
| 主机 SSH | 私钥（可带口令）或密码 |

带凭证的 GitLab、Gitea、Helm 和自定义 Changelog 请求要求 HTTPS，不允许携带凭证跨源跳转或降级到 HTTP。OCI 重定向由[系统设置](../reference/settings.md#global)单独控制。

## Docker / Podman {#containers}

- 本地连接使用 `unix:///var/run/docker.sock`，不是裸路径；Podman 使用实际 Socket 路径并保留 `unix://` 前缀。
- ReleaseTracker 在容器内运行时，需把 Socket 挂载进容器，例如在 Compose 的 `volumes` 中追加 `- /var/run/docker.sock:/var/run/docker.sock`。
- 远程连接使用 `tcp://host:2376` 并启用 TLS 验证；API 版本一般留空。
- **Podman 模式需要原生 `/libpod/` API**。只提供 Docker 兼容接口的 Socket Proxy 请选择 **Docker** 类型；需要 Pod 和原生 Podman 更新、恢复能力时改用原生 Podman service/socket。
- TLS 的 CA、客户端证书和私钥填入凭证的 PEM 字段，而不是容器外的文件路径。用 IP 连接时允许证书不含 IP SAN，但仍验证 CA 链；用域名连接时校验主机名。

!!! warning "运行时连接具有高权限"
    Docker Socket 通常等价于宿主机管理权限，只读挂载也不能让 Docker API 变成只读。不要开放未认证的明文 Docker TCP 端口。

## Kubernetes / Helm {#kubernetes}

集群外选择 Kubernetes 凭证；集群内可启用 **In-Cluster**，使用 Pod 的 ServiceAccount。按需限定命名空间，只授予目标发现和更新所需的最小权限。Helm Release 使用 Kubernetes 连接，不是单独的连接类型。

## Portainer {#portainer}

填写实例地址（优先 HTTPS），选择 Portainer 凭证，并通过 Endpoint 发现选择目标环境。目标是 standalone Stack，不是 Portainer 下的任意容器。

## SSH 主机 {#ssh}

用于更新远程主机上由 Compose 文件管理的项目，无需开放 Docker API。

1. 在 **凭证 → SSH 主机连接** 中填写主机、端口、用户名和认证方式（私钥或密码）。
2. 点击 **获取主机公钥**，与主机上的实际指纹核对后 **确认指纹**，再 **测试连接**。之后公钥变化会拒绝连接。
3. 无法直连时，可选择另一个启用了 **允许作为代理** 的 SSH 连接作为跳板（仅一跳）。

远程主机需要可用的 `docker compose`、`docker-compose`、`podman compose` 或 `podman-compose`，并且 SSH 用户能读写项目文件、执行 Compose。项目发现和版本写入方式见[SSH Compose 目标](executors.md#ssh-compose)。

## 验证连接 {#verify}

创建执行器时选择连接并执行目标发现，确认目标名称、命名空间或 Endpoint 后再绑定来源。发现失败时按[连接排障](../reference/troubleshooting.md#connections)处理，不要通过关闭 TLS 验证绕过证书问题。超时与读取重试可通过 API 调整，见[运行时操作策略](../reference/settings.md#operation-policy)。

## 公开镜像仓库只读探针 {#registry-probe}

排查镜像版本抓取慢时，可在源码环境的 `backend/` 目录运行：

```bash
.venv/bin/python scripts/probe_registry_manifest.py \
  --registry registry.example.com --image team/app --mode auto
```

脚本只读取可匿名访问的仓库，不读取数据库或存储的凭证，也不部署。输出 JSON 记录各阶段请求数量与耗时（默认 10 个版本、单请求 10 秒、总预算 240 秒）；`--mode first_observed` 可对照跳过 Blob 的路径。Tag 列表超时说明尚未进入 Manifest 阶段，不能作为 Manifest 性能依据。
