import { useEffect, useState } from "react"
import { Plus } from "lucide-react"
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

export default function TrackersPage() {
    const { t } = useTranslation()
    const [trackers, setTrackers] = useState<TrackerStatus[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingTracker, setEditingTracker] = useState<string | null>(null)

    const loadTrackers = async () => {
        setLoading(true)
        try {
            const data = await api.getTrackers()
            setTrackers(data)
        } catch (error) {
            console.error("Failed to load trackers", error)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadTrackers()
    }, [])

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
            // Ideally set separate loading state or optimistic update
            await api.checkTracker(name)
            await loadTrackers() // Refresh to see new status
            toast.success(t('common.checkStarted'))
        } catch (error) {
            console.error("Failed to check tracker", error)
            toast.error(t('common.checkFailed'))
        }
    }

    return (
        <div className="space-y-6 h-full overflow-y-auto pr-1">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">{t('trackers.title')}</h2>
                    <p className="text-muted-foreground">
                        {t('trackers.description')}
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    <Button onClick={handleAdd}>
                        <Plus className="mr-2 h-4 w-4" /> {t('trackers.addNew')}
                    </Button>
                </div>
            </div>

            <TrackerList
                trackers={trackers}
                loading={loading}
                onEdit={handleEdit}
                onDelete={handleDeleteClick}
                onCheck={handleCheck}
            />

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
