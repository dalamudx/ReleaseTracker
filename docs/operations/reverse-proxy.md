---
title: 反向代理与子路径
---

# 反向代理与子路径

以下 Nginx 示例假设代理与应用在同一宿主机，应用监听 `127.0.0.1:8000`，且证书文件已存在。若代理也在容器内，改用共享网络中的应用服务名，不能使用代理容器自己的回环地址。

## 根路径部署 {#root}

外部地址为 `https://releases.example.com`，代理保留原请求路径：

```nginx
server {
    listen 443 ssl;
    server_name releases.example.com;
    ssl_certificate /etc/letsencrypt/live/releases.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/releases.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

在 **系统设置 → BASE URL** 保存 `https://releases.example.com`。

## 子路径部署 {#subpath}

外部地址为 `https://example.com/releasetracker`。后端路由仍是 `/api`、`/assets` 等，因此代理需要剥离且只剥离一次 `/releasetracker/` 前缀：

```nginx
server {
    listen 443 ssl;
    server_name example.com;
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location = /releasetracker {
        return 308 /releasetracker/;
    }
    location /releasetracker/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

注意 `proxy_pass` 末尾的 `/`：它决定替换 location 前缀。先通过本机地址完成初始登录，将 BASE URL 设为 `https://example.com/releasetracker`，然后从外部地址重新打开页面。

| 浏览器请求 | 代理发给后端 |
| --- | --- |
| `/releasetracker/` | `/` |
| `/releasetracker/assets/...` | `/assets/...` |
| `/releasetracker/api/...` | `/api/...` |
| `/releasetracker/auth/oidc/provider/callback` | `/auth/oidc/provider/callback` |

BASE URL 会用于生成前端基础路径和 OIDC 地址，不会自动给后端全部路由加前缀，也不能代替代理配置。

## 验证 {#verify}

先运行 `nginx -t`，通过后再重载代理。检查：

1. 首页和直接打开的 `/trackers` 路由均正常（子路径部署加上对应前缀）。
2. 浏览器网络面板中 API 返回预期 JSON，而不是前端 HTML；静态资源没有 404。
3. 通知链接指向外部地址；启用 OIDC 时，注册的 callback 与 [OIDC 配置](accounts-and-oidc.md#oidc)完全一致。

BASE URL 为空可用于本地访问；非空值要求规范的绝对 HTTPS URL。仅修改代理头不能修复错误的 BASE URL。遇到错误见[代理排障](../reference/troubleshooting.md#proxy)。
