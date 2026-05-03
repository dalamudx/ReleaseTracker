import { useEffect, useMemo, useState } from "react"
import { Search, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"
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
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { cn } from "@/lib/utils"
import { buildExecutorTargetDisplay, isHelmReleaseTarget } from "./executorSheetHelpers"

interface ExecutorExecutionHistoryPanelProps {
    executor: ExecutorListItem | null
    refreshKey: number
}

interface ImageChangeRow {
    key: string
    service: string | null
    fromValue: string
    toValue: string
}

const statusVariantMap = {
    queued: "secondary",
    running: "secondary",
    success: "default",
    failed: "destructive",
    skipped: "secondary",
} as const

function buildStructuredImageChangeRows(services: ExecutorRunServiceDiagnostic[]): ImageChangeRow[] {
    return services.map((service) => ({
        key: `service-${service.service}`,
        service: service.service,
        fromValue: service.from_version || '-',
        toValue: service.to_version || '-',
    }))
}

function buildImageChangeRows(
    fromVersion: string | null | undefined,
    toVersion: string | null | undefined,
): ImageChangeRow[] {
    return [{
        key: "single-image-change",
        service: null,
        fromValue: fromVersion?.trim() || '-',
        toValue: toVersion?.trim() || '-',
    }]
}

function ImageCell({ label, value, testId }: { label: string; value: string; testId: string }) {
    return (
        <div className="min-w-0 space-y-1" data-testid={testId} title={value === '-' ? undefined : value}>
            <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                {label}
            </div>
            <code className="block min-w-0 whitespace-pre-wrap break-all rounded-md bg-muted/50 px-2 py-1.5 font-mono text-xs leading-relaxed text-foreground [overflow-wrap:anywhere]">
                {value}
            </code>
        </div>
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
    const titleKey = valueKind === "version" ? 'executors.review.versionChanges' : 'executors.review.imageChanges'
    const fromLabelKey = valueKind === "version" ? 'executors.history.table.fromVersion' : 'executors.history.table.fromImage'
    const toLabelKey = valueKind === "version" ? 'executors.history.table.toVersion' : 'executors.history.table.toImage'
    const fromTestId = valueKind === "version" ? "executor-history-from-version" : "executor-history-from-image"
    const toTestId = valueKind === "version" ? "executor-history-to-version" : "executor-history-to-image"

    return (
        <div className="min-w-0 rounded-lg bg-muted/20 p-3" data-testid="executor-history-image-change-list">
            <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                {t(titleKey)}
            </div>
            <ul className="mt-2 divide-y divide-border/60 overflow-hidden rounded-lg border border-border/60 bg-background/80">
                {rows.map((row) => (
                    <li
                        key={row.key}
                        className={cn(
                            "grid min-w-0 gap-3 p-3",
                            hasServiceColumn ? "md:grid-cols-[10rem_minmax(0,1fr)_minmax(0,1fr)]" : "md:grid-cols-2",
                        )}
                    >
                        {hasServiceColumn ? (
                            <div className="min-w-0 space-y-1">
                                <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                                    {t('executors.fields.service')}
                                </div>
                                <div className="min-w-0 truncate text-sm font-medium text-foreground" title={row.service ?? undefined}>
                                    {row.service ?? '-'}
                                </div>
                            </div>
                        ) : null}
                        <ImageCell
                            label={t(fromLabelKey)}
                            value={row.fromValue}
                            testId={fromTestId}
                        />
                        <ImageCell
                            label={t(toLabelKey)}
                            value={row.toValue}
                            testId={toTestId}
                        />
                    </li>
                ))}
            </ul>
        </div>
    )
}

export function ExecutorExecutionHistoryPanel({ executor, refreshKey }: ExecutorExecutionHistoryPanelProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()
    const [executionHistory, setExecutionHistory] = useState<ExecutorRunHistory[]>([])
    const [loading, setLoading] = useState(false)
    const [page, setPage] = useState(1)
    const [total, setTotal] = useState(0)
    const [search, setSearch] = useState("")
    const [status, setStatus] = useState<"all" | "success" | "failed" | "skipped">("all")
    const [clearDialogOpen, setClearDialogOpen] = useState(false)
    const [clearing, setClearing] = useState(false)

    const pageSize = 10
    const totalPages = Math.max(1, Math.ceil(total / pageSize))

    useEffect(() => {
        if (!executor?.id) {
            return
        }

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
    }, [executor?.id, page, refreshKey, search, status])

    const targetLabel = useMemo(() => {
        if (!executor) {
            return null
        }

        return buildExecutorTargetDisplay(executor.runtime_type, executor.target_ref, t)
    }, [executor, t])
    const historyValueKind = executor && isHelmReleaseTarget(executor.target_ref) ? "version" : "image"

    const handleClearHistory = async () => {
        if (!executor?.id || clearing) {
            return
        }

        setClearing(true)
        try {
            const response = await api.clearExecutorHistory(executor.id)
            setExecutionHistory([])
            setTotal(0)
            setPage(1)
            setClearDialogOpen(false)
            toast.success(t('executors.history.clearSuccess', { count: response.deleted }))
        } catch (error) {
            console.error("Failed to clear executor history", error)
            toast.error(t('executors.history.clearFailed'))
        } finally {
            setClearing(false)
        }
    }

    if (!executor) {
        return (
            <div className="rounded-lg border border-dashed border-border/60 bg-muted/20 p-4 text-sm text-muted-foreground">
                <p>{t('executors.history.emptySelection')}</p>
            </div>
        )
    }

    return (
        <div className="space-y-4">
            <div className="space-y-3">
                <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-3">
                    <div className="min-w-0 space-y-1">
                        <div className="truncate text-sm font-semibold text-foreground">{executor.name}</div>
                        <div className="text-xs text-muted-foreground">
                            <span className="uppercase tracking-[0.12em]">{executor.runtime_type}</span>
                            <span className="px-1.5">·</span>
                            <span>{executor.runtime_connection_name || '-'}</span>
                        </div>
                        {targetLabel ? (
                            <div className="min-w-0 pt-1 text-xs text-muted-foreground [overflow-wrap:anywhere]">
                                <span className="font-medium text-foreground/80">{targetLabel.title}</span>
                                <span className="px-1.5">·</span>
                                <span>{targetLabel.summary}</span>
                                <span className="px-1.5">·</span>
                                <span className="uppercase tracking-[0.12em]">{targetLabel.badges.find((badge) => badge !== executor.runtime_type) ?? targetLabel.badges[0]}</span>
                            </div>
                        ) : null}
                    </div>
                </div>
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="w-full md:w-80">
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
                                placeholder={t('executors.history.searchPlaceholder')}
                            />
                        </InputGroup>
                    </div>
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <Select
                            value={status}
                            onValueChange={(value: "all" | "success" | "failed" | "skipped") => {
                                setStatus(value)
                                setPage(1)
                            }}
                        >
                            <SelectTrigger className="w-full sm:w-44">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t('executors.history.filters.all')}</SelectItem>
                                <SelectItem value="success">{t('executors.results.success')}</SelectItem>
                                <SelectItem value="failed">{t('executors.results.failed')}</SelectItem>
                                <SelectItem value="skipped">{t('executors.results.skipped')}</SelectItem>
                            </SelectContent>
                        </Select>
                        <Button
                            variant="outline"
                            size="sm"
                            className="gap-2 text-destructive hover:text-destructive"
                            onClick={() => setClearDialogOpen(true)}
                            disabled={loading || clearing}
                        >
                            <Trash2 className="h-4 w-4" />
                            {t('executors.history.clearRecords')}
                        </Button>
                    </div>
                </div>
            </div>
            <section aria-label={t('executors.history.title')} className="space-y-3">
                {loading ? (
                    <div className="rounded-xl border border-border/60 bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                        {t('common.loading')}
                    </div>
                ) : executionHistory.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-border/60 bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                        {t('executors.history.noResults')}
                    </div>
                ) : (
                    <ol className="space-y-3" data-testid="executor-history-list">
                        {executionHistory.map((executionHistoryEntry) => (
                            <li
                                key={executionHistoryEntry.id ?? `${executionHistoryEntry.executor_id}-${executionHistoryEntry.started_at}`}
                                className="rounded-xl border border-border/60 bg-card p-4 shadow-sm"
                                data-testid="executor-history-item"
                            >
                                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                    <div className="min-w-0 space-y-1">
                                        <div className="text-sm font-semibold text-foreground">{formatDate(executionHistoryEntry.started_at)}</div>
                                        <div className="text-xs text-muted-foreground">
                                            {executionHistoryEntry.finished_at ? formatDate(executionHistoryEntry.finished_at) : '-'}
                                        </div>
                                    </div>
                                    <Badge variant={statusVariantMap[executionHistoryEntry.status]} className="w-fit capitalize">
                                        {t(`executors.results.${executionHistoryEntry.status}`)}
                                    </Badge>
                                </div>

                                <div className="mt-4 grid gap-3">
                                    <ExecutorHistoryImageChangeList
                                        fromVersion={executionHistoryEntry.from_version}
                                        toVersion={executionHistoryEntry.to_version}
                                        services={executionHistoryEntry.diagnostics?.services}
                                        valueKind={historyValueKind}
                                        t={t}
                                    />

                                    <div className="min-w-0 rounded-lg border border-border/60 bg-background p-3">
                                        <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                                            {t('executors.history.table.message')}
                                        </div>
                                        <div
                                            className="mt-1 min-w-0 whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground [overflow-wrap:anywhere]"
                                            data-testid="executor-history-message"
                                        >
                                            {executionHistoryEntry.message || '-'}
                                        </div>
                                    </div>
                                </div>
                            </li>
                        ))}
                    </ol>
                )}
            </section>
            <div className="flex items-center justify-between">
                <div className="text-sm text-muted-foreground">
                    {t('pagination.totalItems', { count: total })}
                </div>
                <div className="flex items-center gap-2">
                    <div className="text-sm font-medium">{t('pagination.pageOf', { page, total: totalPages })}</div>
                    <Button variant="outline" size="sm" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1}>
                        {t('pagination.previous')}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page >= totalPages}>
                        {t('pagination.next')}
                    </Button>
                </div>
            </div>
            <AlertDialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('executors.history.clearTitle')}</AlertDialogTitle>
                        <AlertDialogDescription>{t('executors.history.clearDescription')}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={clearing}>{t('common.cancel')}</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(event) => {
                                event.preventDefault()
                                void handleClearHistory()
                            }}
                            disabled={clearing}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {clearing ? t('common.loading') : t('executors.history.clearConfirm')}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
