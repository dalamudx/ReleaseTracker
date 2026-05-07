import { useState } from "react"
import { FileText } from "lucide-react"

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
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModal"
import { getReleaseChannelDisplayLabel } from "@/components/dashboard/releaseNotesModalHelpers"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface RecentReleasesProps {
    releases: LatestCurrentReleaseSummary[]
    loading: boolean
}

export function RecentReleases({ releases, loading }: RecentReleasesProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()
    const [selectedRelease, setSelectedRelease] = useState<LatestCurrentReleaseSummary | null>(null)
    const [modalOpen, setModalOpen] = useState(false)

    const handleViewNotes = (release: LatestCurrentReleaseSummary) => {
        setSelectedRelease(release)
        setModalOpen(true)
    }

    const getTrackerChannelTypeLabel = (channelType: string | null | undefined): string => {
        if (!channelType) {
            return t('trackers.aggregate.detail.channelType.unknown')
        }

        const key = `trackers.aggregate.detail.channelType.${channelType}`
        const translated = t(key)

        return translated === key ? channelType : translated
    }

    return (
        <>
            <Card className="glass-card h-full flex flex-col">
                <CardHeader>
                    <CardTitle>{t('dashboard.recentReleases.title')}</CardTitle>
                    <CardDescription>
                        {t('dashboard.recentReleases.description')}
                    </CardDescription>
                </CardHeader>
                <CardContent className="flex-1 overflow-hidden p-0 px-6 pb-6">
                    {loading ? (
                        <div className="space-y-4 pt-2">
                            {[1, 2, 3, 4, 5].map(i => (
                                <div key={i} className="h-12 w-full bg-muted/50 rounded-lg animate-pulse"></div>
                            ))}
                        </div>
                    ) : (
                        <div className="h-full overflow-y-auto overflow-x-hidden pr-2 space-y-2">
                            {releases.map((release) => {
                                const releaseChannelLabel = getReleaseChannelDisplayLabel(release, t)
                                const sourceTypeLabel = getTrackerChannelTypeLabel(release.primary_source?.source_type ?? release.primary_source_type ?? release.tracker_type)
                                const publishedAt = formatDate(release.published_at)

                                return (
                                    <div
                                        key={`${release.tracker_release_history_id}-${release.published_at}`}
                                        className="group rounded-lg p-3 transition-colors hover:bg-muted/50"
                                    >
                                        <div className="flex items-center gap-3">
                                            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground/90">
                                                {release.tracker_name}
                                            </span>
                                            <Badge variant="secondary" className="max-w-[8rem] shrink-0 truncate font-mono text-[10px] px-1.5 h-5 bg-secondary/50 text-secondary-foreground/80 hover:bg-secondary/70">
                                                {release.tag_name}
                                            </Badge>
                                            <span className="max-w-[8.5rem] shrink-0 truncate whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground" title={publishedAt}>
                                                {publishedAt}
                                            </span>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                disabled={!release.body}
                                                onClick={() => handleViewNotes(release)}
                                                title={t('dashboard.recentReleases.viewNotes')}
                                                className="h-7 w-7 shrink-0 transition-colors hover:bg-background/80"
                                            >
                                                <FileText className="h-3.5 w-3.5" />
                                            </Button>
                                        </div>
                                        <div className="mt-2 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                                            <span className="shrink-0">{t('dashboard.recentReleases.columns.source')}</span>
                                            <span className="min-w-0 truncate text-foreground/80">{sourceTypeLabel}</span>
                                            <span className="text-border">/</span>
                                            <span className="shrink-0">{t('dashboard.recentReleases.columns.releaseChannel')}</span>
                                            <span className="min-w-0 truncate text-foreground/80">{releaseChannelLabel || '—'}</span>
                                        </div>
                                    </div>
                                )
                            })}
                            {releases.length === 0 && (
                                <div className="text-center text-muted-foreground py-8">{t('dashboard.recentReleases.noReleases')}</div>
                            )}
                        </div>
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
