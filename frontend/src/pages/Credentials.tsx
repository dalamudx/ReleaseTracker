import { useMemo, useState } from "react"
import { Plus, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { api } from "@/api/client"
import type { ApiCredential, CredentialReferencesResponse } from "@/api/types"
import { CredentialList } from "@/components/credentials/CredentialList"
import { CredentialDialog } from "@/components/credentials/CredentialDialog"
import { DataPagination } from "@/components/common/DataPagination"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import { usePageSize } from "@/hooks/use-page-size"
import { useCredentials, useDeleteCredential } from "@/hooks/queries"
import { getCredentialTypeLabel } from "@/components/credentials/credentialTypeLabels"
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

function CredentialReferenceList({ references }: { references: CredentialReferencesResponse }) {
    const { t } = useTranslation()
    const sections = [
        { key: "runtime_connections", label: t("credentials.references.runtimeConnections") },
        { key: "aggregate_tracker_sources", label: t("credentials.references.trackerSources") },
        { key: "trackers", label: t("credentials.references.trackers") },
    ]

    return (
        <div className="max-h-72 space-y-3 overflow-y-auto rounded-md border bg-muted/30 p-3 text-sm">
            {sections.map((section) => {
                const items = references.references[section.key] || []
                if (items.length === 0) return null

                return (
                    <div key={section.key} className="space-y-1">
                        <div className="font-medium">
                            {section.label} ({items.length})
                        </div>
                        <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                            {items.map((item, index) => (
                                <li key={`${section.key}-${item.id ?? item.name}-${index}`}>
                                    {item.tracker_name ? `${item.tracker_name} / ${item.name}` : item.name}
                                    {item.type ? ` (${item.type})` : ""}
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
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingCredential, setEditingCredential] = useState<ApiCredential | null>(null)
    const [deleteId, setDeleteId] = useState<number | null>(null)
    const [blockedReferences, setBlockedReferences] = useState<CredentialReferencesResponse | null>(null)
    const [search, setSearch] = useState("")

    // Pagination state
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.credentials.pageSize")

    const skip = (page - 1) * pageSize
    const { data, isLoading: loading } = useCredentials({ skip, limit: pageSize })
    const rawCredentials = data?.items ?? []
    const total = data?.total ?? 0

    // Client-side filter — API doesn't accept a search param; this filters the
    // current page locally and is a no-op when the input is empty.
    const credentials = useMemo(() => {
        const term = search.trim().toLowerCase()
        if (!term) return rawCredentials
        return rawCredentials.filter((cred) => {
            if (cred.name.toLowerCase().includes(term)) return true
            if (cred.description?.toLowerCase().includes(term)) return true
            if (cred.type.toLowerCase().includes(term)) return true
            if (getCredentialTypeLabel(t, cred.type).toLowerCase().includes(term)) return true
            return false
        })
    }, [rawCredentials, search, t])

    const deleteCredential = useDeleteCredential()

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
            toast.error(t("common.unexpectedError"))
        }
    }

    const handleConfirmDelete = async () => {
        if (!deleteId) return
        try {
            await deleteCredential.mutateAsync(deleteId)
            toast.success(t("common.deleted"))
        } catch (error: unknown) {
            console.error("Failed to delete credential", error)
            const err = error as { response?: { status?: number; data?: { detail?: { message?: string } | string } } }
            const detail = err.response?.data?.detail
            if (err.response?.status === 409 && typeof detail === "object") {
                toast.error(detail.message || t("credentials.references.blockedTitle"))
            } else {
                toast.error(t("common.deleteFailed"))
            }
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
                            placeholder={t("credentials.searchPlaceholder")}
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
                    <Plus className="mr-2 h-4 w-4" /> {t("credentials.addNew")}
                </Button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                <CredentialList
                    credentials={credentials}
                    loading={loading}
                    onEdit={handleEdit}
                    onDelete={handleDeleteClick}
                />

                <DataPagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={setPage}
                    onPageSizeChange={setPageSize}
                />
            </div>

            <CredentialDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                credential={editingCredential}
            />

            <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("common.confirm")}</AlertDialogTitle>
                        <AlertDialogDescription>{t("common.delete")}?</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirmDelete}>
                            {t("common.confirm")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <AlertDialog
                open={!!blockedReferences}
                onOpenChange={(open) => !open && setBlockedReferences(null)}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("credentials.references.blockedTitle")}</AlertDialogTitle>
                        <AlertDialogDescription asChild>
                            <div className="space-y-3">
                                <p>{t("credentials.references.blockedDescription")}</p>
                                {blockedReferences && (
                                    <CredentialReferenceList references={blockedReferences} />
                                )}
                            </div>
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogAction onClick={() => setBlockedReferences(null)}>
                            {t("common.confirm")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}
