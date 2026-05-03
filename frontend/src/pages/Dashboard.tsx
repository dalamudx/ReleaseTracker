import { useLatestCurrentReleases, useStats } from "@/hooks/queries"
import { StatsCards } from "@/components/dashboard/StatsCards"
import { ReleaseTrendChart } from "@/components/dashboard/ReleaseTrendChart"
import { RecentReleases } from "@/components/dashboard/RecentReleases"
import { motion } from "framer-motion"

const container = {
    hidden: { opacity: 0 },
    show: {
        opacity: 1,
        transition: {
            staggerChildren: 0.1
        }
    }
}

const item = {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0 }
}

export default function DashboardPage() {
    // Use React Query hooks with 60-second cache
    // When switching back to this page, show cached data directly without loading if cache is fresh
    const { data: stats, isLoading: statsLoading } = useStats()
    const { data: releases = [], isLoading: releasesLoading } = useLatestCurrentReleases()

    const loading = statsLoading || releasesLoading

    return (
        <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="space-y-4 h-full overflow-y-auto pr-1"
        >
            <motion.div variants={item}>
                <StatsCards stats={stats ?? null} loading={loading} />
            </motion.div>

            <div className="grid gap-4 grid-cols-1 lg:grid-cols-7">
                <motion.div variants={item} className="lg:col-span-4">
                    <ReleaseTrendChart stats={stats ?? null} loading={loading} />
                </motion.div>
                <motion.div variants={item} className="lg:col-span-3">
                    <RecentReleases releases={releases} loading={loading} />
                </motion.div>
            </div>
        </motion.div>
    )
}
