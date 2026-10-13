import { Suspense, lazy, useMemo } from "react"
import { useLatestCurrentReleases, useStats, useExecutors } from "@/hooks/queries"
import { DashboardHeaderCards } from "@/components/dashboard/DashboardHeaderCards"
import { Card, CardContent, CardHeader } from "@/components/ui/card"

// recharts is ~380 KB; let the KPI cards and recent releases paint before it arrives.
const ReleaseTrendChart = lazy(() => import("@/components/dashboard/ReleaseTrendChart").then((m) => ({ default: m.ReleaseTrendChart })))

function ReleaseTrendChartFallback() {
    return (
        <Card className="glass-card flex min-w-0 flex-col shadow-sm xl:h-full xl:min-h-0">
            <CardHeader className="flex-none p-3.5 pb-2">
                <div className="h-9 w-48 animate-pulse rounded-md bg-muted/20" />
            </CardHeader>
            <CardContent className="flex min-w-0 flex-col px-4 pb-4 pt-1 xl:min-h-0 xl:flex-1">
                <div className="h-[300px] animate-pulse rounded-lg bg-muted/20 xl:min-h-0 xl:flex-1" />
            </CardContent>
        </Card>
    )
}
import { RecentReleases } from "@/components/dashboard/RecentReleases"

export default function DashboardPage() {
    // Queries are cached with staleTime, so navigating back renders instantly.
    const { data: stats, isLoading: statsLoading } = useStats()
    const { data: releases = [], isLoading: releasesLoading } = useLatestCurrentReleases(6)
    const { data: executorsData, isLoading: executorsLoading } = useExecutors()

    const statsReady = !statsLoading
    const releasesReady = !releasesLoading
    const executorsReady = !executorsLoading

    const executorsList = useMemo(() => executorsData?.items ?? [], [executorsData])

    return (
        <div className="flex min-h-full min-w-0 shrink-0 flex-col gap-3.5 animate-in fade-in duration-300 xl:h-full xl:min-h-0 xl:shrink">
            {/* Top Row: 6-in-1 KPI and Operational Metrics Header */}
            <section className="flex-none">
                <DashboardHeaderCards
                    stats={stats ?? null}
                    executors={executorsList}
                    statsLoading={!statsReady}
                    executorsLoading={!executorsReady}
                />
            </section>

            {/* Main Stage: Flexibly expands to fill remaining viewport height */}
            <section className="grid min-w-0 flex-none gap-3.5 xl:min-h-0 xl:flex-1 xl:basis-0 xl:grid-cols-12">
                <div className="min-w-0 xl:min-h-0 xl:col-span-7">
                    <Suspense fallback={<ReleaseTrendChartFallback />}>
                        <ReleaseTrendChart stats={stats ?? null} loading={!statsReady} />
                    </Suspense>
                </div>
                <div className="min-w-0 xl:min-h-0 xl:col-span-5">
                    <RecentReleases releases={releases} loading={!releasesReady} />
                </div>
            </section>
        </div>
    )
}
