# Proposal

## Why

管理员需要在系统设置查看并调整生效的更新、备份和网络策略。当前这些策略分散在数据库、服务进程和迁移入口，并发变量在安装文档和实现中甚至使用了不同名称。

## What Changes

- 将 `system.base_url` 在界面中命名为“公开基础 URL / Public Base URL”。移除 `FRONTEND_URL` 相关命名和部署说明，升级导入也排除此变量。
- **BREAKING** 移除 `RELEASETRACKER_DB_PATH` 的路径覆盖。服务、备份、CLI 和迁移入口共同使用固定数据目录；为已有自定义挂载提供升级步骤。
- 将部署与恢复并发、后台轮询、镜像拉取宽限、部署后巡检、在线恢复、迁移前备份、CORS 来源、可信反向代理和指标令牌纳入管理员全局配置。
- **BREAKING** 已生效的旧环境配置仅可在升级时一次性导入。完成切换后由持久化设置控制相应消费者。保留 TZ 和现有 JWT、加密密钥管理流程。

## Capabilities

### New Capabilities

- `instance-configuration` 管理实例级配置的持久化、升级接续以及对运行服务与维护命令的生效规则。

### Modified Capabilities

无既有能力规格。

## Impact

涉及设置页及 API、调度器和任务队列、就绪观察、备份恢复、HTTP 入口、指标服务、OIDC 回调、容器与 CLI 入口、中英文部署文档及测试。已有自定义路径的部署需要调整卷挂载。
