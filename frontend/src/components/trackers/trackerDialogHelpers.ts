import type { TFunction } from "i18next"

import type {
    AggregateTracker,
    CreateTrackerRequest,
    GitHubFetchMode,
    ReleaseChannelInput,
    TrackerChannelType,
} from "@/api/types"

const RELEASE_CHANNEL_NAME_LABELS: Record<NonNullable<ReleaseChannelInput["name"]>, string> = {
    stable: "channel.stable",
    prerelease: "channel.prerelease",
    beta: "channel.beta",
    canary: "channel.canary",
}

export interface TrackerFormSource {
    source_key: string
    source_type: TrackerChannelType
    enabled: boolean
    credential_name?: string | null
    source_config: {
        repo?: string
        project?: string
        instance?: string
        chart?: string
        image?: string
        registry?: string
        fetch_mode?: GitHubFetchMode
    }
    release_channels: ReleaseChannelInput[]
    source_rank: number
}

export interface TrackerFormValues {
    name: string
    enabled: boolean
    description?: string
    changelog_policy?: "primary_source"
    primary_changelog_source_key: string
    sources: TrackerFormSource[]
    interval: number
    version_sort_mode: "published_at" | "semver"
    fetch_limit: number
    fetch_timeout: number
    fallback_tags: boolean
    github_fetch_mode: GitHubFetchMode
}

export const RELEASE_CHANNEL_PRESETS: Array<{ name: ReleaseChannelInput["name"] }> = [
    { name: "stable" },
    { name: "prerelease" },
    { name: "beta" },
    { name: "canary" },
]

export const RELEASE_TYPE_OPTIONS: Array<{ value: NonNullable<ReleaseChannelInput["type"]>; labelKey: string }> = [
    { value: "release", labelKey: "tracker.fields.release" },
    { value: "prerelease", labelKey: "tracker.fields.preRelease" },
]

export const SOURCE_TYPE_OPTIONS: Array<{ value: TrackerChannelType; labelKey: string }> = [
    { value: "github", labelKey: "trackers.aggregate.detail.channelType.github" },
    { value: "gitlab", labelKey: "trackers.aggregate.detail.channelType.gitlab" },
    { value: "gitea", labelKey: "trackers.aggregate.detail.channelType.gitea" },
    { value: "helm", labelKey: "trackers.aggregate.detail.channelType.helm" },
    { value: "container", labelKey: "trackers.aggregate.detail.channelType.container" },
]

export const REPO_PREFERRED_SOURCE_TYPES: TrackerChannelType[] = ["github", "gitlab", "gitea"]

export const GITHUB_FETCH_MODE_OPTIONS: Array<{ value: GitHubFetchMode; labelKey: string }> = [
    { value: "rest_first", labelKey: "tracker.fields.githubFetchModeRest" },
    { value: "graphql_first", labelKey: "tracker.fields.githubFetchModeGraphql" },
]

export function normalizeTrackerSourceType(sourceType?: string | null): TrackerChannelType {
    return (sourceType ?? "github") as TrackerChannelType
}

export function createDefaultSource(index = 0): TrackerFormSource {
    return {
        source_key: `source-${index + 1}`,
        source_type: "github",
        enabled: true,
        credential_name: "",
        source_config: { repo: "" },
        release_channels: [createDefaultReleaseChannel("stable", "release", `source-${index + 1}`, 0)],
        source_rank: index,
    }
}

export function createDefaultReleaseChannel(
    name: ReleaseChannelInput["name"],
    type: NonNullable<ReleaseChannelInput["type"]> = "release",
    ownerSourceKey = "source",
    releaseChannelIndex = 0,
): ReleaseChannelInput {
    return {
        release_channel_key: buildReleaseChannelKey(ownerSourceKey, releaseChannelIndex, name),
        name,
        type,
        include_pattern: undefined,
        exclude_pattern: undefined,
        enabled: true,
    }
}

export function createDefaultValues(): TrackerFormValues {
    return {
        name: "",
        enabled: true,
        description: "",
        changelog_policy: "primary_source",
        primary_changelog_source_key: "source-1",
        sources: [createDefaultSource(0)],
        interval: 360,
        version_sort_mode: "published_at",
        fetch_limit: 10,
        fetch_timeout: 15,
        fallback_tags: false,
        github_fetch_mode: "rest_first",
    }
}

export function normalizeSourceForForm(source: AggregateTracker["sources"][number], index: number): TrackerFormSource {
    const normalizedSourceKey = source.source_key || source.channel_key || `source-${index + 1}`

    return {
        source_key: normalizedSourceKey,
        source_type: normalizeTrackerSourceType(source.source_type ?? source.channel_type),
        enabled: source.enabled ?? true,
        credential_name: source.credential_name ?? "",
        release_channels: (source.release_channels ?? []).map((releaseChannel, releaseChannelIndex) => (
            normalizeReleaseChannelForForm(releaseChannel, normalizedSourceKey, releaseChannelIndex)
        )),
        source_rank: source.source_rank ?? source.channel_rank ?? index,
        source_config: {
            repo: source.source_config?.repo ?? source.channel_config?.repo ?? "",
            project: source.source_config?.project ?? source.channel_config?.project ?? "",
            instance: source.source_config?.instance ?? source.channel_config?.instance ?? "",
            chart: source.source_config?.chart ?? source.channel_config?.chart ?? "",
            image: source.source_config?.image ?? source.channel_config?.image ?? "",
            registry: source.source_config?.registry ?? source.channel_config?.registry ?? "",
            fetch_mode: source.source_config?.fetch_mode ?? source.channel_config?.fetch_mode ?? undefined,
        },
    }
}

export function normalizeReleaseChannelForForm(
    channel: ReleaseChannelInput,
    ownerSourceKey = "source",
    releaseChannelIndex = 0,
): ReleaseChannelInput {
    return {
        release_channel_key: ensureReleaseChannelKey(channel, ownerSourceKey, releaseChannelIndex),
        name: channel.name,
        type: channel.type ?? "release",
        include_pattern: trimOrUndefined(channel.include_pattern),
        exclude_pattern: trimOrUndefined(channel.exclude_pattern),
        enabled: channel.enabled ?? true,
    }
}

export function buildTrackerFormValues(trackerData: AggregateTracker): TrackerFormValues {
    return {
        name: trackerData.name,
        enabled: trackerData.enabled,
        description: trackerData.description ?? "",
        changelog_policy: "primary_source",
        primary_changelog_source_key: trackerData.primary_changelog_source_key ?? "",
        sources: trackerData.sources.map(normalizeSourceForForm),
        interval: trackerData.interval,
        version_sort_mode: trackerData.version_sort_mode,
        fetch_limit: trackerData.fetch_limit,
        fetch_timeout: trackerData.fetch_timeout,
        fallback_tags: trackerData.fallback_tags,
        github_fetch_mode: trackerData.github_fetch_mode ?? "rest_first",
    }
}

export function buildReleaseChannelPayload(
    channel: ReleaseChannelInput,
    sourceType: TrackerChannelType,
    ownerSourceKey = "source",
    releaseChannelIndex = 0,
): ReleaseChannelInput {
    return {
        release_channel_key: ensureReleaseChannelKey(channel, ownerSourceKey, releaseChannelIndex),
        name: channel.name,
        type: supportsReleaseTypeFilter(sourceType) ? (channel.type ?? "release") : null,
        enabled: channel.enabled ?? true,
        include_pattern: trimOrUndefined(channel.include_pattern),
        exclude_pattern: trimOrUndefined(channel.exclude_pattern),
    }
}

export function supportsReleaseTypeFilter(sourceType: TrackerChannelType): boolean {
    return REPO_PREFERRED_SOURCE_TYPES.includes(sourceType)
}

export function getCredentialTypeFilter(sourceType: TrackerChannelType): string[] {
    if (sourceType === "container") {
        return ["docker", "github", "gitlab"]
    }

    return [sourceType]
}

export function trimOrUndefined(value?: string | null): string | undefined {
    const trimmed = value?.trim()
    return trimmed ? trimmed : undefined
}

function normalizeReleaseChannelKeyPart(value: string | undefined, fallback: string): string {
    const normalizedValue = value?.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    return normalizedValue || fallback
}

export function buildReleaseChannelKey(
    ownerSourceKey: string | undefined,
    releaseChannelIndex: number,
    name: ReleaseChannelInput["name"],
): string {
    const ownerKeyPart = normalizeReleaseChannelKeyPart(ownerSourceKey, "source")
    const nameKeyPart = normalizeReleaseChannelKeyPart(name, "release-channel")
    return `${ownerKeyPart}-${releaseChannelIndex}-${nameKeyPart}`
}

export function ensureReleaseChannelKey(
    channel: ReleaseChannelInput,
    ownerSourceKey: string | undefined,
    releaseChannelIndex: number,
): string {
    return trimOrUndefined(channel.release_channel_key)
        ?? trimOrUndefined(channel.channel_key)
        ?? trimOrUndefined(channel.key)
        ?? buildReleaseChannelKey(ownerSourceKey, releaseChannelIndex, channel.name)
}

function formatApiErrorDetail(detail: unknown): string | null {
    if (typeof detail === "string") {
        return detail.trim() || null
    }

    if (Array.isArray(detail)) {
        const messages = detail.map((entry) => {
            if (typeof entry === "string") {
                return entry
            }

            if (entry && typeof entry === "object") {
                const message = (entry as { msg?: unknown }).msg
                if (typeof message === "string" && message.trim()) {
                    return message
                }

                try {
                    return JSON.stringify(entry)
                } catch {
                    return String(entry)
                }
            }

            return String(entry)
        }).filter((message) => message.trim().length > 0)

        return messages.length > 0 ? messages.join("; ") : null
    }

    if (detail && typeof detail === "object") {
        const message = (detail as { msg?: unknown }).msg
        if (typeof message === "string" && message.trim()) {
            return message
        }

        try {
            return JSON.stringify(detail)
        } catch {
            return String(detail)
        }
    }

    if (detail == null) {
        return null
    }

    return String(detail)
}

export function getApiErrorDetailMessage(error: unknown): string | null {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    return formatApiErrorDetail(detail)
}

export function getRequiredConfigKeys(sourceType: TrackerChannelType): Array<keyof TrackerFormSource["source_config"]> {
    switch (sourceType) {
        case "github":
            return ["repo"]
        case "gitlab":
            return ["instance", "project"]
        case "gitea":
            return ["instance", "repo"]
        case "helm":
            return ["repo", "chart"]
        case "container":
            return ["image", "registry"]
    }
}

export function buildSanitizedSourceConfig(source: TrackerFormSource): TrackerFormSource["source_config"] {
    switch (source.source_type) {
        case "github":
            return {
                repo: trimOrUndefined(source.source_config.repo),
                fetch_mode: source.source_config.fetch_mode ?? "rest_first",
            }
        case "gitlab":
            return {
                instance: trimOrUndefined(source.source_config.instance),
                project: trimOrUndefined(source.source_config.project),
            }
        case "gitea":
            return {
                instance: trimOrUndefined(source.source_config.instance),
                repo: trimOrUndefined(source.source_config.repo),
            }
        case "helm":
            return {
                repo: trimOrUndefined(source.source_config.repo),
                chart: trimOrUndefined(source.source_config.chart),
            }
        case "container":
            return {
                image: trimOrUndefined(source.source_config.image),
                registry: trimOrUndefined(source.source_config.registry),
            }
    }
}

export function getReleaseTypeLabel(
    releaseType: ReleaseChannelInput["type"] | null | undefined,
    t: TFunction,
): string {
    const labelKey = RELEASE_TYPE_OPTIONS.find((option) => option.value === releaseType)?.labelKey
    return labelKey
        ? t(labelKey)
        : t("trackers.aggregate.labels.blankReleaseChannelType")
}

export function getTrackerSourceHeaderLabel(
    sourceKey: string | null | undefined,
    t: TFunction,
): string {
    return trimOrUndefined(sourceKey) ?? t("trackers.aggregate.labels.blankTrackerChannelKey")
}

export function getReleaseChannelHeaderLabel(
    releaseChannel: ReleaseChannelInput,
    t: TFunction,
    sourceType?: TrackerChannelType,
): string {
    const releaseChannelName = trimOrUndefined(releaseChannel.name)
    const localizedChannelName = releaseChannelName
        ? (RELEASE_CHANNEL_NAME_LABELS[releaseChannel.name]?.length
            ? t(RELEASE_CHANNEL_NAME_LABELS[releaseChannel.name])
            : releaseChannelName)
        : t("trackers.aggregate.labels.blankReleaseChannelName")

    if (sourceType && !supportsReleaseTypeFilter(sourceType)) {
        return t("trackers.aggregate.labels.releaseChannelHeaderNoType", {
            name: localizedChannelName,
        })
    }

    return t("trackers.aggregate.labels.releaseChannelHeader", {
        type: getReleaseTypeLabel(releaseChannel.type, t),
        name: localizedChannelName,
    })
}

export function getReleaseChannelIdentity(
    releaseChannel: ReleaseChannelInput,
    ownerSourceKey: string | undefined,
    releaseChannelIndex: number,
): string {
    return ensureReleaseChannelKey(releaseChannel, ownerSourceKey, releaseChannelIndex)
}

export function getEffectivePrimarySourceKey(values: TrackerFormValues): string {
    const repoPreferredSource = values.sources.find((source) =>
        REPO_PREFERRED_SOURCE_TYPES.includes(source.source_type),
    )

    return repoPreferredSource?.source_key?.trim()
        ?? values.sources[0]?.source_key?.trim()
        ?? ""
}

export function validateRegexPattern(pattern: string | undefined, errorMessage: string, fallbackMessage: string): string | null {
    if (!pattern) {
        return null
    }

    try {
        new RegExp(pattern)
        return null
    } catch (error) {
        const reason = error instanceof Error ? error.message : fallbackMessage
        return `${errorMessage} ${reason}`
    }
}

export function buildNormalizedTrackerFormValues(values: TrackerFormValues, effectivePrimarySourceKey: string): TrackerFormValues {
    return {
        ...values,
        changelog_policy: "primary_source",
        primary_changelog_source_key: effectivePrimarySourceKey,
        sources: values.sources.map((source, index) => ({
            ...source,
            source_key: source.source_key.trim(),
            source_rank: index,
            credential_name: trimOrUndefined(source.credential_name),
            release_channels: (source.release_channels ?? []).map((releaseChannel, releaseChannelIndex) => (
                buildReleaseChannelPayload(releaseChannel, source.source_type, source.source_key, releaseChannelIndex)
            )),
        })),
    }
}

export function buildTrackerPayload(values: TrackerFormValues, effectivePrimarySourceKey: string): CreateTrackerRequest {
    const sources = values.sources.map((source, index) => ({
        source_key: source.source_key.trim(),
        source_type: source.source_type,
        enabled: source.enabled ?? true,
        credential_name: trimOrUndefined(source.credential_name),
        source_rank: index,
        source_config: buildSanitizedSourceConfig(source),
        release_channels: (source.release_channels ?? []).map((releaseChannel, releaseChannelIndex) => (
            buildReleaseChannelPayload(releaseChannel, source.source_type, source.source_key, releaseChannelIndex)
        )),
    }))

    return {
        name: values.name.trim(),
        enabled: values.enabled,
        description: trimOrUndefined(values.description),
        changelog_policy: "primary_source",
        primary_changelog_source_key: effectivePrimarySourceKey,
        sources,
        channels: sources.flatMap((source) => source.release_channels),
        interval: values.interval,
        version_sort_mode: values.version_sort_mode,
        fetch_limit: values.fetch_limit,
        fetch_timeout: values.fetch_timeout,
        fallback_tags: values.fallback_tags,
        github_fetch_mode: values.github_fetch_mode,
    }
}
