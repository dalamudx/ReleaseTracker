---
title: 通知
---

# 通知

ReleaseTracker 当前仅支持 Webhook 通知。Webhook 消息体同时兼容 Discord 与 Slack，并支持中英文两种语言。

## 1. 创建 Webhook 通知

打开 **通知 → 新建**，主要填写：

- **名称**：用于在通知列表中识别这条配置。
- **Webhook URL**：目标地址，必须是完整的 HTTP/HTTPS URL。
- **订阅事件**：勾选需要发送的事件；未勾选的事件不会发送。
- **启用状态**：关闭后即使事件触发也不会发送。
- **语言**：选择通知内容使用英文或中文。
- **描述**：可选，用于记录这个 Webhook 对应的频道、团队或用途。

## 2. 支持的事件

| 事件 | 触发时机 |
| ---- | ---- |
| `new_release` | 聚合追踪器在当前视图上出现**新版本**（与上一次的 winner 不同）。 |
| `republish` | 同一版本被**重新发布**（例如容器镜像重新推送相同 tag），但版本号与上一次 winner 相同。 |
| `executor_run_success` | 一次执行器运行成功结束。 |
| `executor_run_failed` | 一次执行器运行失败结束。 |
| `executor_run_skipped` | 一次调度触发被跳过（禁用 / 非维护窗口 / 目标镜像相同 / 条件不满足等）。 |

`new_release` 与 `republish` 由版本聚合调度器发起；三条 `executor_*` 事件由执行器调度器发起。

## 3. 测试发送

每条通知配置都带「测试」按钮，会向目标 URL POST 一条固定内容的测试消息：

- 中文：`这是一条来自 ReleaseTracker 的测试通知`
- 英文：`This is a test notification from ReleaseTracker`

超时：10 秒。若目标返回非 2xx 响应或连接失败，前端会显示失败原因。

## 4. Discord / Slack 兼容性

生成的 JSON 同时包含两套字段：

- Discord 友好：`content`、`embeds`；
- Slack 友好：`text`、`attachments`。

在 Discord 中创建 Incoming Webhook、在 Slack 中创建 Incoming Webhook，直接把 URL 填入即可。其它服务（如自建 HTTP 接收端）也能解析这份 JSON。

## 5. 重试与故障

- Webhook 请求使用 10 秒超时。
- 当前**没有自动重试**：失败会记录到服务端日志，但不会排队重放。
- 若目标暂时不可用且需要补发，可以在可用恢复后再次触发导致事件的操作（例如手动执行一次追踪器 / 执行器）。

## 6. 隐私与安全注意事项

- Webhook URL 一般带有机密路径片段（Discord、Slack 均如此）。该字段保存在 SQLite 中，但**未加密**。与数据库一同访问的人可以读取原文 URL。
- 不要把敏感的 release notes / 镜像名通过 Webhook 发送到团队外的渠道。
- 若 Webhook 目标需要鉴权 header（非 Discord / Slack），当前版本不支持 —— 只能依赖 URL 中的密钥片段。
