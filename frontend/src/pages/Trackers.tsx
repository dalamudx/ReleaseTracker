import { useMemo, useState } from "react"
import { Plus, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import type { TrackerStatus } from "@/api/types"
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

export default function TrackersPage() {
    const { t } = useTranslation()
    const queryClient = useQueryClient()

    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingTracker, setEditingTracker] = useState<string | null>(null)
    const [selectedTrackerName, setSelectedTrackerName] = useState<string | null>(null)
    const [detailRefreshKey, setDetailRefreshKey] = useState(0)
    const [deleteName, setDeleteName] = useState<string | null>(null)
    const [search, setSearch] = useState("")

    // Pagination state
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.trackers.pageSize")

    const skip = (page - 1) * pageSize

    // Use React Query to fetch the Trackers list with 30-second cache.
    const { data, isLoading: loading } = useTrackers({ skip, limit: pageSize })
    const rawTrackers: TrackerStatus[] = useMemo(() => data?.items ?? [], [data?.items])

    // Client-side filter — the current API doesn't accept a search param so we
    // filter what's already on this page. This is fine for the common case
    // (a few dozen trackers) and is a no-op when the search box is empty.
    const trackers = useMemo(() => {
        const term = search.trim().toLowerCase()
        if (!term) return rawTrackers
        return rawTrackers.filter((tracker) => {
            if (tracker.name.toLowerCase().includes(term)) return true
            if (tracker.description?.toLowerCase().includes(term)) return true
            return tracker.sources?.some((source) =>
                source.source_key?.toLowerCase().includes(term)
                || source.source_type?.toLowerCase().includes(term),
            ) ?? false
        })
    }, [rawTrackers, search])

    const total = data?.total ?? 0
    const visibleSelectedTrackerName = selectedTrackerName !== null
        && rawTrackers.some((tracker) => tracker.name === selectedTrackerName)
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
                <Button onClick={handleAdd}>
                    <Plus className="mr-2 h-4 w-4" /> {t("trackers.addNew")}
                </Button>
            </div>

            {/* Two-pane master-detail area. On xl+ the detail sits to the right
                of the list; on smaller screens they stack. The list scrolls
                internally, the detail pane has its own scroll. */}
            <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(360px,2fr)]">
                <div className="flex min-h-0 flex-col gap-3">
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

                <div className="min-h-0 overflow-y-auto">
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
