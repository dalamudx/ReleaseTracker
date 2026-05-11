import { useState } from "react"
import { Loader2, RotateCcw } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ExecutorListItem, SnapshotListItem } from "@/api/types"
import { useRollbackExecutor } from "@/hooks/queries"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"


export interface ExecutorRollbackDialogProps {
    executor: ExecutorListItem | null
    snapshot: SnapshotListItem | null
    open: boolean
    onOpenChange: (open: boolean) => void
    onSuccess?: () => void
}

function getApiErrorDetail(error: unknown): string | undefined {
    const data = (error as { response?: { data?: { detail?: unknown; message?: unknown } } })
        ?.response?.data
    const detail = data?.detail ?? data?.message
    return typeof detail === "string" && detail.trim() ? detail : undefined
}


export function ExecutorRollbackDialog({
    executor,
    snapshot,
    open,
    onOpenChange,
    onSuccess,
}: ExecutorRollbackDialogProps) {
    const { t } = useTranslation()
    const rollbackMutation = useRollbackExecutor()
    const [confirmText, setConfirmText] = useState("")
    const [submitting, setSubmitting] = useState(false)

    const handleOpenChange = (nextOpen: boolean) => {
        if (!nextOpen) {
            setConfirmText("")
            setSubmitting(false)
        }
        onOpenChange(nextOpen)
    }

    if (!executor || !snapshot) {
        return null
    }

    const confirmed = confirmText.trim() === executor.name

    const currentImage = executor.status?.last_version ?? null

    const handleConfirm = async () => {
        if (!executor.id || !confirmed || submitting) {
            return
        }
        setSubmitting(true)
        const toastId = toast.loading(t("executors.rollback.toasts.submitting"))
        try {
            const result = await rollbackMutation.mutateAsync({
                executorId: executor.id,
                snapshotId: snapshot.id,
            })
            if (result.recovery_outcome === "succeeded") {
                toast.success(t("executors.rollback.toasts.success"), { id: toastId })
                onSuccess?.()
                handleOpenChange(false)
            } else {
                const outcomeLabel = t(
                    `executors.history.recoveryOutcome.${result.recovery_outcome}`,
                    { defaultValue: result.recovery_outcome },
                )
                const detail = result.recovery_error ?? result.run?.message ?? ""
                const description = detail
                    ? `${outcomeLabel}: ${detail}`
                    : outcomeLabel
                toast.error(t("executors.rollback.toasts.failed"), {
                    id: toastId,
                    description,
                    duration: 10000,
                })
                // Refresh the caller so the snapshot list + history reload
                // with the just-finalized rollback run visible.
                onSuccess?.()
                handleOpenChange(false)
            }
        } catch (error: unknown) {
            const status = (error as { response?: { status?: number } })?.response?.status
            const apiDetail = getApiErrorDetail(error)
            if (status === 404) {
                toast.error(t("executors.rollback.toasts.notFound"), {
                    id: toastId,
                    description: apiDetail,
                })
            } else if (status === 409) {
                toast.error(t("executors.rollback.toasts.conflict"), {
                    id: toastId,
                    description: apiDetail,
                })
            } else {
                console.error("Rollback failed", error)
                toast.error(apiDetail ?? t("executors.rollback.toasts.failed"), { id: toastId })
            }
        } finally {
            setSubmitting(false)
        }
    }

    const capturedLabel = new Date(snapshot.created_at).toLocaleString()
    const triggerLabel = t(`executors.snapshots.trigger.${snapshot.trigger}`, {
        defaultValue: snapshot.trigger,
    })

    return (
        <AlertDialog open={open} onOpenChange={handleOpenChange}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>{t("executors.rollback.dialog.title")}</AlertDialogTitle>
                    <AlertDialogDescription>
                        {t("executors.rollback.dialog.description", { executor: executor.name })}
                    </AlertDialogDescription>
                </AlertDialogHeader>

                <div className="flex flex-col gap-3 py-2 text-xs">
                    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
                        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                            {t("executors.rollback.dialog.targetLabel")}
                        </div>
                        <div className="flex flex-col gap-0.5">
                            <span className="font-mono break-all">
                                {snapshot.image_at_capture ?? "-"}
                            </span>
                            <span className="text-muted-foreground tabular-nums">
                                {capturedLabel} · {triggerLabel}
                            </span>
                        </div>
                    </div>

                    <div className="rounded-lg border border-border/60 bg-muted/10 p-3">
                        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                            {t("executors.rollback.dialog.currentLabel")}
                        </div>
                        <span className="font-mono break-all">
                            {currentImage ?? "-"}
                        </span>
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="executor-rollback-confirm">
                            {t("executors.rollback.dialog.confirmPrompt")}
                        </Label>
                        <Input
                            id="executor-rollback-confirm"
                            value={confirmText}
                            onChange={(event) => setConfirmText(event.target.value)}
                            placeholder={executor.name}
                            autoComplete="off"
                        />
                    </div>
                </div>

                <AlertDialogFooter>
                    <AlertDialogCancel disabled={submitting}>
                        {t("executors.rollback.dialog.cancel")}
                    </AlertDialogCancel>
                    <AlertDialogAction
                        onClick={handleConfirm}
                        disabled={!confirmed || submitting}
                        data-testid="executor-rollback-confirm"
                    >
                        {submitting ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        {t("executors.rollback.dialog.confirmLabel")}
                    </AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    )
}
