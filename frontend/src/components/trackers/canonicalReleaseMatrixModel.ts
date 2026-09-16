import type {
    AggregateTracker,
    TrackerCurrentMatrixColumn,
    TrackerCurrentMatrixRow,
    TrackerCurrentSourceContribution,
    TrackerSourceType,
    ReleaseArtifactRevision,
    ReleaseHistoryItem,
    TrackerCurrentView,
} from "@/api/types"

const RELEASE_NOTES_PREFERRED_SOURCE_TYPES = new Set<TrackerSourceType>(["github", "gitlab", "gitea"])

export interface TrackerCurrentMatrixPresentationColumn {
    channelKey: string
    channelType: TrackerCurrentMatrixColumn["channel_type"]
    enabled: boolean
}

export interface TrackerCurrentMatrixPresentationRow {
    trackerReleaseHistoryId: number
    identityKey: string
    displayVersion: string
    publishedAt: string
    publishedAtIsEstimated: boolean
    digest: string
    matchedChannelCount: number
    selectedChannelKeys: string[]
    sourceTypeBadges: NonNullable<TrackerCurrentSourceContribution["source_type"]>[]
    helmChartVersion: string | null
    aliases: string[]
    artifacts: ReleaseArtifactRevision[]
    sourceContributions: TrackerCurrentSourceContribution[]
}

export interface TrackerCurrentMatrixPresentationModel {
    columns: TrackerCurrentMatrixPresentationColumn[]
    rows: TrackerCurrentMatrixPresentationRow[]
}

function normalizeGroupingVersion(version: string | null | undefined): string | null {
    if (!version) {
        return null
    }

    const normalizedVersion = normalizeDisplayVersion(version).trim()
    if (!normalizedVersion) {
        return null
    }

    return normalizedVersion.replace(/\+.*$/, "")
}

function getGroupingKey(row: TrackerCurrentMatrixRow): string {
    const contributionVersions = row.source_contributions
        .flatMap((contribution) => [contribution.app_version, contribution.version])
        .map((version) => normalizeGroupingVersion(version))
        .filter((version): version is string => version !== null)

    return contributionVersions[0] ?? normalizeGroupingVersion(row.version) ?? row.identity_key
}

function normalizeDisplayVersion(version: string): string {
    if (/^version\//i.test(version)) {
        return normalizeDisplayVersion(version.slice("version/".length))
    }

    if (/^release\//i.test(version)) {
        return normalizeDisplayVersion(version.slice("release/".length))
    }

    return /^v\d/.test(version) ? version.slice(1) : version
}

function mergeArtifactRevisions(
    ...groups: Array<ReleaseArtifactRevision[] | undefined>
): ReleaseArtifactRevision[] {
    const artifacts = new Map<string, ReleaseArtifactRevision>()
    for (const artifact of groups.flatMap((group) => group ?? [])) {
        const digest = artifact.digest.trim().toLowerCase()
        if (!digest) continue
        const artifactType = artifact.artifact_type ?? "container_image"
        const identity = `${artifactType}:${digest}`
        const existing = artifacts.get(identity)
        const artifactIsNewer = existing
            ? compareIsoDescending(artifact.published_at, existing.published_at) < 0
            : true
        artifacts.set(identity, {
            artifact_type: artifactType,
            digest,
            version: artifactIsNewer ? artifact.version : existing?.version,
            published_at: artifactIsNewer ? artifact.published_at : existing?.published_at,
            aliases: [...new Set([...(existing?.aliases ?? []), ...artifact.aliases])],
            source_keys: [...new Set([...(existing?.source_keys ?? []), ...artifact.source_keys])],
        })
    }
    return [...artifacts.values()]
}

export interface TrackerAliasTableRow {
    key: string
    alias: string
    sourceKey: string | null
    sourceType: TrackerSourceType | null
    artifactType: ReleaseArtifactRevision["artifact_type"] | null
    artifactVersion: string | null
    artifactDigest: string | null
}

export function buildTrackerAliasTableRows(
    row: Pick<
        TrackerCurrentMatrixPresentationRow,
        "displayVersion" | "aliases" | "artifacts" | "sourceContributions"
    >,
): TrackerAliasTableRow[] {
    const sourceTypesByKey = new Map(
        row.sourceContributions.map((contribution) => [
            contribution.source_key,
            contribution.source_type,
        ]),
    )
    const aliasRows = new Map<string, TrackerAliasTableRow>()
    const addAlias = (
        alias: string,
        sourceKey: string | null,
        sourceType: TrackerSourceType | null,
        artifactType: ReleaseArtifactRevision["artifact_type"] | null,
        artifactVersion: string | null,
        artifactDigest: string | null,
    ) => {
        if (!alias || alias === row.displayVersion) return
        const key = [alias, sourceKey ?? "", artifactType ?? "", artifactDigest ?? ""].join("\u0000")
        if (!aliasRows.has(key)) {
            aliasRows.set(key, {
                key, alias, sourceKey, sourceType, artifactType, artifactVersion, artifactDigest,
            })
        }
    }

    for (const artifact of row.artifacts) {
        const sourceKeys = artifact.source_keys.length > 0 ? artifact.source_keys : [null]
        for (const alias of artifact.aliases) {
            for (const sourceKey of sourceKeys) {
                addAlias(
                    alias,
                    sourceKey,
                    sourceKey ? sourceTypesByKey.get(sourceKey) ?? null : null,
                    artifact.artifact_type ?? "container_image",
                    artifact.version ?? null,
                    artifact.digest,
                )
            }
        }
    }

    for (const contribution of row.sourceContributions) {
        for (const alias of contribution.aliases ?? []) {
            const artifactType = contribution.source_type === "container"
                ? "container_image" as const
                : contribution.source_type === "helm"
                    ? "helm_chart" as const
                    : null
            addAlias(
                alias,
                contribution.source_key,
                contribution.source_type,
                artifactType,
                contribution.source_type === "helm"
                    ? contribution.chart_version ?? contribution.version
                    : contribution.version,
                artifactType ? contribution.digest : null,
            )
        }
    }

    for (const alias of row.aliases) {
        if (![...aliasRows.values()].some((candidate) => candidate.alias === alias)) {
            addAlias(alias, null, null, null, null, null)
        }
    }

    return [...aliasRows.values()].sort((left, right) =>
        left.alias.localeCompare(right.alias, undefined, { numeric: true }) ||
        (left.sourceKey ?? "").localeCompare(right.sourceKey ?? "") ||
        (left.artifactDigest ?? "").localeCompare(right.artifactDigest ?? "")
    )
}

function compareIsoDescending(left?: string | null, right?: string | null): number {
    const leftTime = left ? Date.parse(left) : Number.NEGATIVE_INFINITY
    const rightTime = right ? Date.parse(right) : Number.NEGATIVE_INFINITY

    if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) {
        return 0
    }

    if (Number.isNaN(leftTime)) {
        return 1
    }

    if (Number.isNaN(rightTime)) {
        return -1
    }

    return rightTime - leftTime
}

function getReleaseChannelSelectionKey(channel: NonNullable<AggregateTracker["sources"][number]["release_channels"]>[number], index: number): string {
    return channel.release_channel_key ?? channel.channel_key ?? channel.key ?? `${channel.name}-${index}`
}

function matchesReleaseChannel(
    release: Pick<ReleaseHistoryItem, "tag_name" | "prerelease">,
    channel: NonNullable<AggregateTracker["sources"][number]["release_channels"]>[number],
    sourceType?: TrackerSourceType,
): boolean {
    if (channel.enabled === false) {
        return false
    }
    const supportsReleaseType =
        sourceType === undefined ||
        sourceType === "github" ||
        sourceType === "gitlab" ||
        sourceType === "gitea"
    if (supportsReleaseType) {
        if (channel.type === "release" && release.prerelease) {
            return false
        }
        if (channel.type === "prerelease" && !release.prerelease) {
            return false
        }
    }

    if (channel.include_pattern) {
        try {
            if (!new RegExp(channel.include_pattern).test(release.tag_name)) {
                return false
            }
        } catch {
            return false
        }
    }

    if (channel.exclude_pattern) {
        try {
            if (new RegExp(channel.exclude_pattern).test(release.tag_name)) {
                return false
            }
        } catch {
            return false
        }
    }

    return true
}

export function buildTrackerHistoryMatrixPresentationModel(
    sources: AggregateTracker["sources"],
    items: ReleaseHistoryItem[],
    sortMode?: AggregateTracker["version_sort_mode"],
): TrackerCurrentMatrixPresentationModel {
    const columns = sources.flatMap((source, sourceIndex) =>
        (source.release_channels ?? [])
            .filter((channel) => channel.enabled !== false)
            .map((channel, channelIndex) => ({
                channel_key: getReleaseChannelSelectionKey(channel, channelIndex),
                channel_type: channel.type ?? "release",
                enabled: channel.enabled !== false,
                channel_rank: sourceIndex + channelIndex,
            })),
    )

    const rows = items
        .flatMap((item) => {
            const primarySourceKey = item.primary_source?.source_key ?? null
            const primarySource =
                sources.find((candidate) => candidate.source_key === primarySourceKey) ?? null
            const primarySourceType: TrackerSourceType =
                item.primary_source?.source_type ?? primarySource?.source_type ?? "github"
            const fallbackContribution: TrackerCurrentSourceContribution = {
                source_release_history_id:
                    item.primary_source?.source_release_history_id ?? item.tracker_release_history_id,
                tracker_name: item.tracker_name,
                tracker_type: primarySourceType,
                source_key: item.primary_source?.source_key ?? primarySourceKey ?? "",
                source_type: primarySourceType,
                contribution_kind: "primary",
                version: item.version,
                name: item.name,
                tag_name: item.tag_name,
                published_at: item.published_at,
                url: item.changelog_url || item.url,
                changelog_url: item.changelog_url,
                prerelease: item.prerelease,
                body: item.body ?? null,
                digest:
                    primarySourceType === "container" || primarySourceType === "helm"
                        ? item.digest
                        : null,
                app_version: item.app_version ?? null,
                chart_version: item.chart_version ?? null,
                observed_at: item.created_at,
                aliases: item.aliases ?? [],
            }
            const sourceContributions =
                item.source_contributions && item.source_contributions.length > 0
                    ? item.source_contributions
                    : [fallbackContribution]
            const matchedChannelKeys = [
                ...new Set(
                    sourceContributions.flatMap((contribution) => {
                        const source = sources.find(
                            (candidate) => candidate.source_key === contribution.source_key,
                        )
                        return (source?.release_channels ?? [])
                            .filter((channel) =>
                                matchesReleaseChannel(contribution, channel, contribution.source_type),
                            )
                            .map((channel, index) =>
                                getReleaseChannelSelectionKey(channel, index),
                            )
                    }),
                ),
            ]

            if (matchedChannelKeys.length === 0) {
                return []
            }

            const latestPublishedAt = sourceContributions.reduce(
                (latest, contribution) =>
                    compareIsoDescending(contribution.published_at, latest) < 0
                        ? contribution.published_at
                        : latest,
                item.published_at,
            )

            return [{
                tracker_release_history_id: item.tracker_release_history_id,
                identity_key: item.identity_key,
                version: item.version,
                digest: item.digest ?? item.identity_key,
                published_at: latestPublishedAt,
                matched_channel_count: matchedChannelKeys.length,
                channel_keys: matchedChannelKeys,
                primary_source: item.primary_source,
                source_contributions: sourceContributions,
                aliases: item.aliases ?? [],
                artifacts: item.artifacts ?? [],
                cells: Object.fromEntries(
                    columns.map((column) => [
                        column.channel_key,
                        matchedChannelKeys.includes(column.channel_key)
                            ? {
                                channel_key: column.channel_key,
                                channel_type: column.channel_type,
                                selected: true,
                            }
                            : null,
                    ]),
                ),
            } satisfies TrackerCurrentMatrixRow]
        })

    return buildTrackerCurrentMatrixPresentationModel({ columns, rows }, sortMode)
}

function parseVersionParts(version: string): {
    numeric: number[]
    prerelease: string | null
} {
    const normalizedVersion = normalizeGroupingVersion(version) ?? version
    const [coreVersion, prereleasePart] = normalizedVersion.split("-", 2)

    return {
        numeric: coreVersion
            .split(".")
            .map((part) => Number(part))
            .filter((part) => !Number.isNaN(part)),
        prerelease: prereleasePart ?? null,
    }
}

function tokenizePrereleaseIdentifier(identifier: string): Array<string | number> {
    return Array.from(identifier.matchAll(/[A-Za-z]+|\d+/g)).map((match) => {
        const token = match[0]
        const numericToken = Number(token)
        return Number.isNaN(numericToken) ? token : numericToken
    })
}

function comparePrereleaseIdentifierAscending(left: string, right: string): number {
    if (left === right) {
        return 0
    }

    const leftTokens = tokenizePrereleaseIdentifier(left)
    const rightTokens = tokenizePrereleaseIdentifier(right)
    const maxLength = Math.max(leftTokens.length, rightTokens.length)

    for (let index = 0; index < maxLength; index += 1) {
        const leftToken = leftTokens[index]
        const rightToken = rightTokens[index]

        if (leftToken === rightToken) {
            continue
        }
        if (leftToken === undefined) {
            return -1
        }
        if (rightToken === undefined) {
            return 1
        }

        if (typeof leftToken === "number" && typeof rightToken === "number") {
            return leftToken - rightToken
        }
        if (typeof leftToken === "number") {
            return -1
        }
        if (typeof rightToken === "number") {
            return 1
        }

        const comparison = leftToken.localeCompare(rightToken)
        if (comparison !== 0) {
            return comparison
        }
    }

    return left.localeCompare(right)
}

function comparePrereleaseAscending(left: string | null, right: string | null): number {
    if (left === right) {
        return 0
    }
    if (left === null) {
        return 1
    }
    if (right === null) {
        return -1
    }

    const leftParts = left.split(".")
    const rightParts = right.split(".")
    const maxLength = Math.max(leftParts.length, rightParts.length)

    for (let index = 0; index < maxLength; index += 1) {
        const leftPart = leftParts[index]
        const rightPart = rightParts[index]

        if (leftPart === rightPart) {
            continue
        }
        if (leftPart === undefined) {
            return -1
        }
        if (rightPart === undefined) {
            return 1
        }

        const leftNumber = Number(leftPart)
        const rightNumber = Number(rightPart)
        const leftIsNumber = !Number.isNaN(leftNumber)
        const rightIsNumber = !Number.isNaN(rightNumber)

        if (leftIsNumber && rightIsNumber) {
            return leftNumber - rightNumber
        }
        if (leftIsNumber) {
            return -1
        }
        if (rightIsNumber) {
            return 1
        }

        const comparison = comparePrereleaseIdentifierAscending(leftPart, rightPart)
        if (comparison !== 0) {
            return comparison
        }
    }

    return 0
}

function compareVersionDescending(left: string, right: string): number {
    const leftParts = parseVersionParts(left)
    const rightParts = parseVersionParts(right)
    const maxLength = Math.max(leftParts.numeric.length, rightParts.numeric.length)

    for (let index = 0; index < maxLength; index += 1) {
        const leftNumber = leftParts.numeric[index] ?? 0
        const rightNumber = rightParts.numeric[index] ?? 0
        if (leftNumber !== rightNumber) {
            return rightNumber - leftNumber
        }
    }

    return comparePrereleaseAscending(rightParts.prerelease, leftParts.prerelease)
}

function getContributionReleaseNotesPriority(contribution: TrackerCurrentSourceContribution): number {
    const hasBody = Boolean(contribution.body)
    const hasRepoBackedSource = RELEASE_NOTES_PREFERRED_SOURCE_TYPES.has(contribution.source_type)

    if (hasBody && hasRepoBackedSource) {
        return 3
    }

    if (hasBody) {
        return 2
    }

    if (hasRepoBackedSource) {
        return 1
    }

    return 0
}

export function getPreferredTrackerCurrentContributionForRow(
    row: Pick<TrackerCurrentMatrixRow, "source_contributions">,
): TrackerCurrentSourceContribution | null {
    if (row.source_contributions.length === 0) {
        return null
    }

    return row.source_contributions.reduce((preferred, candidate) => {
        const preferredPriority = getContributionReleaseNotesPriority(preferred)
        const candidatePriority = getContributionReleaseNotesPriority(candidate)

        if (candidatePriority !== preferredPriority) {
            return candidatePriority > preferredPriority ? candidate : preferred
        }

        return compareIsoDescending(candidate.published_at, preferred.published_at) < 0
            ? candidate
            : preferred
    })
}

export function buildTrackerCurrentMatrixPresentationModel(
    matrix: TrackerCurrentView["matrix"],
    sortMode?: AggregateTracker["version_sort_mode"],
): TrackerCurrentMatrixPresentationModel {
    const columns: TrackerCurrentMatrixPresentationColumn[] = matrix.columns.map((column) => ({
        channelKey: column.channel_key,
        channelType: column.channel_type,
        enabled: column.enabled,
    }))

    const groupedRows = new Map<string, TrackerCurrentMatrixPresentationRow>()

    for (const row of matrix.rows) {
        const groupingKey = getGroupingKey(row)
        const displayVersion = normalizeGroupingVersion(row.version) ?? normalizeDisplayVersion(row.version)
        const existingRow = groupedRows.get(groupingKey)
        const sourceContributions = existingRow
            ? [...existingRow.sourceContributions, ...row.source_contributions]
            : [...row.source_contributions]
        const selectedChannelKeys = existingRow
            ? [...new Set([...existingRow.selectedChannelKeys, ...row.channel_keys])]
            : [...row.channel_keys]
        const aliases = [...new Set([
            ...(existingRow?.aliases ?? []),
            ...(row.aliases ?? []),
            ...sourceContributions.flatMap((contribution) => contribution.aliases ?? []),
        ])]
        const sourceTypeBadges = [...new Set(sourceContributions.map((contribution) => contribution.source_type))]
        const contributionArtifacts = (row.artifacts?.length ?? 0) > 0
            ? []
            : sourceContributions
                .filter(
                    (contribution): contribution is TrackerCurrentSourceContribution & { digest: string } =>
                        Boolean(contribution.digest) &&
                        (contribution.source_type === "container" || contribution.source_type === "helm"),
                )
                .map((contribution) => ({
                    artifact_type: contribution.source_type === "helm"
                        ? "helm_chart" as const
                        : "container_image" as const,
                    digest: contribution.digest,
                    version: contribution.source_type === "helm"
                        ? contribution.chart_version
                        : contribution.version,
                    published_at: contribution.published_at,
                    aliases: contribution.aliases ?? [],
                    source_keys: [contribution.source_key],
                }))
        const artifacts = mergeArtifactRevisions(
            existingRow?.artifacts,
            row.artifacts,
            contributionArtifacts,
        )
        const latestHelmChartContribution = sourceContributions
            .filter((contribution) => Boolean(contribution.chart_version))
            .sort((left, right) => compareIsoDescending(left.published_at, right.published_at))[0]

        const nextRow: TrackerCurrentMatrixPresentationRow = {
            trackerReleaseHistoryId: existingRow?.trackerReleaseHistoryId ?? row.tracker_release_history_id,
            identityKey: existingRow?.identityKey ?? row.identity_key,
            displayVersion,
            publishedAt: existingRow
                ? (compareIsoDescending(row.published_at, existingRow.publishedAt) < 0 ? row.published_at : existingRow.publishedAt)
                : row.published_at,
            publishedAtIsEstimated: sourceContributions.some(
                (contribution) => contribution.published_at_source === "first_observed",
            ),
            digest: existingRow?.digest ?? row.digest,
            matchedChannelCount: selectedChannelKeys.length,
            selectedChannelKeys,
            sourceTypeBadges,
            helmChartVersion: latestHelmChartContribution?.chart_version ?? null,
            aliases,
            artifacts,
            sourceContributions,
        }

        groupedRows.set(groupingKey, nextRow)
    }

    const ungroupedRows = Array.from(groupedRows.values())
    const shouldFallbackToVersionOrder =
        sortMode === "published_at" &&
        ungroupedRows.length > 0 &&
        ungroupedRows.every((row) => /^\d+(?:\.\d+)+$/.test(row.displayVersion)) &&
        ungroupedRows.some((row) => row.publishedAtIsEstimated)
    const rows = ungroupedRows.sort((left, right) => {
        const publishedAtComparison = compareIsoDescending(left.publishedAt, right.publishedAt)
        const versionComparison = compareVersionDescending(left.displayVersion, right.displayVersion)
        const sourceCountComparison = right.sourceTypeBadges.length - left.sourceTypeBadges.length

        if (sortMode === "published_at") {
            return shouldFallbackToVersionOrder
                ? versionComparison || publishedAtComparison || sourceCountComparison
                : publishedAtComparison || versionComparison || sourceCountComparison
        }
        if (sortMode === "semver") {
            return versionComparison || publishedAtComparison || sourceCountComparison
        }

        // Keep the legacy presentation order for callers that do not specify a mode.
        return sourceCountComparison || versionComparison || publishedAtComparison
    })

    return {
        columns,
        rows,
    }
}
