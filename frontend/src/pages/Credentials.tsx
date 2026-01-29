import { useEffect, useState } from "react"
import { Plus, ChevronLeft, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { api } from "@/api/client"
import type { ApiCredential } from "@/api/types"
import { CredentialList } from "@/components/credentials/CredentialList"
import { CredentialDialog } from "@/components/credentials/CredentialDialog"
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

export default function CredentialsPage() {
    const { t } = useTranslation()
    const [credentials, setCredentials] = useState<ApiCredential[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingCredential, setEditingCredential] = useState<ApiCredential | null>(null)

    // 分页状态
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.credentials.pageSize')
        return saved ? Number(saved) : 15
    })

    const loadCredentials = async () => {
        setLoading(true)
        try {
            const skip = (page - 1) * pageSize
            const data = await api.getCredentials({ skip, limit: pageSize })
            setCredentials(data.items)
            setTotal(data.total)
        } catch (error) {
            console.error("Failed to load credentials", error)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadCredentials()
    }, [page, pageSize])



    const handleAdd = () => {
        setEditingCredential(null)
        setDialogOpen(true)
    }

    const handleEdit = (cred: ApiCredential) => {
        setEditingCredential(cred)
        setDialogOpen(true)
    }

    const [deleteId, setDeleteId] = useState<number | null>(null)

    const handleDeleteClick = (id: number) => {
        setDeleteId(id)
    }

    const handleConfirmDelete = async () => {
        if (!deleteId) return
        try {
            await api.deleteCredential(deleteId)
            await loadCredentials()
            toast.success(t('common.deleted'))
        } catch (error) {
            console.error("Failed to delete credential", error)
            toast.error(t('common.deleteFailed'))
        } finally {
            setDeleteId(null)
        }
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex flex-col h-full space-y-6 pr-1">
            <div className="flex items-center justify-end space-y-2 flex-shrink-0">
                <div className="flex items-center space-x-2">
                    <Button onClick={handleAdd}>
                        <Plus className="mr-2 h-4 w-4" /> {t('credentials.addNew')}
                    </Button>
                </div>
            </div>

            <div className="space-y-4">
                <CredentialList
                    credentials={credentials}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={handleDeleteClick}
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

            <CredentialDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                credential={editingCredential}
                onSuccess={loadCredentials}
            />

            <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('common.confirm')}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t('common.delete')}?
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirmDelete}>{t('common.confirm')}</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div >
    )
}
