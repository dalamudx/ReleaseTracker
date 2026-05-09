import { useEffect, useState } from "react"
import { Loader2, RotateCcw } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { api } from "@/api/client"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"


export interface ExecutorRollbackDialogProps {
    executor: ExecutorListItem | null
    snapshot: SnapshotListItem | null
    open: boolean
    onOpenChange: (open: boolean) => void
    onSuccess?: () => void
}


export function ExecutorRollbackDialog({
    executor,
    snapshot,
    open,
    onOpenChange,
    onSuccess,
}: ExecutorRollbackDialogProps) {
    const { t } = useTranslation()
    const [confirmText, setConfirmText] = useState("")
    const [submitting, setSubmitting] = useState(false)

    // Reset the typed confirmation whenever the dialog opens against a
    // fresh snapshot so an operator cannot accidentally reuse the text
    // from a previous dialog instance.
    useEffect(() => {
        if (!open) {
            setConfirmText("")
            setSubmitting(false)
        }
    }, [open])

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
        try {
            await api.rollbackExecutor(executor.id, { snapshot_id: snapshot.id })
            toast.success(t("executors.rollback.toasts.success"))
            onSuccess?.()
            onOpenChange(false)
        } catch (error: unknown) {
            const status = (error as { response?: { status?: number } })?.response?.status
            if (status === 404) {
                toast.error(t("executors.rollback.toasts.notFound"))
            } else if (status === 409) {
                toast.error(t("executors.rollback.toasts.conflict"))
            } else {
                console.error("Rollback failed", error)
                toast.error(t("executors.rollback.toasts.failed"))
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
        <AlertDialog open={open} onOpenChange={onOpenChange}>
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
