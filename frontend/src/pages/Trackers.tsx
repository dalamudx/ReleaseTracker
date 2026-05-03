import { useState } from "react"
import { Plus, ChevronLeft, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
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
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    useTrackers,
    useDeleteTracker,
    useCheckTracker,
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

    // Pagination state
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.trackers.pageSize')
        return saved ? Number(saved) : 15
    })

    const skip = (page - 1) * pageSize

    // Use React Query to fetch the Trackers list with 30-second cache
    const { data, isLoading: loading } = useTrackers({ skip, limit: pageSize })
    const trackers: TrackerStatus[] = data?.items ?? []
    const total = data?.total ?? 0

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

    const handleDeleteClick = (name: string) => {
        setDeleteName(name)
    }

    const handleConfirmDelete = async () => {
        if (!deleteName) return
        try {
            await deleteTracker.mutateAsync(deleteName)
            // If the deleted tracker is selected, clear the selection
            if (selectedTrackerName === deleteName) {
                setSelectedTrackerName(null)
            }
            toast.success(t('common.deleted'))
        } catch (error) {
            console.error("Failed to delete tracker", error)
            toast.error(t('common.deleteFailed'))
        } finally {
            setDeleteName(null)
        }
    }

    const handleCheck = async (name: string) => {
        toast.success(t('common.checkQueued'))
        try {
            const status = await checkTracker.mutateAsync(name)
            setDetailRefreshKey((value) => value + 1)

            if (status.error) {
                toast.error(`${t('common.checkFailed')}: ${status.error}`)
            }
        } catch (error: unknown) {
            console.error("Failed to check tracker", error)
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || (error as Error).message || t('common.checkFailed')
            toast.error(`${t('common.checkFailed')}: ${detail}`)
        }
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex h-full flex-col space-y-6 pr-1">
            <div className="flex items-center justify-end flex-shrink-0">
                <div className="flex items-center space-x-2">
                    <Button onClick={handleAdd}>
                        <Plus className="mr-2 h-4 w-4" /> {t('trackers.addNew')}
                    </Button>
                </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col space-y-4">
                <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
                    <TrackerList
                        trackers={trackers}
                        loading={loading}
                        selectedTrackerName={selectedTrackerName}
                        onSelect={setSelectedTrackerName}
                        onEdit={handleEdit}
                        onDelete={handleDeleteClick}
                        onCheck={handleCheck}
                    />
                    <div className="min-h-0 overflow-auto">
                        <TrackerDetail trackerName={selectedTrackerName} refreshKey={detailRefreshKey} />
                    </div>
                </div>

                {/* Pagination controls */}
                <div className="flex items-center justify-between flex-shrink-0">
                    <div className="flex-1 text-sm text-muted-foreground">
                        {t('pagination.totalItems', { count: total })}
                    </div>

                    <div className="flex items-center space-x-6 lg:space-x-8">
                        {/* Rows per page */}
                        <div className="flex items-center space-x-2">
                            <p className="text-sm font-medium">{t('pagination.rowsPerPage')}</p>
                            <Select
                                value={`${pageSize}`}
                                onValueChange={(value) => {
                                    const newSize = Number(value)
                                    setPageSize(newSize)
                                    localStorage.setItem('settings.trackers.pageSize', String(newSize))
                                    setPage(1)
                                }}
                            >
                                <SelectTrigger className="h-8 w-[70px]">
                                    <SelectValue placeholder={pageSize} />
                                </SelectTrigger>
                                <SelectContent side="top">
                                    {[10, 15, 20, 30, 40, 50].map((size) => (
                                        <SelectItem key={size} value={`${size}`}>
                                            {size}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Page X of Y */}
                        <div className="flex w-auto min-w-[100px] items-center justify-center text-sm font-medium whitespace-nowrap">
                            {t('pagination.pageOf', { page, total: totalPages || 1 })}
                        </div>

                        {/* Pagination buttons */}
                        <div className="flex items-center space-x-2">
                            <Button
                                variant="outline"
                                className="h-8 w-8 p-0"
                                onClick={() => setPage(page - 1)}
                                disabled={page <= 1}
                            >
                                <span className="sr-only">Go to previous page</span>
                                <ChevronLeft className="h-4 w-4" />
                            </Button>
                            <Button
                                variant="outline"
                                className="h-8 w-8 p-0"
                                onClick={() => setPage(page + 1)}
                                disabled={page >= totalPages}
                            >
                                <span className="sr-only">Go to next page</span>
                                <ChevronRight className="h-4 w-4" />
                            </Button>
                        </div>
                    </div>
                </div>
            </div>

            <TrackerDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                trackerName={editingTracker}
                onSuccess={async (trackerName) => {
                    // After create/update succeeds, invalidate the Trackers list cache
                    await queryClient.invalidateQueries({ queryKey: ['trackers'] })
                    setSelectedTrackerName(trackerName)
                    setDetailRefreshKey((value) => value + 1)
                }}
            />

            <AlertDialog open={!!deleteName} onOpenChange={(open) => !open && setDeleteName(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('common.confirm')}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t('common.delete')} {deleteName}?
                        </AlertDialogDescription>
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
