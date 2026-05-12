---
title: Trackers
---

# Trackers

A tracker defines which upstreams ReleaseTracker scans, how versions are filtered and merged, and which “current version” is offered to executors.

## 1. What you choose when creating a tracker

In **Trackers → New**, you typically configure:

- **Tracker name**: the project or component represented by this tracker.
- **Tracked channels**: one tracker can include multiple upstreams, such as GitHub releases, a Helm chart repository, and a container image registry.
- **Primary changelog channel**: when multiple channels report the same version, release notes shown in the UI come from this channel.
- **Release channels**: split versions from a tracked channel into Stable, Pre-Release, Beta, or Canary.
- **Include / exclude regex**: filter which version tags are visible in each release channel.
- **Credentials**: optional, used to increase GitHub / Docker Hub rate limits or access private repositories.

## 2. Supported tracked channels

| UI channel type | Required input | Optional settings |
| --------------- | -------------- | ----------------- |
| GitHub | Repository path (`owner/name`) | Fetch priority: REST First or GraphQL First |
| GitLab | Project path (`group/project`) | Self-hosted instance URL |
| Gitea | Repository path (`owner/name`) | Gitea instance URL |
| Helm | Chart repository URL and chart name | — |
| Container | Image name (for example `library/nginx` or `owner/image`) and registry root URL | Publish-time strategy: Auto, Prefer real publish time, or First observed time |

Required inputs are validated when saving. Missing repositories, images, charts, or similar key settings return `400` and are surfaced in the UI.

### Notable options

- **GitHub Fetch Priority**
  - **GraphQL First**: try the GraphQL releases endpoint first; fall back to REST if it fails (token scope mismatch, GraphQL quota exhaustion, and similar cases).
  - **REST First** (default): use the REST API directly.
- **Container publish-time strategy**
  - **Auto** (default): fetch the image config blob for well-behaved registries; fall back to “first observed time” for rate-limited anonymous registries such as Docker Hub or Quay without credentials.
  - **Prefer real publish time**: always attempt to fetch the config blob. Operators accept the rate-limit cost.
  - **First observed time**: never fetch the config blob; use the time ReleaseTracker first observed the tag.

### Tag-only repositories

When a GitHub / GitLab / Gitea repository has no release data (common for repositories that only tag), enable the tracker-level “fallback to tags” capability so ReleaseTracker derives versions from tags. This is a whole-tracker switch, not a per-channel setting.

## 3. Release channels

Release channels split versions from a tracked channel into four UI categories: **Stable**, **Pre-Release**, **Beta**, and **Canary**. These are the only supported categories today; custom release channel names are not supported.

Rules:

- Release-type filtering only applies to GitHub / GitLab / Gitea because those platforms distinguish releases from pre-releases. Helm and container image sources ignore this filter.
- The **include regex** must match the version tag for the version to enter the selected release channel. Leaving it empty includes all versions.
- The **exclude regex** blocks matching version tags even if the include regex also matches.
- Disabled release channels do not participate in filtering.
- A single version can qualify for multiple release channels; each channel is evaluated independently.

Include / exclude regexes currently match version tags only. They do not match release body, author, or other metadata.

## 4. Aggregation and the “current view”

The scheduler periodically scans each enabled tracked channel and writes discovered versions into history. ReleaseTracker then filters, deduplicates, merges, and chooses one current highest version per release channel for executors to consume.

When multiple tracked channels report the same version, the version is merged for display; changelog text comes from the primary changelog channel you selected.

## 5. Scheduling and manual checks

- Each tracker has its own scan interval in minutes (default `360`).
- **Check now** from the tracker detail page triggers a one-off scan.
- Manual checks are throttled: a second manual trigger within 30 seconds of the previous completion is skipped, returning the previous result immediately.
- The scheduler enforces per-provider concurrency caps (2 concurrent fetches each for GitHub, GitLab, Gitea, Helm, and Container channels) to avoid hammering upstreams.

## 6. Rate limits and credentials

- **GitHub**: anonymous access has strict rate limits. For any non-trivial list of trackers, configuring a GitHub credential is strongly recommended.
- **Docker Hub**: anonymous manifest / config blob pulls hit rate limits quickly. Configure a Docker credential, or temporarily switch the container publish-time strategy to **First observed time** to avoid config blob pulls.
- **Self-hosted GitLab / Gitea**: include the scheme in the instance URL, for example `https://gitlab.company.internal`.

## 7. Common issues

!!! failure "Saving a tracker returns 400 and says configuration cannot be empty"
    A required setting is missing or empty. Cross-check section 2 and fill in the repository path, image name, registry, chart, or other required input.

!!! failure "GitHub scans fail with 403 / 429"
    You have hit GitHub's rate limits. Attach a GitHub token credential to the tracked channel, or switch GitHub Fetch Priority to GraphQL First (GraphQL has a separate, often higher quota for authenticated calls).

!!! failure "Container tracker shows wrong release times"
    Anonymous access to public registries can make config blob fetches unreliable. Switching to **First observed time** avoids the blob fetch, at the cost of reporting ReleaseTracker's observation time rather than the true publish time.

!!! failure "Channel configuration looks correct but no release appears"
    - Check that the exclude regex is not unintentionally matching (it wins over include).
    - Confirm the release channel is set to the expected Stable, Pre-Release, Beta, or Canary category.
    - Confirm both the tracked channel and release channel are enabled.
