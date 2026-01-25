import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { api } from "@/api/client"
import type { ReleaseStats, Release } from "@/api/types"
import { StatsCards } from "@/components/dashboard/StatsCards"
import { ReleaseTrendChart } from "@/components/dashboard/ReleaseTrendChart"
import { RecentReleases } from "@/components/dashboard/RecentReleases"

export default function DashboardPage() {
    const { t } = useTranslation()
    const [stats, setStats] = useState<ReleaseStats | null>(null)
    const [releases, setReleases] = useState<Release[]>([])
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        const loadData = async () => {
            try {
                setLoading(true)
                const [statsRes, releasesRes] = await Promise.all([
                    api.getStats(),
                    api.getLatestReleases()
                ])
                setStats(statsRes)
                setReleases(releasesRes)
            } catch (e) {
                console.error("Failed to load dashboard data", e)
            } finally {
                setLoading(false)
            }
        }
        loadData()
    }, [])

    return (
        <div className="space-y-4 h-full overflow-y-auto pr-1">
            <div className="flex items-center justify-between">
                <h2 className="text-2xl font-bold tracking-tight">{t('dashboard.title')}</h2>
            </div>

            <StatsCards stats={stats} loading={loading} />

            <div className="grid gap-4 grid-cols-1 lg:grid-cols-7">
                <ReleaseTrendChart stats={stats} loading={loading} />
                <RecentReleases releases={releases} loading={loading} />
            </div>
        </div>
    )
}
