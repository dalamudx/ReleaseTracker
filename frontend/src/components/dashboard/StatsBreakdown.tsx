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

interface EntryListProps {
    title: string
    entries: BreakdownEntry[]
    emptyMessage: string
    loading: boolean
}

function EntryList({ title, entries, emptyMessage, loading }: EntryListProps) {
    return (
        <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold text-foreground/80">{title}</span>
            {loading ? (
                <div className="space-y-2">
                    {[1, 2].map((i) => (
                        <div key={i} className="space-y-1">
                            <div className="flex justify-between">
                                <div className="h-3 w-16 animate-pulse rounded bg-muted/60" />
                                <div className="h-3 w-8 animate-pulse rounded bg-muted/40" />
                            </div>
                            <div className="h-1.5 w-full animate-pulse rounded-full bg-muted/40" />
                        </div>
                    ))}
                </div>
            ) : entries.length === 0 ? (
                <div className="flex items-center justify-center p-3 text-xs text-muted-foreground">
                    {emptyMessage}
                </div>
            ) : (
                <div className="space-y-2">
                    {entries.map((entry) => (
                        <div key={entry.key} className="space-y-1">
                            <div className="flex items-center justify-between gap-2 text-xs">
                                <div className="flex min-w-0 items-center gap-1.5">
                                    <span
                                        className="h-2 w-2 shrink-0 rounded-full"
                                        style={{ backgroundColor: entry.colorVar }}
                                    />
                                    <span className="truncate font-medium text-foreground/90">{entry.label}</span>
                                </div>
                                <div className="flex shrink-0 items-baseline gap-1 text-[11px] tabular-nums text-muted-foreground">
                                    <span className="font-semibold text-foreground/90">{entry.value}</span>
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
        </div>
    )
}

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
        <Card className="glass-card flex h-full flex-col">
            <CardHeader className="flex-none p-3.5 pb-2">
                <CardTitle className="text-sm font-semibold">{t("dashboard.stats.releaseTypeStats")}</CardTitle>
                <CardDescription className="text-[11px]">
                    {t("dashboard.stats.releaseTypeStatsDescription")}
                </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col justify-between gap-3 px-3.5 pb-3">
                <EntryList
                    title={t("dashboard.stats.releaseTypeStats")}
                    entries={releaseTypeEntries}
                    emptyMessage={t("common.noData")}
                    loading={loading}
                />
                <EntryList
                    title={t("dashboard.stats.channelStats")}
                    entries={channelEntries}
                    emptyMessage={t("common.noData")}
                    loading={loading}
                />
            </CardContent>
        </Card>
    )
}
