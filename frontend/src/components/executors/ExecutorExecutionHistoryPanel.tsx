import { useEffect, useMemo, useState } from "react"
import {
    ArrowRight,
    CheckCircle2,
    CircleAlert,
    CircleSlash,
    Clock,
    ListFilter,
    Search,
    Trash2,
    X,
    XCircle,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { ExecutorListItem, ExecutorRunHistory, ExecutorRunServiceDiagnostic } from "@/api/types"
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DataPagination } from "@/components/common/DataPagination"
import { usePageSize } from "@/hooks/use-page-size"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { cn } from "@/lib/utils"
import { buildExecutorTargetDisplay, isHelmReleaseTarget } from "./executorSheetHelpers"

interface ExecutorExecutionHistoryPanelProps {
    executor: ExecutorListItem | null
    refreshKey: number
}

type StatusKey = ExecutorRunHistory["status"]

const STATUS_VARIANT_MAP: Record<StatusKey, "default" | "destructive" | "secondary" | "outline"> = {
    queued: "secondary",
    running: "secondary",
    success: "default",
    failed: "destructive",
    skipped: "outline",
}

const STATUS_ICON_MAP: Record<StatusKey, React.ReactNode> = {
    queued: <Clock className="h-3 w-3" />,
    running: <Clock className="h-3 w-3 animate-pulse" />,
    success: <CheckCircle2 className="h-3 w-3" />,
    failed: <XCircle className="h-3 w-3" />,
    skipped: <CircleSlash className="h-3 w-3" />,
}

// Recovery Hook outcome badge.
type RecoveryOutcomeKey =
    | "succeeded"
    | "failed"
    | "not_supported"
    | "no_snapshot"
    | "invalid_snapshot"
    | "timeout"

const RECOVERY_OUTCOME_VARIANT_MAP: Record<
    RecoveryOutcomeKey,
    "default" | "destructive" | "secondary" | "outline"
> = {
    succeeded: "default",
    failed: "destructive",
    timeout: "destructive",
    invalid_snapshot: "destructive",
    not_supported: "outline",
    no_snapshot: "outline",
}

interface ImageChangeRow {
    key: string
    service: string | null
    fromValue: string
    toValue: string
}

function buildStructuredImageChangeRows(services: ExecutorRunServiceDiagnostic[]): ImageChangeRow[] {
    return services.map((service) => ({
        key: `service-${service.service}`,
        service: service.service,
        fromValue: service.from_version || "-",
        toValue: service.to_version || "-",
    }))
}

function buildImageChangeRows(
    fromVersion: string | null | undefined,
    toVersion: string | null | undefined,
): ImageChangeRow[] {
    return [
        {
            key: "single-image-change",
            service: null,
            fromValue: fromVersion?.trim() || "-",
            toValue: toVersion?.trim() || "-",
        },
    ]
}

function ChangeValue({
    value,
    testId,
    tone = "default",
}: {
    value: string
    testId: string
    tone?: "from" | "to" | "default"
}) {
    return (
        <code
            data-testid={testId}
            className={cn(
                "block min-w-0 rounded-md border px-2 py-1 font-mono text-[11px] leading-snug break-all [overflow-wrap:anywhere]",
                tone === "from"
                    ? "border-border/50 bg-muted/30 text-muted-foreground"
                    : tone === "to"
                        ? "border-primary/20 bg-primary/5 text-foreground"
                        : "border-border/50 bg-muted/30 text-foreground/80",
            )}
        >
            {value}
        </code>
    )
}

function ExecutorHistoryImageChangeList({
    fromVersion,
    toVersion,
    services,
    valueKind,
    t,
}: {
    fromVersion: string | null | undefined
    toVersion: string | null | undefined
    services?: ExecutorRunServiceDiagnostic[] | null
    valueKind: "image" | "version"
    t: ReturnType<typeof useTranslation>["t"]
}) {
    const rows = services ? buildStructuredImageChangeRows(services) : buildImageChangeRows(fromVersion, toVersion)
    const hasServiceColumn = rows.some((row) => row.service)
    const titleKey = valueKind === "version" ? "executors.review.versionChanges" : "executors.review.imageChanges"
    const fromLabelKey = valueKind === "version" ? "executors.history.table.fromVersion" : "executors.history.table.fromImage"
    const toLabelKey = valueKind === "version" ? "executors.history.table.toVersion" : "executors.history.table.toImage"
    const fromTestId = valueKind === "version" ? "executor-history-from-version" : "executor-history-from-image"
    const toTestId = valueKind === "version" ? "executor-history-to-version" : "executor-history-to-image"

    return (
        <div
            className="rounded-lg border border-border/60 bg-muted/10"
            data-testid="executor-history-image-change-list"
        >
            <div className="flex items-center gap-4 border-b border-border/50 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                <span>{t(titleKey)}</span>
                <span className="ml-auto flex items-center gap-2">
                    <span>{t(fromLabelKey)}</span>
                    <ArrowRight aria-hidden className="h-3 w-3 text-muted-foreground/50" />
                    <span>{t(toLabelKey)}</span>
                </span>
            </div>
                <ul className="divide-y divide-border/40">
                    {rows.map((row) => (
                        <li
                            key={row.key}
                            className="grid min-w-0 items-start gap-2 px-3 py-2 sm:grid-cols-[minmax(0,auto)_minmax(0,1fr)_auto_minmax(0,1fr)]"
                        >
                            {hasServiceColumn ? (
                                <span
                                    className="min-w-0 max-w-[12rem] truncate pt-1 text-xs font-medium text-foreground/80"
                                    title={row.service ?? undefined}
                                >
                                    {row.service ?? "-"}
                                </span>
                            ) : (
                                <span className="hidden sm:block" />
                            )}
                            <ChangeValue value={row.fromValue} testId={fromTestId} tone="from" />
                            <ArrowRight
                                aria-hidden
                                className="mt-2 hidden h-3.5 w-3.5 shrink-0 text-muted-foreground/50 sm:block"
                            />
                            <ChangeValue value={row.toValue} testId={toTestId} tone="to" />
                        </li>
                    ))}
                </ul>
        </div>
    )
}

function formatDuration(
    startedAt: string | null | undefined,
    finishedAt: string | null | undefined,
): string | null {
    if (!startedAt || !finishedAt) return null
    const start = new Date(startedAt).getTime()
    const end = new Date(finishedAt).getTime()
    if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null
    const deltaMs = end - start
    if (deltaMs < 1000) return `${deltaMs}ms`
    const seconds = deltaMs / 1000
    if (seconds < 60) return `${seconds.toFixed(1)}s`
    const minutes = Math.floor(seconds / 60)
    const remainder = Math.round(seconds - minutes * 60)
    return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`
}

export function ExecutorExecutionHistoryPanel({ executor, refreshKey }: ExecutorExecutionHistoryPanelProps) {
    const { t, i18n } = useTranslation()
    const formatDate = useDateFormatter()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS
    const [executionHistory, setExecutionHistory] = useState<ExecutorRunHistory[]>([])
    const [loading, setLoading] = useState(false)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.executors.historyPageSize", 10)
    const [total, setTotal] = useState(0)
    const [search, setSearch] = useState("")
    const [status, setStatus] = useState<"all" | "success" | "failed" | "skipped">("all")
    const [clearDialogOpen, setClearDialogOpen] = useState(false)
    const [clearing, setClearing] = useState(false)

    // Reset filters when switching to a different executor.
    useEffect(() => {
        if (!executor?.id) return
        void Promise.resolve().then(() => {
            setPage(1)
            setSearch("")
            setStatus("all")
        })
    }, [executor?.id])

    useEffect(() => {
        if (!executor?.id) {
            void Promise.resolve().then(() => {
                setExecutionHistory([])
                setTotal(0)
            })
            return
        }

        const executorId = executor.id
        const loadExecutionHistory = async () => {
            setLoading(true)
            try {
                const response = await api.getExecutorHistory(executorId, {
                    skip: (page - 1) * pageSize,
                    limit: pageSize,
                    status: status === "all" ? undefined : status,
                    search: search.trim() || undefined,
                })
                setExecutionHistory(response.items)
                setTotal(response.total)
            } catch (error) {
                console.error("Failed to load executor history", error)
            } finally {
                setLoading(false)
            }
        }
        void loadExecutionHistory()
    }, [executor?.id, page, pageSize, refreshKey, search, status])

    const targetLabel = useMemo(
        () => (executor ? buildExecutorTargetDisplay(executor.runtime_type, executor.target_ref, t) : null),
        [executor, t],
    )
    const targetKindLabel = targetLabel
        ? targetLabel.badges.find((badge) => badge !== executor?.runtime_type) ?? targetLabel.badges[0]
        : null
    const historyValueKind = executor && isHelmReleaseTarget(executor.target_ref) ? "version" : "image"

    const handleClearHistory = async () => {
        if (!executor?.id || clearing) return
        setClearing(true)
        try {
            const response = await api.clearExecutorHistory(executor.id)
            setExecutionHistory([])
            setTotal(0)
            setPage(1)
            setClearDialogOpen(false)
            toast.success(t("executors.history.clearSuccess", { count: response.deleted }))
        } catch (error) {
            console.error("Failed to clear executor history", error)
            toast.error(t("executors.history.clearFailed"))
        } finally {
            setClearing(false)
        }
    }

    if (!executor) {
        return (
            <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg border border-dashed border-border/60 bg-muted/10 px-4 py-10 text-sm text-muted-foreground">
                {t("executors.history.emptySelection")}
            </div>
        )
    }

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            {/* Executor identity card — name, runtime, target. */}
            <div className="rounded-lg border border-border/60 bg-muted/10 p-3">
                <div className="min-w-0 space-y-1.5">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-foreground">{executor.name}</span>
                        <Badge variant={executor.enabled ? "secondary" : "outline"} className="h-5 text-[10px]">
                            {executor.enabled ? t("common.enabled") : t("common.disabled")}
                        </Badge>
                    </div>
                    <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span className="shrink-0 font-medium uppercase tracking-wide">{executor.runtime_type}</span>
                        <span aria-hidden>·</span>
                        <span className="truncate">{executor.runtime_connection_name || "-"}</span>
                    </div>
                    {targetLabel ? (
                        <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-muted-foreground [overflow-wrap:anywhere]">
                            <span className="truncate font-medium text-foreground/80">{targetLabel.title}</span>
                            {targetLabel.summary && targetLabel.summary !== "-" ? (
                                <>
                                    <span aria-hidden>·</span>
                                    <span className="truncate">{targetLabel.summary}</span>
                                </>
                            ) : null}
                            {targetKindLabel ? (
                                <>
                                    <span aria-hidden>·</span>
                                    <Badge variant="outline" className="h-5 border-border/60 text-[10px]">
                                        {targetKindLabel}
                                    </Badge>
                                </>
                            ) : null}
                        </div>
                    ) : null}
                </div>
            </div>

            {/* Filter toolbar — search, status filter, clear action. */}
            <div className="flex flex-none flex-wrap items-center gap-2">
                <div className="min-w-[12rem] flex-1">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            value={search}
                            onChange={(event) => {
                                setSearch(event.target.value)
                                setPage(1)
                            }}
                            placeholder={t("executors.history.searchPlaceholder")}
                        />
                        {search ? (
                            <InputGroupAddon align="inline-end">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    onClick={() => setSearch("")}
                                    title={t("common.clear")}
                                >
                                    <X className="h-3.5 w-3.5" />
                                </Button>
                            </InputGroupAddon>
                        ) : null}
                    </InputGroup>
                </div>
                <Select
                    value={status}
                    onValueChange={(value: "all" | "success" | "failed" | "skipped") => {
                        setStatus(value)
                        setPage(1)
                    }}
                >
                    <SelectTrigger className="w-[10rem] gap-2" aria-label={t("executors.history.filters.all")}>
                        <ListFilter className="h-3.5 w-3.5 text-muted-foreground" />
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("executors.history.filters.all")}</SelectItem>
                        <SelectItem value="success">{t("executors.results.success")}</SelectItem>
                        <SelectItem value="failed">{t("executors.results.failed")}</SelectItem>
                        <SelectItem value="skipped">{t("executors.results.skipped")}</SelectItem>
                    </SelectContent>
                </Select>
                <Button
                    variant="outline"
                    size="icon"
                    className="h-9 w-9 text-destructive hover:text-destructive"
                    onClick={() => setClearDialogOpen(true)}
                    disabled={loading || clearing}
                    title={t("executors.history.clearRecords")}
                    aria-label={t("executors.history.clearRecords")}
                >
                    <Trash2 className="h-4 w-4" />
                </Button>
            </div>

            {/* History list — scrolls inside the panel. */}
            <section
                aria-label={t("executors.history.title")}
                className="flex min-h-0 flex-1 flex-col"
            >
                <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                    {loading ? (
                        <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
                            {t("common.loading")}
                        </div>
                    ) : executionHistory.length === 0 ? (
                        <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-border/60 bg-muted/10 px-4 text-sm text-muted-foreground">
                            {t("executors.history.noResults")}
                        </div>
                    ) : (
                        <ol className="space-y-2" data-testid="executor-history-list">
                            {executionHistory.map((entry) => {
                                const startedRelative = formatDistanceToNow(new Date(entry.started_at), {
                                    addSuffix: true,
                                    locale: dateLocale,
                                })
                                const duration = formatDuration(entry.started_at, entry.finished_at)
                                const startedAbsolute = formatDate(entry.started_at)
                                const status = entry.status

                                return (
                                    <li
                                        key={entry.id ?? `${entry.executor_id}-${entry.started_at}`}
                                        className="rounded-lg border border-border/60 bg-card p-3"
                                        data-testid="executor-history-item"
                                    >
                                        <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                                            <div className="flex min-w-0 items-center gap-2">
                                                <Badge
                                                    variant={STATUS_VARIANT_MAP[status]}
                                                    className="h-5 shrink-0 gap-1 text-[10px] capitalize"
                                                >
                                                    {STATUS_ICON_MAP[status]}
                                                    {t(`executors.results.${status}`)}
                                                </Badge>
                                                {typeof entry.diagnostics?.recovery_outcome === "string" ? (
                                                    <Badge
                                                        variant={
                                                            RECOVERY_OUTCOME_VARIANT_MAP[
                                                                entry.diagnostics.recovery_outcome as RecoveryOutcomeKey
                                                            ] ?? "outline"
                                                        }
                                                        className="h-5 shrink-0 gap-1 text-[10px]"
                                                        data-testid="executor-history-recovery-outcome"
                                                    >
                                                        {t(
                                                            `executors.history.recoveryOutcome.${entry.diagnostics.recovery_outcome}`,
                                                            { defaultValue: entry.diagnostics.recovery_outcome },
                                                        )}
                                                    </Badge>
                                                ) : null}
                                                <span
                                                    className="truncate text-xs text-muted-foreground tabular-nums"
                                                    title={startedAbsolute}
                                                >
                                                    {startedRelative}
                                                </span>
                                                {duration ? (
                                                    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground tabular-nums">
                                                        <Clock className="h-3 w-3" />
                                                        {duration}
                                                    </span>
                                                ) : null}
                                            </div>
                                        </div>

                                        <div className="mt-2.5 space-y-2">
                                            <ExecutorHistoryImageChangeList
                                                fromVersion={entry.from_version}
                                                toVersion={entry.to_version}
                                                services={entry.diagnostics?.services}
                                                valueKind={historyValueKind}
                                                t={t}
                                            />

                                            {entry.message ? (
                                                <div
                                                    className={cn(
                                                        "flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed",
                                                        status === "failed"
                                                            ? "border-destructive/30 bg-destructive/5 text-destructive"
                                                            : "border-border/60 bg-muted/10 text-muted-foreground",
                                                    )}
                                                >
                                                    {status === "failed" ? (
                                                        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                    ) : null}
                                                    <span
                                                        className="min-w-0 whitespace-pre-wrap break-words [overflow-wrap:anywhere]"
                                                        data-testid="executor-history-message"
                                                    >
                                                        {entry.message}
                                                    </span>
                                                </div>
                                            ) : (
                                                <span
                                                    className="sr-only"
                                                    data-testid="executor-history-message"
                                                >
                                                    -
                                                </span>
                                            )}
                                        </div>
                                    </li>
                                )
                            })}
                        </ol>
                    )}
                </div>
            </section>

            <DataPagination
                page={page}
                pageSize={pageSize}
                total={total}
                onPageChange={setPage}
                onPageSizeChange={setPageSize}
            />

            <AlertDialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("executors.history.clearTitle")}</AlertDialogTitle>
                        <AlertDialogDescription>{t("executors.history.clearDescription")}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={clearing}>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(event) => {
                                event.preventDefault()
                                void handleClearHistory()
                            }}
                            disabled={clearing}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {clearing ? t("common.loading") : t("executors.history.clearConfirm")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
