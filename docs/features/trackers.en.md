---
title: Trackers
---

# Trackers

A tracker defines which version sources ReleaseTracker scans, how it filters and merges releases, and what version view is produced for executors to consume.

## 1. Model

Each tracker is an **aggregate tracker** that binds one or more **tracker sources**. Each source can carry its own set of **release channel** rules.

```text
AggregateTracker
├── primary_changelog_source_key   # Chooses which source provides release notes
├── sources[]
│   ├── source_key                  # Unique within the tracker
│   ├── source_type                 # github / gitlab / gitea / helm / container
│   ├── source_config               # Type-specific fields
│   ├── credential_name             # Reference into Credentials
│   └── release_channels[]          # Channel filter rules
└── ...
```

## 2. Supported source types

| Type | Required `source_config` | Optional |
| ---- | ------------------------ | -------- |
| `github` | `repo` (`owner/name`) | `fetch_mode` (`graphql_first` / `rest_first`; default `rest_first`) |
| `gitlab` | `project` (`group/project`) | `instance` (self-hosted URL) |
| `gitea` | `repo` (`owner/name`) | `instance` (Gitea URL) |
| `helm` | `repo` (chart repository URL) + `chart` | — |
| `container` | `image` (e.g. `library/nginx` or `owner/image`) + `registry` | `published_at_mode` (`auto` / `prefer_real` / `first_observed`; default `auto`) |

All fields are validated as non-empty strings at save time; invalid configs return `400`.

### Notable options

- **`github.fetch_mode`**
  - `graphql_first`: try the GraphQL releases endpoint first; fall back to REST if it fails (token scope mismatch, GraphQL quota exhaustion, …).
  - `rest_first` (default): use the REST API directly.
- **`container.published_at_mode`**
  - `auto` (default): fetch the image config blob for well-behaved registries; fall back to "first observed time" for rate-limited anonymous registries such as Docker Hub or Quay without credentials.
  - `prefer_real`: always attempt to fetch the config blob. Operators accept the rate-limit cost.
  - `first_observed`: never fetch the config blob; use the time ReleaseTracker first observed the tag.

### `fallback_tags` (aggregate level)

When a source has no release data (common for repositories that only tag), enabling `fallback_tags=true` lets the tracker derive versions from `refs/tags`. This is an aggregate-level toggle, not per source.

## 3. Release channels

Release channels split releases from a source into one of four slots: `stable`, `prerelease`, `beta`, `canary`.

```text
ReleaseChannel {
  release_channel_key: string         # Unique within the source
  name:                stable | prerelease | beta | canary
  type:                release | prerelease | null
  include_pattern:     regex | null
  exclude_pattern:     regex | null
  enabled:             bool
}
```

Rules:

- `name` **must** be one of the four enum values; custom names are not allowed.
- `type` only applies to GitHub / GitLab / Gitea sources (platforms that distinguish release vs. prerelease). It is ignored for Helm and container sources.
- `include_pattern` uses Python's `re.search` against `tag_name`; the release must match to be included.
- `exclude_pattern` excludes matching releases and **takes precedence** over `include_pattern`.
- A channel with `enabled=false` does not participate in filtering (effectively inactive).
- A single release may qualify for multiple channels; each channel is evaluated independently.

Match scope: `include_pattern` and `exclude_pattern` both currently match against `tag_name` only.

## 4. Aggregation and the "current view"

The scheduler periodically scans each enabled source and writes observations into their histories:

```
Upstream source
  ↓ scheduler fetch
SourceReleaseObservation  (raw per-fetch observation)
  ↓ dedup
SourceReleaseHistory      (per-source history, keyed by identity_key)
  ↓ channel filter + cross-source merge
TrackerReleaseHistory     (aggregate-level history)
  ↓ pick the best entry per rule
TrackerCurrentRelease     (current view)
```

The current view keeps exactly **one** entry per channel — the latest executable version, consumed by executors. The changelog source is chosen by `primary_changelog_source_key`: when the same version appears in multiple sources, the body from the selected source is used for display.

## 5. Scheduling and manual checks

- Each aggregate tracker has its own `interval` in minutes (default `360`).
- "Check now" from the tracker detail page triggers a one-off scan.
- Manual checks are throttled: a second manual trigger within 30 seconds of the previous completion is skipped, returning the previous result immediately.
- The scheduler enforces per-provider concurrency caps (2 concurrent fetches each for GitHub, GitLab, Gitea, Helm, and Container sources) to avoid hammering upstreams.

## 6. Rate limits and credentials

- **GitHub**: anonymous access has strict rate limits. For any non-trivial list of trackers, configuring a GitHub credential (`credential_type=github`) is strongly recommended.
- **Docker Hub**: anonymous manifest / config blob pulls hit rate limits quickly. Configure a `docker` credential, or use `published_at_mode=first_observed` as a temporary workaround.
- **Self-hosted GitLab / Gitea**: include the scheme in `instance`, for example `https://gitlab.company.internal`.

## 7. Common issues

!!! failure "Saving a tracker returns `source_config must be a non-empty string`"
    A required source_config field is missing or empty. Cross-check the required fields in section 2.

!!! failure "GitHub scans fail with 403 / 429"
    You have hit GitHub's rate limits. Attach a GitHub token credential, or switch `fetch_mode` to `graphql_first` (GraphQL has a separate, often higher quota for authenticated calls).

!!! failure "Container tracker shows wrong release times"
    Anonymous access to public registries can make config blob fetches unreliable. Switching to `published_at_mode=first_observed` avoids the blob fetch, at the cost of reporting ReleaseTracker's observation time rather than the true publish time.

!!! failure "Channel configuration looks correct but no release appears"
    - Check that `exclude_pattern` is not unintentionally matching (it wins over include).
    - Confirm the channel `name` is one of `stable` / `prerelease` / `beta` / `canary`.
    - Confirm the source-level and channel-level `enabled` flags are both `true`.
