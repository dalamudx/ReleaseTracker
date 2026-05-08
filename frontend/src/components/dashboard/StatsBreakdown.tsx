import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card"
import type { ReleaseStats } from "@/api/types"
import { getChannelLabel, getReleaseTypeLabel } from "@/lib/channel"

interface StatsBreakdownProps {
    stats: ReleaseStats | null
    loading: boolean
}

interface BreakdownEntry {
    key: string
    label: string
    value: number
    percentage: number
    colorVar: string
}

const BAR_COLOR_VARS = [
    "var(--chart-1)",
    "var(--chart-2)",
    "var(--chart-3)",
    "var(--chart-4)",
    "var(--chart-5)",
]

function toEntries(
    raw: Record<string, number> | null | undefined,
    resolveLabel: (key: string) => string,
): BreakdownEntry[] {
    if (!raw) return []
    const pairs = Object.entries(raw).map(([key, value]) => ({
        key,
        label: resolveLabel(key),
        value: Number(value) || 0,
    }))

    const total = pairs.reduce((acc, item) => acc + item.value, 0)
    if (total === 0) return []

    return pairs
        .sort((a, b) => b.value - a.value)
        .map((pair, index) => ({
            ...pair,
            percentage: (pair.value / total) * 100,
            colorVar: BAR_COLOR_VARS[index % BAR_COLOR_VARS.length],
        }))
}

interface BreakdownCardProps {
    title: string
    description: string
    entries: BreakdownEntry[]
    loading: boolean
    emptyMessage: string
}

function BreakdownCard({ title, description, entries, loading, emptyMessage }: BreakdownCardProps) {
    return (
        <Card className="glass-card flex h-full min-h-0 flex-col">
            <CardHeader className="flex-none pb-3">
                <CardTitle className="text-base">{title}</CardTitle>
                <CardDescription className="text-xs">{description}</CardDescription>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col px-6 pb-4">
                {loading ? (
                    <div className="space-y-3">
                        {[1, 2, 3, 4].map((i) => (
                            <div key={i} className="space-y-1.5">
                                <div className="flex justify-between">
                                    <div className="h-3 w-20 animate-pulse rounded bg-muted/60" />
                                    <div className="h-3 w-10 animate-pulse rounded bg-muted/40" />
                                </div>
                                <div className="h-2 w-full animate-pulse rounded-full bg-muted/40" />
                            </div>
                        ))}
                    </div>
                ) : entries.length === 0 ? (
                    <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                        {emptyMessage}
                    </div>
                ) : (
                    <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-1">
                        {entries.map((entry) => (
                            <div key={entry.key} className="space-y-1.5">
                                <div className="flex items-center justify-between gap-3 text-xs">
                                    <div className="flex min-w-0 items-center gap-2">
                                        <span
                                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                                            style={{ backgroundColor: entry.colorVar }}
                                        />
                                        <span className="truncate font-medium text-foreground/90">{entry.label}</span>
                                    </div>
                                    <div className="flex shrink-0 items-baseline gap-1.5 tabular-nums text-muted-foreground">
                                        <span className="font-medium text-foreground/90">{entry.value}</span>
                                        <span>·</span>
                                        <span>{entry.percentage.toFixed(1)}%</span>
                                    </div>
                                </div>
                                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/40">
                                    <div
                                        className="h-full rounded-full transition-all"
                                        style={{
                                            width: `${Math.max(entry.percentage, 1.5)}%`,
                                            backgroundColor: entry.colorVar,
                                        }}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </CardContent>
        </Card>
    )
}

/**
 * Replaces the embedded mini-charts that used to sit inside the KPI cards.
 * Renders the release-type split and the channel split as proper horizontal
 * bar lists where every label and value actually fits.
 */
export function StatsBreakdown({ stats, loading }: StatsBreakdownProps) {
    const { t } = useTranslation()

    const releaseTypeEntries = useMemo(
        () => toEntries(stats?.release_type_stats ?? null, (key) => getReleaseTypeLabel(key, t)),
        [stats, t],
    )

    const channelEntries = useMemo(
        () => toEntries(stats?.channel_stats ?? null, (key) => getChannelLabel(key, t)),
        [stats, t],
    )

    return (
        <div className="grid h-full min-h-0 gap-4 md:grid-cols-2">
            <BreakdownCard
                title={t("dashboard.stats.releaseTypeStats")}
                description={t("dashboard.stats.releaseTypeStatsDescription")}
                entries={releaseTypeEntries}
                loading={loading}
                emptyMessage={t("common.noData")}
            />
            <BreakdownCard
                title={t("dashboard.stats.channelStats")}
                description={t("dashboard.stats.channelStatsDescription")}
                entries={channelEntries}
                loading={loading}
                emptyMessage={t("common.noData")}
            />
        </div>
    )
}
