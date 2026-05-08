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

// Source-type colour accents come from the shared entity-colour helper so
// they match the other list pages.


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
            <Card className="glass-card flex h-full min-h-0 flex-col">
                <CardHeader className="flex-none pb-3">
                    <CardTitle className="text-base">{t("dashboard.recentReleases.title")}</CardTitle>
                    <CardDescription className="text-xs">
                        {t("dashboard.recentReleases.description")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="flex min-h-0 flex-1 flex-col px-0 pb-3">
                    {loading ? (
                        <div className="space-y-1.5 px-4">
                            {[1, 2, 3, 4, 5].map((i) => (
                                <div key={i} className="h-14 w-full animate-pulse rounded-lg bg-muted/40" />
                            ))}
                        </div>
                    ) : releases.length === 0 ? (
                        <div className="flex flex-1 items-center justify-center px-6 text-sm text-muted-foreground">
                            {t("dashboard.recentReleases.noReleases")}
                        </div>
                    ) : (
                        <ul className="flex min-h-0 flex-1 flex-col divide-y divide-border/40 overflow-y-auto">
                            {releases.map((release) => {
                                const sourceType = release.primary_source?.source_type
                                    ?? release.primary_source_type
                                    ?? release.tracker_type
                                    ?? null
                                const releaseChannelLabel = getReleaseChannelDisplayLabel(release, t)
                                const sourceTypeLabel = getTrackerChannelTypeLabel(sourceType)
                                const absolutePublished = formatDate(release.published_at)
                                const relativePublished = formatRelative(release.published_at)
                                const linkHref = resolveLinkTarget(release)

                                return (
                                    <li
                                        key={`${release.tracker_release_history_id}-${release.published_at}`}
                                        className="group relative flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-muted/40"
                                    >
                                        {/* Hover accent strip — uses the active theme primary colour. */}
                                        <span
                                            aria-hidden
                                            className="absolute left-0 top-1/2 h-7 w-[3px] -translate-y-1/2 rounded-r-full bg-primary opacity-0 transition-opacity group-hover:opacity-100"
                                        />

                                        {/* Tracker name — takes the flexible space. */}
                                        <span
                                            className="min-w-0 flex-1 truncate text-sm font-medium text-foreground"
                                            title={release.tracker_name}
                                        >
                                            {release.tracker_name}
                                        </span>

                                        {/* Version tag. */}
                                        <Badge
                                            variant="outline"
                                            className="max-w-[9rem] shrink-0 truncate border-border/60 bg-muted/40 px-1.5 font-mono text-[10px] h-5 text-foreground/80"
                                            title={release.tag_name}
                                        >
                                            {release.tag_name}
                                        </Badge>

                                        {release.prerelease ? (
                                            <Badge
                                                variant="outline"
                                                className="h-5 shrink-0 border-warning/60 bg-transparent px-1.5 text-[10px] font-medium text-warning"
                                            >
                                                {t("channel.prerelease")}
                                            </Badge>
                                        ) : null}

                                        {/* Source type & channel — hidden on narrow panels. */}
                                        <div className="hidden min-w-0 shrink items-center gap-1.5 text-[11px] text-muted-foreground sm:flex">
                                            <span className="shrink-0 uppercase tracking-wide">{sourceTypeLabel}</span>
                                            {releaseChannelLabel ? (
                                                <>
                                                    <span aria-hidden className="h-0.5 w-0.5 shrink-0 rounded-full bg-muted-foreground/40" />
                                                    <span className="max-w-[7rem] truncate">{releaseChannelLabel}</span>
                                                </>
                                            ) : null}
                                        </div>

                                        {/* Relative time. */}
                                        <span
                                            className="shrink-0 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground"
                                            title={absolutePublished}
                                        >
                                            {relativePublished}
                                        </span>

                                        {/* Actions. */}
                                        <div className="flex shrink-0 items-center gap-0.5 text-muted-foreground/60 transition-colors group-hover:text-muted-foreground">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                disabled={!release.body}
                                                onClick={() => handleViewNotes(release)}
                                                title={t("dashboard.recentReleases.viewNotes")}
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
                                                >
                                                    <a href={linkHref} target="_blank" rel="noreferrer">
                                                        <ExternalLink className="h-3.5 w-3.5" />
                                                    </a>
                                                </Button>
                                            ) : null}
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
