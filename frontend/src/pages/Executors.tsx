import { useCallback, useEffect, useRef, useState } from "react"
import { Edit, Play, Plus, RefreshCw, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router"

import { api } from "@/api/client"
import type { ExecutorListItem, RuntimeConnection, TrackerStatus } from "@/api/types"
import { ExecutorExecutionHistoryPanel } from "@/components/executors/ExecutorExecutionHistoryPanel"
import { ExecutorList } from "@/components/executors/ExecutorList"
import { ExecutorSheet } from "@/components/executors/ExecutorSheet"
import { ExecutorSnapshotsPanel } from "@/components/executors/ExecutorSnapshotsPanel"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Spinner } from "@/components/ui/spinner"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
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
import { DataPagination } from "@/components/common/DataPagination"
import { QueryErrorState } from "@/components/common/QueryErrorState"
import { usePageSize } from "@/hooks/use-page-size"
import { toast } from "sonner"

const SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"
const EXECUTOR_RUN_POLL_INTERVAL_MS = 2000
const EXECUTOR_RUN_POLL_TIMEOUT_MS = 5 * 60 * 1000
const TERMINAL_EXECUTOR_RUN_STATUSES = new Set(["success", "failed", "skipped"])

function supportsFullConfigSnapshots(executor: ExecutorListItem | null) {
    if (!executor) {
        return false
    }
    const targetMode = executor.target_ref?.mode ?? "container"
    if (targetMode !== "container" && targetMode !== "docker_compose") {
        return false
    }
    return executor.runtime_type === "docker" || executor.runtime_type === "podman"
}

function getBrowserTimezone() {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
}

interface LoadExecutorsOptions {
    showLoading?: boolean
    showErrorToast?: boolean
    refreshAuxiliary?: boolean
}

export default function ExecutorsPage() {
    const { t } = useTranslation()
    const [executors, setExecutors] = useState<ExecutorListItem[]>([])
    const [runtimeConnections, setRuntimeConnections] = useState<RuntimeConnection[]>([])
    const [trackers, setTrackers] = useState<TrackerStatus[]>([])
    const [systemTimezone, setSystemTimezone] = useState(getBrowserTimezone())
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState(false)
    const [prerequisitesLoading, setPrerequisitesLoading] = useState(true)
    const [sheetOpen, setSheetOpen] = useState(false)
    const [executionHistorySheetOpen, setExecutionHistorySheetOpen] = useState(false)
    const [editingExecutorId, setEditingExecutorId] = useState<number | null>(null)
    const [deleteExecutorId, setDeleteExecutorId] = useState<number | null>(null)
    const [selectedExecutorId, setSelectedExecutorId] = useState<number | null>(null)
    const [selectedExecutorSnapshot, setSelectedExecutorSnapshot] = useState<ExecutorListItem | null>(null)
    const [executionHistoryRefreshKey, setExecutionHistoryRefreshKey] = useState(0)

    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize('settings.executors.pageSize')
    const [search, setSearch] = useState("")
    const [submittingExecutorIds, setSubmittingExecutorIds] = useState<ReadonlySet<number>>(new Set())
    const submittingExecutorIdsRef = useRef(new Set<number>())
    const executionHistorySheetOpenRef = useRef(executionHistorySheetOpen)
    const selectedExecutorIdRef = useRef(selectedExecutorId)
    const auxiliaryLoadedRef = useRef(false)
    const runPollTimeoutsRef = useRef(new Map<number, { runId: number; timeout: number }>())
    const mountedRef = useRef(true)

    useEffect(() => {
        executionHistorySheetOpenRef.current = executionHistorySheetOpen
    }, [executionHistorySheetOpen])

    useEffect(() => {
        selectedExecutorIdRef.current = selectedExecutorId
    }, [selectedExecutorId])

    const closeExecutionHistorySheet = useCallback(() => {
        setExecutionHistorySheetOpen(false)
        setSelectedExecutorSnapshot(null)
    }, [])

    const loadExecutors = useCallback(async (options: LoadExecutorsOptions = {}) => {
        await Promise.resolve()
        const showLoading = options.showLoading ?? true
        const showErrorToast = options.showErrorToast ?? true
        const refreshAuxiliary = options.refreshAuxiliary ?? !auxiliaryLoadedRef.current

        if (showLoading) {
            setLoading(true)
            setLoadError(false)
        }
        if (refreshAuxiliary) {
            setPrerequisitesLoading(true)
        }

        const skip = (page - 1) * pageSize
        const executorRequest = api.getExecutors({
            skip,
            limit: pageSize,
            search: search.trim() || undefined,
        })
        const auxiliaryRequest = refreshAuxiliary
            ? Promise.all([
                api.getRuntimeConnections({ skip: 0, limit: 1000 }),
                api.getTrackers({ skip: 0, limit: 1000 }),
                api.getSettings(),
            ]).then((data) => ({ data })).catch((error: unknown) => ({ error }))
            : null

        try {
            const executorData = await executorRequest
            setExecutors(executorData.items)
            setTotal(executorData.total)
            const currentSelectedExecutorId = selectedExecutorIdRef.current
            const matchedSelectedExecutor = currentSelectedExecutorId
                ? executorData.items.find((item) => item.id === currentSelectedExecutorId) ?? null
                : null

            if (matchedSelectedExecutor) {
                setSelectedExecutorSnapshot(matchedSelectedExecutor)
            } else if (!executionHistorySheetOpenRef.current) {
                setSelectedExecutorSnapshot(null)
            }

            setSelectedExecutorId((current) => {
                if (current && executorData.items.some((item) => item.id === current)) {
                    return current
                }
                if (executionHistorySheetOpenRef.current && current) {
                    return current
                }
                return null
            })
        } catch (error) {
            console.error('Failed to load executors', error)
            setLoadError(true)
            if (showErrorToast) {
                toast.error(t('executors.toasts.loadFailed'))
            }
        } finally {
            if (showLoading) {
                setLoading(false)
            }
        }

        if (!auxiliaryRequest) {
            return
        }

        try {
            const auxiliaryResult = await auxiliaryRequest
            if ("error" in auxiliaryResult) {
                throw auxiliaryResult.error
            }

            const [runtimeData, trackerData, settingsData] = auxiliaryResult.data
            setRuntimeConnections(runtimeData.items)
            setTrackers(trackerData.items)
            const timezoneValue = settingsData.find((item) => item.key === SYSTEM_TIMEZONE_SETTING_KEY)?.value
            setSystemTimezone(typeof timezoneValue === "string" && timezoneValue.trim() ? timezoneValue.trim() : getBrowserTimezone())
            auxiliaryLoadedRef.current = true
        } catch (error) {
            console.error('Failed to load executor prerequisites', error)
            if (showErrorToast) {
                toast.error(t('executors.toasts.loadFailed'))
            }
        } finally {
            setPrerequisitesLoading(false)
        }
    }, [page, pageSize, search, t])

    useEffect(() => {
        void Promise.resolve().then(() => loadExecutors())
    }, [loadExecutors])

    const handleAdd = () => {
        closeExecutionHistorySheet()
        setEditingExecutorId(null)
        setSheetOpen(true)
    }

    const handleEdit = (executorId: number) => {
        closeExecutionHistorySheet()
        setEditingExecutorId(executorId)
        setSheetOpen(true)
    }

    const handleOpenExecutionHistory = (executorId: number) => {
        const executor = executors.find((item) => item.id === executorId) ?? null
        setSheetOpen(false)
        setSelectedExecutorId(executorId)
        setSelectedExecutorSnapshot(executor)
        setExecutionHistorySheetOpen(true)
    }

    const silentlyRefreshExecutors = useCallback(() => {
        void loadExecutors({ showLoading: false, showErrorToast: false })
    }, [loadExecutors])

    const pollExecutorRun = useCallback((executorId: number, runId: number, taskId?: number) => {
        const existing = runPollTimeoutsRef.current.get(executorId)
        if (existing) {
            window.clearTimeout(existing.timeout)
        }
        const startedAt = Date.now()

        const poll = async () => {
            const active = runPollTimeoutsRef.current.get(executorId)
            if (!mountedRef.current || active?.runId !== runId) {
                return
            }
            try {
                const task = taskId ? await api.getTask(taskId) : null
                if (task && !["queued", "running", "retry_wait"].includes(task.state)) {
                    runPollTimeoutsRef.current.delete(executorId)
                    silentlyRefreshExecutors()
                    setExecutionHistoryRefreshKey((value) => value + 1)
                    return
                }
                const detail = await api.getExecutor(executorId)
                const latestRun = taskId ? null : detail.latest_run
                if (
                    latestRun?.id === runId
                    && TERMINAL_EXECUTOR_RUN_STATUSES.has(latestRun.status)
                ) {
                    runPollTimeoutsRef.current.delete(executorId)
                    silentlyRefreshExecutors()
                    setExecutionHistoryRefreshKey((value) => value + 1)
                    return
                }
            } catch (error) {
                console.error("Failed to poll executor run", error)
            }
            if (Date.now() - startedAt >= EXECUTOR_RUN_POLL_TIMEOUT_MS) {
                runPollTimeoutsRef.current.delete(executorId)
                return
            }
            const timeout = window.setTimeout(() => void poll(), EXECUTOR_RUN_POLL_INTERVAL_MS)
            runPollTimeoutsRef.current.set(executorId, { runId, timeout })
        }

        const timeout = window.setTimeout(() => void poll(), EXECUTOR_RUN_POLL_INTERVAL_MS)
        runPollTimeoutsRef.current.set(executorId, { runId, timeout })
    }, [silentlyRefreshExecutors])

    useEffect(() => {
        mountedRef.current = true
        const runPollTimeouts = runPollTimeoutsRef.current
        return () => {
            mountedRef.current = false
            for (const { timeout } of runPollTimeouts.values()) {
                window.clearTimeout(timeout)
            }
            runPollTimeouts.clear()
        }
    }, [])

    const handleRollbackQueued = useCallback(() => {
        silentlyRefreshExecutors()
        setExecutionHistoryRefreshKey((value) => value + 1)
        setTimeout(() => {
            silentlyRefreshExecutors()
            setExecutionHistoryRefreshKey((value) => value + 1)
        }, 2000)
    }, [silentlyRefreshExecutors])

    const handleRun = (executorId: number) => {
        if (submittingExecutorIdsRef.current.has(executorId)) return
        const executor = executors.find((item) => item.id === executorId) ?? (selectedExecutorSnapshot?.id === executorId ? selectedExecutorSnapshot : null)
        if (executor && !executor.enabled) {
            toast.error(t('executors.toasts.runDisabled'))
            return
        }

        submittingExecutorIdsRef.current.add(executorId)
        setSubmittingExecutorIds(new Set(submittingExecutorIdsRef.current))
        const toastId = toast.loading(t('executors.toasts.runSubmitting'))
        void api.runExecutor(executorId).then((response) => {
            if ("task_id" in response) {
                toast.info(t("tasks.submitted", { name: executor?.name ?? t("tasks.executorTarget", { id: executorId }), operation: t("tasks.kind.deploy") }), { id: toastId })
                setSelectedExecutorId(executorId)
                pollExecutorRun(executorId, response.task_id, response.task_id)
                return
            }
            const message = response.status === "queued"
                ? t('executors.toasts.runQueued')
                : t('executors.toasts.runStarted')
            toast.success(message, { id: toastId })
            setSelectedExecutorId(executorId)
            if (executor) {
                setSelectedExecutorSnapshot(executor)
            }
            setExecutionHistoryRefreshKey((value) => value + 1)
            silentlyRefreshExecutors()
            pollExecutorRun(executorId, response.run_id)
        }).catch((error: unknown) => {
            console.error('Failed to run executor', error)
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            const message = typeof detail === "string" && /^Executor \d+ is disabled$/.test(detail)
                ? t('executors.toasts.runDisabled')
                : typeof detail === "string" && /^Executor \d+ is already running$/.test(detail)
                    ? t('executors.toasts.runAlreadyRunning')
                    : detail || t('executors.toasts.runFailed')
            toast.error(message, { id: toastId })
        }).finally(() => {
            submittingExecutorIdsRef.current.delete(executorId)
            if (mountedRef.current) setSubmittingExecutorIds(new Set(submittingExecutorIdsRef.current))
        })
    }

    const handleConfirmDelete = async () => {
        if (deleteExecutorId === null) {
            return
        }

        try {
            if (selectedExecutorId === deleteExecutorId) {
                closeExecutionHistorySheet()
                setSelectedExecutorId(null)
            }
            await api.deleteExecutor(deleteExecutorId)
            await loadExecutors()
            toast.success(t('common.deleted'))
        } catch (error) {
            console.error('Failed to delete executor', error)
            toast.error(t('common.deleteFailed'))
        } finally {
            setDeleteExecutorId(null)
        }
    }

    const selectedExecutor = executors.find((executor) => executor.id === selectedExecutorId) ?? selectedExecutorSnapshot
    const canViewSnapshots = supportsFullConfigSnapshots(selectedExecutor)


    const hasRuntimeConnections = runtimeConnections.length > 0
    const hasTrackers = trackers.length > 0
    const addDisabled = prerequisitesLoading || !hasRuntimeConnections || !hasTrackers
    const prerequisiteState = loading || prerequisitesLoading
        ? null
        : !hasRuntimeConnections && !hasTrackers
            ? 'both'
            : !hasRuntimeConnections
                ? 'runtime'
                : !hasTrackers
                    ? 'tracker'
                    : null

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            <div className="flex flex-none flex-wrap items-center gap-3">
                <div className="w-full min-w-0 sm:w-auto sm:flex-1 sm:max-w-sm">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t("executors.searchPlaceholder")}
                            aria-label={t("executors.searchPlaceholder")}
                            value={search}
                            onChange={(event) => {
                                setSearch(event.target.value)
                                setPage(1)
                            }}
                        />
                        {search ? (
                            <InputGroupAddon align="inline-end">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    onClick={() => {
                                        setSearch("")
                                        setPage(1)
                                    }}
                                    aria-label={t("common.clear")}
                                    title={t("common.clear")}
                                >
                                    <X className="h-3.5 w-3.5" />
                                </Button>
                            </InputGroupAddon>
                        ) : null}
                    </InputGroup>
                </div>
                <div className="ml-auto flex items-center gap-2">
                    <Button variant="outline" size="icon" className="size-9" disabled={loading} aria-label={t('executors.refresh')} onClick={() => { void loadExecutors(); setExecutionHistoryRefreshKey(value => value + 1) }}>
                        {loading ? <Spinner className="size-4" /> : <RefreshCw className="size-4" />}
                    </Button>
                    <Button size="sm" className="h-9" onClick={handleAdd} disabled={addDisabled}>
                        <Plus className="size-4" /> {t('executors.addNew')}
                    </Button>
                </div>
            </div>

            {prerequisiteState ? (
                <div className="rounded-lg border border-dashed border-border/60 bg-card/80 p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="space-y-1">
                            <div className="text-sm font-semibold">
                                {t(`executors.prerequisites.${prerequisiteState}.title`)}
                            </div>
                            <p className="max-w-2xl text-sm text-muted-foreground">
                                {t(`executors.prerequisites.${prerequisiteState}.description`)}
                            </p>
                        </div>

                        <div className="flex flex-wrap gap-2">
                            {!hasRuntimeConnections ? (
                                <Button asChild>
                                    <Link to="/runtime-connections">
                                        {t('executors.prerequisites.actions.runtimeConnections')}
                                    </Link>
                                </Button>
                            ) : null}
                            {!hasTrackers ? (
                                <Button asChild variant={!hasRuntimeConnections ? 'outline' : 'default'}>
                                    <Link to="/trackers">
                                        {t('executors.prerequisites.actions.trackers')}
                                    </Link>
                                </Button>
                            ) : null}
                        </div>
                    </div>
                </div>
            ) : null}

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                {loadError ? (
                    <QueryErrorState onRetry={() => void loadExecutors()} />
                ) : (
                    <>
                <ExecutorList
                    executors={executors}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={setDeleteExecutorId}
                    onRun={handleRun}
                    onViewExecutionHistory={handleOpenExecutionHistory}
                    selectedExecutorId={selectedExecutorId}
                    onSelect={setSelectedExecutorId}
                    submittingExecutorIds={submittingExecutorIds}
                />

                <DataPagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={setPage}
                    onPageSizeChange={setPageSize}
                    onBeforeChange={closeExecutionHistorySheet}
                />
                    </>
                )}
            </div>

            <ExecutorSheet
                open={sheetOpen}
                onOpenChange={setSheetOpen}
                executorId={editingExecutorId}
                runtimeConnections={runtimeConnections}
                trackers={trackers}
                systemTimezone={systemTimezone}
                onSuccess={loadExecutors}
            />

            <Sheet
                open={executionHistorySheetOpen}
                onOpenChange={(open) => {
                    if (!open) {
                        closeExecutionHistorySheet()
                        return
                    }
                    setExecutionHistorySheetOpen(true)
                }}
            >
                <SheetContent side="right" className="flex w-full flex-col border-l sm:max-w-4xl">
                    <SheetHeader className="border-b border-border/60 pb-4">
                        <SheetTitle className="break-words pr-8">{selectedExecutor?.name ?? t('executors.history.title')}</SheetTitle>
                        <SheetDescription>
                            {selectedExecutor ? t('executors.history.description') : t('executors.history.emptySelection')}
                        </SheetDescription>
                        {selectedExecutor?.id && (
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                                <Badge variant="outline">{selectedExecutor.runtime_type.toUpperCase()}</Badge>
                                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={selectedExecutor.runtime_connection_name ?? undefined}>{selectedExecutor.runtime_connection_name}</span>
                                <Button variant="outline" size="sm" disabled={submittingExecutorIds.has(selectedExecutor.id)} onClick={() => handleEdit(selectedExecutor.id!)}><Edit className="size-3.5" />{t('common.edit')}</Button>
                                <Button size="sm" disabled={!selectedExecutor.enabled || Boolean(selectedExecutor.invalid_config_error) || submittingExecutorIds.has(selectedExecutor.id)} onClick={() => handleRun(selectedExecutor.id!)}>
                                    {submittingExecutorIds.has(selectedExecutor.id) ? <Spinner className="size-3.5" /> : <Play className="size-3.5" />}{t('executors.actions.runNow')}
                                </Button>
                            </div>
                        )}
                    </SheetHeader>

                    <Tabs defaultValue="history" className="flex min-h-0 flex-1 flex-col px-4 pt-3 sm:px-6">
                        <TabsList className="shrink-0 self-start">
                            <TabsTrigger value="history">{t('executors.history.title')}</TabsTrigger>
                            {canViewSnapshots ? (
                                <TabsTrigger value="snapshots">{t('executors.snapshots.tab')}</TabsTrigger>
                            ) : null}
                        </TabsList>
                        <TabsContent
                            value="history"
                            className="mt-3 min-h-0 flex-1 overflow-y-auto pb-4 data-[state=inactive]:hidden"
                        >
                            <ExecutorExecutionHistoryPanel executor={selectedExecutor} refreshKey={executionHistoryRefreshKey} />
                        </TabsContent>
                        {canViewSnapshots ? (
                            <TabsContent
                                value="snapshots"
                                className="mt-3 min-h-0 flex-1 overflow-hidden pb-4 data-[state=inactive]:hidden"
                            >
                                <ExecutorSnapshotsPanel
                                    executor={selectedExecutor}
                                    refreshKey={executionHistoryRefreshKey}
                                    onRollbackQueued={handleRollbackQueued}
                                />
                            </TabsContent>
                        ) : null}
                    </Tabs>
                </SheetContent>
            </Sheet>

            <AlertDialog open={deleteExecutorId !== null} onOpenChange={(open) => !open && setDeleteExecutorId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('common.confirm')}</AlertDialogTitle>
                        <AlertDialogDescription>{t('executors.deleteConfirm')}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirmDelete}>{t('common.confirm')}</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
