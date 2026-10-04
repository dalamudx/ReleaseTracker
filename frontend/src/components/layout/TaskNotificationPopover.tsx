import { ReadinessSummary } from "@/components/executors/ReadinessSummary"
import { useEffect, useId, useState } from "react"
import { Link } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { useTranslation } from "react-i18next"
import { taskErrorLabel } from "@/lib/task-errors"
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
    ListX,
} from "lucide-react"
import { api } from "@/api/client"
import type { QueueTask, TaskState } from "@/api/task-types"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import { isClearableNotification, isDismissedNotification, isUnreadTask, useTaskNotificationRead } from "@/hooks/use-task-notification-read"

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
                    <span className="size-1.5 rounded-full bg-info" aria-hidden="true" />
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
    const descriptionId = useId()
    const { read, dismissed, markRead, dismissRead } = useTaskNotificationRead()

    const tasksQuery = useQuery({
        queryKey: ["tasks", "recent-popover"],
        queryFn: () => api.getTasks(),
        refetchInterval: 5000,
    })

    const tasks = (Array.isArray(tasksQuery.data) ? tasksQuery.data : [])
        .filter(task => !isDismissedNotification(task, dismissed))
    const recentTasks = tasks.slice(0, 5)

    const runningCount = tasks.filter((t) => t.state === "running").length
    const attentionCount = tasks.filter((task) => task.state === "needs_attention").length
    const unread = tasks.filter((task) => isUnreadTask(task, read))
    const unreadFailures = unread.some((task) => task.state === "failed")
    const approvalCount = tasks.filter((task) => task.state === "awaiting_approval" || task.approval_pending).length
    const retryCount = tasks.filter((t) => t.state === "retry_wait").length
    const activeCount = tasks.filter((t) => ["queued", "running", "retry_wait"].includes(t.state)).length

    const readTasks = recentTasks.filter((task) => isClearableNotification(task) &&
        read[task.id] !== undefined && task.updated_at <= read[task.id])
    const indicator = attentionCount > 0 || unreadFailures ? "attention"
        : approvalCount > 0 || retryCount > 0 ? "warning"
        : unread.length > 0 ? "unread"
        : runningCount > 0 ? "running" : null
    const indicatorDescription = attentionCount > 0 ? t("tasks.attentionNotifications", { count: attentionCount })
        : unreadFailures ? t("tasks.unreadTasks", { count: unread.length })
        : approvalCount > 0 ? t("tasks.approvalRequired")
        : retryCount > 0 ? t("tasks.state.retry_wait")
        : unread.length > 0 ? t("tasks.unreadTasks", { count: unread.length })
        : runningCount > 0 ? t("tasks.state.running") : t("tasks.allRead")

    useEffect(() => {
        // Only the rows actually presented in the popover become read. New
        // results received while closed or outside this five-row list do not.
        if (open && !tasksQuery.isError && Array.isArray(tasksQuery.data)) {
            markRead(tasksQuery.data.filter(task => !isDismissedNotification(task, dismissed)).slice(0, 5))
        }
    }, [open, tasksQuery.data, tasksQuery.isError, dismissed, markRead])

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
                    size="icon-sm"
                    className="relative"
                    aria-label={t("tasks.notifications")}
                    aria-describedby={descriptionId}
                >
                    <Bell className="size-4" />

                    {indicator && (
                        <span
                            aria-hidden="true"
                            data-indicator={indicator}
                            className={cn("pointer-events-none absolute right-1 top-1 size-1.5 rounded-full ring-2 ring-background",
                                indicator === "attention" ? "bg-destructive" : indicator === "warning" ? "bg-warning" : "bg-info")}
                        />
                    )}
                    <span id={descriptionId} className="sr-only">{indicatorDescription}</span>
                </Button>
            </PopoverTrigger>

            <PopoverContent
                align="end"
                sideOffset={8}
                collisionPadding={16}
                className="w-80 max-w-[calc(100vw-2rem)] p-0 sm:w-96"
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
                        size="icon-sm"
                        disabled={tasksQuery.isFetching}
                        onClick={() => void tasksQuery.refetch()}
                        aria-label={t("tasks.refresh")}
                    >
                        <RefreshCw className={cn("size-3", tasksQuery.isFetching && "motion-safe:animate-spin")} />
                    </Button>
                </div>

                <div className="max-h-[340px] overflow-y-auto divide-y divide-border/40">
                    {tasksQuery.isLoading ? (
                        <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
                            <Spinner className="size-4" />
                            <span>{t("common.loading", { defaultValue: "Loading..." })}</span>
                        </div>
                    ) : tasksQuery.isError ? (
                        <div role="alert" className="px-3 py-8 text-center text-xs text-destructive">
                            {t("tasks.loadFailed")}
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
                                        {taskErrorLabel(t, task.error_code)}
                                    </div>
                                )}
                            </div>
                        ))
                    )}
                </div>

                <div className="flex items-center justify-between gap-2 border-t border-border/60 p-1.5">
                    <Button
                        variant="ghost"
                        size="xs"
                        disabled={tasksQuery.isError || readTasks.length === 0}
                        title={t("tasks.clearReadDescription")}
                        onClick={() => {
                            const cleared = dismissRead(readTasks)
                            if (cleared) toast.success(t("tasks.clearedRead", { count: cleared }))
                        }}
                    >
                        <ListX className="size-3.5" aria-hidden="true" />
                        {t("tasks.clearRead")}
                    </Button>
                    <Button
                        variant="ghost"
                        size="xs"
                        asChild
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
