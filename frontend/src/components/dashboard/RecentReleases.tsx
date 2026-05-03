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

    return (
        <>
            <Card className="col-span-1 lg:col-span-3 glass-card h-full flex flex-col">
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

                                return (
                                <div key={`${release.tracker_release_history_id}-${release.published_at}`} className="group -mx-3 flex items-center gap-4 rounded-lg p-3 transition-colors hover:bg-muted/50">
                                    <div className="flex items-center gap-2 min-w-0 flex-1">
                                        <span className="text-sm font-medium truncate text-foreground/90">
                                            {release.tracker_name}
                                        </span>
                                        <Badge variant="secondary" className="font-mono text-[10px] px-1.5 h-5 shrink-0 bg-secondary/50 text-secondary-foreground/80 hover:bg-secondary/70">
                                            {release.tag_name}
                                        </Badge>
                                    </div>

                                    <div className="flex items-center gap-3 shrink-0">
                                        {releaseChannelLabel ? (
                                            <Badge
                                                variant="outline"
                                                className="h-5 bg-background/40 px-2 py-0 text-[10px] font-normal"
                                            >
                                                {releaseChannelLabel}
                                            </Badge>
                                        ) : (
                                            <span className="w-[4.5rem] text-right text-[10px] text-muted-foreground">—</span>
                                        )}
                                        <span className="whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground">
                                            {formatDate(release.published_at)}
                                        </span>
                                        <div className="w-8 flex justify-end">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                disabled={!release.body}
                                                onClick={() => handleViewNotes(release)}
                                                title="View Release Notes"
                                                className="h-7 w-7 transition-all hover:bg-background/80 hover:shadow-sm"
                                            >
                                                <FileText className="h-3.5 w-3.5" />
                                            </Button>
                                        </div>
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
