import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Plus, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { api } from "@/api/client"
import type { ExecutorListItem, RuntimeConnection, TrackerStatus } from "@/api/types"
import { ExecutorExecutionHistoryPanel } from "@/components/executors/ExecutorExecutionHistoryPanel"
import { ExecutorList } from "@/components/executors/ExecutorList"
import { ExecutorSheet } from "@/components/executors/ExecutorSheet"
import { Button } from "@/components/ui/button"
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
import { usePageSize } from "@/hooks/use-page-size"
import { toast } from "sonner"

const SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"

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
    const executionHistorySheetOpenRef = useRef(executionHistorySheetOpen)
    const selectedExecutorIdRef = useRef(selectedExecutorId)
    const auxiliaryLoadedRef = useRef(false)

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
        }
        if (refreshAuxiliary) {
            setPrerequisitesLoading(true)
        }

        const skip = (page - 1) * pageSize
        const executorRequest = api.getExecutors({ skip, limit: pageSize })
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
                if (executorData.items.length === 0) {
                    return null
                }
                return executorData.items[0].id ?? null
            })
        } catch (error) {
            console.error('Failed to load executors', error)
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
    }, [page, pageSize, t])

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

    const handleRun = (executorId: number) => {
        const executor = executors.find((item) => item.id === executorId) ?? null
        if (executor && !executor.enabled) {
            toast.error(t('executors.toasts.runDisabled'))
            return
        }

        void api.runExecutor(executorId).then(() => {
            toast.success(t('executors.toasts.runQueued'))
            setSelectedExecutorId(executorId)
            if (executor) {
                setSelectedExecutorSnapshot(executor)
            }
            setExecutionHistoryRefreshKey((value) => value + 1)
            silentlyRefreshExecutors()
            setTimeout(() => {
                silentlyRefreshExecutors()
                setExecutionHistoryRefreshKey((value) => value + 1)
            }, 2000)
        }).catch((error: unknown) => {
            console.error('Failed to run executor', error)
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            const message = typeof detail === "string" && /^Executor \d+ is disabled$/.test(detail)
                ? t('executors.toasts.runDisabled')
                : detail || t('executors.toasts.runFailed')
            toast.error(message)
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

    // Client-side filter. The API doesn't accept a search param yet, so we
    // filter the current page locally — good enough for the common case and a
    // no-op when the search box is empty.
    const filteredExecutors = useMemo(() => {
        const term = search.trim().toLowerCase()
        if (!term) return executors
        return executors.filter((executor) => {
            if (executor.name.toLowerCase().includes(term)) return true
            if (executor.description?.toLowerCase().includes(term)) return true
            if (executor.tracker_name?.toLowerCase().includes(term)) return true
            if (executor.runtime_connection_name?.toLowerCase().includes(term)) return true
            if (executor.runtime_type?.toLowerCase().includes(term)) return true
            return false
        })
    }, [executors, search])

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
            {/* Toolbar — search + primary action. */}
            <div className="flex flex-none flex-wrap items-center justify-between gap-3">
                <div className="w-full max-w-sm">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t("executors.searchPlaceholder")}
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
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
                <Button onClick={handleAdd} disabled={addDisabled}>
                    <Plus className="mr-2 h-4 w-4" /> {t('executors.addNew')}
                </Button>
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
                <ExecutorList
                    executors={filteredExecutors}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={setDeleteExecutorId}
                    onRun={handleRun}
                    onViewExecutionHistory={handleOpenExecutionHistory}
                    selectedExecutorId={selectedExecutorId}
                />

                <DataPagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={setPage}
                    onPageSizeChange={setPageSize}
                    onBeforeChange={closeExecutionHistorySheet}
                />
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
                        <SheetTitle>{t('executors.history.title')}</SheetTitle>
                        <SheetDescription>
                            {selectedExecutor ? t('executors.history.description') : t('executors.history.emptySelection')}
                        </SheetDescription>
                    </SheetHeader>

                    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
                        <ExecutorExecutionHistoryPanel executor={selectedExecutor} refreshKey={executionHistoryRefreshKey} />
                    </div>
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
