import { useState } from "react"
import { ExternalLink, FileText } from "lucide-react"
import { useTranslation } from "react-i18next"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"

import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { LatestCurrentReleaseSummary } from "@/api/types"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModalLazy"
import { getReleaseChannelDisplayLabel } from "@/components/dashboard/releaseNotesModalHelpers"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface RecentReleasesProps {
    releases: LatestCurrentReleaseSummary[]
    loading: boolean
}

export function RecentReleases({ releases, loading }: RecentReleasesProps) {
    const { t, i18n } = useTranslation()
    const formatDate = useDateFormatter()
    const [selectedRelease, setSelectedRelease] = useState<LatestCurrentReleaseSummary | null>(null)
    const [modalOpen, setModalOpen] = useState(false)

    const handleViewNotes = (release: LatestCurrentReleaseSummary) => {
        setSelectedRelease(release)
        setModalOpen(true)
    }

    const getTrackerChannelTypeLabel = (channelType: string | null | undefined): string => {
        if (!channelType) {
            return t("trackers.aggregate.detail.channelType.unknown")
        }
        const key = `trackers.aggregate.detail.channelType.${channelType}`
        const translated = t(key)
        return translated === key ? channelType : translated
    }

    const resolveLinkTarget = (release: LatestCurrentReleaseSummary): string | undefined => {
        const candidate = release.changelog_url ?? release.url
        return typeof candidate === "string" && candidate.trim() ? candidate : undefined
    }

    const formatRelative = (value: string): string => {
        try {
            return formatDistanceToNow(new Date(value), {
                addSuffix: true,
                locale: i18n.language === "zh" ? zhCN : enUS,
            })
        } catch {
            return formatDate(value)
        }
    }

    return (
        <>
            <Card className="glass-card flex h-full min-h-0 flex-col shadow-sm">
                <CardHeader className="flex-none p-4 pb-2.5">
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="text-sm font-semibold">{t("dashboard.recentReleases.title")}</CardTitle>
                            <CardDescription className="text-xs">
                                {t("dashboard.recentReleases.description")}
                            </CardDescription>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="flex min-h-0 flex-1 flex-col p-0 overflow-hidden">
                    {loading ? (
                        <div className="space-y-1.5 p-3">
                            {[1, 2, 3, 4, 5].map((i) => (
                                <div key={i} className="h-8 w-full animate-pulse rounded bg-muted/40" />
                            ))}
                        </div>
                    ) : releases.length === 0 ? (
                        <div className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground">
                            {t("dashboard.recentReleases.noReleases")}
                        </div>
                    ) : (
                        <ul className="flex flex-col divide-y divide-border/40">
                            {releases.map((release) => {
                                const sourceType = release.primary_source?.source_type
                                    ?? release.primary_source_type
                                    ?? release.tracker_type
                                    ?? null
                                const releaseChannelLabel = getReleaseChannelDisplayLabel(release, t)
                                const sourceTypeLabel = getTrackerChannelTypeLabel(sourceType)
                                const releaseTypeLabel = releaseChannelLabel
                                    ?? (release.prerelease ? t("channel.prerelease") : t("channel.stable"))
                                const versionLabel = release.tag_name || release.version
                                const relativePublished = formatRelative(release.published_at)
                                const linkHref = resolveLinkTarget(release)

                                return (
                                    <li
                                        key={`${release.tracker_release_history_id}-${release.published_at}`}
                                        className="group relative flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-muted/40"
                                    >
                                        {/* Hover accent strip */}
                                        <span
                                            aria-hidden
                                            className="absolute left-0 top-1/2 h-5 w-[2.5px] -translate-y-1/2 rounded-r-full bg-primary opacity-0 transition-opacity group-hover:opacity-100"
                                        />

                                        {/* Left: Tracker name + Badges */}
                                        <div className="flex min-w-0 flex-1 items-center gap-2.5">
                                            <span className="truncate text-xs font-semibold text-foreground max-w-[120px] sm:max-w-[150px]">
                                                {release.tracker_name}
                                            </span>

                                            <div className="flex shrink-0 items-center gap-1.5">
                                                <Badge
                                                    variant="secondary"
                                                    className="h-5 rounded px-1.5 text-[10px] font-medium leading-none"
                                                >
                                                    {sourceTypeLabel}
                                                </Badge>
                                                <Badge
                                                    variant="outline"
                                                    className="h-5 rounded border-border/60 bg-background px-1.5 text-[10px] font-medium leading-none text-foreground/80"
                                                >
                                                    {releaseTypeLabel}
                                                </Badge>
                                            </div>

                                            <span className="truncate rounded border border-border/60 bg-muted/40 px-2 py-0.5 font-mono text-[11px] leading-none text-foreground/90 font-medium max-w-[160px] sm:max-w-[220px]">
                                                {versionLabel}
                                            </span>
                                        </div>

                                        {/* Right: Published time + Actions */}
                                        <div className="flex shrink-0 items-center gap-1.5">
                                            <span className="text-right text-[11px] tabular-nums text-muted-foreground whitespace-nowrap" title={formatDate(release.published_at)}>
                                                {relativePublished}
                                            </span>

                                            <div className="flex items-center text-muted-foreground/60 transition-colors group-hover:text-muted-foreground">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    disabled={!release.body}
                                                    onClick={() => handleViewNotes(release)}
                                                    title={t("dashboard.recentReleases.viewNotes")}
                                                    aria-label={t("dashboard.recentReleases.viewNotes")}
                                                    className="h-6 w-6"
                                                >
                                                    <FileText className="h-3.5 w-3.5" />
                                                </Button>
                                                {linkHref ? (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        asChild
                                                        className="h-6 w-6"
                                                        title={t("dashboard.releaseNotes.viewSource")}
                                                        aria-label={t("dashboard.releaseNotes.viewSource")}
                                                    >
                                                        <a href={linkHref} target="_blank" rel="noreferrer">
                                                            <ExternalLink className="h-3.5 w-3.5" />
                                                        </a>
                                                    </Button>
                                                ) : null}
                                            </div>
                                        </div>
                                    </li>
                                )
                            })}
                        </ul>
                    )}
                </CardContent>
            </Card>

            <ReleaseNotesModal
                open={modalOpen}
                onOpenChange={setModalOpen}
                release={selectedRelease}
            />
        </>
    )
}
