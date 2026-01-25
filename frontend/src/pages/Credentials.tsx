import { useEffect, useState } from "react"
import { Plus } from "lucide-react"
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

export default function CredentialsPage() {
    const { t } = useTranslation()
    const [credentials, setCredentials] = useState<ApiCredential[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingCredential, setEditingCredential] = useState<ApiCredential | null>(null)

    const loadCredentials = async () => {
        setLoading(true)
        try {
            const data = await api.getCredentials()
            setCredentials(data)
        } catch (error) {
            console.error("Failed to load credentials", error)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadCredentials()
    }, [])

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

    return (
        <div className="space-y-6 h-full overflow-y-auto pr-1">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">{t('credentials.title')}</h2>
                    <p className="text-muted-foreground">
                        {t('credentials.description')}
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    <Button onClick={handleAdd}>
                        <Plus className="mr-2 h-4 w-4" /> {t('credentials.addNew')}
                    </Button>
                </div>
            </div>

            <CredentialList
                credentials={credentials}
                loading={loading}
                onEdit={handleEdit}
                onDelete={handleDeleteClick}
            />

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
        </div>
    )
}
