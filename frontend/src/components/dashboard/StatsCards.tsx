import { Activity, Package } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Bar, BarChart, ResponsiveContainer, Tooltip, Cell, XAxis, YAxis, LabelList } from "recharts"

import {
    Card,
    CardContent,
} from "@/components/ui/card"
import type { ReleaseStats } from "@/api/types"
import { getChannelLabel } from "@/lib/channel"

interface StatsCardsProps {
    stats: ReleaseStats | null
    loading: boolean
}

const CHART_COLORS = [
    "var(--chart-1)",
    "var(--chart-2)",
    "var(--chart-3)",
    "var(--chart-4)",
    "var(--chart-5)",
]

export function StatsCards({ stats, loading }: StatsCardsProps) {
    const { t } = useTranslation()

    // Get release type stats (正式版 vs 预发布版) sorted by count
    const releaseTypeEntries = stats?.release_type_stats
        ? Object.entries(stats.release_type_stats)
            .map(([key, count]) => ({ name: getChannelLabel(key), value: count }))
            .sort((a, b) => b.value - a.value)
        : []

    // Get channel stats sorted by count
    const channelEntries = stats?.channel_stats
        ? Object.entries(stats.channel_stats)
            .map(([key, count]) => ({ name: getChannelLabel(key), value: count }))
            .sort((a, b) => b.value - a.value)
        : []

    const items = [
        {
            label: t('dashboard.stats.totalReleases'),
            value: stats?.total_releases ?? 0,
            icon: Package,
            trend: `${stats?.recent_releases ?? 0} ${t('dashboard.stats.recentInLast7Days')}`,
            data: releaseTypeEntries,
            chartTitle: t('dashboard.stats.releaseTypeStats'),
        },
        {
            label: t('dashboard.stats.activeTrackers'),
            value: stats?.total_trackers ?? 0,
            icon: Activity,
            trend: t('dashboard.stats.currentlyTracking'),
            data: channelEntries,
            chartTitle: t('dashboard.stats.channelStats'),
        },
    ]

    if (loading) {
        return (
            <div className="grid gap-4 md:grid-cols-2">
                {[1, 2].map((i) => (
                    <Card key={i} className="animate-pulse h-32">
                        <CardContent className="p-0 flex h-full">
                            <div className="w-[30%] bg-muted/20 border-r p-4">
                                <div className="h-3 w-16 bg-muted rounded mb-2"></div>
                                <div className="h-6 w-10 bg-muted rounded"></div>
                            </div>
                            <div className="flex-1 bg-muted/5 p-4">
                                <div className="h-full w-full bg-muted/10 rounded"></div>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>
        )
    }

    return (
        <div className="grid gap-4 md:grid-cols-2">
            {items.map((item, index) => (
                <Card key={item.label} className="overflow-hidden h-32 glass-card bg-transparent border-0 shadow-none">
                    <CardContent className="p-0 flex h-full">
                        {/* Left Side - Stats (Approx 30%) */}
                        <div className="w-[30%] min-w-[140px] flex flex-col justify-center items-center p-4 text-center border-r border-border/10">
                            <div>
                                <p className="text-xs font-medium text-muted-foreground">{item.label}</p>
                                <div className="text-2xl font-bold mt-1.5">{item.value}</div>
                            </div>
                            <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground truncate mt-2" title={item.trend}>
                                <item.icon className="h-3.5 w-3.5 shrink-0" />
                                <span className="truncate">{item.trend}</span>
                            </div>
                        </div>

                        {/* Right Side - Chart (Approx 70%) */}
                        <div className="flex-1 min-w-0 flex flex-col">
                            <div className="flex-1 p-2 pb-0 min-h-0">
                                {item.data.length > 0 ? (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={item.data.slice(0, 10)} layout="vertical" margin={{ left: 0, right: 40, top: 0, bottom: 0 }}>
                                            <XAxis type="number" hide />
                                            <YAxis type="category" dataKey="name" hide />
                                            <Tooltip
                                                cursor={{ fill: 'transparent' }}
                                                content={({ active, payload }) => {
                                                    if (active && payload && payload.length) {
                                                        const data = payload[0].payload;
                                                        return (
                                                            <div className="rounded-lg border bg-background p-2 shadow-sm text-xs">
                                                                <div className="font-semibold">{data.name}</div>
                                                                <div className="text-muted-foreground">{data.value}</div>
                                                            </div>
                                                        );
                                                    }
                                                    return null;
                                                }}
                                            />
                                            <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={20}>
                                                {item.data.map((entry, i) => (
                                                    <Cell key={`cell-${i}`} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                                                ))}
                                                <LabelList dataKey="value" position="right" className="fill-muted-foreground text-xs" />
                                            </Bar>
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : (
                                    <div className="h-full flex items-center justify-center text-xs text-muted-foreground">
                                        No data
                                    </div>
                                )}
                            </div>
                            <div className="px-2 pb-1 text-[10px] text-center text-muted-foreground">
                                {item.chartTitle}
                            </div>
                        </div>
                    </CardContent>
                </Card>
            ))}
        </div>
    )
}
