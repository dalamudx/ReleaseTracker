import { useTranslation } from "react-i18next"
import { Link } from "react-router"
import { Activity, ArrowUpRight, Clock, FolderGit2, Tag } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"

import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import type { ReleaseStats } from "@/api/types"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface DashboardKpiCardsProps {
    stats: ReleaseStats | null
    loading: boolean
}

export function DashboardKpiCards({ stats, loading }: DashboardKpiCardsProps) {
    const { t, i18n } = useTranslation()
    const formatDate = useDateFormatter()

    const formatRelative = (dateString: string | null | undefined): string => {
        if (!dateString) return t("dashboard.kpi.never")
        try {
            return formatDistanceToNow(new Date(dateString), {
                addSuffix: true,
                locale: i18n.language === "zh" ? zhCN : enUS,
            })
        } catch {
            return formatDate(dateString)
        }
    }

    const totalTrackers = stats?.total_trackers ?? 0
    const totalReleases = stats?.total_releases ?? 0
    const recentReleases = stats?.recent_releases ?? 0
    const latestUpdate = stats?.latest_update ?? null
    const relativeTime = formatRelative(latestUpdate)

    return (
        <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            {/* Card 1: Tracked Projects */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.totalTrackers")}
                        </span>
                        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                            <FolderGit2 className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between gap-1">
                        {loading ? (
                            <div className="h-6 w-14 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <span data-testid="kpi-total-trackers" className="text-xl font-bold tracking-tight text-foreground tabular-nums">
                                {totalTrackers.toLocaleString()}
                            </span>
                        )}
                        <Link
                            to="/trackers"
                            className="inline-flex items-center text-[11px] font-medium text-primary hover:underline"
                        >
                            <span>{t("dashboard.kpi.viewAllTrackers")}</span>
                            <ArrowUpRight className="h-3 w-3" />
                        </Link>
                    </div>
                </CardContent>
            </Card>

            {/* Card 2: Total Indexed Releases */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.totalReleases")}
                        </span>
                        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-chart-1/10 text-[var(--chart-1)]">
                            <Tag className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between gap-1">
                        {loading ? (
                            <div className="h-6 w-16 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <span data-testid="kpi-total-releases" className="text-xl font-bold tracking-tight text-foreground tabular-nums">
                                {totalReleases.toLocaleString()}
                            </span>
                        )}
                        <Link
                            to="/history"
                            className="inline-flex items-center text-[11px] font-medium text-muted-foreground hover:text-foreground hover:underline"
                        >
                            <span>{t("dashboard.kpi.viewAllReleases")}</span>
                            <ArrowUpRight className="h-3 w-3" />
                        </Link>
                    </div>
                </CardContent>
            </Card>

            {/* Card 3: 24h Activity */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.recentReleases")}
                        </span>
                        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-success/10 text-success">
                            <Activity className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between gap-1">
                        {loading ? (
                            <div className="h-6 w-10 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <div className="flex items-baseline gap-1.5">
                                <span data-testid="kpi-recent-releases" className="text-xl font-bold tracking-tight text-foreground tabular-nums">
                                    {recentReleases.toLocaleString()}
                                </span>
                                {recentReleases > 0 ? (
                                    <Badge variant="success" className="h-4 px-1 py-0 text-[10px] leading-none">
                                        {t("dashboard.kpi.activeBadge", { count: recentReleases })}
                                    </Badge>
                                ) : (
                                    <span className="text-[10px] text-muted-foreground">
                                        {t("dashboard.kpi.quietBadge")}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Card 4: Latest Update Pulse */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.latestUpdate")}
                        </span>
                        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-warning/10 text-warning">
                            <Clock className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between gap-1">
                        {loading ? (
                            <div className="h-6 w-20 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <div className="flex items-center gap-1.5 truncate">
                                {latestUpdate && (
                                    <span className="relative flex h-1.5 w-1.5 shrink-0">
                                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success/45 opacity-75" />
                                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
                                    </span>
                                )}
                                <span className="truncate text-sm font-semibold text-foreground" title={latestUpdate ? formatDate(latestUpdate) : undefined}>
                                    {relativeTime}
                                </span>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
