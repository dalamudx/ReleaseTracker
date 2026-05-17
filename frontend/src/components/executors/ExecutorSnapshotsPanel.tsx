import { useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, Info, Loader2, Lock, LockOpen, RotateCcw, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ExecutorListItem, SnapshotListItem } from "@/api/types"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DataPagination } from "@/components/common/DataPagination"
import {
    useDeleteExecutorSnapshot,
    useExecutorSnapshots,
    useLockExecutorSnapshot,
    useUnlockExecutorSnapshot,
} from "@/hooks/queries"
import { usePageSize } from "@/hooks/use-page-size"
import { cn } from "@/lib/utils"

import { ExecutorRollbackDialog } from "./ExecutorRollbackDialog"


export interface ExecutorSnapshotsPanelProps {
    executor: ExecutorListItem | null
    /**
     * Bumped by parent components to force a reload (e.g., after a new
     * run completes and a fresh snapshot lands).
     */
    refreshKey?: number
    onRollbackQueued?: () => void
}

type TriggerKey = SnapshotListItem["trigger"]

const TRIGGER_BADGE_VARIANT: Record<TriggerKey, "default" | "secondary" | "outline"> = {
    pre_update: "secondary",
    manual: "default",
    pre_rollback: "outline",
}

export function ExecutorSnapshotsPanel({
    executor,
    refreshKey = 0,
    onRollbackQueued,
}: ExecutorSnapshotsPanelProps) {
    const { t, i18n } = useTranslation()

    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.executors.snapshots.pageSize")
    const [deleteSnapshot, setDeleteSnapshot] = useState<SnapshotListItem | null>(null)
    const [rollbackSnapshot, setRollbackSnapshot] = useState<SnapshotListItem | null>(null)

    const snapshotsQuery = useExecutorSnapshots(executor?.id ?? null, {
        page,
        page_size: pageSize,
    })
    const deleteSnapshotMutation = useDeleteExecutorSnapshot()
    const lockSnapshotMutation = useLockExecutorSnapshot()
    const unlockSnapshotMutation = useUnlockExecutorSnapshot()
    const previousRefreshKeyRef = useRef(refreshKey)

    const items = useMemo(() => snapshotsQuery.data?.items ?? [], [snapshotsQuery.data?.items])
    const total = snapshotsQuery.data?.total ?? 0
    const loading = snapshotsQuery.isLoading
    const deleting = deleteSnapshotMutation.isPending
    const refetchSnapshots = snapshotsQuery.refetch

    const hasUnredacted = useMemo(
        () => items.some((item) => item.unredacted_persisted),
        [items],
    )

    // Show the pruning banner only when we have more snapshots than the
    // page is displaying AND we're on the first page; this gives the
    // operator a hint that retention is actively trimming history.
    const showPruningBanner = total > pageSize && page === 1 && items.length === pageSize

    useEffect(() => {
        if (snapshotsQuery.error) {
            toast.error(t("executors.snapshots.toasts.loadFailed"))
        }
    }, [snapshotsQuery.error, t])

    useEffect(() => {
        if (previousRefreshKeyRef.current !== refreshKey) {
            previousRefreshKeyRef.current = refreshKey
            if (executor?.id != null) {
                void refetchSnapshots()
            }
        }
    }, [executor?.id, refreshKey, refetchSnapshots])

    const handleDeleteSnapshot = async () => {
        if (!executor?.id || !deleteSnapshot) {
            return
        }

        try {
            await deleteSnapshotMutation.mutateAsync({
                executorId: executor.id,
                snapshotId: deleteSnapshot.id,
            })
            toast.success(t("executors.snapshots.toasts.deleteSuccess"))
            setDeleteSnapshot(null)
            if (items.length === 1 && page > 1) {
                setPage(page - 1)
            }
        } catch {
            toast.error(t("executors.snapshots.toasts.deleteFailed"))
        }
    }

    const handleToggleLock = async (item: SnapshotListItem) => {
        if (!executor?.id) return
        try {
            if (item.locked) {
                await unlockSnapshotMutation.mutateAsync({
                    executorId: executor.id,
                    snapshotId: item.id,
                })
                toast.success(t("executors.snapshots.toasts.unlockSuccess"))
            } else {
                await lockSnapshotMutation.mutateAsync({
                    executorId: executor.id,
                    snapshotId: item.id,
                })
                toast.success(t("executors.snapshots.toasts.lockSuccess"))
            }
        } catch {
            toast.error(
                item.locked
                    ? t("executors.snapshots.toasts.unlockFailed")
                    : t("executors.snapshots.toasts.lockFailed"),
            )
        }
    }

    if (!executor) {
        return (
            <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
                {t("executors.snapshots.emptySelection")}
            </div>
        )
    }

    return (
        <div className="flex h-full flex-col gap-3 p-3 sm:p-4">
            <div className="flex flex-none flex-col gap-1">
                <h3 className="text-sm font-semibold">{t("executors.snapshots.title")}</h3>
                <p className="text-xs text-muted-foreground">
                    {t("executors.snapshots.description")}
                </p>
            </div>

            {hasUnredacted ? (
                <div className="flex items-start gap-2 rounded-lg border border-amber-400/50 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-200">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{t("executors.snapshots.banners.unredacted")}</span>
                </div>
            ) : null}

            {showPruningBanner ? (
                <div className="flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{t("executors.snapshots.banners.pruning")}</span>
                </div>
            ) : null}

            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-border/60">
                {loading ? (
                    <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        {t("common.loading")}
                    </div>
                ) : items.length === 0 ? (
                    <div className="flex h-40 items-center justify-center p-4 text-center text-sm text-muted-foreground">
                        {t("executors.snapshots.noSnapshots")}
                    </div>
                ) : (
                    <ol className="divide-y divide-border/60">
                        {items.map((item) => {
                            const captured = new Date(item.created_at)
                            const capturedLabel = captured.toLocaleString(i18n.language)
                            const isLockPending =
                                (lockSnapshotMutation.isPending || unlockSnapshotMutation.isPending) &&
                                (lockSnapshotMutation.variables?.snapshotId === item.id ||
                                    unlockSnapshotMutation.variables?.snapshotId === item.id)
                            return (
                                <li
                                    key={item.id}
                                    className="flex flex-col gap-2 p-3"
                                    data-testid="executor-snapshot-item"
                                >
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge
                                            variant={TRIGGER_BADGE_VARIANT[item.trigger] ?? "outline"}
                                            className="h-5 shrink-0 text-[10px]"
                                        >
                                            {t(`executors.snapshots.trigger.${item.trigger}`, {
                                                defaultValue: item.trigger,
                                            })}
                                        </Badge>
                                        <span className="text-xs text-muted-foreground tabular-nums">
                                            {capturedLabel}
                                        </span>
                                        {item.locked ? (
                                            <Badge
                                                variant="outline"
                                                className="h-5 shrink-0 gap-1 text-[10px]"
                                                data-testid="executor-snapshot-locked-badge"
                                            >
                                                <Lock className="h-3 w-3" />
                                                {t("executors.snapshots.locked")}
                                            </Badge>
                                        ) : null}
                                        {item.unredacted_persisted ? (
                                            <Badge
                                                variant="outline"
                                                className="h-5 shrink-0 gap-1 text-[10px] text-amber-700 dark:text-amber-200"
                                            >
                                                <AlertTriangle className="h-3 w-3" />
                                                {t("executors.snapshots.banners.unredacted", {
                                                    defaultValue: "Contains unredacted fields",
                                                })}
                                            </Badge>
                                        ) : null}
                                    </div>

                                    <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                                        <dl className="flex min-w-0 flex-1 flex-col gap-0.5 text-xs">
                                            <div className="flex gap-1">
                                                <dt className="shrink-0 text-muted-foreground">
                                                    {t("executors.snapshots.columns.imageAtCapture")}:
                                                </dt>
                                                <dd className={cn("min-w-0 break-all font-mono", !item.image_at_capture && "text-muted-foreground")}>
                                                    {item.image_at_capture ?? "-"}
                                                </dd>
                                            </div>
                                            {item.executor_run_id != null ? (
                                                <div className="flex gap-1">
                                                    <dt className="shrink-0 text-muted-foreground">
                                                        {t("executors.snapshots.columns.relatedRun")}:
                                                    </dt>
                                                    <dd className="min-w-0 font-mono">#{item.executor_run_id}</dd>
                                                </div>
                                            ) : null}
                                        </dl>

                                        <div className="flex shrink-0 gap-2">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => void handleToggleLock(item)}
                                                disabled={isLockPending}
                                                aria-label={
                                                    item.locked
                                                        ? t("executors.snapshots.actions.unlock")
                                                        : t("executors.snapshots.actions.lock")
                                                }
                                                data-testid={item.locked ? "executor-snapshot-unlock" : "executor-snapshot-lock"}
                                            >
                                                {isLockPending ? (
                                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                ) : item.locked ? (
                                                    <LockOpen className="h-3.5 w-3.5" />
                                                ) : (
                                                    <Lock className="h-3.5 w-3.5" />
                                                )}
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => setDeleteSnapshot(item)}
                                                disabled={item.locked}
                                                data-testid="executor-snapshot-delete"
                                            >
                                                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                                                {t("executors.snapshots.actions.delete")}
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => setRollbackSnapshot(item)}
                                                data-testid="executor-snapshot-rollback"
                                            >
                                                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                                                {t("executors.snapshots.actions.rollback")}
                                            </Button>
                                        </div>
                                    </div>
                                </li>
                            )
                        })}
                    </ol>
                )}
            </div>

            <DataPagination
                page={page}
                pageSize={pageSize}
                total={total}
                onPageChange={setPage}
                onPageSizeChange={setPageSize}
            />

            <AlertDialog open={deleteSnapshot !== null} onOpenChange={(open) => {
                if (!open && !deleting) setDeleteSnapshot(null)
            }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("executors.snapshots.deleteDialog.title")}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t("executors.snapshots.deleteDialog.description")}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(event) => {
                                event.preventDefault()
                                void handleDeleteSnapshot()
                            }}
                            disabled={deleting}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {deleting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("executors.snapshots.deleteDialog.confirm")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <ExecutorRollbackDialog
                executor={executor}
                snapshot={rollbackSnapshot}
                open={rollbackSnapshot !== null}
                onOpenChange={(open) => {
                    if (!open) setRollbackSnapshot(null)
                }}
                onSuccess={() => {
                    setRollbackSnapshot(null)
                    void refetchSnapshots()
                    onRollbackQueued?.()
                }}
            />
        </div>
    )
}
