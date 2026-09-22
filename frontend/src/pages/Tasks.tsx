import { ReadinessSummary } from "@/components/executors/ReadinessSummary"
import { QueryErrorState } from "@/components/common/QueryErrorState"
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router"
import { useTranslation } from "react-i18next"
import {
    AlertTriangle,
    ArrowDownToLine,
    ChevronDown,
    Clock,
    History,
    Inbox,
    RefreshCw,
    Rocket,
    ShieldAlert,
    Timer,
    ListX,
} from "lucide-react"
import { toast } from "sonner"
import { api } from "@/api/client"
import type { QueueTask, TaskState } from "@/api/task-types"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import {
    AlertDialog,
    AlertDialogTrigger,
    AlertDialogContent,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogCancel,
    AlertDialogAction,
} from "@/components/ui/alert-dialog"
import { cn } from "@/lib/utils"
import { canRecheckReadiness } from "@/lib/readiness"

const states: TaskState[] = [
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "no_change",
    "skipped",
    "failed",
    "needs_attention",
    "cancelled",
    "superseded",
]

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

function TaskStateBadge({ state, phase }: { state: TaskState; phase?: string }) {
    const { t } = useTranslation()
    const label = state === "running" && phase === "health_checking" ? t("readiness.waiting") : t(`tasks.state.${state}`, { defaultValue: state })

    switch (state) {
        case "running":
            return (
                <Badge variant="info" className="gap-1.5 font-medium">
                    <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info-foreground/45" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-info-foreground" />
                    </span>
                    {label}
                </Badge>
            )
        case "succeeded":
        case "no_change":
            return (
                <Badge variant="success" className="font-medium">
                    {label}
                </Badge>
            )
        case "retry_wait":
            return (
                <Badge variant="warning" className="font-medium gap-1">
                    <Timer className="size-3" />
                    {label}
                </Badge>
            )
        case "needs_attention":
        case "failed":
            return (
                <Badge variant="destructive" className="font-medium gap-1">
                    {state === "needs_attention" && <AlertTriangle className="size-3" />}
                    {label}
                </Badge>
            )
        case "queued":
            return (
                <Badge variant="outline" className="border-border/80 bg-muted/30 text-muted-foreground font-medium">
                    {label}
                </Badge>
            )
        default:
            return (
                <Badge variant="secondary" className="font-normal text-muted-foreground">
                    {label}
                </Badge>
            )
    }
}

function approvalErrorMessageKey(error: unknown): string {
    const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response
    const detail = typeof response?.data?.detail === "string" ? response.data.detail : ""
    if (response?.status === 410 || detail.includes("expired")) return "tasks.approvalExpired"
    if (detail === "target_already_owned" || detail.includes("ownership")) return "tasks.approvalConflict"
    if (detail === "deployment_plan_changed" || detail.includes("changed")) return "tasks.approvalChanged"
    if (detail === "approval_stale_or_blocked" || detail === "task_not_awaiting_approval") return "tasks.approvalStale"
    return "tasks.approvalFailed"
}

function TaskItem({ task }: { task: QueueTask }) {
    const { t, i18n } = useTranslation()
    const [open, setOpen] = useState(false)
    const [approvalError, setApprovalError] = useState<string | null>(null)
    const cache = useQueryClient()
    const detail = useQuery({
        queryKey: ["tasks", task.id],
        queryFn: () => api.getTask(task.id),
        enabled: open,
        refetchInterval: open ? 5000 : false,
    })
    const plan = useQuery({
        queryKey: ["tasks", task.id, "deployment-plan"],
        queryFn: () => api.getDeploymentPlan(task.id),
        enabled: open && task.kind === "deploy" && task.approval_pending === true,
    })
    const approve = useMutation({
        mutationFn: () => {
            if (!plan.data) throw new Error("deployment_plan_unavailable")
            return api.approveDeployment(task.id, { plan_id: plan.data.id, fingerprint: plan.data.fingerprint })
        },
        onSuccess: () => {
            setApprovalError(null)
            toast.success(t("tasks.planApproved"))
            void cache.invalidateQueries({ queryKey: ["tasks"] })
        },
        onError: (error) => {
            const messageKey = approvalErrorMessageKey(error)
            setApprovalError(messageKey)
            toast.error(t(messageKey))
            void cache.invalidateQueries({ queryKey: ["tasks"] })
            void cache.invalidateQueries({ queryKey: ["tasks", task.id, "deployment-plan"] })
        },
    })
    const action = useMutation({
        mutationFn: (mode: "cancel" | "retry" | "resolve") =>
            mode === "cancel"
                ? api.cancelTask(task.id)
                : mode === "retry"
                  ? api.retryTask(task.id)
                  : api.resolveTask(task.id),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["tasks"] })
        },
        onError: (_error, mode) =>
            toast.error(t(mode === "resolve" ? "tasks.resolveBlocked" : "common.unexpectedError")),
    })

    const recheck = useMutation({
        mutationFn: () => api.recheckTask(task.id),
        onSuccess: () => {
            toast.success(t("readiness.recheckQueued", { name: task.target_label }))
            void cache.invalidateQueries({ queryKey: ["tasks"] })
            void cache.invalidateQueries({ queryKey: ["executors"] })
        },
        onError: () => toast.error(t("readiness.recheckFailed")),
    })

    const time = (seconds: number) => new Date(seconds * 1000).toLocaleString(i18n.language)

    const getStateClass = (state: TaskState): string | undefined => {
        switch (state) {
            case "running":
                return "border-info/35 bg-info/[0.025]"
            case "needs_attention":
            case "failed":
                return "border-destructive/35 bg-destructive/[0.025]"
            case "retry_wait":
                return "border-warning/35 bg-warning/[0.025]"
            case "succeeded":
            case "no_change":
                return "border-success/30 bg-success/[0.02]"
            default:
                return undefined
        }
    }

    const waiting = task.state === "queued" || task.state === "retry_wait"
    const summaryTime = waiting ? task.due_at : task.created_at

    return (
        <Card className={cn("glass-card @container min-w-0 gap-0 py-0 transition-colors hover:border-border/80", getStateClass(task.state))}>
            <CardContent className="px-3 py-2">
                <ReadinessSummary result={task.result?.health_check} recheck={task.result?.readiness_recheck} />
                <ReadinessSummary result={task.result?.health_recheck} recheck />
                <Collapsible open={open} onOpenChange={setOpen}>
                    <div data-testid="task-summary" className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 @[760px]:grid-cols-[minmax(0,1fr)_9rem_7rem_13rem_2rem]">
                        <div className="col-start-1 row-start-1 flex min-w-0 items-center gap-2">
                            <Badge variant="outline" className="shrink-0 gap-1 px-2 py-0.5 text-xs font-medium">
                                {getKindIcon(task.kind)}
                                <span>{t(`tasks.kind.${task.kind}`)}</span>
                            </Badge>
                            <Button asChild variant="link" className="h-auto min-w-0 shrink justify-start overflow-hidden p-0 text-sm font-semibold text-foreground">
                                <Link className="truncate transition-colors hover:text-primary" title={task.target_label} to={task.kind === "fetch" ? "/trackers" : "/executors"}>
                                    <span className="truncate">{task.target_label}</span>
                                </Link>
                            </Button>
                            <span className="shrink-0 font-mono text-xs text-muted-foreground/80">#{task.id}</span>
                        </div>
                        <div data-testid="task-summary-state" className="col-start-1 row-start-2 flex items-center @[760px]:col-start-2 @[760px]:row-start-1">
                            <TaskStateBadge state={task.state} phase={task.result?.phase} />
                        </div>
                        <div className="contents text-xs text-muted-foreground">
                            <span data-testid="task-summary-attempts" className={cn(
                                "col-start-2 row-start-2 rounded px-1.5 py-0.5 text-center font-medium tabular-nums @[760px]:col-start-3 @[760px]:row-start-1",
                                task.attempts > 1 ? "bg-warning/10 text-warning" : "bg-muted text-muted-foreground",
                            )}>
                                {t("tasks.attempts", { count: task.attempts, total: task.max_retries + 1 })}
                            </span>
                            <div data-testid="task-summary-time" className={cn("col-span-2 col-start-1 row-start-3 flex min-w-0 items-center gap-1 tabular-nums @[760px]:col-span-1 @[760px]:col-start-4 @[760px]:row-start-1", waiting && "font-medium text-warning")} title={waiting ? t("tasks.due", { time: time(summaryTime) }) : time(summaryTime)}>
                                {waiting ? <Timer className="size-3 shrink-0" aria-hidden="true" /> : <Clock className="size-3 shrink-0" aria-hidden="true" />}
                                <time className="truncate" dateTime={new Date(summaryTime * 1000).toISOString()} aria-label={waiting ? t("tasks.due", { time: time(summaryTime) }) : undefined}>{time(summaryTime)}</time>
                            </div>
                        </div>
                        <CollapsibleTrigger asChild>
                            <Button variant="ghost" size="icon" className="col-start-2 row-start-1 size-8 justify-self-end @[760px]:col-start-5" aria-label={t("tasks.details", { id: task.id })}>
                                <ChevronDown className={cn("size-4 transition-transform duration-200", open && "rotate-180")} />
                            </Button>
                        </CollapsibleTrigger>
                    </div>
                    <CollapsibleContent>
                        <div className="mt-2 space-y-3 border-t border-border/60 pt-3">
                            {waiting && <div className="flex items-center gap-1 text-xs text-muted-foreground"><Clock className="size-3" aria-hidden="true" /><time dateTime={new Date(task.created_at * 1000).toISOString()}>{time(task.created_at)}</time></div>}
                            {task.error_code && <p className="break-words text-xs font-medium text-destructive">{t(`tasks.errors.${task.error_code}`, { defaultValue: task.error_code })}</p>}
                            {task.message && <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">{task.message}</p>}
                            {task.state === "needs_attention" && (
                                <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                                    <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                                    <p className="leading-relaxed">{t("tasks.attention")}</p>
                                </div>
                            )}
                            {detail.isLoading && (
                                <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
                                    <Spinner className="size-4" />
                                    <span>{t("common.loading", { defaultValue: "Loading..." })}</span>
                                </div>
                            )}

                            {detail.isError && <p role="alert" className="text-xs text-destructive">{t("common.unexpectedError")}</p>}

                            {task.approval_pending && task.kind === "deploy" && (
                                <div className="space-y-3 rounded-lg border border-warning/35 bg-warning/[0.06] p-3">
                                    <div className="flex items-start gap-2">
                                        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
                                        <div className="min-w-0 space-y-1">
                                            <p className="text-sm font-semibold text-foreground">{t("tasks.approvalRequired")}</p>
                                            <p className="text-xs leading-relaxed text-muted-foreground">{t("tasks.planReviewRequired")}</p>
                                        </div>
                                    </div>
                                    {plan.isLoading && <p className="text-xs text-muted-foreground">{t("common.loading", { defaultValue: "Loading..." })}</p>}
                                    {plan.data && (
                                        <div className="grid gap-1.5 rounded-md border border-border/60 bg-background/50 p-2.5 text-xs">
                                            <p>{t("tasks.planTarget", { target: plan.data.summary.target_label ?? task.target_label })}</p>
                                            <p>{t("tasks.planIdentity", { identity: plan.data.summary.identity_key ?? "—" })}</p>
                                            <p>{t("tasks.planRecovery", { scope: plan.data.summary.recovery_scope ?? "—" })}</p>
                                            <p className="text-muted-foreground">{t("tasks.planNoData")}</p>
                                        </div>
                                    )}
                                    {plan.isError && <p role="alert" className="text-xs text-destructive">{t("common.unexpectedError")}</p>}
                                    {approvalError && <p role="alert" className="text-xs text-destructive">{t(approvalError)}</p>}
                                    <div className="flex flex-wrap justify-end gap-2">
                                        <Button size="sm" variant="outline" disabled={!plan.data || approve.isPending || plan.isFetching} onClick={() => void plan.refetch()}>
                                            {t("tasks.reviewPlan")}
                                        </Button>
                                        <Button size="sm" disabled={!plan.data || approve.isPending} onClick={() => approve.mutate()}>
                                            {approve.isPending && <Spinner className="size-3.5" />}
                                            {t("tasks.approvePlan")}
                                        </Button>
                                    </div>
                                </div>
                            )}

                            {detail.data?.triggers && detail.data.triggers.length > 0 && (
                                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                                    <span className="text-muted-foreground">{t("tasks.triggers")}:</span>
                                    {[...new Set(detail.data.triggers.map((trigger) => t(`tasks.trigger.${trigger.trigger_mode}`, { defaultValue: trigger.trigger_mode })))].map((mode) => (
                                        <Badge key={mode} variant="secondary" className="text-[11px] font-normal">
                                            {mode}
                                        </Badge>
                                    ))}
                                </div>
                            )}

                            {detail.data?.attempt_history && detail.data.attempt_history.length > 0 && (
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                                        <History className="size-3.5 text-primary" />
                                        <span>{t("tasks.timeline")}</span>
                                    </div>
                                    <ol className="space-y-1.5 rounded-lg border border-border/50 bg-muted/20 p-2.5 text-xs">
                                        {detail.data.attempt_history.map((attempt) => (
                                            <li key={attempt.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                                                <span className="font-mono font-medium">#{attempt.attempt}</span>
                                                <span className="text-muted-foreground">·</span>
                                                <span className="font-medium text-foreground">
                                                    {t(`tasks.state.${attempt.state}`, { defaultValue: attempt.state })}
                                                </span>
                                                <time className="text-muted-foreground/80">{time(attempt.started_at)}</time>
                                                {attempt.error_code && (
                                                    <span className="rounded bg-destructive/10 px-1 py-0.5 text-[11px] text-destructive break-all">
                                                        {t(`tasks.errors.${attempt.error_code}`, { defaultValue: attempt.error_code })}
                                                    </span>
                                                )}
                                            </li>
                                        ))}
                                    </ol>
                                </div>
                            )}

                            {detail.data?.result?.source_fetch_run_ids && Object.keys(detail.data.result.source_fetch_run_ids).length > 0 && (
                                <div className="flex flex-wrap items-center gap-2">
                                    {Object.entries(detail.data.result.source_fetch_run_ids).map(([source, run]) => (
                                        <Badge key={source} variant="outline" className="text-xs font-normal text-muted-foreground">
                                            {t("tasks.sourceRun", { source, run })}
                                        </Badge>
                                    ))}
                                </div>
                            )}

                            {detail.data?.result?.run_id && (
                                <p className="text-xs text-muted-foreground font-mono">
                                    {t("tasks.deployRun", { run: detail.data.result.run_id })}
                                </p>
                            )}

                            <div className="flex flex-wrap justify-end gap-2 pt-1">
                                {canRecheckReadiness(task) && (
                                    <div className="flex min-w-0 flex-col items-end gap-1">
                                        <Button variant="outline" size="sm" disabled={recheck.isPending || action.isPending} onClick={() => recheck.mutate()}>
                                            {recheck.isPending ? <Spinner className="size-3.5" /> : <RefreshCw className="size-3.5" aria-hidden="true" />}
                                            {t("readiness.recheck")}
                                        </Button>
                                        <p className="text-xs text-muted-foreground">{t("readiness.recheckHelp")}</p>
                                    </div>
                                )}
                                {task.state === "needs_attention" && task.kind !== "fetch" && (
                                    <AlertDialog>
                                        <AlertDialogTrigger asChild>
                                            <Button variant="outline" size="sm" className="border-destructive/40 text-destructive hover:bg-destructive/10" disabled={action.isPending}>
                                                {t("tasks.resolve")}
                                            </Button>
                                        </AlertDialogTrigger>
                                        <AlertDialogContent>
                                            <AlertDialogHeader>
                                                <AlertDialogTitle>{t("tasks.resolve")}</AlertDialogTitle>
                                                <AlertDialogDescription>{t("tasks.resolveDescription")}</AlertDialogDescription>
                                            </AlertDialogHeader>
                                            <AlertDialogFooter>
                                                <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                                                <AlertDialogAction onClick={() => action.mutate("resolve")}>
                                                    {t("tasks.resolveConfirm")}
                                                </AlertDialogAction>
                                            </AlertDialogFooter>
                                        </AlertDialogContent>
                                    </AlertDialog>
                                )}
                                {["queued", "retry_wait"].includes(task.state) && (
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        disabled={action.isPending}
                                        onClick={() => action.mutate("cancel")}
                                    >
                                        {t("common.cancel")}
                                    </Button>
                                )}
                                {task.kind === "fetch" && task.state === "failed" && (
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="text-primary hover:bg-primary/10 border-primary/40"
                                        disabled={action.isPending}
                                        onClick={() => action.mutate("retry")}
                                    >
                                        <RefreshCw className="size-3.5 mr-1" />
                                        {t("tasks.retry")}
                                    </Button>
                                )}
                            </div>
                        </div>
                    </CollapsibleContent>
                </Collapsible>
            </CardContent>
        </Card>
    )
}

export default function TasksPage() {
    const { t } = useTranslation()
    const [state, setState] = useState("all")
    const [before, setBefore] = useState<number>()
    const [clearOpen, setClearOpen] = useState(false)
    const cache = useQueryClient()
    const clearTasks = useMutation({
        mutationFn: () => api.clearFinishedTasks(),
        onSuccess: async ({ cleared }) => {
            setClearOpen(false)
            setBefore(undefined)
            toast.success(t("tasks.cleared", { count: cleared }))
            await cache.invalidateQueries({ queryKey: ["tasks"] })
        },
        onError: () => toast.error(t("tasks.clearFailed")),
    })
    const tasks = useQuery({
        queryKey: ["tasks", { state, before }],
        queryFn: () => api.getTasks({ state: state === "all" ? undefined : state, before }),
        refetchInterval: 5000,
    })

    const activeCount = tasks.data?.filter((t) => ["queued", "running", "retry_wait"].includes(t.state)).length ?? 0
    const totalCount = tasks.data?.length ?? 0

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2.5">
                    <h1 className="text-xl font-bold tracking-tight">{t("tasks.title")}</h1>
                    {tasks.data && (
                        <div className="flex items-center gap-1.5">
                            {activeCount > 0 ? (
                                <Badge variant="info" className="text-xs gap-1">
                                    <span className="relative flex h-1.5 w-1.5">
                                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info-foreground/45" />
                                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-info-foreground" />
                                    </span>
                                    {t("tasks.activeTasks", { count: activeCount })}
                                </Badge>
                            ) : null}
                            <span className="text-xs text-muted-foreground">
                                {t("tasks.totalTasks", { count: totalCount })}
                            </span>
                        </div>
                    )}
                </div>

                <div className="flex flex-wrap items-center gap-2 ml-auto">
                    <AlertDialog open={clearOpen} onOpenChange={open => { if (!clearTasks.isPending) setClearOpen(open) }}>
                        <AlertDialogTrigger asChild>
                            <Button variant="outline" size="sm" className="h-8" disabled={clearTasks.isPending}>
                                <ListX className="size-3.5" aria-hidden="true" />
                                {t("tasks.clearFinished")}
                            </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                            <AlertDialogHeader>
                                <AlertDialogTitle>{t("tasks.clearTitle")}</AlertDialogTitle>
                                <AlertDialogDescription>{t("tasks.clearDescription")}</AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                                <AlertDialogCancel disabled={clearTasks.isPending}>{t("common.cancel")}</AlertDialogCancel>
                                <AlertDialogAction disabled={clearTasks.isPending} onClick={event => {
                                    event.preventDefault()
                                    clearTasks.mutate()
                                }}>
                                    {clearTasks.isPending && <Spinner className="size-4" />}
                                    {t("tasks.clearConfirm")}
                                </AlertDialogAction>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                    <Select
                        value={state}
                        onValueChange={(value) => {
                            setState(value)
                            setBefore(undefined)
                        }}
                    >
                        <SelectTrigger className="w-40 h-8 text-xs" aria-label={t("tasks.filter")}>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t("tasks.all")}</SelectItem>
                            {states.map((value) => (
                                <SelectItem key={value} value={value} className="text-xs">
                                    {t(`tasks.state.${value}`)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>

                    <Button
                        variant="outline"
                        size="icon"
                        className="size-8"
                        disabled={tasks.isFetching}
                        onClick={() => void tasks.refetch()}
                        aria-label={t("tasks.refresh")}
                    >
                        <RefreshCw className={cn("size-3.5", tasks.isFetching && "animate-spin text-primary")} />
                    </Button>
                </div>
            </div>

            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
                {tasks.isLoading && (
                    <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-2">
                        <Spinner className="size-6 text-primary" />
                        <span className="text-xs">{t("common.loading", { defaultValue: "Loading tasks..." })}</span>
                    </div>
                )}

                {tasks.isError && (
                    <QueryErrorState onRetry={() => void tasks.refetch()} />
                )}

                {!tasks.isLoading && !tasks.isError && (!tasks.data || tasks.data.length === 0) && (
                    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/80 p-12 text-center">
                        <div className="rounded-full bg-muted/60 p-3 text-muted-foreground mb-3">
                            <Inbox className="size-6" />
                        </div>
                        <p className="text-sm font-medium text-foreground">{t("tasks.empty")}</p>
                    </div>
                )}

                {tasks.data?.map((task) => (
                    <TaskItem key={task.id} task={task} />
                ))}
            </div>

            <div className="flex justify-end gap-2 pt-1">
                {before && (
                    <Button variant="outline" size="sm" onClick={() => setBefore(undefined)}>
                        {t("tasks.latest")}
                    </Button>
                )}
                {tasks.data?.length === 50 && (
                    <Button variant="outline" size="sm" onClick={() => setBefore(tasks.data?.[tasks.data.length - 1]?.id)}>
                        {t("tasks.older")}
                    </Button>
                )}
            </div>
        </div>
    )
}
