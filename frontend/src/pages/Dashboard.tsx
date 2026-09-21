import { useMemo } from "react"
import { useLatestCurrentReleases, useStats, useExecutors } from "@/hooks/queries"
import { DashboardHeaderCards } from "@/components/dashboard/DashboardHeaderCards"
import { ReleaseTrendChart } from "@/components/dashboard/ReleaseTrendChart"
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
        <div className="flex h-full min-h-0 flex-col gap-3.5 animate-in fade-in duration-300">
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
            <section className="grid min-h-0 flex-1 basis-0 gap-3.5 xl:grid-cols-12">
                <div className="min-h-0 xl:col-span-7">
                    <ReleaseTrendChart stats={stats ?? null} loading={!statsReady} />
                </div>
                <div className="min-h-0 xl:col-span-5">
                    <RecentReleases releases={releases} loading={!releasesReady} />
                </div>
            </section>
        </div>
    )
}
