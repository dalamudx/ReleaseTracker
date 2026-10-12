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
| 版本选择 | 按最高可比较版本选择；Helm 比较 Chart 版本，不是 appVersion |
| Release 回退 | 上游没有返回 Release 时，可回退到原始 Git Tag |

不再提供时间排序。旧 `published_at` 排序配置兼容读取，但使用版本选择规则。相同版本的不同制品不能靠时间决定先后：同一个 tag 跟踪当前 digest；等价版本标签指向不同制品时需要先消除冲突。`latest`、`nightly` 等非版本标签须通过精确包含规则（例如 `^nightly$`）显式绑定，不参与最高版本竞争。

容器抓取不请求 Config Blob、构建时间或 OCI 标签元数据；旧 `published_at_mode` 参数兼容忽略。时间不参与版本选择。默认日期依次选择真实推送时间、仓库入库时间、明确标注的仓库修改时间；缺失显示“未知”，不借用发现或构建时间。紧凑日期支持悬停或聚焦查看完整语义，手机端保留时间列。

| 仓库 | 日期获取与语义 |
| --- | --- |
| Docker Hub | 公共 tag API `tag_last_pushed`；精确匹配 tag、顶层 manifest digest，不发送仓库凭证 |
| GHCR | GitHub Packages 包版本 `created_at`；匹配 digest 和 tag，显示入库时间，不使用 `updated_at` 冒充推送 |
| Quay / 自托管 Quay | 原生 tag API `start_ts` / `last_modified`；匹配 tag、manifest digest，分别为标签入库 / 修改时间 |
| Harbor | 原生 artifact API `push_time`；匹配 digest 和 tag，通过同源 `/service/token` 挑战识别 |
| JFrog / 其他 OCI 仓库 | 提供 digest 的同一次 manifest HEAD/GET 响应 `Last-Modified`，明确显示仓库修改时间，不冒充推送；无额外请求 |

元数据最多追加 10 次请求、总预算 5 秒；超时、权限不足、限流或接口不可用不影响版本和 digest 获取。同一 digest 可保留已知日期，换 digest 不继承旧日期。发现和最近确认时间仅供后台扫描、审计。

容器来源的仓库时间查询直接复用该来源的拉取凭证，不另设凭证。GHCR 仅在拉取凭证本身是可识别的 GitHub token（需 `read:packages`）时用于 `api.github.com`（固定首方配对，不复用交换的 OCI bearer token）；Quay/Harbor 仅向同源仓库发送该凭证，自托管 Quay 通过同源 `/v2/auth` 挑战识别。凭证权限不足或接口不可用时，仍可展示已校验的 manifest 修改时间，否则显示未知。元数据拒绝跨源重定向。

升级迁移会保留历史身份与任务记录，旧复核批准失效；启动时先从本地历史重建当前版本视图，不发起抓取、通知或部署。旧自动目标快照不会被无声替换为新目标，须按新规则重新生成复核。
每个追踪渠道独立使用历史保留额度。例如两个渠道、保留 20 条，分别选择各自的 20 条；交叠记录共享存储但分别计入额度，清理保留集合的并集。当前目标、未完成部署和恢复任务的必要记录作为额外保护记录统计。抓取后与启动时补做清理，定时维护兜底。

当前标签指向与渠道选择快照独立于历史保留；版本视图首先显示当前快照，历史分页不会隐藏当前目标。容器默认接受带点分隔的版本，纯整数标签需显式选择兼容模式；固定浮动标签需使用精确匹配规则。迁移回填时，与最近一次观察到的标签指向（digest）一致的标签直接视为已确认，无需重新检查；缺少这类记录的标签等待下次仓库检查。完整标签列表中的最高候选尚未确认 digest 或等价版本存在制品冲突时，渠道显示待确认／冲突，不自动选择更低版本部署。非容器来源仍受上游抓取覆盖范围限制。

## 自定义 Changelog {#changelog}

上游已有 Release Notes 时保留默认设置。只有说明存放在仓库文件中时，才启用自定义 Changelog；追踪器必须包含 GitHub、GitLab 或 Gitea 来源。

| 文件结构 | 路径模板 | 提取方式 |
| --- | --- | --- |
| 单文件记录多个版本 | `CHANGELOG.md` | 匹配版本段落 |
| 每版本一个文件 | `docs/releases/{version}.md` | 整个文件 |
| Kubernetes 风格 | `CHANGELOG/CHANGELOG-{major}.{minor}.md` | 从匹配段落的子标题开始 |

路径支持 `{tag}`、`{version}`、`{major}`、`{minor}`、`{patch}`。读取引用可选默认分支、发布标签或指定分支 / tag / commit。版本标题留空时自动匹配常见格式；匹配失败再指定标题模板。子标题提取需设置前缀，例如 `Changelog since`。

![自定义 Changelog 的来源、路径与提取配置](../images/trackers-changelog.png)

## 仓库 Webhook {#repository-webhooks}

定时检查之外，可让 Git 平台在发布版本或 Actions 运行成功时通知 ReleaseTracker 立即拉取。Webhook 只触发按既有规则的来源拉取，请求内容不会直接写入版本，也不会触发部署。

1. 在 **Webhook → 仓库 Webhook** 中选择 Git 来源和平台协议（GitHub、GitLab、Gitea、Forgejo）。
2. 设置至少 16 个字符的验证密钥。GitHub / Gitea / Forgejo 使用请求体 HMAC-SHA256 签名；GitLab 推荐 Signing Token（`whsec_`），Secret Token 仅用于旧实例。
3. 选择接收的事件：版本发布、Actions 运行成功。分支和工作流过滤仅作用于 Actions 事件。
4. 需要在构建成功后同时刷新镜像或 Chart 时，在 **联动制品来源** 中选择同一追踪器的 OCI / Helm 来源。
5. 将生成的接收地址（`{BASE URL}/api/webhooks/repository/{id}`）和密钥填入 Git 平台，启用后在投递记录中确认收到事件。

接收地址必须能从 Git 平台访问。签名缺失或不匹配的请求被拒绝；重复投递会去重并计入投递记录。

## 验证结果 {#verify}

手动检查后核对版本标签、来源、发布渠道和发布说明。关闭追踪器会停止定时检查，不会删除配置。结果不符合预期时，按[版本缺失或未更新](../reference/troubleshooting.md#versions)排查；先修正规则，再启用自动执行。
