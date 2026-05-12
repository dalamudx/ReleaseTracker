---
title: 凭证管理
---

# 凭证管理

凭证模块用于集中保存访问 GitHub、GitLab、镜像仓库、Kubernetes、Portainer 等外部系统所需的敏感信息。创建追踪器或运行时连接时，可以选择已有凭证，避免在多个配置里重复填写 Token 或 kubeconfig。

所有敏感输入在写入 SQLite 前都会使用 Fernet 加密，加密密钥保存在 `data/system-secrets.json`。

## 1. 支持的凭证类型

| 凭证类型 | 用途 | 需要填写的内容 |
| ---- | ---- | ---- |
| `github` | GitHub release / tag 扫描 | GitHub Personal Access Token 或 Fine-grained PAT。 |
| `gitlab` | GitLab release / tag 扫描（含自托管） | GitLab Personal Access Token 或 Project Access Token。 |
| `gitea` | Gitea release / tag 扫描 | Gitea Access Token。 |
| `helm` | 私有 Helm Chart 仓库 | Basic Auth 字符串、仓库令牌，或仓库要求的认证内容。 |
| `docker` | OCI 镜像仓库拉取鉴权（GHCR / Docker Hub / 私有 Registry） | `username:password`、Registry Token，或仓库要求的登录令牌。 |
| `docker_runtime` | Docker 运行时连接 | 多数本地 Docker API 不需要密钥；仅在远程连接或证书认证场景填写。 |
| `podman_runtime` | Podman 运行时连接 | 多数本地 Podman API 不需要密钥；仅在远程连接或证书认证场景填写。 |
| `kubernetes_runtime` | Kubernetes 运行时连接 | 完整 kubeconfig YAML 文本。 |
| `portainer_runtime` | Portainer 运行时连接 | Portainer API Key。 |

UI 会显示本地化后的类型名称；上表中的英文值用于帮助排查日志或 API 返回里的类型标识。

## 2. 创建或编辑凭证

打开 **凭证管理 → 新建凭证**，主要填写：

- **名称**：在 ReleaseTracker 内唯一，用于在追踪器或运行时连接中选择这条凭证。建议使用能说明用途的名称，例如 `github-prod-readonly`、`portainer-main`。
- **类型**：选择凭证对应的服务或运行时。类型决定后续表单展示哪些敏感输入项。
- **敏感内容**：填写 Token、API Key、kubeconfig 或用户名密码组合。不同类型需要的格式见下方建议。
- **描述**：可选，用于记录来源、权限范围、过期时间或负责人。

编辑凭证时，如果敏感输入框留空，系统会保留原值；只有填写新值时才会覆盖已有密钥。

## 3. 在哪些地方使用凭证

- **追踪器**：GitHub、GitLab、Gitea、Helm、Docker / OCI 等来源可以选择对应凭证，以访问私有资源或提高公开资源的访问额度。
- **运行时连接**：Kubernetes、Portainer 或需要认证的远程 Docker / Podman 连接可以选择运行时凭证。

删除凭证前，系统会检查是否仍被追踪器或运行时连接引用。若存在引用，删除会被拒绝，并显示需要先解除的关联项。

## 4. 显示与安全行为

- 列表和详情中不会显示完整敏感值，只会显示掩码后的摘要。
- 明文只会在服务端执行扫描、发现目标、运行执行器等操作时临时解密使用，不会通过 API 返回给前端。
- 如果需要轮换 Token，请编辑凭证并输入新值；保存后使用该凭证的追踪器和运行时连接会在下一次操作中使用新值。

## 5. 几个常见凭证的填写建议

### GitHub Token

- 形式：`ghp_*` 的 Personal Access Token，或 Fine-grained PAT。
- 权限：至少读取目标仓库的 release / tag。对公共仓库亦建议填 token，可显著缓解匿名访问的速率限制。
- 用途：同时作用于 GraphQL 与 REST 两条抓取路径；追踪器中的「GitHub 抓取优先策略」决定优先尝试哪一条。

### GitLab Token

- 形式：Personal Access Token 或 Project Access Token。
- 权限：`read_api` 即可。
- 自托管实例同样使用此凭证；对应追踪器的实例地址填写你的 GitLab 域名。

### OCI Registry Token

- Docker Hub / GHCR / 自建 Registry 共用 `docker` 凭证类型。
- 推荐格式：`username:password` 或具体 Registry 的令牌。
- 未配置凭证时，匿名访问者会受 Docker Hub 的速率限制，容易触发追踪器失败。

### Portainer API Key

- 在 Portainer Web UI 的「My Account → Access Tokens」创建。
- 仅需要 Portainer 对应 endpoint 的 stack 读写权限。

### Kubernetes kubeconfig

- 填入一份完整的 kubeconfig YAML。
- 若运行时连接勾选「使用集群内配置」，则不需要额外凭证，此时 kubeconfig 可省略。

## 6. 加密与密钥轮换

- 凭证中的敏感内容会以 Fernet 加密字符串保存在 SQLite 中。
- 加密密钥存放在 `data/system-secrets.json`；丢失该文件意味着已加密的凭证无法还原。
- 轮换加密密钥会批量重新加密所有凭证；若其中任何一条无法用当前密钥解密（通常来自手工修改数据库或 `system-secrets.json` 与数据库不匹配），轮换会以 400 错误中断，不会修改任何数据。详见 [系统设置](system-settings.md) 的「系统密钥轮换」。

## 7. 备份建议

- 备份必须包括 `releases.db` 与 `system-secrets.json`，二者缺一不可。
- 对运维团队之间的传递（例如异地灾备），推荐用 GPG 加密打包后再存放。
