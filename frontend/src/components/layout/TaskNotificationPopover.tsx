import { ReadinessSummary } from "@/components/executors/ReadinessSummary"
import { useState } from "react"
import { Link } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"
import {
    AlertTriangle,
    ArrowDownToLine,
    ArrowRight,
    Bell,
    CheckCircle2,
    RefreshCw,
    Rocket,
    ShieldAlert,
    Timer,
} from "lucide-react"
import { api } from "@/api/client"
import type { QueueTask, TaskState } from "@/api/task-types"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"

function getKindIcon(kind: QueueTask["kind"]) {
    switch (kind) {
        case "fetch":
            return <ArrowDownToLine className="size-3.5 text-primary shrink-0" />
        case "deploy":
            return <Rocket className="size-3.5 text-primary shrink-0" />
        case "recover":
            return <ShieldAlert className="size-3.5 text-warning shrink-0" />
        default:
            return null
    }
}

function MiniStateBadge({ state, phase }: { state: TaskState; phase?: string }) {
    const { t } = useTranslation()
    const label = state === "running" && phase === "health_checking" ? t("readiness.waiting") : t(`tasks.state.${state}`, { defaultValue: state })

    switch (state) {
        case "running":
            return (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-info">
                    <span className="relative flex h-1.5 w-1.5">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info/45 opacity-75" />
                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-info" />
                    </span>
                    {label}
                </span>
            )
        case "succeeded":
        case "no_change":
            return (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-success">
                    <CheckCircle2 className="size-3 text-success" />
                    {label}
                </span>
            )
        case "retry_wait":
            return (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-warning">
                    <Timer className="size-3 text-warning" />
                    {label}
                </span>
            )
        case "needs_attention":
        case "failed":
            return (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-destructive">
                    <AlertTriangle className="size-3 text-destructive" />
                    {label}
                </span>
            )
        default:
            return (
                <span className="text-[11px] text-muted-foreground">
                    {label}
                </span>
            )
    }
}

export function TaskNotificationPopover() {
    const { t, i18n } = useTranslation()
    const [open, setOpen] = useState(false)

    const tasksQuery = useQuery({
        queryKey: ["tasks", "recent-popover"],
        queryFn: () => api.getTasks(),
        refetchInterval: 5000,
    })

    const tasks = Array.isArray(tasksQuery.data) ? tasksQuery.data : []
    const recentTasks = tasks.slice(0, 5)

    const runningCount = tasks.filter((t) => t.state === "running").length
    const attentionCount = tasks.filter((t) => ["needs_attention", "failed"].includes(t.state)).length
    const retryCount = tasks.filter((t) => t.state === "retry_wait").length
    const activeCount = tasks.filter((t) => ["queued", "running", "retry_wait"].includes(t.state)).length

    const formatRelative = (seconds: number): string => {
        try {
            return formatDistanceToNow(new Date(seconds * 1000), {
                addSuffix: true,
                locale: i18n.language === "zh" ? zhCN : enUS,
            })
        } catch {
            return new Date(seconds * 1000).toLocaleTimeString(i18n.language)
        }
    }

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="ghost"
                    size="icon"
                    className="relative h-8 w-8 text-foreground/80 hover:text-foreground"
                    aria-label={t("tasks.notifications")}
                >
                    <Bell className="size-4" />

                    {runningCount > 0 ? (
                        <span className="absolute top-1.5 right-1.5 flex h-2 w-2">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info/45 opacity-75" />
                            <span className="relative inline-flex h-2 w-2 rounded-full bg-info" />
                        </span>
                    ) : attentionCount > 0 ? (
                        <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-destructive shadow-xs" />
                    ) : retryCount > 0 ? (
                        <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-warning shadow-xs" />
                    ) : activeCount > 0 ? (
                        <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-primary/70" />
                    ) : null}

                    <span className="sr-only">{t("tasks.notifications")}</span>
                </Button>
            </PopoverTrigger>

            <PopoverContent
                align="end"
                sideOffset={8}
                className="w-80 sm:w-96 p-0 shadow-lg border-border/80 bg-popover/95 backdrop-blur-sm"
            >
                <div className="flex items-center justify-between border-b border-border/60 px-3.5 py-2.5">
                    <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold tracking-tight">{t("tasks.recentTasks")}</span>
                        {activeCount > 0 && (
                            <Badge variant="secondary" className="h-4.5 rounded-full px-1.5 text-[10px] font-normal">
                                {t("tasks.activeTasks", { count: activeCount })}
                            </Badge>
                        )}
                    </div>

                    <Button
                        variant="ghost"
                        size="icon"
                        className="size-6 text-muted-foreground hover:text-foreground"
                        disabled={tasksQuery.isFetching}
                        onClick={() => void tasksQuery.refetch()}
                        aria-label={t("tasks.refresh")}
                    >
                        <RefreshCw className={cn("size-3", tasksQuery.isFetching && "animate-spin text-primary")} />
                    </Button>
                </div>

                <div className="max-h-[340px] overflow-y-auto divide-y divide-border/40">
                    {tasksQuery.isLoading ? (
                        <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
                            <Spinner className="size-4" />
                            <span>{t("common.loading", { defaultValue: "Loading..." })}</span>
                        </div>
                    ) : recentTasks.length === 0 ? (
                        <div className="py-8 text-center text-xs text-muted-foreground">
                            {t("tasks.noRecentTasks")}
                        </div>
                    ) : (
                        recentTasks.map((task) => (
                            <div
                                key={task.id}
                                className="group flex flex-col gap-1 p-3 transition-colors hover:bg-muted/40"
                            >
                                <div className="flex items-center justify-between gap-2">
                                    <div className="flex items-center gap-1.5 min-w-0">
                                        {getKindIcon(task.kind)}
                                        <Link
                                            to={task.kind === "fetch" ? "/trackers" : "/executors"}
                                            onClick={() => setOpen(false)}
                                            className="truncate text-xs font-semibold text-foreground hover:text-primary transition-colors"
                                        >
                                            {task.target_label}
                                        </Link>
                                        <span className="font-mono text-[10px] text-muted-foreground/70">#{task.id}</span>
                                    </div>

                                    <MiniStateBadge state={task.state} phase={task.result?.phase} />
                                </div>

                                <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                                    <div className="flex items-center gap-1 truncate">
                                        {task.triggers && task.triggers[0] && (
                                            <span>
                                                {t(`tasks.trigger.${task.triggers[0].trigger_mode}`, {
                                                    defaultValue: task.triggers[0].trigger_mode,
                                                })}
                                            </span>
                                        )}
                                        {task.attempts > 1 && (
                                            <span className="text-warning font-medium">
                                                ({t("tasks.retryAttempt", { count: task.attempts })})
                                            </span>
                                        )}
                                    </div>

                                    <span className="shrink-0 text-[10px] text-muted-foreground/70">
                                        {formatRelative(task.created_at)}
                                    </span>
                                </div>

                                <ReadinessSummary result={task.result?.health_check} recheck={task.result?.readiness_recheck} />
                                <ReadinessSummary result={task.result?.health_recheck} recheck />
                                {task.error_code && (
                                    <div className="mt-0.5 rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive truncate">
                                        {t(`tasks.errors.${task.error_code}`, { defaultValue: task.error_code })}
                                    </div>
                                )}
                            </div>
                        ))
                    )}
                </div>

                <div className="border-t border-border/60 p-1.5 bg-muted/20">
                    <Button
                        variant="ghost"
                        size="sm"
                        asChild
                        className="w-full justify-between h-7 text-xs text-muted-foreground hover:text-foreground"
                    >
                        <Link to="/tasks" onClick={() => setOpen(false)}>
                            <span>{t("tasks.viewAll")}</span>
                            <ArrowRight className="size-3 text-muted-foreground" />
                        </Link>
                    </Button>
                </div>
            </PopoverContent>
        </Popover>
    )
}
