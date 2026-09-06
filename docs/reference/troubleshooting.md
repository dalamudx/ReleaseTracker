---
title: 故障排查
---

# 故障排查

先记录发生时间、执行器 / 追踪器名称、运行 ID 和错误类别。分享日志前删除令牌、密码、Webhook URL、kubeconfig 和快照中的敏感字段。

## 登录或密码不可用 {#login}

首次部署从启动日志读取引导密码；旧安装不会重新生成。忘记密码或旧默认密码被禁用，使用[管理员恢复命令](../operations/accounts-and-oidc.md#password-recovery)。不要删除用户表或密钥文件尝试重新初始化。

## OIDC 失败 {#oidc}

- `redirect_uri_mismatch`：逐字核对 IdP 回调与 `{BASE URL}/auth/oidc/{slug}/callback`，包括 HTTPS、子路径和 Slug。
- 提供商已配置但无法登录：确认管理员绑定已完成，不仅是创建了提供商。
- 绑定或回调失败：检查 HTTPS、浏览器 Cookie、系统时间和 IdP 签名配置。保留错误类别，不公开完整授权 URL。

修正配置后重新发起完整登录流程，不重放旧回调。见[管理员与 OIDC](../operations/accounts-and-oidc.md#oidc)。

## UI 正常但 API / 资源 404 {#proxy}

核对[根路径与子路径示例](../operations/reverse-proxy.md)：根路径保留请求路径，子路径剥离一次前缀。检查 BASE URL 与外部地址一致，并确认 API 返回 JSON 而不是 SPA 页面。更改后从外部地址重新加载页面。

## 版本缺失或不是预期版本 {#versions}

按顺序检查：来源地址与凭证 → 上游限流 / 请求错误 → Release 与 Tag 回退 → 发布渠道、包含和排除规则 → 拉取深度 → 发布时间 / SemVer 排序。

先手动检查一个来源，确认实际返回的版本。规则只匹配标签，不能按正文筛选。参阅[版本规则](../guides/trackers.md#channels)。

## 有新版本但未执行更新 {#not-running}

检查执行器与运行时是否启用、具体来源和发布渠道是否绑定正确、是否处于手动模式、是否在维护窗口内，以及目标是否已经是当前版本。查看执行历史中的跳过原因和活动运行，不要只看追踪器的展示版本。

见[执行策略](../guides/executors.md#policies)。

## 运行时连接或发现失败 {#connections}

| 目标 | 优先检查 |
| --- | --- |
| Docker / Podman | `unix://` 或 `tcp://` 前缀、Socket 是否挂载、进程权限、服务是否监听 |
| TLS 连接 | CA 信任、证书有效期与主机名；不要关闭验证绕过错误 |
| Kubernetes | 当前上下文、凭证有效性、命名空间权限、ServiceAccount |
| Portainer | API 密钥、Endpoint ID、平台可用性与 Stack 类型 |

完整配置见[凭证与运行时](../guides/runtime-connections.md)。

## 健康检查失败 {#health}

确认目标支持当前检查流程，再核对实例到目标的网络可达性。HTTP 的私网地址或重定向被拒绝是安全策略，不是 DNS 故障；启动慢时调整等待期和探测窗口。TCP 成功只证明端口可连接。

检查失败不会自动回滚；按[健康检查与回滚](../guides/health-and-rollback.md)决定下一步。

## 更新请求超时 {#update-timeout}

暂停新的自动执行，先在 Docker / Portainer / Kubernetes / Helm 中检查实际镜像、服务状态和平台事件，再核对执行历史。写请求可能已经被服务端接受，不能把超时当作“未执行”并立即重复更新。

区分版本抓取超时、控制面请求超时和健康探测超时；[运行时操作策略](settings.md#operation-policy)不是整个升级流程的总时限。

## 没有快照或快照无效 {#snapshots}

检查目标是否在[支持矩阵](support.md#runtimes)中、是否执行过可捕获快照的更新、快照是否被清理。`legacy_unverified` 不等于损坏；`invalid` 不应继续恢复。使用[预检 API](../guides/health-and-rollback.md#preview)查看原因，不手工修改摘要让快照通过校验。

## 通知未收到 {#notifications}

先使用测试发送，再检查通知器启用状态、事件过滤、公网地址和允许端口，以及接收方的限流与消息格式。失败事件没有持久化重放队列；完整规则见[Webhook 通知](../guides/notifications.md)。

## 无法启动、写入或迁移 {#startup}

1. 检查日志中的实际错误，确认挂载路径、可写权限、磁盘空间和文件系统限制；不要直接递归放宽全部权限。
2. 确认只有一个活动实例，数据目录与 `system-secrets.json` 匹配。
3. 迁移问题先停止实例并备份，再使用 `migrate` 入口查看错误。不要在线手工改表。
4. 若是降级或数据不一致，使用[匹配版本的备份恢复](../operations/backup-and-upgrade.md#restore)。

提交问题时附版本、部署方式、复现步骤和脱敏日志，不附真实数据库、密钥文件或完整快照。
