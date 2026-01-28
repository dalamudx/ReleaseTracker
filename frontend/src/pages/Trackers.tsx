import { useEffect, useState } from "react"
import { Plus, ChevronLeft, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { api } from "@/api/client"
import type { TrackerStatus } from "@/api/types"
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

export default function TrackersPage() {
    const { t } = useTranslation()
    const [trackers, setTrackers] = useState<TrackerStatus[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingTracker, setEditingTracker] = useState<string | null>(null)

    // Pagination state
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.trackers.pageSize')
        return saved ? Number(saved) : 15
    })

    const loadTrackers = async () => {
        setLoading(true)
        try {
            const skip = (page - 1) * pageSize
            const data = await api.getTrackers({ skip, limit: pageSize })
            setTrackers(data.items)
            setTotal(data.total)
        } catch (error) {
            console.error("Failed to load trackers", error)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadTrackers()
    }, [page, pageSize])



    const handleAdd = () => {
        setEditingTracker(null)
        setDialogOpen(true)
    }

    const handleEdit = (name: string) => {
        setEditingTracker(name)
        setDialogOpen(true)
    }

    const [deleteName, setDeleteName] = useState<string | null>(null)

    const handleDeleteClick = (name: string) => {
        setDeleteName(name)
    }

    const handleConfirmDelete = async () => {
        if (!deleteName) return
        try {
            await api.deleteTracker(deleteName)
            await loadTrackers()
            toast.success(t('common.deleted'))
        } catch (error) {
            console.error("Failed to delete tracker", error)
            toast.error(t('common.deleteFailed'))
        } finally {
            setDeleteName(null)
        }
    }

    const handleCheck = async (name: string) => {
        try {
            const status = await api.checkTracker(name)
            await loadTrackers()

            if (status.error) {
                toast.error(`${t('common.checkFailed')}: ${status.error}`)
            } else {
                toast.success(t('common.checkStarted'))
            }
        } catch (error: any) {
            console.error("Failed to check tracker", error)
            const detail = error.response?.data?.detail || error.message || t('common.checkFailed')
            toast.error(`${t('common.checkFailed')}: ${detail}`)
        }
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex flex-col h-full space-y-6 pr-1">
            <div className="flex items-center justify-end space-y-2 flex-shrink-0">
                <div className="flex items-center space-x-2">
                    <Button onClick={handleAdd}>
                        <Plus className="mr-2 h-4 w-4" /> {t('trackers.addNew')}
                    </Button>
                </div>
            </div>

            <div className="space-y-4">
                <TrackerList
                    trackers={trackers}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={handleDeleteClick}
                    onCheck={handleCheck}
                />

                {/* Pagination Controls */}
                <div className="flex items-center justify-between mt-3 flex-shrink-0">
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
                                    setPageSize(Number(value))
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
                        <div className="flex w-[100px] items-center justify-center text-sm font-medium">
                            {t('pagination.pageOf', { page, total: totalPages || 1 })}
                        </div>

                        {/* Navigation Buttons */}
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
                onSuccess={loadTrackers}
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
