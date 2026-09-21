import { Fragment, useCallback, useEffect, useMemo, useState } from "react"
import { BookOpen, ChevronDown, Copy, Edit, Inbox, Loader2, Play } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"
import { cn } from "@/lib/utils"

import type {
    AggregateTracker,
    ReleaseNotesSubject,
    TrackerCurrentSourceContribution,
} from "@/api/types"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import {
    useTracker,
    useTrackerCurrentView,
    useTrackerReleaseHistory,
} from "@/hooks/queries"
import {
    buildTrackerAliasTableRows,
    buildTrackerHistoryMatrixPresentationModel,
    getPreferredTrackerCurrentContributionForRow,
} from "@/components/trackers/canonicalReleaseMatrixModel"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModalLazy"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableRow,
} from "@/components/ui/table"
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { getTrackerChannelConfigValueLabel } from "./trackerDetailHelpers"

function getTrackerChannelTypeLabel(
    channelType: string | null | undefined,
    t: ReturnType<typeof useTranslation>["t"],
): string {
    if (!channelType) {
        return t("trackers.aggregate.detail.channelType.unknown")
    }
    const key = `trackers.aggregate.detail.channelType.${channelType}`
    const translated = t(key)
    return translated === key ? channelType : translated
}

function getTrackerChannelConfigLabel(
    key: string,
    t: ReturnType<typeof useTranslation>["t"],
): string {
    const translated = t(`trackers.aggregate.detail.configLabel.${key}`)
    return translated === `trackers.aggregate.detail.configLabel.${key}` ? key : translated
}

function mapContributionToReleaseNotesSubject(
    contribution: TrackerCurrentSourceContribution,
    trackerName: string,
    selectedChannelKeys: string[],
): ReleaseNotesSubject {
    return {
        tracker_name: trackerName,
        tracker_type: contribution.source_type,
        name: contribution.name,
        tag_name: contribution.tag_name,
        version: contribution.version,
        published_at: contribution.published_at,
        url: contribution.changelog_url || contribution.url,
        changelog_url: contribution.changelog_url,
        prerelease: contribution.prerelease,
        body: contribution.body,
        channel_name: contribution.channel_name ?? null,
        channel_keys: selectedChannelKeys,
    }
}

interface TrackerDetailProps {
    trackerName: string | null
    refreshKey: number
    onEdit?: (name: string) => void
    onCheck?: (name: string) => void
    checking?: boolean
}

function normalizeArtifactDigest(digest: string): string {
    return /^[0-9a-f]{64}$/i.test(digest) ? `sha256:${digest}` : digest
}

export function TrackerDetail({
    trackerName,
    refreshKey,
    onEdit,
    onCheck,
    checking = false,
}: TrackerDetailProps) {
    const { t, i18n } = useTranslation()
    const formatDate = useDateFormatter()
    const [sourcesOpen, setSourcesOpen] = useState(true)
    const [selectedRelease, setSelectedRelease] = useState<ReleaseNotesSubject | null>(null)

    const formatRelative = (dateString: string | null | undefined): string => {
        if (!dateString) return ""
        try {
            return formatDistanceToNow(new Date(dateString), {
                addSuffix: true,
                locale: i18n.language === "zh" ? zhCN : enUS,
            })
        } catch {
            return formatDate(dateString)
        }
    }
    const [releaseNotesOpen, setReleaseNotesOpen] = useState(false)
    const [expandedVersionIdentity, setExpandedVersionIdentity] = useState<string | null | undefined>(undefined)
    const [expandedArtifactAliases, setExpandedArtifactAliases] = useState<Set<string>>(() => new Set())
    const trackerQuery = useTracker(trackerName)
    const trackerCurrentViewQuery = useTrackerCurrentView(trackerName)
    const trackerReleaseHistoryQuery = useTrackerReleaseHistory(trackerName, { limit: 100 })
    const { refetch: refetchTracker } = trackerQuery
    const { refetch: refetchTrackerCurrentView } = trackerCurrentViewQuery
    const { refetch: refetchTrackerReleaseHistory } = trackerReleaseHistoryQuery

    const tracker: AggregateTracker | null = trackerQuery.data ?? null
    const trackerCurrentView = trackerCurrentViewQuery.data ?? null
    const loading = trackerQuery.isLoading
        || trackerCurrentViewQuery.isLoading
        || trackerReleaseHistoryQuery.isLoading
    const hasFetchError = trackerQuery.isError
        || trackerCurrentViewQuery.isError
        || trackerReleaseHistoryQuery.isError

    const refetchDetailQueries = useCallback(() => Promise.all([
        refetchTracker(),
        refetchTrackerCurrentView(),
        refetchTrackerReleaseHistory(),
    ]), [refetchTracker, refetchTrackerCurrentView, refetchTrackerReleaseHistory])


    const copyArtifactDigest = useCallback(async (digest: string) => {
        try {
            await navigator.clipboard.writeText(digest)
            toast.success(t("trackers.aggregate.detail.digestCopied"))
        } catch {
            toast.error(t("common.unexpectedError"))
        }
    }, [t])

    const toggleArtifactAliases = useCallback((artifactKey: string) => {
        setExpandedArtifactAliases((current) => {
            const next = new Set(current)
            if (next.has(artifactKey)) {
                next.delete(artifactKey)
            } else {
                next.add(artifactKey)
            }
            return next
        })
    }, [])

    useEffect(() => {
        if (!trackerName) return
        void refetchDetailQueries()
    }, [refetchDetailQueries, refreshKey, trackerName])

    const versionViewMatrixModel = useMemo(
        () =>
            tracker && trackerReleaseHistoryQuery.data
                ? buildTrackerHistoryMatrixPresentationModel(
                    tracker.sources,
                    trackerReleaseHistoryQuery.data.items,
                    tracker.version_sort_mode,
                )
                : null,
        [tracker, trackerReleaseHistoryQuery.data],
    )
    const effectiveExpandedVersionIdentity = expandedVersionIdentity === null
        ? null
        : versionViewMatrixModel?.rows.some((row) => row.identityKey === expandedVersionIdentity)
            ? expandedVersionIdentity
            : versionViewMatrixModel?.rows[0]?.identityKey ?? null

    if (!trackerName) {
        return (
            <Card className="flex h-full min-h-[320px] items-center justify-center border-dashed">
                <div className="flex w-full max-w-sm flex-col items-center gap-3 px-6 py-10 text-center">
                    <Inbox className="h-10 w-10 text-muted-foreground/60" aria-hidden />
                    <div className="space-y-1.5">
                        <div className="text-base font-semibold text-foreground">
                            {t("trackers.aggregate.detail.emptyTitle")}
                        </div>
                        <p className="text-sm text-muted-foreground">
                            {t("trackers.aggregate.detail.emptyDescription")}
                        </p>
                    </div>
                </div>
            </Card>
        )
    }

    if (loading && !tracker) {
        return (
            <div className="space-y-4">
                <Card>
                    <CardHeader>
                        <Skeleton className="h-5 w-48" />
                        <Skeleton className="mt-2 h-3 w-64" />
                    </CardHeader>
                    <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        {[1, 2, 3, 4].map((i) => (
                            <Skeleton key={i} className="h-20 w-full" />
                        ))}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader><Skeleton className="h-5 w-40" /></CardHeader>
                    <CardContent><Skeleton className="h-24 w-full" /></CardContent>
                </Card>
            </div>
        )
    }

    if ((!tracker || !trackerCurrentView) && hasFetchError) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("trackers.aggregate.detail.loadFailedTitle")}</CardTitle>
                    <CardDescription>{t("trackers.aggregate.detail.loadFailedDescription")}</CardDescription>
                </CardHeader>
            </Card>
        )
    }

    if (!tracker || !trackerCurrentView || !versionViewMatrixModel) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("trackers.aggregate.detail.loadFailedTitle")}</CardTitle>
                    <CardDescription>{t("trackers.aggregate.detail.loadFailedDescription")}</CardDescription>
                </CardHeader>
            </Card>
        )
    }

    const primaryChannel = tracker.sources.find(
        (channel) => channel.source_key === tracker.primary_changelog_source_key,
    )

    const trackerChannelReleaseCount = tracker.sources.reduce(
        (count, channel) => count + (channel.release_channels?.length ?? 0),
        0,
    )

    const latestReleaseVersion = trackerCurrentView.latest_release?.version
        ?? trackerCurrentView.status.last_version
        ?? tracker.status.last_version

    return (
        <div className="space-y-4">
            {/* Summary card — title, description, 4 quick stats. */}
            <Card className="glass-card gap-3 p-4 shadow-xs">
                <div className="flex flex-col gap-3">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 space-y-0.5">
                            <div className="flex items-center gap-2">
                                <span className="text-base font-bold tracking-tight text-foreground truncate">{tracker.name}</span>
                                <Badge variant={tracker.enabled ? "secondary" : "outline"} className="h-5 shrink-0 text-[10px]">
                                    {tracker.enabled ? t("common.enabled") : t("common.disabled")}
                                </Badge>
                            </div>
                            {tracker.description && (
                                <p className="text-xs text-muted-foreground line-clamp-1">
                                    {tracker.description}
                                </p>
                            )}
                        </div>

                        {/* Top Action Buttons for current tracker */}
                        <div className="flex items-center gap-1.5 shrink-0">
                            {onCheck && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 px-2.5 text-xs font-medium"
                                    onClick={() => onCheck(tracker.name)}
                                    disabled={checking}
                                >
                                    {checking ? (
                                        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                        <Play className="mr-1 h-3.5 w-3.5 text-primary" />
                                    )}
                                    <span>{t("common.check")}</span>
                                </Button>
                            )}
                            {onEdit && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 px-2.5 text-xs font-medium"
                                    onClick={() => onEdit(tracker.name)}
                                >
                                    <Edit className="mr-1 h-3.5 w-3.5" />
                                    <span>{t("common.edit")}</span>
                                </Button>
                            )}
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <SummaryStat
                            label={t("trackers.aggregate.detail.primarySource")}
                            value={primaryChannel?.source_key || "—"}
                            hint={getTrackerChannelTypeLabel(primaryChannel?.source_type, t)}
                        />
                        <SummaryStat
                            label={t("trackers.aggregate.detail.sourceCount")}
                            value={`${tracker.status.enabled_source_count} / ${tracker.status.source_count}`}
                        />
                        <SummaryStat
                            label={t("trackers.aggregate.detail.latestCanonical")}
                            value={latestReleaseVersion || "—"}
                            mono
                        />
                        <SummaryStat
                            label={t("trackers.aggregate.detail.releaseChannels")}
                            value={String(trackerChannelReleaseCount)}
                        />
                    </div>
                </div>
            </Card>

            {/* Section 1: Source Channels Config (collapsible, defaultOpen=true) */}
            <Collapsible open={sourcesOpen} onOpenChange={setSourcesOpen}>
                <Card className="glass-card gap-0 py-0 shadow-xs">
                    <CardHeader className="p-3.5 pb-2">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <CardTitle className="text-sm font-semibold">{t("trackers.aggregate.detail.trackerChannelsTitle")}</CardTitle>
                                <Badge variant="secondary" className="h-4.5 rounded px-1.5 text-[10px] font-normal text-muted-foreground">
                                    {tracker.sources.length}
                                </Badge>
                            </div>
                            <CollapsibleTrigger asChild>
                                <Button variant="ghost" size="icon" className="h-6 w-6">
                                    <ChevronDown className={cn("h-3.5 w-3.5 transition-transform duration-200", sourcesOpen ? "" : "-rotate-90")} />
                                    <span className="sr-only">Toggle channels</span>
                                </Button>
                            </CollapsibleTrigger>
                        </div>
                    </CardHeader>
                    <CollapsibleContent>
                        <CardContent className="grid gap-2.5 p-3.5 pt-0 sm:grid-cols-2">
                    {tracker.sources.map((channel) => {
                        const isPrimary = tracker.primary_changelog_source_key === channel.source_key
                        const channelConfigEntries = Object.entries(channel.source_config ?? {})
                            .filter(([, value]) => Boolean(value))

                        return (
                            <div
                                key={channel.source_key}
                                className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-2.5"
                            >
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0 space-y-0.5">
                                        <div className="flex items-center gap-1.5">
                                            <span className="truncate text-sm font-semibold text-foreground">
                                                {channel.source_key}
                                            </span>
                                            {isPrimary ? (
                                                <Badge
                                                    variant="secondary"
                                                    className="h-5 shrink-0 bg-primary/10 px-1.5 text-[10px] font-medium text-primary"
                                                >
                                                    {t("trackers.aggregate.detail.primaryBadge")}
                                                </Badge>
                                            ) : null}
                                        </div>
                                        <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                                            {getTrackerChannelTypeLabel(channel.source_type, t)}
                                        </div>
                                    </div>
                                    <Badge
                                        variant={channel.enabled ? "secondary" : "outline"}
                                        className="h-5 shrink-0 text-[10px]"
                                    >
                                        {channel.enabled ? t("common.enabled") : t("common.disabled")}
                                    </Badge>
                                </div>

                                {(channelConfigEntries.length > 0 || channel.credential_name) ? (
                                    <dl className="space-y-0.5 pl-1 text-[11px] leading-tight">
                                        {channelConfigEntries.map(([key, value]) => (
                                            <div key={key} className="flex min-w-0 items-start gap-2">
                                                <dt className="shrink-0 font-medium text-muted-foreground">
                                                    {getTrackerChannelConfigLabel(key, t)}
                                                </dt>
                                                <dd className="min-w-0 break-words text-foreground/80">
                                                    {getTrackerChannelConfigValueLabel(key, value, t)}
                                                </dd>
                                            </div>
                                        ))}
                                        {channel.credential_name ? (
                                            <div className="flex min-w-0 items-start gap-2">
                                                <dt className="shrink-0 font-medium text-muted-foreground">
                                                    {t("trackers.aggregate.detail.credential")}
                                                </dt>
                                                <dd className="min-w-0 break-words text-foreground/80">
                                                    {channel.credential_name}
                                                </dd>
                                            </div>
                                        ) : null}
                                    </dl>
                                ) : null}
                            </div>
                        )
                    })}
                        </CardContent>
                    </CollapsibleContent>
                </Card>
            </Collapsible>

            {/* Section 2: Canonical Release Matrix */}
            <Card className="glass-card gap-0 py-0 shadow-xs">
                <CardHeader className="p-3.5 pb-2">
                    <CardTitle className="text-sm font-semibold">{t("trackers.aggregate.detail.releaseViewsTitle")}</CardTitle>
                    <CardDescription className="text-xs">
                        {t("trackers.aggregate.detail.canonicalDiagramDescription")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="p-3.5 pt-0">
                    {versionViewMatrixModel.rows.length === 0 ? (
                        <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 py-8 text-center">
                            <span className="text-xs text-muted-foreground">
                                {t("trackers.aggregate.detail.emptyCanonical")}
                            </span>
                            {onCheck && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-xs"
                                    onClick={() => onCheck(tracker.name)}
                                    disabled={checking}
                                >
                                    <Play className="mr-1 h-3 w-3 text-primary" />
                                    <span>{t("trackers.aggregate.detail.checkNow")}</span>
                                </Button>
                            )}
                        </div>
                    ) : (
                        <div className="overflow-hidden rounded-lg border border-border/60">
                            <Table className="table-fixed" containerClassName="overflow-hidden">
                                <TableBody>
                                    {versionViewMatrixModel.rows.map((row) => {
                                        const preferredContribution = getPreferredTrackerCurrentContributionForRow({
                                            source_contributions: row.sourceContributions,
                                        })
                                        const releaseForNotes = preferredContribution
                                            ? mapContributionToReleaseNotesSubject(
                                                preferredContribution,
                                                tracker.name,
                                                row.selectedChannelKeys,
                                            )
                                            : null
                                        const canViewReleaseNotes = Boolean(releaseForNotes?.body?.trim())
                                        const isExpanded = effectiveExpandedVersionIdentity === row.identityKey
                                        const aliasRows = buildTrackerAliasTableRows(row)
                                        const artifacts = [...row.artifacts].sort((left, right) => {
                                            const typeComparison =
                                                (left.artifact_type === "container_image" ? 0 : 1) -
                                                (right.artifact_type === "container_image" ? 0 : 1)
                                            if (typeComparison !== 0) return typeComparison
                                            const leftTime = left.published_at
                                                ? Date.parse(left.published_at)
                                                : Number.NEGATIVE_INFINITY
                                            const rightTime = right.published_at
                                                ? Date.parse(right.published_at)
                                                : Number.NEGATIVE_INFINITY
                                            return rightTime - leftTime
                                        })
                                        const sourceTypesByKey = new Map(
                                            row.sourceContributions.map((contribution) => [
                                                contribution.source_key,
                                                contribution.source_type,
                                            ]),
                                        )

                                        return (
                                            <Fragment key={row.identityKey}>
                                                <TableRow className="bg-muted/30 hover:bg-muted/40">
                                                    <TableCell colSpan={4} className="whitespace-normal p-0">
                                                        <div className="flex min-w-0 items-center gap-2 px-3 py-1">
                                                            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                                                                <span
                                                                    className="max-w-full truncate font-mono text-sm font-semibold text-foreground"
                                                                >
                                                                    {row.displayVersion}
                                                                </span>
                                                                <span className="text-[10px] text-muted-foreground">
                                                                    {t("trackers.aggregate.detail.versionDetailsSummary", {
                                                                        artifacts: artifacts.length,
                                                                        aliases: aliasRows.length,
                                                                    })}
                                                                </span>
                                                            </div>
                                                            <div className="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-2">
                                                                {row.publishedAt ? (
                                                                    <span
                                                                        className="text-[11px] tabular-nums text-muted-foreground whitespace-nowrap"
                                                                        title={formatDate(row.publishedAt)}
                                                                    >
                                                                        {formatRelative(row.publishedAt)}
                                                                    </span>
                                                                ) : null}
                                                                <div className="flex items-center gap-1">
                                                                    {row.sourceTypeBadges.map((sourceType) => (
                                                                        <Badge
                                                                            key={`${row.identityKey}-${sourceType}`}
                                                                            variant="outline"
                                                                            className="h-5 border-border/60 bg-background/80 text-[10px] uppercase tracking-wide"
                                                                        >
                                                                            {getTrackerChannelTypeLabel(sourceType, t)}
                                                                        </Badge>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                            <Button
                                                                type="button"
                                                                variant="ghost"
                                                                size="icon"
                                                                onClick={() => setExpandedVersionIdentity(
                                                                    isExpanded ? null : row.identityKey,
                                                                )}
                                                                aria-expanded={isExpanded}
                                                                aria-label={isExpanded
                                                                    ? t("trackers.aggregate.detail.collapseVersion")
                                                                    : t("trackers.aggregate.detail.expandVersion")}
                                                                title={isExpanded
                                                                    ? t("trackers.aggregate.detail.collapseVersion")
                                                                    : t("trackers.aggregate.detail.expandVersion")}
                                                                className="h-7 w-7 shrink-0"
                                                            >
                                                                <ChevronDown
                                                                    className={`h-4 w-4 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                                                                    aria-hidden="true"
                                                                />
                                                            </Button>
                                                            <Button
                                                                variant="ghost"
                                                                size="icon"
                                                                disabled={!canViewReleaseNotes}
                                                                onClick={() => {
                                                                    if (!releaseForNotes) return
                                                                    setSelectedRelease(releaseForNotes)
                                                                    setReleaseNotesOpen(true)
                                                                }}
                                                                title={t("dashboard.recentReleases.viewNotes")}
                                                                aria-label={t("dashboard.recentReleases.viewNotes")}
                                                                className="h-7 w-7 shrink-0"
                                                            >
                                                                <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
                                                            </Button>
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                                {isExpanded ? (
                                                    <TableRow className="bg-muted/20 hover:bg-muted/20">
                                                        <TableHead
                                                            scope="col"
                                                            className="h-7 py-0 w-[28%] whitespace-normal text-[10px] md:w-[20%] xl:w-[18%]"
                                                        >
                                                            {t("trackers.aggregate.detail.artifactTable.source")}
                                                        </TableHead>
                                                        <TableHead
                                                            scope="col"
                                                            className="h-7 py-0 w-[72%] whitespace-normal text-[10px] md:w-[50%] xl:w-[42%]"
                                                        >
                                                            {t("trackers.aggregate.detail.artifactTable.version")}
                                                        </TableHead>
                                                        <TableHead
                                                            scope="col"
                                                            className="hidden h-7 py-0 whitespace-normal text-[10px] md:table-cell md:w-[30%] xl:w-[25%]"
                                                        >
                                                            {t("trackers.aggregate.detail.artifactTable.digest")}
                                                        </TableHead>
                                                        <TableHead
                                                            scope="col"
                                                            className="hidden h-7 py-0 whitespace-normal text-[10px] xl:table-cell xl:w-[15%]"
                                                        >
                                                            {t("trackers.aggregate.detail.artifactTable.publishedAt")}
                                                        </TableHead>
                                                    </TableRow>
                                                ) : null}

                                                {isExpanded && (artifacts.length === 0 ? (
                                                    <TableRow>
                                                        <TableCell
                                                            colSpan={4}
                                                            className="whitespace-normal py-4 text-center text-xs text-muted-foreground"
                                                        >
                                                            {t("trackers.aggregate.detail.noArtifactRevisions")}
                                                        </TableCell>
                                                    </TableRow>
                                                ) : artifacts.map((artifact, artifactIndex) => {
                                                    const digest = normalizeArtifactDigest(artifact.digest)
                                                    const artifactKey = [
                                                        row.identityKey,
                                                        artifact.artifact_type ?? "container_image",
                                                        digest,
                                                        artifactIndex,
                                                    ].join(":")
                                                    const artifactVersion = artifact.version ?? row.displayVersion
                                                    const aliases = [...new Set([
                                                        ...artifact.aliases,
                                                        ...aliasRows
                                                            .filter((aliasRow) =>
                                                                aliasRow.artifactDigest
                                                                && normalizeArtifactDigest(aliasRow.artifactDigest) === digest,
                                                            )
                                                            .map((aliasRow) => aliasRow.alias),
                                                    ])]
                                                        .filter((alias) => alias !== artifactVersion)
                                                        .sort((left, right) =>
                                                            left.localeCompare(right, undefined, { numeric: true }),
                                                        )
                                                    const aliasesExpanded = expandedArtifactAliases.has(artifactKey)
                                                    const visibleAliases = aliasesExpanded ? aliases : aliases.slice(0, 2)
                                                    const hiddenAliasCount = Math.max(aliases.length - visibleAliases.length, 0)
                                                    const fallbackSourceType = artifact.artifact_type === "helm_chart"
                                                        ? "helm"
                                                        : "container"
                                                    const artifactSourceTypes = [...new Set(
                                                        artifact.source_keys.length > 0
                                                            ? artifact.source_keys.map((sourceKey) =>
                                                                sourceTypesByKey.get(sourceKey) ?? fallbackSourceType,
                                                            )
                                                            : [fallbackSourceType],
                                                    )]

                                                    return (
                                                        <TableRow key={artifactKey}>
                                                            <TableCell className="whitespace-normal py-1.5 px-2.5 align-middle">
                                                                <ul className="space-y-0.5">
                                                                    {artifactSourceTypes.map((sourceType) => (
                                                                        <li key={`${artifactKey}-${sourceType}`} className="min-w-0">
                                                                            <Badge
                                                                                variant="secondary"
                                                                                className="max-w-full font-normal h-4.5 text-[10px] px-1.5 leading-none"
                                                                            >
                                                                            <span className="truncate">
                                                                                    {getTrackerChannelTypeLabel(sourceType, t)}
                                                                                </span>
                                                                            </Badge>
                                                                        </li>
                                                                    ))}
                                                                </ul>
                                                            </TableCell>
                                                            <TableCell className="whitespace-normal py-1.5 px-2.5 align-middle">
                                                                <ul className="min-w-0 space-y-0.5">
                                                                    <li className="min-w-0">
                                                                        <code
                                                                            className="block truncate font-mono text-xs font-medium"
                                                                        >
                                                                            {artifactVersion}
                                                                        </code>
                                                                    </li>
                                                                    {visibleAliases.map((alias) => (
                                                                        <li key={alias} className="min-w-0">
                                                                            <code className="block truncate font-mono text-xs text-muted-foreground">
                                                                                {alias}
                                                                            </code>
                                                                        </li>
                                                                    ))}
                                                                    {aliases.length > 2 ? (
                                                                        <li>
                                                                            <Button
                                                                                type="button"
                                                                                variant="ghost"
                                                                                className="h-5 px-1.5 text-[10px] text-muted-foreground"
                                                                                onClick={() => toggleArtifactAliases(artifactKey)}
                                                                                aria-expanded={aliasesExpanded}
                                                                                aria-label={aliasesExpanded
                                                                                    ? t("trackers.aggregate.detail.showFewerAliases")
                                                                                    : t("trackers.aggregate.detail.showMoreAliases", {
                                                                                        count: hiddenAliasCount,
                                                                                    })}
                                                                            >
                                                                                {aliasesExpanded
                                                                                    ? t("trackers.aggregate.detail.showFewerAliases")
                                                                                    : `+${hiddenAliasCount}`}
                                                                            </Button>
                                                                        </li>
                                                                    ) : null}
                                                                </ul>
                                                                <span
                                                                    className="mt-1 block truncate text-[10px] text-muted-foreground xl:hidden"
                                                                >
                                                                    {artifact.published_at
                                                                        ? formatDate(artifact.published_at)
                                                                        : "—"}
                                                                </span>
                                                                <div className="mt-1 flex min-w-0 items-center gap-1 md:hidden">
                                                                    <code
                                                                        className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted-foreground"
                                                                        title={digest}
                                                                    >
                                                                        {digest}
                                                                    </code>
                                                                    <Button
                                                                        type="button"
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="h-8 w-8 shrink-0"
                                                                        onClick={() => void copyArtifactDigest(digest)}
                                                                        aria-label={t("trackers.aggregate.detail.copyDigest")}
                                                                        title={t("trackers.aggregate.detail.copyDigest")}
                                                                    >
                                                                        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                                                                    </Button>
                                                                </div>
                                                            </TableCell>
                                                            <TableCell className="hidden whitespace-normal py-1.5 px-2.5 align-middle md:table-cell">
                                                                <div className="flex min-w-0 items-center gap-1">
                                                                    <code
                                                                        className="min-w-0 flex-1 truncate font-mono text-xs text-foreground/80"
                                                                        title={digest}
                                                                    >
                                                                        {digest}
                                                                    </code>
                                                                    <Button
                                                                        type="button"
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="h-6 w-6 shrink-0"
                                                                        onClick={() => void copyArtifactDigest(digest)}
                                                                        aria-label={t("trackers.aggregate.detail.copyDigest")}
                                                                        title={t("trackers.aggregate.detail.copyDigest")}
                                                                    >
                                                                        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                                                                    </Button>
                                                                </div>
                                                            </TableCell>
                                                            <TableCell
                                                                className="hidden whitespace-normal py-1.5 px-2.5 align-middle text-xs text-muted-foreground xl:table-cell"
                                                            >
                                                                {artifact.published_at
                                                                    ? formatDate(artifact.published_at)
                                                                    : "—"}
                                                            </TableCell>
                                                        </TableRow>
                                                    )
                                                }))}
                                            </Fragment>
                                        )
                                    })}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            <ReleaseNotesModal
                release={selectedRelease}
                open={releaseNotesOpen}
                onOpenChange={setReleaseNotesOpen}
            />
        </div>
    )
}

interface SummaryStatProps {
    label: string
    value: string
    hint?: string
    mono?: boolean
}

function SummaryStat({ label, value, hint, mono }: SummaryStatProps) {
    return (
        <div className="rounded-lg border border-border/60 bg-muted/10 p-2">
            <div>
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    {label}
                </div>
                <div
                    className={`mt-0.5 truncate text-xs font-semibold text-foreground ${mono ? "font-mono" : ""}`}
                    title={value}
                >
                    {value}
                </div>
                {hint ? (
                    <div className="truncate text-[9px] uppercase text-muted-foreground leading-none mt-0.5" title={hint}>
                        {hint}
                    </div>
                ) : null}
            </div>
        </div>
    )
}
