import { useMemo } from "react"
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
import { getChannelLabel } from "@/lib/channel"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface ReleaseTrendChartProps {
    stats: ReleaseStats | null
    loading: boolean
}

export function ReleaseTrendChart({ stats, loading }: ReleaseTrendChartProps) {
    const { t, i18n } = useTranslation()
    const formatDate = useDateFormatter()
    const { chartData, chartConfig, channels } = useMemo(() => {
        if (!stats?.daily_stats) return { chartData: [], chartConfig: {}, channels: [] }

        // 1. Identify all unique Display Names from the data
        const uniqueDisplayNames = new Set<string>()
        stats.daily_stats.forEach(item => {
            if (item.channels) {
                Object.keys(item.channels).forEach(originalKey => {
                    const label = getChannelLabel(originalKey)
                    uniqueDisplayNames.add(label)
                })
            }
        })
        const displayList = Array.from(uniqueDisplayNames).sort()

        // 2. Build Chart Config (Display Name -> Config)
        const config: ChartConfig = {}
        const displayToSafeKey: Record<string, string> = {} // "正式版" -> "channel_0"

        displayList.forEach((displayName, index) => {
            const safeKey = `channel_${index}`
            displayToSafeKey[displayName] = safeKey

            const chartIndex = (index % 5) + 1
            const colorVar = `var(--chart-${chartIndex})`

            config[safeKey] = {
                label: displayName,
                color: colorVar
            }
        })

        // 3. Aggregate Data
        const data = stats.daily_stats.map(item => {
            const row: Record<string, any> = { date: item.date }

            // Initialize 0
            displayList.forEach(name => {
                const key = displayToSafeKey[name]
                if (key) row[key] = 0
            })

            if (item.channels) {
                Object.entries(item.channels).forEach(([originalKey, count]) => {
                    const label = getChannelLabel(originalKey)
                    const safeKey = displayToSafeKey[label]
                    if (safeKey) {
                        row[safeKey] = (row[safeKey] || 0) + count
                    }
                })
            }
            return row
        })

        // 4. Return channels list for rendering
        const mappedChannels = displayList.map(name => ({
            name: name,
            key: displayToSafeKey[name]
        }))

        return { chartData: data, chartConfig: config, channels: mappedChannels }
    }, [stats, i18n.language])

    if (loading) {
        return (
            <Card className="col-span-1 lg:col-span-4 h-[400px] animate-pulse bg-muted/20">
                <CardHeader>
                    <CardTitle>Release Trend</CardTitle>
                </CardHeader>
            </Card>
        )
    }

    return (
        <Card className="col-span-1 lg:col-span-4">
            <CardHeader>
                <CardTitle>{t('dashboard.releaseTrend.title')}</CardTitle>
                <CardDescription>
                    {t('dashboard.releaseTrend.description')}
                </CardDescription>
            </CardHeader>
            <CardContent>
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
                                name={channel.name} // Set name for tooltip fallback
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
            </CardContent>
        </Card>
    )
}
