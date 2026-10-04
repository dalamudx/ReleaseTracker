---
title: 消息通知
---

# 消息通知

通知用于接收版本和执行事件，不会驱动或阻止执行器更新。在 **消息通知** 页面添加通知器，消息时间按[系统设置](../reference/settings.md#global)中的时区显示。

## 通道类型 {#channels}

| 类型 | 接收地址示例 | 说明 |
| --- | --- | --- |
| 通用 Webhook | 任意公网 HTTP(S) 地址 | JSON 包含 Discord / Slack 兼容字段 |
| 企业微信 | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` | Markdown 消息 |
| 飞书 | `https://open.feishu.cn/open-apis/bot/v2/hook/...` | 卡片消息，按事件着色 |
| 钉钉 | `https://oapi.dingtalk.com/robot/send?access_token=...` | Markdown 消息 |
| Discord | `https://discord.com/api/webhooks/...` | Embed 消息 |
| Slack | `https://hooks.slack.com/services/...` | 文本消息 |
| Telegram | `https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat_id>` | 可追加 `message_thread_id` 发送到话题 |

各类型按对应平台协议组装消息并校验响应体中的错误码。飞书、钉钉机器人暂不支持“加签”，请改用自定义关键词或 IP 白名单；使用关键词时把关键词写进通知模板。企业微信、Discord、Telegram 单条消息上限约 4096 字节，超出部分截断。

## 配置与测试 {#setup}

1. 添加通知器，选择通道类型、接收地址、消息语言和消息模板。
2. 选择通知事件（见下表）。
3. 保存后点击 **测试发送**，确认接收方实际收到且显示正常。

| 通知事件 | 何时发送 |
| --- | --- |
| 发现新版本 / 版本重新发布 | 发布渠道出现新版本；同一标签的上游内容发生变化 |
| 部署完成 / 部署未完成 / 已跳过部署 | 执行器运行结束 |
| 服务核验结果 | 部署后的就绪或健康核验完成 |
| 需要确认部署 / 部署已阻断 | 部署计划需要人工确认，或因纳管冲突、安全校验被阻断，见[部署计划确认](executors.md#approval) |
| 任务处理异常 | 定时备份失败、部署后巡检异常等系统告警 |

启用自动更新前，至少验证一次失败类事件能送达。

## 消息模板 {#templates}

每个通知器引用一份模板，默认为 **系统内置模板**。在模板编辑器中可新建或另存模板：

- 一份 Jinja 模板覆盖全部事件，分别编写标题与正文，可使用条件、循环和 macro。
- 常用变量：`release`（version、source、channel、digest、notes、published_at）、`services`（name、from_display、to_display、check_label）、`health`（outcome_label 等）、`labels`（中英文文案）。
- **文案字典**以 JSON 补充或覆盖中英文词条，例如 `{"zh": {"custom": "提示"}}`，在模板中写 `{{ labels.custom }}`。
- **实时渲染预览**按事件、语言和渠道格式使用脱敏示例数据渲染，不会真实发送。

保存时会校验所有事件的中英文渲染；已入队的消息保留入队时的内容。被通知器引用的模板不能删除。核验说明和详情链接由系统追加，模板无法去掉。

## 地址和安全要求 {#security}

- 仅允许公网 HTTP(S) 地址：HTTP 端口 `80/8080`，HTTPS 端口 `443/8443/9443`。
- 私有、回环、链路本地、容器网络和云元数据地址会被拒绝；不跟随重定向。
- 接收地址以明文存储在 SQLite 中，通常包含令牌。保护数据库和备份，不要把完整 URL 贴到公开日志或问题报告。
- 不支持自定义 HTTP 请求头。

## 投递行为 {#delivery}

事件先写入持久化队列再由后台发送，服务重启或发送中断后继续投递。失败按 30 秒、2 分钟、10 分钟、30 分钟退避重试，共 4 次后标记失败，不提供手动重放。同一事件、版本和摘要在本地去重，但进程在发送后、确认前中断时仍可能重发，接收方应自行幂等。停用通知器或取消订阅事件会丢弃对应的待发消息。

新版本按**发布渠道**判断：预发布版本成为追踪器整体最新版本时，稳定渠道的新版本仍会单独通知。定时备份失败在一段连续失败中只通知一次，成功后重新计数，告警不含路径或密钥。

跳转链接依赖 [BASE URL](../reference/settings.md#global)。收不到消息时见[通知排障](../reference/troubleshooting.md#notifications)。
