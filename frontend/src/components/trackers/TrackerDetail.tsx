import { useCallback, useEffect, useMemo, useState } from "react"
import { FileText } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { AggregateTracker, ReleaseNotesSubject, TrackerCurrentSourceContribution } from "@/api/types"
import { useTracker, useTrackerCurrentView, useTrackerReleaseHistory } from "@/hooks/queries"
import {
    buildTrackerHistoryMatrixPresentationModel,
    getPreferredTrackerCurrentContributionForRow,
} from "@/components/trackers/canonicalReleaseMatrixModel"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModal"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { getTrackerChannelConfigValueLabel } from "./trackerDetailHelpers"

function getTrackerChannelTypeLabel(
    channelType: string | null | undefined,
    t: ReturnType<typeof useTranslation>["t"],
): string {
    if (!channelType) {
        return t('trackers.aggregate.detail.channelType.unknown')
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

function getTrackerChannelReleaseChannels(
    channels?: AggregateTracker["sources"][number]["release_channels"],
): AggregateTracker["sources"][number]["release_channels"] extends (infer T)[] | null | undefined ? T[] : never {
    return channels ?? []
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
    const loading = trackerQuery.isLoading || trackerCurrentViewQuery.isLoading || trackerReleaseHistoryQuery.isLoading
    const hasFetchError = trackerQuery.isError || trackerCurrentViewQuery.isError || trackerReleaseHistoryQuery.isError

    const refetchDetailQueries = useCallback(() => Promise.all([
        refetchTracker(),
        refetchTrackerCurrentView(),
        refetchTrackerReleaseHistory(),
    ]), [refetchTracker, refetchTrackerCurrentView, refetchTrackerReleaseHistory])

    useEffect(() => {
        if (!trackerName) {
            return
        }

        void refetchDetailQueries()
    }, [refetchDetailQueries, refreshKey, trackerName])

    const versionViewMatrixModel = useMemo(
        () => tracker && trackerReleaseHistoryQuery.data ? buildTrackerHistoryMatrixPresentationModel(tracker.sources, trackerReleaseHistoryQuery.data.items) : null,
        [tracker, trackerReleaseHistoryQuery.data],
    )

    if (!trackerName) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>{t('trackers.aggregate.detail.emptyTitle')}</CardTitle>
                    <CardDescription>{t('trackers.aggregate.detail.emptyDescription')}</CardDescription>
                </CardHeader>
            </Card>
        )
    }

    if (loading && !tracker) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>{t('trackers.aggregate.detail.loadingTitle')}</CardTitle>
                    <CardDescription>{t('common.loading')}</CardDescription>
                </CardHeader>
            </Card>
        )
    }

    if ((!tracker || !trackerCurrentView) && hasFetchError) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>{t('trackers.aggregate.detail.loadFailedTitle')}</CardTitle>
                    <CardDescription>{t('trackers.aggregate.detail.loadFailedDescription')}</CardDescription>
                </CardHeader>
            </Card>
        )
    }

    if (!tracker || !trackerCurrentView || !versionViewMatrixModel) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>{t('trackers.aggregate.detail.loadFailedTitle')}</CardTitle>
                    <CardDescription>{t('trackers.aggregate.detail.loadFailedDescription')}</CardDescription>
                </CardHeader>
            </Card>
        )
    }

    const primaryChannel = tracker.sources.find((channel) => channel.source_key === tracker.primary_changelog_source_key)

    const trackerChannelReleaseCount = tracker.sources.reduce(
        (count, channel) => count + getTrackerChannelReleaseChannels(channel.release_channels).length,
        0,
    )

    const canonicalDiagramDescription = t('trackers.aggregate.detail.canonicalDiagramDescription')
    const latestReleaseVersion = trackerCurrentView.latest_release?.version ?? trackerCurrentView.status.last_version ?? tracker.status.last_version

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader>
                    <div className="space-y-1.5">
                        <CardTitle>{tracker.name}</CardTitle>
                        <CardDescription>{tracker.description || t('trackers.aggregate.detail.noDescription')}</CardDescription>
                    </div>
                </CardHeader>
                <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-lg border border-border/60 p-4">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{t('trackers.aggregate.detail.primarySource')}</div>
                        <div className="mt-2 text-sm font-medium">{primaryChannel?.source_key || "-"}</div>
                        <div className="mt-1 text-xs uppercase text-muted-foreground">{getTrackerChannelTypeLabel(primaryChannel?.source_type, t)}</div>
                    </div>
                    <div className="rounded-lg border border-border/60 p-4">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{t('trackers.aggregate.detail.sourceCount')}</div>
                        <div className="mt-2 text-sm font-medium">{tracker.status.enabled_source_count} / {tracker.status.source_count}</div>
                    </div>
                    <div className="rounded-lg border border-border/60 p-4">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{t('trackers.aggregate.detail.latestCanonical')}</div>
                        <div className="mt-2 font-mono text-sm">{latestReleaseVersion || "-"}</div>
                    </div>
                    <div className="rounded-lg border border-border/60 p-4">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{t('trackers.aggregate.detail.releaseChannels')}</div>
                        <div className="mt-2 text-sm font-medium">{trackerChannelReleaseCount}</div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>{t('trackers.aggregate.detail.trackerChannelsTitle')}</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3 lg:grid-cols-2">
                    {tracker.sources.map((channel) => (
                        <div key={channel.source_key} className="rounded-xl border border-border/60 bg-muted/20 p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="font-medium">{channel.source_key}</div>
                                    <div className="mt-1 text-xs uppercase text-muted-foreground">{getTrackerChannelTypeLabel(channel.source_type, t)}</div>
                                </div>
                                <div className="flex gap-2">
                                    {tracker.primary_changelog_source_key === channel.source_key ? <Badge>{t('trackers.aggregate.detail.primaryBadge')}</Badge> : null}
                                    <Badge variant={channel.enabled ? 'secondary' : 'outline'}>{channel.enabled ? t('common.enabled') : t('common.disabled')}</Badge>
                                </div>
                            </div>
                            <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                                {Object.entries(channel.source_config ?? {}).filter(([, value]) => Boolean(value)).map(([key, value]) => (
                                    <div key={key} className="flex min-w-0 items-start gap-2">
                                        <span className="shrink-0 whitespace-nowrap font-medium text-foreground">{getTrackerChannelConfigLabel(key, t)}:</span>
                                        <span className="min-w-0 break-words">{getTrackerChannelConfigValueLabel(key, value, t)}</span>
                                    </div>
                                ))}
                                {channel.credential_name ? (
                                    <div className="flex gap-2">
                                        <span className="font-medium text-foreground">{t('trackers.aggregate.detail.credential')}:</span>
                                        <span>{channel.credential_name}</span>
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    ))}
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>{t('trackers.aggregate.detail.releaseViewsTitle')}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    {canonicalDiagramDescription ? (
                        <p className="text-sm text-muted-foreground">
                            {canonicalDiagramDescription}
                        </p>
                    ) : null}
                    {versionViewMatrixModel.rows.length === 0 ? (
                        <p className="text-sm text-muted-foreground">{t('trackers.aggregate.detail.emptyCanonical')}</p>
                    ) : (
                        <div className="space-y-4 rounded-xl border border-border/60 bg-muted/10 p-4">
                            <div className="space-y-4">
                                {versionViewMatrixModel.rows.map((row) => {
                                    const preferredContribution = getPreferredTrackerCurrentContributionForRow({
                                        source_contributions: row.sourceContributions,
                                    })
                                    const releaseForNotes = preferredContribution
                                        ? mapContributionToReleaseNotesSubject(preferredContribution, tracker.name, row.selectedChannelKeys)
                                        : null
                                    const canViewReleaseNotes = Boolean(releaseForNotes?.body?.trim())

                                    return (
                                        <div key={row.identityKey} className="rounded-2xl border border-border/60 bg-background/60 p-4 shadow-sm">
                                            <div className="flex flex-col items-center gap-3 lg:flex-row lg:items-center lg:gap-4">
                                                <div className="flex w-full justify-center lg:w-auto lg:justify-start">
                                                    <div className="relative inline-flex min-w-[11rem] max-w-full rounded-2xl border border-border/60 bg-muted/20 text-center shadow-sm">
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="h-auto w-11 shrink-0 rounded-l-2xl rounded-r-none border-r border-border/60 bg-background/50 shadow-none hover:bg-background/80"
                                                            disabled={!canViewReleaseNotes}
                                                            onClick={() => {
                                                                if (!releaseForNotes) {
                                                                    return
                                                                }
                                                                setSelectedRelease(releaseForNotes)
                                                                setReleaseNotesOpen(true)
                                                            }}
                                                            title={t('dashboard.recentReleases.viewNotes')}
                                                        >
                                                            <FileText className="h-4 w-4" />
                                                        </Button>
                                                        <div className="flex min-w-0 flex-1 flex-col items-center px-4 py-3 lg:items-start lg:text-left">
                                                            <div className="font-mono text-sm font-semibold text-foreground">{row.displayVersion}</div>
                                                        </div>
                                                        <div className="pointer-events-none absolute -right-1 top-1/2 hidden h-px w-6 -translate-y-1/2 bg-[repeating-linear-gradient(to_right,hsl(var(--border))_0_6px,transparent_6px_10px)] opacity-70 lg:block" />
                                                        {row.helmChartVersion ? (
                                                            <div className="absolute right-3 top-0 -translate-y-1/2 rounded-full border border-border/60 bg-background/95 px-2 py-1 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground shadow-sm">
                                                                {t('trackers.aggregate.detail.helmChartVersion', { version: row.helmChartVersion })}
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                </div>

                                                <div className="flex justify-center lg:hidden">
                                                    <div className="h-6 w-px bg-[repeating-linear-gradient(to_bottom,hsl(var(--border))_0_6px,transparent_6px_10px)] opacity-70" />
                                                </div>

                                                <div className="hidden min-w-8 flex-1 items-center lg:flex">
                                                    <div className="h-px w-full bg-[repeating-linear-gradient(to_right,hsl(var(--border))_0_6px,transparent_6px_10px)] opacity-70" />
                                                </div>

                                                <div className="w-full lg:w-auto lg:max-w-[65%]">
                                                    <div className="rounded-2xl border border-dashed border-border/60 bg-background/80 px-3 py-3 shadow-sm">
                                                        <div className="flex flex-wrap justify-center gap-2 lg:justify-start">
                                                            {row.sourceTypeBadges.map((sourceType, index) => (
                                                                <Badge
                                                                    key={`${row.identityKey}-${sourceType}-${index}`}
                                                                    variant="outline"
                                                                    className="border-border/70 bg-muted/20 text-xs uppercase tracking-[0.12em]"
                                                                >
                                                                    {getTrackerChannelTypeLabel(sourceType, t)}
                                                                </Badge>
                                                            ))}
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
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
