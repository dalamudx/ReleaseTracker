---
title: 管理员与 OIDC
---

# 管理员与 OIDC

ReleaseTracker 使用一个稳定的管理员身份，不提供注册、多租户或 RBAC。更改管理员用户名不会转移权限；OIDC 为同一个管理员增加登录方式，不会创建新用户。

首次登录见[安装与首次运行](../getting-started/installation.md)。

## 本地密码恢复 {#password-recovery}

忘记密码或升级后旧默认密码被禁用时，在有权访问数据目录的宿主机执行：

=== "Docker Compose"

    ```bash
    docker compose exec releasetracker python -m releasetracker.cli reset-admin-password
    ```

=== "Docker run"

    ```bash
    docker exec -it releasetracker python -m releasetracker.cli reset-admin-password
    ```

命令交互式读取新密码，直接使用挂载的数据目录，并撤销现有会话。不通过 HTTP API，也不要把密码放进命令行参数。该命令重置现有管理员密码，不重建已删除的管理员；管理员记录丢失时从可信备份恢复。

## 绑定 OIDC {#oidc}

1. 配置 [HTTPS 和 BASE URL](reverse-proxy.md)。
2. 在 IdP 注册应用，回调地址填写 `{BASE URL}/auth/oidc/{slug}/callback`，其中 `slug` 与下表一致。
3. 在 **系统设置 → OIDC** 创建提供商，推荐使用 Discovery。
4. 使用本地密码登录，在管理员绑定操作中输入当前本地密码，再跳转到 IdP 完成授权。
5. 返回后确认已绑定，再测试退出后使用 OIDC 登录。保留本地密码作为恢复方式。

| 字段 | 要求 |
| --- | --- |
| Slug | 小写字母、数字和连字符；创建后不可修改 |
| Client ID / Secret | 来自 IdP；编辑时 Secret 留空保留原值 |
| Issuer URL | Discovery 的 HTTPS 地址，必须与签发者一致 |
| 手工端点 | 不使用 Discovery 时配置；授权、Token、JWKS 等端点要求 HTTPS |
| Scopes | 默认 `openid email profile`；必须包含 `openid` |

仅配置提供商不会启用登录。系统只接受一个提供商和一个经过验证的 issuer + subject 绑定，不按邮箱自动匹配用户。修改或删除已绑定的提供商前，先用本地密码确认解绑。

## API 与排障 {#api}

日常操作优先使用界面。需要自动化时，管理员 API 包括：

| 操作 | 端点 |
| --- | --- |
| 查询绑定 | `GET /api/oidc-providers/admin-binding` |
| 发起绑定 | `POST /api/oidc-providers/{provider_id}/admin-binding/authorize` |
| 解除绑定 | `POST /api/oidc-providers/admin-binding/unbind` |

请求需要 ReleaseTracker 管理员 JWT；发起绑定和解绑还需当前本地密码。API 不直接接受 IdP 令牌。参数结构可在实例 `/docs` 中查看；不要在公开示例中粘贴 JWT、Client Secret 或授权回调 URL。

登录失败先检查[OIDC 排障](../reference/troubleshooting.md#oidc)，不要通过关闭签名验证或放宽身份绑定绕过错误。
