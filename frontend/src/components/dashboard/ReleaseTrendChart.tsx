import { useCallback, useMemo } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"

import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card"
import {
    ChartContainer,
    ChartTooltip,
    ChartTooltipContent,
    ChartLegend,
    ChartLegendContent
} from "@/components/ui/chart"
import type { ChartConfig } from "@/components/ui/chart"
import type { ReleaseStats } from "@/api/types"
import { CHANNEL_LABELS } from "@/lib/channel"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface ReleaseTrendChartProps {
    stats: ReleaseStats | null
    loading: boolean
}

export function ReleaseTrendChart({ stats, loading }: ReleaseTrendChartProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()

    const { stableKeys, keyToSafeKey } = useMemo(() => {
        if (!stats?.daily_stats) return { stableKeys: [], keyToSafeKey: {} }

        const uniqueOriginalKeys = new Set<string>()
        stats.daily_stats.forEach(item => {
            if (item.channels) {
                Object.keys(item.channels).forEach(k => uniqueOriginalKeys.add(k))
            }
        })
        const sorted = Array.from(uniqueOriginalKeys).sort()

        const mapping: Record<string, string> = {}
        sorted.forEach((originalKey, index) => {
            mapping[originalKey] = `channel_${index}`
        })

        return { stableKeys: sorted, keyToSafeKey: mapping }
    }, [stats])

    const resolveLabel = useCallback((originalKey: string): string => {
        const translationKey = CHANNEL_LABELS[originalKey]
        if (translationKey) return t(translationKey)
        if (!originalKey) return t('channel.unclassified')
        return originalKey
    }, [t])

    const { chartData, chartConfig, channels } = useMemo(() => {
        if (stableKeys.length === 0) return { chartData: [], chartConfig: {}, channels: [] }

        const config: ChartConfig = {}
        stableKeys.forEach((originalKey, index) => {
            const safeKey = keyToSafeKey[originalKey]
            const chartIndex = (index % 5) + 1
            config[safeKey] = {
                label: resolveLabel(originalKey),
                color: `var(--chart-${chartIndex})`
            }
        })

        const data = (stats?.daily_stats ?? []).map(item => {
            const row: Record<string, number | string> = { date: item.date }

            stableKeys.forEach(k => { row[keyToSafeKey[k]] = 0 })

            if (item.channels) {
                Object.entries(item.channels).forEach(([originalKey, count]) => {
                    const safeKey = keyToSafeKey[originalKey]
                    if (safeKey) {
                        row[safeKey] = ((row[safeKey] as number) || 0) + count
                    }
                })
            }
            return row
        })

        const mappedChannels = stableKeys.map(originalKey => ({
            key: keyToSafeKey[originalKey],
            name: resolveLabel(originalKey)
        }))

        return { chartData: data, chartConfig: config, channels: mappedChannels }
    }, [stableKeys, keyToSafeKey, stats, resolveLabel])

    return (
        <Card className="col-span-1 lg:col-span-4 glass-card h-full">
            <CardHeader>
                <CardTitle>{t('dashboard.releaseTrend.title')}</CardTitle>
                <CardDescription>
                    {t('dashboard.releaseTrend.description')}
                </CardDescription>
            </CardHeader>
            <CardContent>
                {loading ? (
                    <div className="h-[300px] w-full bg-muted/10 animate-pulse rounded-lg" />
                ) : (
                    <ChartContainer config={chartConfig} className="h-[300px] w-full">
                        <AreaChart
                            accessibilityLayer
                            data={chartData}
                            margin={{
                                left: 12,
                                right: 12,
                                top: 12,
                            }}
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
                            />
                            <ChartTooltip
                                cursor={false}
                                content={<ChartTooltipContent indicator="dot" />}
                            />

                            {channels.map((channel) => (
                                <Area
                                    key={channel.key}
                                    dataKey={channel.key}
                                    name={channel.name}
                                    type="monotone"
                                    fill={`var(--color-${channel.key})`}
                                    fillOpacity={0.15}
                                    stroke={`var(--color-${channel.key})`}
                                    strokeWidth={3}
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
