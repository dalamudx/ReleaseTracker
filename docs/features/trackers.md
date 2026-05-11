---
title: 追踪器
---

# 追踪器

追踪器定义了 ReleaseTracker 从哪些版本源拉取 release / tag、按什么规则筛选与归并，以及生成什么样的版本视图供后续执行器使用。

## 1. 模型

每个追踪器是一个 **聚合追踪器**（Aggregate Tracker），可以绑定一个或多个 **版本源**（Tracker Source）。每个版本源可以携带自己的一组**发布渠道规则**（Release Channel）。

```text
AggregateTracker
├── primary_changelog_source_key   # 决定 release notes 来源
├── sources[]
│   ├── source_key                  # 源内唯一标识
│   ├── source_type                 # github / gitlab / gitea / helm / container
│   ├── source_config               # 类型相关字段
│   ├── credential_name             # 引用的凭证
│   └── release_channels[]          # 渠道筛选规则
└── ...
```

## 2. 支持的版本源类型

| 类型 | `source_config` 必填 | 可选字段 |
| ---- | ---- | ---- |
| `github` | `repo`（`owner/name`） | `fetch_mode`（`graphql_first` / `rest_first`，默认 `rest_first`） |
| `gitlab` | `project`（`group/project`） | `instance`（自托管实例 URL） |
| `gitea` | `repo`（`owner/name`） | `instance`（Gitea 实例 URL） |
| `helm` | `repo`（Chart 仓库 URL） + `chart`（Chart 名） | —— |
| `container` | `image`（例如 `library/nginx` 或 `owner/image`） + `registry`（Registry 根地址） | `published_at_mode`（`auto` / `prefer_real` / `first_observed`，默认 `auto`） |

字段会在保存时做类型校验（全部必须为非空字符串），配置不合法时 API 直接返回 400。

### 关键选项

- **`github.fetch_mode`**
  - `graphql_first`：优先走 GraphQL releases 接口；如失败（例如 token 无权限 / GraphQL 配额耗尽）再回落到 REST。
  - `rest_first`（默认）：直接使用 REST API。
- **`container.published_at_mode`**
  - `auto`（默认）：对行为良好的 Registry（如自建 / GHCR）拉取 image config blob 获取真实发布时间；对匿名访问受限的 Registry（如 Docker Hub、未登录的 Quay）退化为「首次观察到的时间」。
  - `prefer_real`：总是尝试拉取 config blob，操作员接受由此产生的速率限制成本。
  - `first_observed`：从不拉取 config blob，仅用服务端首次观察到的时间作为版本发布时间。

### fallback_tags（聚合追踪器级）

当版本源本身没有 release 数据（常见于只打 tag、不用 release 功能的仓库），开启 `fallback_tags=true` 会让追踪器退化到「从 `refs/tags` 解析版本」。这是追踪器级的开关，不是每个 source 独立开关。

## 3. 发布渠道

发布渠道用于把一个版本源产出的所有 release 按规则拆分到 `stable` / `prerelease` / `beta` / `canary` 四个槽位。

```text
ReleaseChannel {
  release_channel_key: string    # 源内唯一
  name:                stable | prerelease | beta | canary
  type:                release | prerelease | null
  include_pattern:     regex | null
  exclude_pattern:     regex | null
  enabled:             bool
}
```

规则：

- `name` **必须**是上述四个枚举之一，不允许自定义名字。
- `type` 只对 GitHub / GitLab / Gitea 三类源生效（这些源在平台层区分 release 与 prerelease）；对 Helm、Container 源会被忽略。
- `include_pattern` 使用 Python `re.search` 匹配版本的 `tag_name`；匹配才纳入。
- `exclude_pattern` 匹配则排除，**优先级高于** `include_pattern`。
- `enabled=false` 的渠道不参与筛选，相当于"不生效"。
- 单个版本可以同时进入多个渠道（不同渠道独立判断）。

匹配候选集：当前 `exclude_pattern` 仅对 `tag_name` 做匹配；`include_pattern` 同样如此。

## 4. 聚合与「当前视图」

调度器定期扫描每个启用的版本源，并把发现的版本写入各自的历史：

```
上游源
  ↓ 调度器抓取
SourceReleaseObservation（每次抓取的原始观察）
  ↓ 去重
SourceReleaseHistory（按 identity_key 去重的历史）
  ↓ 渠道筛选 + 源间合并
TrackerReleaseHistory（聚合追踪器级历史）
  ↓ 按规则选出当前最高版本
TrackerCurrentRelease（当前视图）
```

「当前视图」里每个渠道只会保留**一条**最新可执行版本，供执行器消费。changelog 来源由 `primary_changelog_source_key` 决定：在多个源都产出对应版本时，取指定 source 的 release body 作为展示内容。

## 5. 调度与手动检查

- 每个聚合追踪器有独立的 `interval`（分钟，默认 `360`）。
- 可在追踪器详情页点击「立即检查」手动触发一次扫描。
- 手动检查受服务端节流：同一追踪器距离上次完成的检查小于 30 秒时，再次手动触发会被跳过并直接返回上次结果。
- 调度器内部对每种源类型都有并发上限（GitHub、GitLab、Gitea、Helm、Container 各自允许 2 并发），避免同一时间对同一上游施压过多。

## 6. 速率限制与凭证

- **GitHub**：未配置 token 的匿名访问速率非常有限。对中等规模的追踪器列表而言，强烈建议配置 GitHub 凭证（`credential_type=github`）。
- **Docker Hub**：匿名拉取镜像 manifest / config blob 的速率限制很严，推荐配置 `docker` 类型凭证；`published_at_mode=first_observed` 可以作为临时规避手段。
- **自托管 GitLab / Gitea**：`instance` 需要包含协议，例如 `https://gitlab.company.internal`。

## 7. 常见问题

!!! failure "新建追踪器保存时报 400：`source_config must be a non-empty string`"
    源配置字段漏填或填了空值。对照第 2 节的必填字段表补齐。

!!! failure "GitHub 扫描很快失败，日志里出现 403 / 429"
    命中了 GitHub 的速率限制。配置一个 GitHub token 类型凭证并在源上引用，或将 `fetch_mode` 切到 `graphql_first`（GraphQL 用 token 时有较高配额）。

!!! failure "容器追踪器版本时间不准"
    如果用匿名访问公共 Registry，切到 `published_at_mode=first_observed` 可以避开 config blob 拉取，代价是时间是"首次被 ReleaseTracker 观察到"的时刻。

!!! failure "渠道配置看起来正确，但版本没出现"
    - 检查 `exclude_pattern` 是否意外匹配（它优先于 include）。
    - 检查 `name` 是否拼写错误（只能是 `stable` / `prerelease` / `beta` / `canary`）。
    - 检查源级 `enabled` 与渠道级 `enabled` 是否都为 `true`。
