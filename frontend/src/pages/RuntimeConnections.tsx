import { useMemo, useState } from "react"
import { Plus, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { RuntimeConnection } from "@/api/types"
import { RuntimeConnectionDialog } from "@/components/runtime-connections/RuntimeConnectionDialog"
import { RuntimeConnectionList } from "@/components/runtime-connections/RuntimeConnectionList"
import { Button } from "@/components/ui/button"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
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
    useDeleteRuntimeConnection,
    useRuntimeConnections,
} from "@/hooks/queries"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

export default function RuntimeConnectionsPage() {
    const { t } = useTranslation()
    const queryClient = useQueryClient()
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingRuntimeConnection, setEditingRuntimeConnection] = useState<RuntimeConnection | null>(null)
    const [deleteId, setDeleteId] = useState<number | null>(null)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.runtimeConnections.pageSize")
    const [search, setSearch] = useState("")

    const skip = (page - 1) * pageSize
    const { data, isLoading: loading } = useRuntimeConnections({ skip, limit: pageSize })
    const rawRuntimeConnections = data?.items ?? []
    const total = data?.total ?? 0

    // Client-side filter — API doesn't accept a search param; this filters the
    // current page locally and is a no-op when the input is empty.
    const runtimeConnections = useMemo(() => {
        const term = search.trim().toLowerCase()
        if (!term) return rawRuntimeConnections
        return rawRuntimeConnections.filter((connection) => {
            if (connection.name.toLowerCase().includes(term)) return true
            if (connection.description?.toLowerCase().includes(term)) return true
            if (connection.type.toLowerCase().includes(term)) return true
            if (connection.credential_name?.toLowerCase().includes(term)) return true
            return false
        })
    }, [rawRuntimeConnections, search])

    const deleteRuntimeConnection = useDeleteRuntimeConnection()

    const handleAdd = () => {
        setEditingRuntimeConnection(null)
        setDialogOpen(true)
    }

    const handleEdit = (runtimeConnection: RuntimeConnection) => {
        setEditingRuntimeConnection(runtimeConnection)
        setDialogOpen(true)
    }

    const handleConfirmDelete = async () => {
        if (deleteId === null) return
        try {
            await deleteRuntimeConnection.mutateAsync(deleteId)
            toast.success(t("common.deleted"))
        } catch (error) {
            console.error("Failed to delete runtime connection", error)
            toast.error(t("common.deleteFailed"))
        } finally {
            setDeleteId(null)
        }
    }

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
                            placeholder={t("runtimeConnections.searchPlaceholder")}
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
                    <Plus className="mr-2 h-4 w-4" /> {t("runtimeConnections.addNew")}
                </Button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                <RuntimeConnectionList
                    runtimeConnections={runtimeConnections}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={setDeleteId}
                />

                <DataPagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={setPage}
                    onPageSizeChange={setPageSize}
                />
            </div>

            <RuntimeConnectionDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                runtimeConnection={editingRuntimeConnection}
                onSuccess={() => queryClient.invalidateQueries({ queryKey: ["runtime-connections"] })}
            />

            <AlertDialog open={deleteId !== null} onOpenChange={(open) => !open && setDeleteId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("common.confirm")}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t("runtimeConnections.deleteConfirm")}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirmDelete}>
                            {t("common.confirm")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
