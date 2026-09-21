import { useMemo, useState } from "react"
import { FilterX, Plus, RefreshCw, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { api } from "@/api/client"
import type { ApiCredential, CredentialReferencesResponse, CredentialType } from "@/api/types"
import { CredentialList } from "@/components/credentials/CredentialList"
import { QueryErrorState } from "@/components/common/QueryErrorState"

const EMPTY_CREDENTIALS: ApiCredential[] = []

import { CredentialDialog } from "@/components/credentials/CredentialDialog"
import { DataPagination } from "@/components/common/DataPagination"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { usePageSize } from "@/hooks/use-page-size"
import { queryKeys, useCredentials, useDeleteCredential } from "@/hooks/queries"
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

// 凭证类型用途分类定义
type CredentialCategory = "all" | "tracker" | "runtime" | "ssh"

const TRACKER_CREDENTIAL_TYPES: CredentialType[] = [
    "github",
    "gitlab",
    "gitea",
    "helm",
    "docker",
]

const RUNTIME_CREDENTIAL_TYPES: CredentialType[] = [
    "docker_runtime",
    "podman_runtime",
    "kubernetes_runtime",
    "portainer_runtime",
]

const ALL_CREDENTIAL_TYPES: CredentialType[] = [
    ...TRACKER_CREDENTIAL_TYPES,
    ...RUNTIME_CREDENTIAL_TYPES,
    "ssh",
]

function getCredentialCategory(type: CredentialType): "tracker" | "runtime" | "ssh" {
    if (type === "ssh") return "ssh"
    if (RUNTIME_CREDENTIAL_TYPES.includes(type)) return "runtime"
    return "tracker"
}

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
    const queryClient = useQueryClient()
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingCredential, setEditingCredential] = useState<ApiCredential | null>(null)
    const [deleteId, setDeleteId] = useState<number | null>(null)
    const [blockedReferences, setBlockedReferences] = useState<CredentialReferencesResponse | null>(null)
    const [search, setSearch] = useState("")
    const [selectedCategory, setSelectedCategory] = useState<CredentialCategory>("all")
    const [selectedType, setSelectedType] = useState<string>("all")

    // Pagination state
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.credentials.pageSize")

    const skip = (page - 1) * pageSize
    const { data, isLoading: loading, isFetching, isError, refetch } = useCredentials({ skip, limit: pageSize })
    const rawCredentials = data?.items ?? EMPTY_CREDENTIALS
    const total = data?.total ?? 0

    // 根据选定的用途分类，动态计算可筛选的类型列表
    const filteredTypeOptions = useMemo(() => {
        if (selectedCategory === "tracker") return TRACKER_CREDENTIAL_TYPES
        if (selectedCategory === "runtime") return RUNTIME_CREDENTIAL_TYPES
        if (selectedCategory === "ssh") return ["ssh" as CredentialType]
        return ALL_CREDENTIAL_TYPES
    }, [selectedCategory])

    const handleCategoryChange = (category: CredentialCategory) => {
        setSelectedCategory(category)
        setSelectedType("all")
        setPage(1)
    }

    const credentials = useMemo(() => {
        const term = search.trim().toLowerCase()
        return rawCredentials.filter((cred) => {
            // 搜索过滤
            if (term) {
                const nameMatch = cred.name.toLowerCase().includes(term)
                const descMatch = cred.description?.toLowerCase().includes(term)
                const typeMatch = cred.type.toLowerCase().includes(term)
                const labelMatch = getCredentialTypeLabel(t, cred.type).toLowerCase().includes(term)
                if (!nameMatch && !descMatch && !typeMatch && !labelMatch) return false
            }

            // 用途分类过滤
            if (selectedCategory !== "all") {
                const credCat = getCredentialCategory(cred.type)
                if (credCat !== selectedCategory) return false
            }

            // 具体类型过滤
            if (selectedType !== "all" && cred.type !== selectedType) {
                return false
            }

            return true
        })
    }, [rawCredentials, search, selectedCategory, selectedType, t])

    const hasActiveFilters = search !== "" || selectedCategory !== "all" || selectedType !== "all"

    const handleResetFilters = () => {
        setSearch("")
        setSelectedCategory("all")
        setSelectedType("all")
        setPage(1)
    }

    const handleRefresh = () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.credentials() })
    }

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
            {/* Toolbar — 丰富筛选 + 添加操作 */}
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
                                placeholder={t("credentials.searchPlaceholder")}
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

                    {/* 用途分类筛选 */}
                    <Select value={selectedCategory} onValueChange={handleCategoryChange}>
                        <SelectTrigger className="h-9 w-full sm:w-[10.5rem]" aria-label={t("credentials.filters.categoryLabel")}>
                            <SelectValue placeholder={t("credentials.filters.allCategories")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t("credentials.filters.allCategories")}</SelectItem>
                            <SelectItem value="tracker">{t("credentials.filters.categoryTracker")}</SelectItem>
                            <SelectItem value="runtime">{t("credentials.filters.categoryRuntime")}</SelectItem>
                            <SelectItem value="ssh">{t("credentials.filters.categorySsh")}</SelectItem>
                        </SelectContent>
                    </Select>

                    {/* 具体凭证类型筛选 */}
                    <Select
                        value={selectedType}
                        onValueChange={(val) => {
                            setSelectedType(val)
                            setPage(1)
                        }}
                    >
                        <SelectTrigger className="h-9 w-full sm:w-[11rem]" aria-label={t("credentials.filters.typeLabel")}>
                            <SelectValue placeholder={t("credentials.filters.allTypes")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t("credentials.filters.allTypes")}</SelectItem>
                            {filteredTypeOptions.map((type) => (
                                <SelectItem key={type} value={type}>
                                    {getCredentialTypeLabel(t, type)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>

                    {/* 重置筛选按钮 */}
                    {hasActiveFilters ? (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={handleResetFilters}
                            className="h-9 px-2.5 text-xs text-muted-foreground hover:text-foreground"
                            title={t("credentials.filters.reset")}
                        >
                            <FilterX className="mr-1.5 h-3.5 w-3.5" />
                            {t("credentials.filters.reset")}
                        </Button>
                    ) : null}
                </div>

                {/* 右侧：统计、刷新与添加操作 */}
                <div className="flex items-center gap-2 self-end sm:self-center">
                    <span className="hidden text-xs text-muted-foreground sm:inline-block">
                        {t("credentials.filters.totalCount", { count: total })}
                    </span>
                    <Button
                        variant="outline"
                        size="icon"
                        className="h-9 w-9"
                        onClick={handleRefresh}
                        disabled={isFetching}
                        title={t("common.refresh")}
                    >
                        <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
                        <span className="sr-only">{t("common.refresh")}</span>
                    </Button>
                    <Button onClick={handleAdd} className="h-9 shadow-xs">
                        <Plus className="mr-1.5 h-4 w-4" /> {t("credentials.addNew")}
                    </Button>
                </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                {isError ? (
                    <QueryErrorState onRetry={() => void refetch()} />
                ) : (
                    <>
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
                    onPageSizeChange={(newPageSize) => {
                        setPageSize(newPageSize)
                        setPage(1)
                    }}
                />
                    </>
                )}
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
