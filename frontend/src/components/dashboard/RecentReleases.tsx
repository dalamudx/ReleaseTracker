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
            <Card className="col-span-1 lg:col-span-3">
                <CardHeader>
                    <CardTitle>{t('dashboard.recentReleases.title')}</CardTitle>
                    <CardDescription>
                        {t('dashboard.recentReleases.description')}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="space-y-8">
                        {releases.map((release) => (
                            <div key={`${release.id}-${release.published_at}`} className="flex items-center">
                                <div className="space-y-1 min-w-0 flex-1 mr-2">
                                    <p className="text-sm font-medium leading-none truncate">
                                        {release.tracker_name}
                                        <span className="text-muted-foreground ml-2 font-mono text-xs">
                                            {release.tag_name}
                                        </span>
                                    </p>
                                    <div className="flex items-center pt-1 gap-2">
                                        <Badge
                                            variant="outline"
                                            className={`px-1.5 py-0 text-[10px] h-5 ${release.prerelease
                                                ? "border-amber-500 text-amber-500"
                                                : "border-emerald-500 text-emerald-500"
                                                }`}
                                        >
                                            {getChannelLabel(release.channel_name || (release.prerelease ? "prerelease" : "stable"))}
                                        </Badge>
                                        <span className="text-xs text-muted-foreground">
                                            {formatDate(release.published_at)}
                                        </span>
                                    </div>
                                </div>
                                <div className="ml-auto font-medium">
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        disabled={!release.body}
                                        onClick={() => handleViewNotes(release)}
                                        title="View Release Notes"
                                    >
                                        <FileText className="h-4 w-4" />
                                    </Button>
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

