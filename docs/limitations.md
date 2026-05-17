---
title: 已知限制
---

# 已知限制

本页列出运行 / 部署 ReleaseTracker 时需要事先知晓的限制。项目仍在演进，部分限制会随版本消失；每一条都会标注来源以便对照代码。

## 1. 部署架构

- **单进程 / 单实例**：ReleaseTracker 是一个 FastAPI 进程，数据库是本地 SQLite（WAL 模式）。**不支持横向扩展**（多副本会竞争同一 SQLite 文件），也没有分布式协调层。负载加大时唯一纵向优化手段是提升宿主机 I/O。
- **容器架构**：官方镜像目前只构建 `linux/amd64`。其它架构需要自行构建。
- **CORS 默认放开**：后端 CORS 配置为 `allow_origins=["*"]`。直接暴露在公网前务必加反向代理做访问控制。

## 2. 快照与回滚覆盖范围

完整运行时配置快照与手动回滚**仅对以下破坏性重建目标生效**：

- Docker / Podman 单容器
- Docker / Podman Compose 分组更新

这些目标会在更新前捕获重建所需的运行时配置，并可在有可用快照时通过执行器详情页回滚。Compose / Podman pod 场景不保证容器 ID 或 pod ID 稳定，回滚会按稳定名称解析当前运行时对象。

以下目标当前不作为 ReleaseTracker 管理的完整运行时配置快照目标：

- Portainer Stack：通过声明式 stack-file API 更新，主要依赖 Portainer / stack 文件本身的状态。
- Kubernetes 工作负载：更新 Deployment / StatefulSet / DaemonSet 的镜像配置，回滚请使用 Kubernetes 原生机制。
- Helm Release：通过 Helm 3 upgrade / release history 管理，回滚请使用 Helm 原生命令。

这些目标的快照回滚调用在没有可用快照时会返回 404。需要回滚时，通过原生工具完成（`kubectl rollout undo`、`helm rollback`、Portainer UI 等）。

## 3. 健康检查框架

- **手动 HTTP 探针** / **手动 TCP 探针** 支持显式主机、端口、路径等探测配置；Docker / Podman 可使用运行时原生 healthcheck 或运行状态回退。Kubernetes、Portainer、Helm 的分组更新流水线仍在逐步接入健康检查，不应假定它们已支持任意 host-port 探测。
- 更新失败或健康检查失败只会记录失败结果，不会自动回滚。只有目标具备可用快照时，操作员才能在确认后手动触发 ReleaseTracker 快照回滚；没有完整快照的目标需要用原生工具恢复。
- 默认超时策略（15 秒等待期、10 秒单次超时、5 秒探测间隔、180 秒总探测时长）对大多数工作负载是合理的，但复杂启动序列需要手动调整。

## 4. 鉴权与账号

- **密钥轮换仅限用户名等于 `admin` 的账号**。这是后端 `get_current_admin_user` 的硬编码检查，不是一个可配置角色。
  - 如果你把默认 `admin` 账号删除或改名，密钥轮换能力会随之丢失，需要从数据库手动恢复。
- **没有多角色 / 细粒度权限**。所有已登录用户看到同一套数据、有同样的操作权限（除了密钥轮换）。
- **默认账号 `admin` / `admin`**。首次登录后必须立即修改密码；生产环境暴露默认凭证等价于整台机器被接管。

## 5. 供应链检查

当前发布工作流已包含依赖供应链校验，但范围限于锁定依赖与审计：

- 前端按 `package-lock.json` 执行 `npm ci`，运行高严重性级别的 `npm audit`，并上传 CycloneDX SBOM 产物；`frontend/.npmrc` 默认禁用依赖安装脚本以降低安装期执行风险。
- 后端使用 uv 的锁定模式安装依赖，导出锁定 requirements 产物，并用 `pip-audit` 扫描该 requirements 文件。
- GitHub Actions token 权限按 job 最小化：默认 `contents: read`，只有镜像发布获得 `packages: write`，GitHub Release 创建获得 `contents: write`。

这些检查发生在 CI / 发布流程中，不要求部署者编辑 SBOM 或 requirements 产物。当前没有镜像漏洞扫描步骤；如需镜像级扫描，请在自己的部署流水线中补充。

## 6. Portainer

- 仅支持 Portainer 中的 `standalone` stack（Swarm stack 不支持）。发现阶段会跳过非 standalone stack，执行器绑定保存时会报错。
- 当前版本没有对 Portainer endpoint 的健康状态做前置检查；endpoint 不健康时更新请求会直接返回 Portainer 错误。

## 7. Kubernetes / Helm

- 仅支持 Helm 3，不支持 Helm 2。
- Helm release 的识别依赖 Helm 3 把 release 存储在 Secret 中的事实；若部署使用 ConfigMap 存储（罕见自定义），识别会失败。
- Kubernetes 工作负载只支持 `Deployment`、`StatefulSet`、`DaemonSet` 三类；CronJob、Job 等暂不支持。
- 多服务工作负载（一个 Deployment 内有多个 container）需要在执行器的服务绑定步骤中为每个 container 显式选择版本来源。

## 8. 通知

- 仅支持 Webhook 通道。
- Webhook URL 存放在 SQLite 中但**未加密**。与数据库一同访问的人可以读取原文。
- Webhook 请求失败 **没有自动重试**，失败会记录日志但不会排队重放。
- 不支持带自定义 HTTP 头的 webhook —— 只能依赖 URL 中的密钥片段做鉴权。

## 9. 追踪器

- 发布渠道只能使用正式版、预发布版、测试版、金丝雀版四个分类，不能自定义。
- 包含 / 排除正则当前仅匹配版本标签。无法基于 release body、author 等更高级信息筛选。
- 匿名访问 GitHub 与 Docker Hub 的速率限制，实际使用中推荐使用凭证。
- 容器来源的发布时间准确性取决于 Registry；在受限 Registry 上可能不得不使用「首次观察时间」。

## 10. 数据库与迁移

- dbmate 迁移是**向前兼容的**。一旦新版本的迁移已执行，回退到旧版本容器可能因 schema 不匹配而启动失败，恢复需要从备份重建。
- 数据库备份必须与 `system-secrets.json` 成对保存，否则已加密数据无法解密。

## 11. API / UI

- 没有公开的 API 版本化策略。`/api` 下是隐式的 v1；破坏性变更不频繁，但会通过 README 路线图与 release notes 告知。
- 没有内置审计日志。运行历史（`ExecutorRunHistory` / `SourceFetchRun`）承担了主要的可追溯性职责。
- 前端仅提供 zh / en 两种语言。
- OIDC 目前用于用户登录，不支持以 OIDC 身份调用 API（API 仍然使用本地 JWT）。
- 密码策略非常宽松（仅校验最小长度 6 位）；若需要更强的策略，建议通过 OIDC 接入支持密码策略的 IdP。

---

发现未列出的限制或已过期的条目，请在 GitHub 仓库提 issue 或 PR。本页会随版本迭代持续更新。
