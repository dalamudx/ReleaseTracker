---
title: 已知限制
---

# 已知限制

本页列出运行 / 部署 ReleaseTracker 时需要事先知晓的限制。项目仍在演进，部分限制会随版本消失；每一条都会标注来源以便对照代码。

## 1. 部署架构

- **单进程 / 单实例**：ReleaseTracker 是一个 FastAPI 进程，数据库是本地 SQLite（WAL 模式）。**不支持横向扩展**（多副本会竞争同一 SQLite 文件），也没有分布式协调层。负载加大时唯一纵向优化手段是提升宿主机 I/O。
- **容器架构**：官方镜像目前只构建 `linux/amd64`。其它架构需要自行构建。
- **容器以 root 运行**：Dockerfile 没有声明 `USER`。rootless Docker / SELinux 等受限场景下需要额外处理数据目录权限。
- **CORS 默认放开**：后端 CORS 配置为 `allow_origins=["*"]`。直接暴露在公网前务必加反向代理做访问控制。

## 2. 快照与回滚覆盖范围

更新前快照与手动回滚**仅对以下目标模式生效**：

- `container`（Docker / Podman 单容器）
- `helm_release`（Kubernetes 上的 Helm 3 release）

以下模式不会在更新前生成快照，也无法通过 `POST /api/executors/{id}/rollback` 回滚：

- `docker_compose`
- `portainer_stack`
- `kubernetes_workload`

对这三种模式的 UI 上仍会看到「回滚」相关控件，但调用会因缺少可用快照返回 404。需要回滚时，通过原生工具完成（`kubectl rollout undo`、`docker compose up -d`、Portainer UI 等）。

## 3. 健康检查框架

- `http` / `tcp` 策略已开放使用，但 **`recovery hook`（失败时自动回滚）** 只能在那些既支持快照又支持运行时原生探测的目标上端到端工作。分组目标模式即使勾选 `failure_policy=mark_failed_and_recover`，也只会被标记为失败，不会真正回滚。
- 默认超时策略（`grace=15s / attempt=10s / interval=5s / window=180s`）对大多数工作负载是合理的，但复杂启动序列需要手动调整。

## 4. 鉴权与账号

- **密钥轮换仅限用户名等于 `admin` 的账号**。这是后端 `get_current_admin_user` 的硬编码检查，不是一个可配置角色。
  - 如果你把默认 `admin` 账号删除或改名，密钥轮换能力会随之丢失，需要从数据库手动恢复。
- **没有多角色 / 细粒度权限**。所有已登录用户看到同一套数据、有同样的操作权限（除了密钥轮换）。
- **默认账号 `admin` / `admin`**。首次登录后必须立即修改密码；生产环境暴露默认凭证等价于整台机器被接管。

## 5. Portainer

- 仅支持 Portainer 中的 `standalone` stack（Swarm stack 不支持）。发现阶段会跳过非 standalone stack，执行器绑定保存时会报错。
- 当前版本没有对 Portainer endpoint 的健康状态做前置检查；endpoint 不健康时更新请求会直接返回 Portainer 错误。

## 6. Kubernetes / Helm

- 仅支持 Helm 3，不支持 Helm 2。
- Helm release 的识别依赖 Helm 3 把 release 存储在 Secret 中的事实；若部署使用 ConfigMap 存储（罕见自定义），识别会失败。
- Kubernetes 工作负载只支持 `Deployment`、`StatefulSet`、`DaemonSet` 三类；CronJob、Job 等暂不支持。
- 多服务工作负载（一个 Deployment 内有多个 container）需要通过 `service_bindings` 显式绑定。

## 7. 通知

- 仅支持 Webhook 通道。
- Webhook URL 存放在 SQLite 中但**未加密**。与数据库一同访问的人可以读取原文。
- Webhook 请求失败 **没有自动重试**，失败会记录日志但不会排队重放。
- 不支持带自定义 HTTP 头的 webhook —— 只能依赖 URL 中的密钥片段做鉴权。

## 8. 追踪器

- 发布渠道只能命名为 `stable` / `prerelease` / `beta` / `canary` 四个枚举值之一，不能自定义。
- `include_pattern` 与 `exclude_pattern` 当前仅对 `tag_name` 做匹配。无法基于 release body、author 等更高级字段筛选。
- 匿名访问 GitHub 与 Docker Hub 的速率限制非常严，实际使用中几乎必须配置凭证。
- Container 源的 `published_at` 时间准确性取决于 Registry；在受限 Registry 上可能不得不使用「首次观察时间」。

## 9. 数据模型与迁移

- dbmate 迁移是**向前兼容的**。一旦新版本的迁移已执行，回退到旧版本容器可能因 schema 不匹配而启动失败，恢复需要从备份重建。
- 数据库备份必须与 `system-secrets.json` 成对保存，否则已加密数据无法解密。

## 10. API / UI

- 没有公开的 API 版本化策略。`/api` 下是隐式的 v1；破坏性变更不频繁，但会通过 README 路线图与 release notes 告知。
- 没有内置审计日志。运行历史（`ExecutorRunHistory` / `SourceFetchRun`）承担了主要的可追溯性职责。
- 前端仅提供 zh / en 两种语言。
- OIDC 目前用于用户登录，不支持以 OIDC 身份调用 API（API 仍然使用本地 JWT）。
- 密码策略非常宽松（仅校验最小长度 6 位）；若需要更强的策略，建议通过 OIDC 接入支持密码策略的 IdP。

---

发现未列出的限制或已过期的条目，请在 GitHub 仓库提 issue 或 PR。本页会随版本迭代持续更新。
