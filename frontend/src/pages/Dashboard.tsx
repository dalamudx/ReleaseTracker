import { useLatestCurrentReleases, useStats } from "@/hooks/queries"
import { StatsBreakdown } from "@/components/dashboard/StatsBreakdown"
import { ReleaseTrendChart } from "@/components/dashboard/ReleaseTrendChart"
import { RecentReleases } from "@/components/dashboard/RecentReleases"

export default function DashboardPage() {
    // Both queries are cached for 60 seconds, so switching back to the page
    // renders from cache without a perceptible loading flash.
    const { data: stats, isLoading: statsLoading } = useStats()
    const { data: releases = [], isLoading: releasesLoading } = useLatestCurrentReleases()

    const statsReady = !statsLoading
    const releasesReady = !releasesLoading

    return (
        // Dashboard fills the viewport instead of relying on the outer <main>
        // scrollbar. Inner cards scroll on their own when their content exceeds
        // the allotted space, so the overall page height stays constant across
        // browser zoom levels.
        <div className="flex h-full min-h-0 flex-col gap-4 animate-in fade-in duration-300">
            {/* Row 1 — release-type + channel breakdown. Allowed to shrink
                its list content with internal overflow when the viewport is short. */}
            <section className="min-h-0 flex-[2] basis-0">
                <StatsBreakdown stats={stats ?? null} loading={!statsReady} />
            </section>

            {/* Row 2 — main visualisation area.
                Trend chart and recent-releases list share equal width on xl+
                screens for a balanced look; they stack on smaller viewports. */}
            <section className="grid min-h-0 flex-[3] basis-0 gap-4 xl:grid-cols-2">
                <div className="min-h-0">
                    <ReleaseTrendChart stats={stats ?? null} loading={!statsReady} />
                </div>
                <div className="min-h-0">
                    <RecentReleases releases={releases} loading={!releasesReady} />
                </div>
            </section>
        </div>
    )
}
