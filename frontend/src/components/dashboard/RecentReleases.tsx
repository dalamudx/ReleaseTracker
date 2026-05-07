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
                        <div className="h-full overflow-y-auto overflow-x-hidden pr-2">
                            <div className="grid grid-cols-[minmax(4rem,1fr)_4.75rem_5.5rem_minmax(4.25rem,0.65fr)_minmax(4.5rem,0.75fr)_1.75rem] items-center gap-3 px-3 pb-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                                <span className="truncate">{t('dashboard.recentReleases.columns.tracker')}</span>
                                <span className="truncate">{t('dashboard.recentReleases.columns.source')}</span>
                                <span className="truncate">{t('dashboard.recentReleases.columns.releaseChannel')}</span>
                                <span className="truncate">{t('dashboard.recentReleases.columns.version')}</span>
                                <span className="truncate text-right">{t('dashboard.recentReleases.columns.published')}</span>
                                <span></span>
                            </div>
                            <div className="space-y-2">
                                {releases.map((release) => {
                                    const releaseChannelLabel = getReleaseChannelDisplayLabel(release, t)
                                    const sourceTypeLabel = getTrackerChannelTypeLabel(release.primary_source?.source_type ?? release.primary_source_type ?? release.tracker_type)

                                    return (
                                        <div
                                            key={`${release.tracker_release_history_id}-${release.published_at}`}
                                            className="group grid grid-cols-[minmax(4rem,1fr)_4.75rem_5.5rem_minmax(4.25rem,0.65fr)_minmax(4.5rem,0.75fr)_1.75rem] items-center gap-3 rounded-lg p-3 transition-colors hover:bg-muted/50"
                                        >
                                            <span className="min-w-0 truncate text-sm font-medium text-foreground/90">
                                                {release.tracker_name}
                                            </span>
                                            <Badge variant="secondary" className="h-5 min-w-0 justify-center overflow-hidden px-1.5 py-0 text-[10px] font-normal uppercase">
                                                <span className="truncate">{sourceTypeLabel}</span>
                                            </Badge>
                                            {releaseChannelLabel ? (
                                                <Badge
                                                    variant="outline"
                                                    className="h-5 min-w-0 justify-center overflow-hidden bg-background/40 px-1.5 py-0 text-[10px] font-normal"
                                                >
                                                    <span className="truncate">{releaseChannelLabel}</span>
                                                </Badge>
                                            ) : (
                                                <span className="text-center text-[10px] text-muted-foreground">—</span>
                                            )}
                                            <div className="min-w-0">
                                                <Badge variant="secondary" className="max-w-full truncate font-mono text-[10px] px-1.5 h-5 bg-secondary/50 text-secondary-foreground/80 hover:bg-secondary/70">
                                                    {release.tag_name}
                                                </Badge>
                                            </div>
                                            <span className="truncate whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground" title={formatDate(release.published_at)}>
                                                {formatDate(release.published_at)}
                                            </span>
                                            <div className="flex justify-end">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    disabled={!release.body}
                                                    onClick={() => handleViewNotes(release)}
                                                    title={t('dashboard.recentReleases.viewNotes')}
                                                    className="h-7 w-7 transition-all hover:bg-background/80 hover:shadow-sm"
                                                >
                                                    <FileText className="h-3.5 w-3.5" />
                                                </Button>
                                            </div>
                                        </div>
                                    )
                                })}
                                {releases.length === 0 && (
                                    <div className="text-center text-muted-foreground py-8">{t('dashboard.recentReleases.noReleases')}</div>
                                )}
                            </div>
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
