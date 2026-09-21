# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **核心用户**：拥有私有基础设施、自建服务群或多环境集群的独立运维工程师、平台工程师、家庭实验室（Homelab）爱好者及小型技术团队技术负责人。
- **使用场景**：
  - 追踪散落在不同仓库（GitHub、GitLab、Gitea）、Helm 仓库或容器 Registry（Docker Hub、Harbor 等）的多来源应用版本发布；
  - 自动/受控将最新镜像或 Chart 版本应用到本地/远程运行时（Docker、Podman、Portainer、Kubernetes、SSH Compose），免除手动修改配置与逐一拉取部署的重复劳动；
  - 观察发布后的服务就绪状态（Healthcheck、Rollout），并在发生异常时收到精准通知与执行诊断。

## Product Purpose

- **产品使命**：提供一个开箱即用、轻量可控、单实例部署的版本监控与更新编排中枢。
- **解决的问题**：
  - 解决“发现新版本”与“实际运行环境部署更新”之间的割裂与繁琐手工操作；
  - 解决多来源（代码 Release 与容器镜像构建完成）与多渠道（正式版、预发布版、自定义分支标签）的异构版本聚合与精确对齐；
  - 解决自动化部署过程中“更新提交”与“服务真正就绪（健康检查）”缺乏统一可观测性的痛点。
- **成功标准**：
  - 用户只需配置一次追踪器与执行器，系统即可可靠捕获版本、按策略编排部署并在控制台一目了然呈现版本演进全景。

## Positioning

- **核心主张**：**非侵入式的端到端轻量化持续交付网关**。
- **差异化机制**：
  - **解耦声明式状态与物理部署**：多来源采集与当前版本投影解耦，将版本历史聚合为逻辑 Release 与不可变 Artifact（Digest/Tag）；
  - **严谨的单实例安全与零隐式回滚**：不盲目自动回滚掩盖故障，配置更新前原生快照锁定，支持显式安全恢复；
  - **原生就绪核验**：深度适配 Docker/Podman 原生 Healthcheck、Kubernetes 真实 Generation/Rollout 状态以及统一任务队列；
  - **极简部署依赖**：Python FastAPI + React 单体，内嵌 SQLite，无需 Redis、Celery 或重量级外部消息队列即可稳定运行。

## Operating Context

- **运行模式**：单实例自托管（Docker / Docker Compose 容器部署，支持裸机或反向代理接入）。
- **用户访问环境**：桌面端为主（复杂矩阵、多配置项、执行历史比对），兼顾移动端快速巡检、任务查看与手动触发。
- **日常工作流**：
  1. 仪表盘观察整体拉取/部署活跃态势与最近发布；
  2. 在「追踪器」管理异构版本源与聚合规则；
  3. 在「执行器」绑定运行时目标与自动/手动部署策略；
  4. 在「任务队列」查看统一抓取与部署任务执行时间线；
  5. 接收企业微信、Webhook 等事件通知。

## Capabilities and Constraints

- **核心能力**：
  - 多来源聚合（GitHub, GitLab, Gitea, Helm, Container Registry）与语义化版本过滤；
  - 统一入站仓库 Webhook 触发主动刷新，带批量合并、冷却与租约重试；
  - 运行时执行器（Docker, Podman, SSH Compose, Portainer, Kubernetes, Helm）；
  - 统一任务队列与部署前快照、部署后原生健康状态/就绪观察；
  - 统一凭证管理与系统安全密钥轮换（JWT Secret, 字段级数据加密）；
  - 中英文双语、深色/浅色主题适配。
- **坚守约束**：
  - **单管理员模式**：无复杂 RBAC，依靠管理员凭据或显式绑定 OIDC；
  - **无失败自动回滚**：执行失败绝不静默覆盖现场，通过快照提供明确的人工恢复路径；
  - **SSH Compose 限制**：严格限定最多单跳 SSH 代理，远程项目发现保持不可变性；
  - **不修改 Wiki 充当开发文档**：用户文档与工程代码边界明确。

## Brand Commitments

- **名称**：ReleaseTracker
- **调性**：专业、稳健、清晰、工程感（Precise, Robust, Scanable, Operational Craft）。
- **UI 风格基调**：现代化暗色/明色统一支持、紧凑对齐的控制台布局、清晰的语义化状态色（成功绿、警示黄、危险红、说明蓝）、无横向溢出滚动、注重表格与卡片的可扫描性（Scanability）。

## Evidence on Hand

- 真实完整的全栈代码库（Python 3.12+ FastAPI 后端与 React 19 / Vite 前端）。
- 全套测试覆盖（1300+ 后端自动化测试、350+ 前端单元测试及 Playwright 端到端浏览器测试）。
- 详尽的文档矩阵（README、FEATURES.md、mkdocs 用户文档及完整的 i18n 双语字典）。

## Product Principles

1. **真实与透明第一 (Truth & Transparency First)**：
   提交更新不等于业务就绪；未配置健康检查明确展示为运行状态，不虚假宣称业务健康。
2. **确定性高于隐式魔法 (Determinism Over Implicit Magic)**：
   不搞不确定性的自动推断，容器歧义多 Tag/多 Digest 精确核验，目标发现保持不可变。
3. **沉浸高效的操作流 (Scanable & Operational Efficiency)**：
   作为控制台工具，信息层级分明，关键元数据一目了然，常用操作快捷触达，避免多层嵌套与无谓折叠。
4. **防御性工程设计 (Defensive Craft)**：
   加密敏感凭据，脱敏显示密钥与镜像，失败任务保持幂等与可恢复性。
