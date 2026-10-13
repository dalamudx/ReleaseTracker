import { useState } from "react"
import { Plus, RefreshCw, Search, X } from "lucide-react"
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
import { QueryErrorState } from "@/components/common/QueryErrorState"
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
    const [pageSize, setPageSize] = usePageSize()
    const [search, setSearch] = useState("")

    const skip = (page - 1) * pageSize
    const { data, isLoading: loading, isError, refetch } = useRuntimeConnections({
        skip,
        limit: pageSize,
        search: search.trim() || undefined,
    })
    const runtimeConnections = data?.items ?? []
    const total = data?.total ?? 0

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
            {/* Toolbar — 搜索 + 统计与操作 */}
            <div className="flex flex-none flex-col gap-2.5 rounded-lg border border-border/60 bg-card/60 p-3 shadow-xs sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
                <div className="flex flex-1 flex-wrap items-center gap-2">
                    {/* 搜索框 */}
                    <div className="w-full min-w-[14rem] sm:w-64">
                        <InputGroup>
                            <InputGroupAddon align="inline-start">
                                <InputGroupText>
                                    <Search className="h-4 w-4" />
                                </InputGroupText>
                            </InputGroupAddon>
                            <InputGroupInput
                                placeholder={t("runtimeConnections.searchPlaceholder")}
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
                </div>
                <div className="flex items-center gap-2 self-end sm:self-center">
                    <span className="hidden text-xs text-muted-foreground sm:inline-block">
                        {t("runtimeConnections.totalCount", { count: total })}
                    </span>
                    <Button
                        variant="outline"
                        size="icon"
                        className="h-9 w-9"
                        onClick={() => void refetch()}
                        disabled={loading}
                        title={t("common.refresh")}
                    >
                        <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        <span className="sr-only">{t("common.refresh")}</span>
                    </Button>
                    <Button onClick={handleAdd} className="h-9 shadow-xs">
                        <Plus className="mr-1.5 h-4 w-4" /> {t("runtimeConnections.addNew")}
                    </Button>
                </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                {isError ? (
                    <QueryErrorState onRetry={() => void refetch()} />
                ) : (
                    <>
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
                    </>
                )}
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
