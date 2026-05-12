---
title: 系统设置
---

# 系统设置

所有运行参数均通过 Web UI 的「系统设置」管理，不依赖 `.env` 或环境变量。

## 1. 可配置项

| 设置 | 键名 | 默认值 | 取值 / 约束 |
| ---- | ---- | ---- | ---- |
| 时区 | `system.timezone` | `UTC` | 任意 IANA 时区名称（例如 `Asia/Shanghai`）；非法时区会被拒绝。 |
| 日志级别 | `system.log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` 之一，保存后立即生效。 |
| 版本历史保留数量 | `system.release_history_retention_count` | `20` | 整数，范围 `1..1000`。每个追踪器保留的历史版本条数。 |
| 执行器快照保留数量 | `system.executor_snapshot_retention_count` | `10` | 整数，范围 `1..1000`。每个执行器保留的快照条数。 |
| BASE URL | `system.base_url` | 空 | 必须是 http / https 绝对地址，不允许 query / fragment；支持子路径。 |

## 2. BASE URL

BASE URL 是浏览器访问 ReleaseTracker 的公开地址，用于：

- 反向代理部署下生成正确的 OIDC callback URL；
- OIDC 登录完成后回跳。

配置路径：**系统设置 → 全局配置 → BASE URL**。示例：

- `https://releases.example.com`
- `https://example.com/releasetracker`（子路径部署必须包含完整子路径）

OIDC callback 的完整形式：

```text
{BASE URL}/auth/oidc/{provider}/callback
```

若 BASE URL 为空，系统将使用浏览器侧推导的地址；在反向代理环境下强烈建议显式配置，避免 OIDC 回跳到错误域名。

## 3. 数据保留策略

**版本历史保留**按追踪器计数，超出数量的旧版本会在清理动作中删除。可通过 **系统设置 → 维护 → 清理版本历史** 手动触发（也会在 SQLite 层顺带执行 `PRAGMA optimize` 与 WAL checkpoint）。

**执行器快照保留**按执行器计数，超出数量的旧快照会在以下时机被修剪：

- 每次成功捕获更新前快照或回滚前快照之后；
- 手动触发 **系统设置 → 维护 → 清理快照历史** 时。

!!! note "快照捕获范围有限"
    完整运行时配置快照仅用于 Docker / Podman 的破坏性重建目标：单容器与 Compose 分组更新。Portainer Stack、Kubernetes Workload、Helm Release 不作为 ReleaseTracker 管理的完整配置快照目标，因此保留数量对它们通常无实际影响。详见 [已知限制](../limitations.md)。

## 4. 系统密钥轮换

ReleaseTracker 维护两把密钥，保存在 `data/system-secrets.json`：

| 密钥 | 用途 | 轮换影响 |
| ---- | ---- | ---- |
| JWT 签名密钥 | 签发 / 校验登录 token | 轮换后**所有在线会话失效**，用户需要重新登录。 |
| Fernet 加密密钥 | 加密凭证、OIDC 客户端密钥、运行时连接密钥 | 轮换会对所有加密字段重新加密；若存在任何无法用当前密钥解密的数据，轮换会整体失败。 |

### 谁可以轮换

密钥轮换接口需要 **用户名恰好为 `admin`** 的账号登录 —— 这是服务端对 `get_current_admin_user` 的硬性检查，不是可配置角色。如果将默认 `admin` 账号删除或重命名，将无法通过 UI 轮换密钥。

### 轮换流程

1. **系统设置 → 安全 → 密钥管理** 查看当前指纹与库存状态。
2. 点击对应密钥的「轮换」按钮；可让系统自动生成新密钥，也可手动提供。
3. 轮换 **加密密钥** 前，确认 `undecryptable_count`（无法解密项数）为 `0`；否则轮换会以 400 错误中断，且不会改动任何数据。
4. 轮换 **JWT 密钥** 后，所有用户需重新登录。

!!! danger "数据与密钥必须一同备份"
    `system-secrets.json` 丢失或损坏会使已加密字段无法还原。任何备份方案都必须同时包含 `releases.db` 与 `system-secrets.json`。详见 [安装部署](../getting-started/installation.md) 的「数据目录结构」。

## 5. 日志级别变更

日志级别保存后立即生效（调用 `logging.getLogger().setLevel(...)`），无需重启容器。常见取值：

- `INFO`（默认）：涵盖调度器启停、追踪器扫描、执行器运行、密钥轮换等关键事件。
- `DEBUG`：增加 HTTP 请求 / 调度器 tick / 解析细节等诊断输出，仅在定位问题时使用。
- `WARNING` / `ERROR`：仅记录异常路径，适合将日志投递至外部收集系统时压缩体量。

## 6. 时区对哪些行为生效

- 维护窗口：执行器中选择「维护窗口」策略时，允许日期和时间窗口会按该时区解析。
- 清理统计：版本历史清理、调度日志中的时间戳按该时区展示。
- 前端格式化：界面上多数时间显示会遵循该时区（除了一些强制以 UTC 显示的系统字段）。

修改时区后，需要刷新前端页面才能看到显示层更新。
