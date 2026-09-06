---
title: 追踪器与版本规则
---

# 追踪器与版本规则

追踪器负责发现和筛选版本，不会自行修改服务。只有配置了[执行器](executors.md)后，版本才会用于运行时更新。

## 添加版本来源 {#sources}

在 **追踪器 → 添加** 中设置不可变的追踪器名称，并添加一个或多个来源。界面中的“追踪渠道”指版本来源，不是下文的发布渠道。

| 来源 | 必需信息 | 需要注意 |
| --- | --- | --- |
| GitHub | `owner/repo` | 无凭证先选 REST 优先；GraphQL 优先需要 PAT |
| GitLab | 项目 ID 或 `group/project` | 自托管填写实例 URL |
| Gitea | `owner/repo`、实例 URL | 私有仓库需要令牌 |
| Helm Chart | Chart 名称、仓库 URL | 私有仓库可使用 Basic Auth |
| OCI 镜像 | 镜像名、Registry 地址 | 私有仓库需要匹配的凭证 |

凭证选择与 HTTPS 要求见[凭证与运行时连接](runtime-connections.md#credentials)。一个追踪器可同时包含 GitHub Release 与 OCI 镜像来源；执行器需要绑定具体来源，而不是仅选择项目名称。

## 筛选发布渠道 {#channels}

每个来源可配置 `stable`、`prerelease`、`beta`、`canary` 发布渠道。渠道名称与上游的 Release / Pre-Release 状态是两件事；不要只凭名称推断筛选结果。

| 设置 | 规则 |
| --- | --- |
| 发布类型 | GitHub / GitLab / Gitea 可按 Release 或 Pre-Release 过滤 |
| 包含正则 | 只保留匹配的版本标签；留空不限制 |
| 排除正则 | 在包含规则之后排除匹配标签；排除优先 |

例如只跟踪 `1.2.x` 的三段版本号，可使用 `^v?1\.2\.\d+$`。保存后先手动检查，确认返回的标签符合预期，再将该渠道绑定到执行器。

## 抓取与最新版本 {#fetching}

| 设置 | 如何选择 |
| --- | --- |
| 检查间隔 | 默认 360 分钟；缩短会增加上游请求量 |
| 单次拉取深度 | 默认 10 条；频繁发布的项目可适当增大 |
| 请求超时 | 默认 15 秒；只影响版本抓取，不是运行时更新超时 |
| 按发布时间排序 | 关注最近发布的版本，包括旧版本线的新补丁 |
| 按 SemVer 排序 | 关注最高语义版本；旧版本线补丁不会因此成为最高版本 |
| Release 回退 | 上游没有返回 Release 时，可回退到原始 Git Tag |

容器来源的发布时间可选自动、读取镜像构建时间或首次观察时间。首次观察时间不是上游发布时间，不能用于精确还原历史发布顺序。

## 自定义 Changelog {#changelog}

上游已有 Release Notes 时保留默认设置。只有说明存放在仓库文件中时，才启用自定义 Changelog；追踪器必须包含 GitHub、GitLab 或 Gitea 来源。

| 文件结构 | 路径模板 | 提取方式 |
| --- | --- | --- |
| 单文件记录多个版本 | `CHANGELOG.md` | 匹配版本段落 |
| 每版本一个文件 | `docs/releases/{version}.md` | 整个文件 |
| Kubernetes 风格 | `CHANGELOG/CHANGELOG-{major}.{minor}.md` | 从匹配段落的子标题开始 |

路径支持 `{tag}`、`{version}`、`{major}`、`{minor}`、`{patch}`。读取引用可选默认分支、发布标签或指定分支 / tag / commit。版本标题留空时自动匹配常见格式；匹配失败再指定标题模板。子标题提取需设置前缀，例如 `Changelog since`。

![自定义 Changelog 的来源、路径与提取配置](../images/trackers-changelog.png)

## 验证结果 {#verify}

手动检查后核对版本标签、来源、发布渠道和发布说明。关闭追踪器会停止定时检查，不会删除配置。结果不符合预期时，按[版本缺失或未更新](../reference/troubleshooting.md#versions)排查；先修正规则，再启用自动执行。
