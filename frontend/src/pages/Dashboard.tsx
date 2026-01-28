import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { api } from "@/api/client"
import type { ReleaseStats, Release } from "@/api/types"
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
        <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="space-y-4 h-full overflow-y-auto pr-1"
        >
            <motion.div variants={item} className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight bg-gradient-to-r from-primary to-primary/60 bg-clip-text text-transparent">{t('dashboard.title')}</h2>
                    <p className="text-muted-foreground">{t('dashboard.description')}</p>
                </div>
            </motion.div>

            <motion.div variants={item}>
                <StatsCards stats={stats} loading={loading} />
            </motion.div>

            <div className="grid gap-4 grid-cols-1 lg:grid-cols-7">
                <motion.div variants={item} className="lg:col-span-4">
                    <ReleaseTrendChart stats={stats} loading={loading} />
                </motion.div>
                <motion.div variants={item} className="lg:col-span-3">
                    <RecentReleases releases={releases} loading={loading} />
                </motion.div>
            </div>
        </motion.div>
    )
}
