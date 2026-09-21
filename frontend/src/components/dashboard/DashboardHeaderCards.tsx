import { useMemo } from "react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router"
import { Activity, ArrowUpRight, Clock, Cpu, FolderGit2, Layers, Tag } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"

import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import type { ReleaseStats, ExecutorListItem } from "@/api/types"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { getChannelLabel } from "@/lib/channel"

interface DashboardHeaderCardsProps {
    stats: ReleaseStats | null
    executors: ExecutorListItem[] | undefined
    statsLoading: boolean
    executorsLoading: boolean
}

const BAR_COLOR_VARS = [
    "var(--chart-1)",
    "var(--chart-2)",
    "var(--chart-3)",
    "var(--chart-4)",
    "var(--chart-5)",
]

export function DashboardHeaderCards({
    stats,
    executors,
    statsLoading,
    executorsLoading,
}: DashboardHeaderCardsProps) {
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

    // Executors status calculations
    const { totalExecutors, enabledExecutors } = useMemo(() => {
        const list = executors ?? []
        const total = list.length
        const enabled = list.filter((e) => e.enabled).length
        return {
            totalExecutors: total,
            enabledExecutors: enabled,
        }
    }, [executors])

    // Channels breakdown summary
    const channelItems = useMemo(() => {
        if (!stats?.channel_stats) return []
        const pairs = Object.entries(stats.channel_stats).map(([k, v]) => ({
            key: k,
            label: getChannelLabel(k, t),
            value: Number(v) || 0,
        }))
        const total = pairs.reduce((acc, p) => acc + p.value, 0)
        if (total === 0) return []
        return pairs
            .sort((a, b) => b.value - a.value)
            .map((pair, index) => ({
                ...pair,
                percentage: (pair.value / total) * 100,
                colorVar: BAR_COLOR_VARS[index % BAR_COLOR_VARS.length],
            }))
    }, [stats, t])

    return (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {/* Card 1: Trackers */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.totalTrackers")}
                        </span>
                        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
                            <FolderGit2 className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1.5 flex items-baseline justify-between gap-1">
                        {statsLoading ? (
                            <div className="h-6 w-12 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <span data-testid="kpi-total-trackers" className="text-lg font-bold tracking-tight text-foreground tabular-nums">
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

            {/* Card 2: Total Releases */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.totalReleases")}
                        </span>
                        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-chart-1/10 text-[var(--chart-1)]">
                            <Tag className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1.5 flex items-baseline justify-between gap-1">
                        {statsLoading ? (
                            <div className="h-6 w-14 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <span data-testid="kpi-total-releases" className="text-lg font-bold tracking-tight text-foreground tabular-nums">
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
                        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-success/10 text-success">
                            <Activity className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1.5 flex items-baseline justify-between gap-1">
                        {statsLoading ? (
                            <div className="h-6 w-10 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <div className="flex items-baseline gap-1.5">
                                <span data-testid="kpi-recent-releases" className="text-lg font-bold tracking-tight text-foreground tabular-nums">
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

            {/* Card 4: Latest Update */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.kpi.latestUpdate")}
                        </span>
                        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-warning/10 text-warning">
                            <Clock className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1.5 flex items-baseline justify-between gap-1">
                        {statsLoading ? (
                            <div className="h-6 w-16 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <div className="flex items-center gap-1.5 truncate">
                                {latestUpdate && (
                                    <span className="relative flex h-1.5 w-1.5 shrink-0">
                                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success/45 opacity-75" />
                                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
                                    </span>
                                )}
                                <span className="truncate text-xs font-semibold text-foreground" title={latestUpdate ? formatDate(latestUpdate) : undefined}>
                                    {relativeTime}
                                </span>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Card 5: Channel Breakdown Compact */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.stats.channelStats")}
                        </span>
                        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
                            <Layers className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1.5">
                        {statsLoading ? (
                            <div className="h-6 w-full animate-pulse rounded bg-muted/60" />
                        ) : channelItems.length === 0 ? (
                            <span className="text-xs text-muted-foreground">
                                {t("common.noData")}
                            </span>
                        ) : (
                            <div className="space-y-1">
                                <div className="flex items-center justify-between gap-1 text-[11px] tabular-nums">
                                    <span className="truncate text-foreground/90 font-medium">
                                        {channelItems[0]?.label}
                                    </span>
                                    <span className="font-semibold text-foreground">
                                        {channelItems[0]?.value} ({channelItems[0]?.percentage.toFixed(0)}%)
                                    </span>
                                </div>
                                <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted/40 gap-0.5">
                                    {channelItems.map((item) => (
                                        <div
                                            key={item.key}
                                            className="h-full rounded-full transition-all"
                                            style={{
                                                width: `${Math.max(item.percentage, 4)}%`,
                                                backgroundColor: item.colorVar,
                                            }}
                                            title={`${item.label}: ${item.value} (${item.percentage.toFixed(1)}%)`}
                                        />
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Card 6: Deployment Executors Overview */}
            <Card className="glass-card transition-all duration-200 hover:border-primary/40 hover:shadow-xs">
                <CardContent className="p-3">
                    <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-xs font-medium text-muted-foreground">
                            {t("dashboard.executorsOverview.title")}
                        </span>
                        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-info/10 text-info">
                            <Cpu className="h-3.5 w-3.5" />
                        </div>
                    </div>
                    <div className="mt-1.5 flex items-baseline justify-between gap-1">
                        {executorsLoading ? (
                            <div className="h-6 w-14 animate-pulse rounded bg-muted/60" />
                        ) : (
                            <div className="flex items-baseline gap-1.5">
                                <span data-testid="executors-total-count" className="text-lg font-bold tracking-tight text-foreground tabular-nums">
                                    {totalExecutors}
                                </span>
                                <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                                    (<span className="text-success font-medium" data-testid="executors-enabled-count">{enabledExecutors}</span> {t("dashboard.executorsOverview.enabledCount")})
                                </span>
                            </div>
                        )}
                        <Link
                            to="/executors"
                            className="inline-flex items-center text-[11px] font-medium text-primary hover:underline"
                        >
                            <span>{t("dashboard.executorsOverview.manageExecutors")}</span>
                            <ArrowUpRight className="h-3 w-3" />
                        </Link>
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
