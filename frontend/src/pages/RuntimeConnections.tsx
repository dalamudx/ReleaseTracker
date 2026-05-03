import { useCallback, useEffect, useState } from "react"
import { ChevronLeft, ChevronRight, Plus } from "lucide-react"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import type { RuntimeConnection } from "@/api/types"
import { RuntimeConnectionDialog } from "@/components/runtime-connections/RuntimeConnectionDialog"
import { RuntimeConnectionList } from "@/components/runtime-connections/RuntimeConnectionList"
import { Button } from "@/components/ui/button"
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
import { toast } from "sonner"

export default function RuntimeConnectionsPage() {
    const { t } = useTranslation()
    const [runtimeConnections, setRuntimeConnections] = useState<RuntimeConnection[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingRuntimeConnection, setEditingRuntimeConnection] = useState<RuntimeConnection | null>(null)
    const [deleteId, setDeleteId] = useState<number | null>(null)
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.runtimeConnections.pageSize')
        return saved ? Number(saved) : 15
    })

    const loadRuntimeConnections = useCallback(async () => {
        await Promise.resolve()
        setLoading(true)

        try {
            const skip = (page - 1) * pageSize
            const data = await api.getRuntimeConnections({ skip, limit: pageSize })
            setRuntimeConnections(data.items)
            setTotal(data.total)
        } catch (error) {
            console.error('Failed to load runtime connections', error)
            toast.error(t('runtimeConnections.toasts.loadFailed'))
        } finally {
            setLoading(false)
        }
    }, [page, pageSize, t])

    useEffect(() => {
        void Promise.resolve().then(loadRuntimeConnections)
    }, [loadRuntimeConnections])

    const handleAdd = () => {
        setEditingRuntimeConnection(null)
        setDialogOpen(true)
    }

    const handleEdit = (runtimeConnection: RuntimeConnection) => {
        setEditingRuntimeConnection(runtimeConnection)
        setDialogOpen(true)
    }

    const handleConfirmDelete = async () => {
        if (deleteId === null) {
            return
        }

        try {
            await api.deleteRuntimeConnection(deleteId)
            await loadRuntimeConnections()
            toast.success(t('common.deleted'))
        } catch (error) {
            console.error('Failed to delete runtime connection', error)
            toast.error(t('common.deleteFailed'))
        } finally {
            setDeleteId(null)
        }
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex h-full flex-col space-y-6 pr-1">
            <div className="flex flex-shrink-0 items-center justify-end">
                <Button onClick={handleAdd}>
                    <Plus className="mr-2 h-4 w-4" /> {t('runtimeConnections.addNew')}
                </Button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col space-y-4">
                <RuntimeConnectionList
                    runtimeConnections={runtimeConnections}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={setDeleteId}
                />

                <div className="flex flex-shrink-0 items-center justify-between">
                    <div className="flex-1 text-sm text-muted-foreground">
                        {t('pagination.totalItems', { count: total })}
                    </div>

                    <div className="flex items-center space-x-6 lg:space-x-8">
                        <div className="flex items-center space-x-2">
                            <p className="text-sm font-medium">{t('pagination.rowsPerPage')}</p>
                            <Select
                                value={`${pageSize}`}
                                onValueChange={(value) => {
                                    const nextPageSize = Number(value)
                                    setPageSize(nextPageSize)
                                    setPage(1)
                                    localStorage.setItem('settings.runtimeConnections.pageSize', String(nextPageSize))
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

                        <div className="flex w-auto min-w-[100px] items-center justify-center text-sm font-medium whitespace-nowrap">
                            {t('pagination.pageOf', { page, total: totalPages || 1 })}
                        </div>

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

            <RuntimeConnectionDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                runtimeConnection={editingRuntimeConnection}
                onSuccess={loadRuntimeConnections}
            />

            <AlertDialog open={deleteId !== null} onOpenChange={(open) => !open && setDeleteId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('common.confirm')}</AlertDialogTitle>
                        <AlertDialogDescription>{t('runtimeConnections.deleteConfirm')}</AlertDialogDescription>
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
