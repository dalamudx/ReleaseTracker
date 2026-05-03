import { useEffect, useState, useCallback } from "react"
import { Plus, ChevronLeft, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { api } from "@/api/client"
import type { ApiCredential, CredentialReferencesResponse } from "@/api/types"
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

function CredentialReferenceList({ references }: { references: CredentialReferencesResponse }) {
    const { t } = useTranslation()
    const sections = [
        { key: 'runtime_connections', label: t('credentials.references.runtimeConnections') },
        { key: 'aggregate_tracker_sources', label: t('credentials.references.trackerSources') },
        { key: 'trackers', label: t('credentials.references.trackers') },
    ]

    return (
        <div className="max-h-72 space-y-3 overflow-y-auto rounded-md border bg-muted/30 p-3 text-sm">
            {sections.map((section) => {
                const items = references.references[section.key] || []
                if (items.length === 0) {
                    return null
                }

                return (
                    <div key={section.key} className="space-y-1">
                        <div className="font-medium">{section.label} ({items.length})</div>
                        <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                            {items.map((item, index) => (
                                <li key={`${section.key}-${item.id ?? item.name}-${index}`}>
                                    {item.tracker_name ? `${item.tracker_name} / ${item.name}` : item.name}
                                    {item.type ? ` (${item.type})` : ''}
                                </li>
                            ))}
                        </ul>
                    </div>
                )
            })}
        </div>
    )
}

export default function CredentialsPage() {
    const { t } = useTranslation()
    const [credentials, setCredentials] = useState<ApiCredential[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingCredential, setEditingCredential] = useState<ApiCredential | null>(null)
    const [deleteId, setDeleteId] = useState<number | null>(null)
    const [blockedReferences, setBlockedReferences] = useState<CredentialReferencesResponse | null>(null)

    // Pagination state
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.credentials.pageSize')
        return saved ? Number(saved) : 15
    })

    const loadCredentials = useCallback(async () => {
        await Promise.resolve()
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
    }, [page, pageSize])

    useEffect(() => {
        void Promise.resolve().then(loadCredentials)
    }, [loadCredentials])



    const handleAdd = () => {
        setEditingCredential(null)
        setDialogOpen(true)
    }

    const handleEdit = (cred: ApiCredential) => {
        setEditingCredential(cred)
        setDialogOpen(true)
    }

    const handleDeleteClick = async (id: number) => {
        try {
            const references = await api.getCredentialReferences(id)
            if (!references.deletable) {
                setBlockedReferences(references)
                return
            }
            setDeleteId(id)
        } catch (error) {
            console.error("Failed to check credential references", error)
            toast.error(t('common.unexpectedError'))
        }
    }

    const handleConfirmDelete = async () => {
        if (!deleteId) return
        try {
            await api.deleteCredential(deleteId)
            await loadCredentials()
            toast.success(t('common.deleted'))
        } catch (error: unknown) {
            console.error("Failed to delete credential", error)
            const err = error as { response?: { status?: number; data?: { detail?: { message?: string } | string } } }
            const detail = err.response?.data?.detail
            if (err.response?.status === 409 && typeof detail === 'object') {
                toast.error(detail.message || t('credentials.references.blockedTitle'))
            } else {
                toast.error(t('common.deleteFailed'))
            }
        } finally {
            setDeleteId(null)
        }
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex h-full flex-col space-y-6 pr-1">
            <div className="flex items-center justify-end flex-shrink-0">
                <div className="flex items-center space-x-2">
                    <Button onClick={handleAdd}>
                        <Plus className="mr-2 h-4 w-4" /> {t('credentials.addNew')}
                    </Button>
                </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col space-y-4">
                <CredentialList
                    credentials={credentials}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={handleDeleteClick}
                />

                {/* Pagination Controls */}
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
                        <div className="flex w-auto min-w-[100px] items-center justify-center text-sm font-medium whitespace-nowrap">
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

            <AlertDialog open={!!blockedReferences} onOpenChange={(open) => !open && setBlockedReferences(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('credentials.references.blockedTitle')}</AlertDialogTitle>
                        <AlertDialogDescription asChild>
                            <div className="space-y-3">
                                <p>{t('credentials.references.blockedDescription')}</p>
                                {blockedReferences && <CredentialReferenceList references={blockedReferences} />}
                            </div>
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogAction onClick={() => setBlockedReferences(null)}>{t('common.confirm')}</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div >
    )
}
