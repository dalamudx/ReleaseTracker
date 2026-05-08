import { useCallback, useEffect, useMemo, useState } from "react"
import { FileText, Inbox } from "lucide-react"
import { useTranslation } from "react-i18next"

import type {
    AggregateTracker,
    ReleaseNotesSubject,
    TrackerCurrentSourceContribution,
} from "@/api/types"
import {
    useTracker,
    useTrackerCurrentView,
    useTrackerReleaseHistory,
} from "@/hooks/queries"
import {
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
}

export function TrackerDetail({ trackerName, refreshKey }: TrackerDetailProps) {
    const { t } = useTranslation()
    const [selectedRelease, setSelectedRelease] = useState<ReleaseNotesSubject | null>(null)
    const [releaseNotesOpen, setReleaseNotesOpen] = useState(false)
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

    useEffect(() => {
        if (!trackerName) return
        void refetchDetailQueries()
    }, [refetchDetailQueries, refreshKey, trackerName])

    const versionViewMatrixModel = useMemo(
        () =>
            tracker && trackerReleaseHistoryQuery.data
                ? buildTrackerHistoryMatrixPresentationModel(tracker.sources, trackerReleaseHistoryQuery.data.items)
                : null,
        [tracker, trackerReleaseHistoryQuery.data],
    )

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
            <Card className="gap-3 py-4">
                <CardHeader className="pb-0">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 space-y-1">
                            <CardTitle className="flex items-center gap-2 text-base">
                                <span className="truncate">{tracker.name}</span>
                                <Badge variant={tracker.enabled ? "secondary" : "outline"} className="h-5 shrink-0 text-[10px]">
                                    {tracker.enabled ? t("common.enabled") : t("common.disabled")}
                                </Badge>
                            </CardTitle>
                            <CardDescription className="text-xs">
                                {tracker.description || t("trackers.aggregate.detail.noDescription")}
                            </CardDescription>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
                </CardContent>
            </Card>

            {/* Source channels card. */}
            <Card className="gap-3 py-4">
                <CardHeader className="pb-0">
                    <CardTitle className="text-base">{t("trackers.aggregate.detail.trackerChannelsTitle")}</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3 md:grid-cols-2">
                    {tracker.sources.map((channel) => {
                        const isPrimary = tracker.primary_changelog_source_key === channel.source_key
                        const channelConfigEntries = Object.entries(channel.source_config ?? {})
                            .filter(([, value]) => Boolean(value))

                        return (
                            <div
                                key={channel.source_key}
                                className="space-y-3 rounded-lg border border-border/60 bg-muted/20 p-3"
                            >
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0 space-y-0.5">
                                        <div className="flex items-center gap-1.5">
                                            <span className="truncate text-sm font-medium text-foreground">
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
                                    <dl className="space-y-1 pl-2 text-xs">
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
            </Card>

            {/* Release views / canonical version matrix. */}
            <Card className="gap-3 py-4">
                <CardHeader className="pb-0">
                    <CardTitle className="text-base">{t("trackers.aggregate.detail.releaseViewsTitle")}</CardTitle>
                    <CardDescription className="text-xs">
                        {t("trackers.aggregate.detail.canonicalDiagramDescription")}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {versionViewMatrixModel.rows.length === 0 ? (
                        <div className="flex items-center justify-center rounded-lg border border-dashed border-border/60 py-8 text-sm text-muted-foreground">
                            {t("trackers.aggregate.detail.emptyCanonical")}
                        </div>
                    ) : (
                        <ul className="space-y-1.5">
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

                                return (
                                    <li
                                        key={row.identityKey}
                                        className="group flex items-center gap-3 rounded-lg border border-border/60 bg-muted/10 px-3 py-2 transition-colors hover:bg-muted/30"
                                    >
                                        {/* Canonical version. */}
                                        <div className="flex min-w-0 flex-1 items-center gap-2">
                                            <span
                                                className="truncate font-mono text-sm font-semibold text-foreground"
                                                title={row.displayVersion}
                                            >
                                                {row.displayVersion}
                                            </span>
                                            {row.helmChartVersion ? (
                                                <Badge
                                                    variant="outline"
                                                    className="h-5 shrink-0 gap-1 border-border/60 bg-background/80 px-1.5 text-[10px] font-normal"
                                                >
                                                    <span className="font-medium uppercase tracking-wide text-muted-foreground">
                                                        {t("trackers.aggregate.detail.helmChartVersionLabel")}
                                                    </span>
                                                    <span className="font-mono text-foreground/80">
                                                        {row.helmChartVersion}
                                                    </span>
                                                </Badge>
                                            ) : null}
                                        </div>

                                        {/* Source type badges showing where this canonical
                                            version came from. */}
                                        <div className="flex shrink flex-wrap items-center justify-end gap-1">
                                            {row.sourceTypeBadges.map((sourceType, index) => (
                                                <Badge
                                                    key={`${row.identityKey}-${sourceType}-${index}`}
                                                    variant="outline"
                                                    className="h-5 border-border/60 bg-background/80 text-[10px] uppercase tracking-wide"
                                                >
                                                    {getTrackerChannelTypeLabel(sourceType, t)}
                                                </Badge>
                                            ))}
                                        </div>

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
                                            className="h-7 w-7 shrink-0"
                                        >
                                            <FileText className="h-3.5 w-3.5" />
                                        </Button>
                                    </li>
                                )
                            })}
                        </ul>
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
        <div className="rounded-lg border border-border/60 bg-muted/10 p-3">
            <div>
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    {label}
                </div>
                <div
                    className={`mt-1.5 truncate text-sm font-semibold text-foreground ${mono ? "font-mono" : ""}`}
                    title={value}
                >
                    {value}
                </div>
                {hint ? (
                    <div className="mt-0.5 truncate text-[10px] uppercase text-muted-foreground" title={hint}>
                        {hint}
                    </div>
                ) : null}
            </div>
        </div>
    )
}
