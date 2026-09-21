import { useMemo } from "react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router"
import { ArrowUpRight, CheckCircle2, Cpu, PlayCircle, AlertCircle, XCircle } from "lucide-react"

import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card"
import type { ExecutorListItem } from "@/api/types"

interface ExecutorsOverviewProps {
    executors: ExecutorListItem[] | undefined
    loading: boolean
}

export function ExecutorsOverview({ executors, loading }: ExecutorsOverviewProps) {
    const { t } = useTranslation()

    const { total, enabledCount, disabledCount, statusCounts } = useMemo(() => {
        const list = executors ?? []
        const total = list.length
        const enabledCount = list.filter((e) => e.enabled).length
        const disabledCount = total - enabledCount

        const statusCounts = {
            success: 0,
            failed: 0,
            running: 0,
            queued: 0,
            skipped: 0,
            idle: 0,
        }

        list.forEach((executor) => {
            const lastResult = executor.status?.last_result
            if (lastResult === "success") {
                statusCounts.success += 1
            } else if (lastResult === "failed") {
                statusCounts.failed += 1
            } else if (lastResult === "skipped") {
                statusCounts.skipped += 1
            } else {
                statusCounts.idle += 1
            }
        })

        return { total, enabledCount, disabledCount, statusCounts }
    }, [executors])

    return (
        <Card className="glass-card flex h-full min-h-0 flex-col">
            <CardHeader className="flex-none p-3.5 pb-2">
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="text-sm font-semibold flex items-center gap-1.5">
                            <Cpu className="h-4 w-4 text-primary" />
                            <span>{t("dashboard.executorsOverview.title")}</span>
                        </CardTitle>
                        <CardDescription className="text-[11px]">
                            {t("dashboard.executorsOverview.description")}
                        </CardDescription>
                    </div>
                    <Link
                        to="/executors"
                        className="inline-flex items-center gap-0.5 text-xs font-medium text-primary hover:underline"
                    >
                        <span>{t("dashboard.executorsOverview.manageExecutors")}</span>
                        <ArrowUpRight className="h-3 w-3" />
                    </Link>
                </div>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col px-3.5 pb-3">
                {loading ? (
                    <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-2">
                            <div className="h-12 animate-pulse rounded-lg bg-muted/50" />
                            <div className="h-12 animate-pulse rounded-lg bg-muted/50" />
                        </div>
                        <div className="h-16 animate-pulse rounded-lg bg-muted/40" />
                    </div>
                ) : total === 0 ? (
                    <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
                        <span>{t("dashboard.executorsOverview.noExecutors")}</span>
                        <Link
                            to="/executors"
                            className="text-xs text-primary hover:underline"
                        >
                            {t("dashboard.executorsOverview.manageExecutors")}
                        </Link>
                    </div>
                ) : (
                    <div className="flex flex-1 flex-col justify-between gap-3">
                        {/* Top quick counts */}
                        <div className="grid grid-cols-3 gap-2">
                            <div className="rounded-lg border border-border/50 bg-background/50 p-2 text-center">
                                <span className="text-[11px] text-muted-foreground block">
                                    {t("dashboard.executorsOverview.totalExecutors")}
                                </span>
                                <span data-testid="executors-total-count" className="text-base font-bold text-foreground tabular-nums">
                                    {total}
                                </span>
                            </div>
                            <div className="rounded-lg border border-success/25 bg-success/[0.04] p-2 text-center">
                                <span className="text-[11px] text-success block">
                                    {t("dashboard.executorsOverview.enabledCount")}
                                </span>
                                <span data-testid="executors-enabled-count" className="text-base font-bold text-success tabular-nums">
                                    {enabledCount}
                                </span>
                            </div>
                            <div className="rounded-lg border border-border/50 bg-background/50 p-2 text-center">
                                <span className="text-[11px] text-muted-foreground block">
                                    {t("dashboard.executorsOverview.disabledCount")}
                                </span>
                                <span data-testid="executors-disabled-count" className="text-base font-bold text-muted-foreground tabular-nums">
                                    {disabledCount}
                                </span>
                            </div>
                        </div>

                        {/* Recent Status Badges */}
                        <div className="space-y-1.5">
                            <span className="text-xs font-medium text-foreground/80 block">
                                {t("dashboard.executorsOverview.lastRunStatus")}
                            </span>
                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="flex items-center justify-between rounded-md bg-muted/40 px-2.5 py-1.5">
                                    <div className="flex items-center gap-1.5 text-success">
                                        <CheckCircle2 className="h-3.5 w-3.5" />
                                        <span>{t("dashboard.executorsOverview.statusSuccess")}</span>
                                    </div>
                                    <span className="font-semibold tabular-nums text-foreground">
                                        {statusCounts.success}
                                    </span>
                                </div>
                                <div className="flex items-center justify-between rounded-md bg-muted/40 px-2.5 py-1.5">
                                    <div className="flex items-center gap-1.5 text-destructive">
                                        <AlertCircle className="h-3.5 w-3.5" />
                                        <span>{t("dashboard.executorsOverview.statusFailed")}</span>
                                    </div>
                                    <span className="font-semibold tabular-nums text-foreground">
                                        {statusCounts.failed}
                                    </span>
                                </div>
                                <div className="flex items-center justify-between rounded-md bg-muted/40 px-2.5 py-1.5">
                                    <div className="flex items-center gap-1.5 text-muted-foreground">
                                        <PlayCircle className="h-3.5 w-3.5" />
                                        <span>{t("dashboard.executorsOverview.statusIdle")}</span>
                                    </div>
                                    <span className="font-semibold tabular-nums text-foreground">
                                        {statusCounts.idle}
                                    </span>
                                </div>
                                <div className="flex items-center justify-between rounded-md bg-muted/40 px-2.5 py-1.5">
                                    <div className="flex items-center gap-1.5 text-warning">
                                        <XCircle className="h-3.5 w-3.5" />
                                        <span>{t("dashboard.executorsOverview.statusSkipped")}</span>
                                    </div>
                                    <span className="font-semibold tabular-nums text-foreground">
                                        {statusCounts.skipped}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    )
}
