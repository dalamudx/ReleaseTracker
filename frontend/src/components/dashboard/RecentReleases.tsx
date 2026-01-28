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
import type { Release } from "@/api/types"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModal"
import { getChannelLabel } from "@/lib/channel"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface RecentReleasesProps {
    releases: Release[]
    loading: boolean
}

export function RecentReleases({ releases, loading }: RecentReleasesProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()
    const [selectedRelease, setSelectedRelease] = useState<Release | null>(null)
    const [modalOpen, setModalOpen] = useState(false)

    if (loading) {
        return (
            <Card className="col-span-1 lg:col-span-3">
                <CardHeader>
                    <CardTitle>Recent Releases</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    {[1, 2, 3, 4, 5].map(i => (
                        <div key={i} className="h-12 w-full bg-muted rounded animate-pulse"></div>
                    ))}
                </CardContent>
            </Card>
        )
    }

    const handleViewNotes = (release: Release) => {
        setSelectedRelease(release)
        setModalOpen(true)
    }

    return (
        <>
            <Card className="col-span-1 lg:col-span-3 glass-card bg-transparent border-0 shadow-none h-full flex flex-col">
                <CardHeader>
                    <CardTitle>{t('dashboard.recentReleases.title')}</CardTitle>
                    <CardDescription>
                        {t('dashboard.recentReleases.description')}
                    </CardDescription>
                </CardHeader>
                <CardContent className="flex-1 overflow-hidden p-0 px-6 pb-6">
                    <div className="h-full overflow-y-auto overflow-x-hidden pr-2 space-y-2">
                        {releases.map((release) => (
                            <div key={`${release.id}-${release.published_at}`} className="flex items-center gap-4 p-3 -mx-3 rounded-lg hover:bg-muted/50 transition-colors group">
                                <div className="flex items-center gap-2 min-w-0 flex-1">
                                    <span className="text-sm font-medium truncate text-foreground/90">
                                        {release.tracker_name}
                                    </span>
                                    <Badge variant="secondary" className="font-mono text-[10px] px-1.5 h-5 shrink-0 bg-secondary/50 text-secondary-foreground/80 hover:bg-secondary/70">
                                        {release.tag_name}
                                    </Badge>
                                </div>

                                <div className="flex items-center gap-3 shrink-0">
                                    <Badge
                                        variant="outline"
                                        className={`px-2 py-0 text-[10px] h-5 font-normal ${release.prerelease
                                            ? "border-amber-500/30 text-amber-600 dark:text-amber-400 bg-amber-500/5 group-hover:bg-amber-500/10"
                                            : "border-emerald-500/30 text-emerald-600 dark:text-emerald-400 bg-emerald-500/5 group-hover:bg-emerald-500/10"
                                            }`}
                                    >
                                        {getChannelLabel(release.channel_name || (release.prerelease ? "prerelease" : "stable"))}
                                    </Badge>
                                    <span className="text-xs text-muted-foreground w-20 text-right tabular-nums">
                                        {formatDate(release.published_at)}
                                    </span>
                                    <div className="w-8 flex justify-end">
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            disabled={!release.body}
                                            onClick={() => handleViewNotes(release)}
                                            title="View Release Notes"
                                            className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-all focus:opacity-100 hover:bg-background/80 hover:shadow-sm"
                                        >
                                            <FileText className="h-3.5 w-3.5" />
                                        </Button>
                                    </div>
                                </div>
                            </div>
                        ))}
                        {releases.length === 0 && (
                            <div className="text-center text-muted-foreground py-8">{t('dashboard.recentReleases.noReleases')}</div>
                        )}
                    </div>
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

