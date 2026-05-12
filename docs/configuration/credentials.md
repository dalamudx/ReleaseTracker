---
title: 凭证管理
---

# 凭证管理

凭证模块集中管理 Token、账号密码、运行时鉴权材料等敏感信息。所有敏感字段在入库前使用 Fernet 加密（密钥来自 `system-secrets.json`）。

## 1. 支持的凭证类型

| 类型 | 用途 | 典型 `secrets` 字段 |
| ---- | ---- | ---- |
| `github` | GitHub release / tag 扫描 | `token` |
| `gitlab` | GitLab release / tag 扫描（含自托管） | `token` |
| `gitea` | Gitea release / tag 扫描 | `token` |
| `helm` | 私有 Helm Chart 仓库 | `token`（`username:password` 或 Basic Auth 令牌） |
| `docker` | OCI 镜像仓库拉取鉴权（GHCR / Docker Hub / 私有 Registry） | `token`（`username:password` 或镜像仓库令牌） |
| `docker_runtime` | Docker 运行时连接（仅做归档，Docker API 通常不需要鉴权） | 运行时自身无密钥需求时仅保留元数据 |
| `podman_runtime` | Podman 运行时连接 | 同上 |
| `kubernetes_runtime` | Kubernetes 运行时连接 | `kubeconfig`（完整 YAML 文本） |
| `portainer_runtime` | Portainer 运行时连接 | `token`（Portainer API Key） |

UI 上的中文标签在「凭证管理」列表中显示；上表是后端 `CredentialType` 的真实枚举值。

## 2. 字段模型

```text
Credential {
  name:        string            # 全局唯一
  type:        CredentialType    # 见上表
  token:       string            # 兼容字段，会自动同步到 secrets.token
  secrets:     map<string, any>  # 主要的凭证容器（加密）
  description: string | null
}
```

- `token` 字段存在的唯一目的是向后兼容早期版本；新建或编辑时，建议直接把 token 值填到 `secrets.token`。模型层会自动双向同步（若填了 `token` 会镜像到 `secrets.token`，反之亦然）。
- `secrets` 字段中除 `token` 以外的自定义键也会加密存储，可用来放 OIDC 相关密钥 / 自定义 header 等（未来扩展方向）。

## 3. 列表与详情显示

- 列表和详情接口都会**掩码**所有敏感值（长度 > 8 的字符串显示首尾各 4 位，其余显示为 `****`）。
- 明文仅在调度器 / 执行器调用时在服务端解密，用于完成一次具体操作；不会回写到 API 响应。
- 编辑时凭证值输入框为空则保持不变；填写新值才会覆盖。

## 4. 删除规则

删除凭证前，系统会检查引用：

- 被任意追踪器引用；
- 被任意运行时连接引用。

若有引用，删除接口返回 409，并附带引用列表。先解除所有引用（修改追踪器或运行时连接，选用其它凭证或清空）后再删除。

## 5. 几个常见凭证的填写建议

### GitHub Token

- 形式：`ghp_*` 的 Personal Access Token，或 Fine-grained PAT。
- 权限：至少读取目标仓库的 release / tag。对公共仓库亦建议填 token，可显著缓解匿名访问的速率限制。
- 用途：同时作用于 GraphQL 与 REST 两条抓取路径；追踪器中的「GitHub 抓取优先策略」决定优先尝试哪一条。

### GitLab Token

- 形式：Personal Access Token 或 Project Access Token。
- 权限：`read_api` 即可。
- 自托管实例同样使用此凭证；对应追踪器的 `instance` 字段指向你的 GitLab 域名。

### OCI Registry Token

- Docker Hub / GHCR / 自建 Registry 共用 `docker` 凭证类型。
- 推荐格式：`username:password` 或具体 Registry 的令牌。
- 未配置凭证时，匿名访问者会受 Docker Hub 的速率限制，容易触发追踪器失败。

### Portainer API Key

- 在 Portainer Web UI 的「My Account → Access Tokens」创建。
- 仅需要 Portainer 对应 endpoint 的 stack 读写权限。

### Kubernetes kubeconfig

- 填入一份完整的 kubeconfig YAML 到 `secrets.kubeconfig`。
- 若运行时连接勾选「使用集群内配置（`in_cluster=true`）」，则不需要凭证，此时 `kubeconfig` 可省略。

## 6. 加密与密钥轮换

- 所有 `secrets` 字段以及 `token` 字段在 SQLite 中以 Fernet 加密的字符串保存。
- 加密密钥存放在 `data/system-secrets.json`；丢失该文件意味着已加密的凭证无法还原。
- 轮换加密密钥会批量重新加密所有凭证；若其中任何一条无法用当前密钥解密（通常来自手工修改数据库或 `system-secrets.json` 与数据库不匹配），轮换会以 400 错误中断，不会修改任何数据。详见 [系统设置](system-settings.md) 的「系统密钥轮换」。

## 7. 备份建议

- 备份必须包括 `releases.db` 与 `system-secrets.json`，二者缺一不可。
- 对运维团队之间的传递（例如异地灾备），推荐用 GPG 加密打包后再存放。
