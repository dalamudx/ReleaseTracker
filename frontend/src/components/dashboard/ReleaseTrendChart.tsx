import { useCallback, useMemo } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { useTranslation } from "react-i18next"

import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card"
import {
    ChartContainer,
    ChartLegend,
    ChartLegendContent,
    ChartTooltip,
    ChartTooltipContent,
} from "@/components/ui/chart"
import type { ChartConfig } from "@/components/ui/chart"
import type { ReleaseStats } from "@/api/types"
import { CHANNEL_LABELS } from "@/lib/channel"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface ReleaseTrendChartProps {
    stats: ReleaseStats | null
    loading: boolean
}

export function ReleaseTrendChart({ stats, loading }: ReleaseTrendChartProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()

    const { stableKeys, keyToSafeKey } = useMemo(() => {
        if (!stats?.daily_stats) return { stableKeys: [], keyToSafeKey: {} as Record<string, string> }

        const uniqueOriginalKeys = new Set<string>()
        stats.daily_stats.forEach((item) => {
            if (item.channels) {
                Object.keys(item.channels).forEach((k) => uniqueOriginalKeys.add(k))
            }
        })
        const sorted = Array.from(uniqueOriginalKeys).sort()

        const mapping: Record<string, string> = {}
        sorted.forEach((originalKey, index) => {
            mapping[originalKey] = `channel_${index}`
        })

        return { stableKeys: sorted, keyToSafeKey: mapping }
    }, [stats])

    const resolveLabel = useCallback(
        (originalKey: string): string => {
            const translationKey = CHANNEL_LABELS[originalKey]
            if (translationKey) return t(translationKey)
            if (!originalKey) return t("channel.unclassified")
            return originalKey
        },
        [t],
    )

    const { chartData, chartConfig, channels, totalInRange, peakDay } = useMemo(() => {
        const emptyResult = {
            chartData: [],
            chartConfig: {} as ChartConfig,
            channels: [] as { key: string; name: string }[],
            totalInRange: 0,
            peakDay: null as null | { date: string; total: number },
        }
        if (stableKeys.length === 0 || !stats?.daily_stats) return emptyResult

        const config: ChartConfig = {}
        stableKeys.forEach((originalKey, index) => {
            const safeKey = keyToSafeKey[originalKey]
            const chartIndex = (index % 5) + 1
            config[safeKey] = {
                label: resolveLabel(originalKey),
                color: `var(--chart-${chartIndex})`,
            }
        })

        let rangeTotal = 0
        let peak: { date: string; total: number } | null = null

        const data = stats.daily_stats.map((item) => {
            const row: Record<string, number | string> = { date: item.date }
            stableKeys.forEach((k) => {
                row[keyToSafeKey[k]] = 0
            })

            let dailyTotal = 0
            if (item.channels) {
                Object.entries(item.channels).forEach(([originalKey, count]) => {
                    const safeKey = keyToSafeKey[originalKey]
                    if (safeKey) {
                        const numeric = Number(count) || 0
                        row[safeKey] = ((row[safeKey] as number) || 0) + numeric
                        dailyTotal += numeric
                    }
                })
            }

            rangeTotal += dailyTotal
            if (!peak || dailyTotal > peak.total) {
                peak = { date: item.date, total: dailyTotal }
            }
            return row
        })

        const mappedChannels = stableKeys.map((originalKey) => ({
            key: keyToSafeKey[originalKey],
            name: resolveLabel(originalKey),
        }))

        return {
            chartData: data,
            chartConfig: config,
            channels: mappedChannels,
            totalInRange: rangeTotal,
            peakDay: peak,
        }
    }, [stableKeys, keyToSafeKey, stats, resolveLabel])

    const hasData = chartData.length > 0 && channels.length > 0

    return (
        <Card className="glass-card flex h-full min-h-0 flex-col">
            <CardHeader className="flex-none pb-3">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <CardTitle className="text-base">{t("dashboard.releaseTrend.title")}</CardTitle>
                        <CardDescription className="text-xs">{t("dashboard.releaseTrend.description")}</CardDescription>
                    </div>
                    {!loading && hasData ? (
                        <div className="flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
                            <div className="text-right">
                                <div className="text-[10px] uppercase tracking-wide">
                                    {t("dashboard.releaseTrend.totalInRange")}
                                </div>
                                <div className="text-sm font-semibold text-foreground tabular-nums">
                                    {totalInRange}
                                </div>
                            </div>
                            {peakDay ? (
                                <div className="text-right">
                                    <div className="text-[10px] uppercase tracking-wide">
                                        {t("dashboard.releaseTrend.peakDay")}
                                    </div>
                                    <div className="text-sm font-semibold text-foreground tabular-nums">
                                        {formatDate(peakDay.date, "MM/dd")} · {peakDay.total}
                                    </div>
                                </div>
                            ) : null}
                        </div>
                    ) : null}
                </div>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col px-6 pb-4">
                {loading ? (
                    <div className="min-h-0 flex-1 animate-pulse rounded-lg bg-muted/20" />
                ) : !hasData ? (
                    <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                        {t("common.noData")}
                    </div>
                ) : (
                    <ChartContainer config={chartConfig} className="min-h-0 w-full flex-1">
                        <AreaChart
                            accessibilityLayer
                            data={chartData}
                            margin={{ left: 12, right: 12, top: 12 }}
                        >
                            <CartesianGrid vertical={false} />
                            <XAxis
                                dataKey="date"
                                tickLine={false}
                                axisLine={false}
                                tickMargin={8}
                                tickFormatter={(value) => formatDate(value, "MM/dd")}
                            />
                            <YAxis
                                tickLine={false}
                                axisLine={false}
                                tickMargin={8}
                                width={30}
                                allowDecimals={false}
                            />
                            <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="dot" />} />

                            {channels.map((channel) => (
                                <Area
                                    key={channel.key}
                                    dataKey={channel.key}
                                    name={channel.name}
                                    type="monotone"
                                    fill={`var(--color-${channel.key})`}
                                    fillOpacity={0.18}
                                    stroke={`var(--color-${channel.key})`}
                                    strokeWidth={2.25}
                                />
                            ))}
                            <ChartLegend content={<ChartLegendContent />} />
                        </AreaChart>
                    </ChartContainer>
                )}
            </CardContent>
        </Card>
    )
}
