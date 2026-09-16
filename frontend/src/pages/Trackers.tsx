import { useRef, useState } from "react"
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react"
import { Plus, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import { TrackerDetail } from "@/components/trackers/TrackerDetail"
import { TrackerList } from "@/components/trackers/TrackerList"
import { TrackerDialog } from "@/components/trackers/TrackerDialog"
import { toast } from "sonner"
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
import {
    useCheckTracker,
    useDeleteTracker,
    useTrackers,
} from "@/hooks/queries"
import { useQueryClient } from "@tanstack/react-query"

const TRACKER_LIST_WIDTH_STORAGE_KEY = "settings.trackers.listWidthPercent"
const DEFAULT_TRACKER_LIST_WIDTH = 60
const MIN_TRACKER_LIST_WIDTH = 35
const MAX_TRACKER_LIST_WIDTH = 70

function clampTrackerListWidth(value: number): number {
    return Math.min(MAX_TRACKER_LIST_WIDTH, Math.max(MIN_TRACKER_LIST_WIDTH, value))
}

function getInitialTrackerListWidth(): number {
    if (typeof window === "undefined") return DEFAULT_TRACKER_LIST_WIDTH
    const storedValue = Number.parseFloat(window.localStorage.getItem(TRACKER_LIST_WIDTH_STORAGE_KEY) ?? "")
    return Number.isFinite(storedValue)
        ? clampTrackerListWidth(storedValue)
        : DEFAULT_TRACKER_LIST_WIDTH
}

export default function TrackersPage() {
    const { t } = useTranslation()
    const queryClient = useQueryClient()

    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingTracker, setEditingTracker] = useState<string | null>(null)
    const [selectedTrackerName, setSelectedTrackerName] = useState<string | null>(null)
    const [detailRefreshKey, setDetailRefreshKey] = useState(0)
    const [deleteName, setDeleteName] = useState<string | null>(null)
    const [search, setSearch] = useState("")
    const [trackerListWidth, setTrackerListWidth] = useState(getInitialTrackerListWidth)
    const [resizingPanels, setResizingPanels] = useState(false)
    const splitPaneRef = useRef<HTMLDivElement>(null)
    const trackerListWidthRef = useRef(trackerListWidth)

    // Pagination state
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.trackers.pageSize")

    const skip = (page - 1) * pageSize

    // Use React Query to fetch the Trackers list with 30-second cache.
    const { data, isLoading: loading } = useTrackers({
        skip,
        limit: pageSize,
        search: search.trim() || undefined,
    })
    const trackers = data?.items ?? []
    const total = data?.total ?? 0
    const visibleSelectedTrackerName = selectedTrackerName !== null
        && trackers.some((tracker) => tracker.name === selectedTrackerName)
        ? selectedTrackerName
        : null

    const deleteTracker = useDeleteTracker()
    const checkTracker = useCheckTracker()

    const handleAdd = () => {
        setEditingTracker(null)
        setDialogOpen(true)
    }

    const handleEdit = (name: string) => {
        setEditingTracker(name)
        setDialogOpen(true)
    }

    const handleConfirmDelete = async () => {
        if (!deleteName) return
        try {
            await deleteTracker.mutateAsync(deleteName)
            if (selectedTrackerName === deleteName) {
                setSelectedTrackerName(null)
            }
            toast.success(t("common.deleted"))
        } catch (error) {
            console.error("Failed to delete tracker", error)
            toast.error(t("common.deleteFailed"))
        } finally {
            setDeleteName(null)
        }
    }

    const handleCheck = async (name: string) => {
        const toastId = toast.loading(t("common.checkSubmitting"))
        try {
            const status = await checkTracker.mutateAsync(name)
            setDetailRefreshKey((value) => value + 1)

            if (status.manual_check_outcome === "skipped") {
                const message = status.manual_check_reason === "cooldown"
                    ? t("common.checkSkippedCooldown")
                    : status.manual_check_reason === "already_running"
                        ? t("common.checkSkippedAlreadyRunning")
                        : t("common.checkSkipped")
                toast.info(message, { id: toastId })
                return
            }

            if (status.manual_check_outcome === "failed") {
                toast.error(`${t("common.checkFailed")}: ${status.error || t("common.unexpectedError")}`, { id: toastId })
                return
            }

            if (status.error) {
                toast.warning(`${t("common.checkCompletedWithWarnings")}: ${status.error}`, { id: toastId })
                return
            }

            toast.success(t("common.checkCompleted"), { id: toastId })
        } catch (error: unknown) {
            console.error("Failed to check tracker", error)
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
                || (error as Error).message
                || t("common.checkFailed")
            toast.error(`${t("common.checkFailed")}: ${detail}`, { id: toastId })
        }
    }

    const updateTrackerListWidth = (value: number, persist = false) => {
        const nextWidth = clampTrackerListWidth(value)
        trackerListWidthRef.current = nextWidth
        setTrackerListWidth(nextWidth)
        if (persist) {
            window.localStorage.setItem(TRACKER_LIST_WIDTH_STORAGE_KEY, String(nextWidth))
        }
    }

    const updateTrackerListWidthFromPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
        const bounds = splitPaneRef.current?.getBoundingClientRect()
        if (!bounds || bounds.width <= 0) return
        updateTrackerListWidth(((event.clientX - bounds.left) / bounds.width) * 100)
    }

    const finishPanelResize = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
        }
        setResizingPanels(false)
        window.localStorage.setItem(
            TRACKER_LIST_WIDTH_STORAGE_KEY,
            String(trackerListWidthRef.current),
        )
    }

    const handlePanelResizeKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
        const keyboardStep = event.shiftKey ? 5 : 2
        let nextWidth: number | null = null
        if (event.key === "ArrowLeft") nextWidth = trackerListWidthRef.current - keyboardStep
        if (event.key === "ArrowRight") nextWidth = trackerListWidthRef.current + keyboardStep
        if (event.key === "Home") nextWidth = MIN_TRACKER_LIST_WIDTH
        if (event.key === "End") nextWidth = MAX_TRACKER_LIST_WIDTH
        if (nextWidth === null) return
        event.preventDefault()
        updateTrackerListWidth(nextWidth, true)
    }

    const splitPaneStyle = {
        "--tracker-list-width": `${trackerListWidth}%`,
    } as CSSProperties

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            {/* Toolbar — search on the left, primary action on the right. */}
            <div className="flex flex-none flex-wrap items-center justify-between gap-3">
                <div className="w-full max-w-sm">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t("trackers.searchPlaceholder")}
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
                                    title={t("common.clear")}
                                >
                                    <X className="h-3.5 w-3.5" />
                                </Button>
                            </InputGroupAddon>
                        ) : null}
                    </InputGroup>
                </div>
                <Button onClick={handleAdd}>
                    <Plus className="mr-2 h-4 w-4" /> {t("trackers.addNew")}
                </Button>
            </div>

            {/* Two-pane master-detail area. On xl+ the separator resizes both panes;
                smaller screens retain the stacked layout. */}
            <div
                ref={splitPaneRef}
                className={`flex min-h-0 flex-1 flex-col gap-4 xl:flex-row xl:gap-0 ${resizingPanels ? "select-none" : ""}`}
                style={splitPaneStyle}
            >
                <div
                    id="tracker-list-pane"
                    className="flex min-h-0 w-full flex-col gap-3 xl:w-[var(--tracker-list-width)] xl:min-w-0 xl:flex-none xl:pr-2"
                >
                    <TrackerList
                        trackers={trackers}
                        loading={loading}
                        selectedTrackerName={visibleSelectedTrackerName}
                        onSelect={setSelectedTrackerName}
                        onEdit={handleEdit}
                        onDelete={setDeleteName}
                        onCheck={handleCheck}
                    />

                    <DataPagination
                        page={page}
                        pageSize={pageSize}
                        total={total}
                        onPageChange={setPage}
                        onPageSizeChange={setPageSize}
                    />
                </div>

                <div
                    role="separator"
                    aria-label={t("trackers.resizePanels")}
                    aria-orientation="vertical"
                    aria-controls="tracker-list-pane tracker-detail-pane"
                    aria-valuemin={MIN_TRACKER_LIST_WIDTH}
                    aria-valuemax={MAX_TRACKER_LIST_WIDTH}
                    aria-valuenow={Math.round(trackerListWidth)}
                    tabIndex={0}
                    className="group hidden w-4 shrink-0 touch-none cursor-col-resize items-stretch justify-center rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring xl:flex"
                    onPointerDown={(event) => {
                        event.currentTarget.setPointerCapture(event.pointerId)
                        setResizingPanels(true)
                    }}
                    onPointerMove={updateTrackerListWidthFromPointer}
                    onPointerUp={finishPanelResize}
                    onPointerCancel={finishPanelResize}
                    onKeyDown={handlePanelResizeKeyDown}
                    onDoubleClick={() => updateTrackerListWidth(DEFAULT_TRACKER_LIST_WIDTH, true)}
                >
                    <span
                        className={`w-px transition-colors ${resizingPanels
                            ? "bg-primary"
                            : "bg-border group-hover:bg-primary/70"}`}
                        aria-hidden="true"
                    />
                </div>

                <div
                    id="tracker-detail-pane"
                    className="min-h-0 w-full min-w-0 overflow-y-auto xl:flex-1 xl:pl-2"
                >
                    <TrackerDetail
                        trackerName={visibleSelectedTrackerName}
                        refreshKey={detailRefreshKey}
                    />
                </div>
            </div>

            <TrackerDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                trackerName={editingTracker}
                onSuccess={async (trackerName) => {
                    await queryClient.invalidateQueries({ queryKey: ["trackers"] })
                    setSelectedTrackerName(trackerName)
                    setDetailRefreshKey((value) => value + 1)
                }}
            />

            <AlertDialog open={!!deleteName} onOpenChange={(open) => !open && setDeleteName(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("common.confirm")}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t("common.delete")} {deleteName}?
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirmDelete}>{t("common.confirm")}</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
