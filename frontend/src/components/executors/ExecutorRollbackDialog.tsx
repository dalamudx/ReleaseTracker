import { useState } from "react"
import { Loader2, RotateCcw } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ExecutorListItem, SnapshotListItem } from "@/api/types"
import { useExecutorRollbackPreview, useRollbackExecutor } from "@/hooks/queries"
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
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"


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
    const preview = useExecutorRollbackPreview(open ? executor?.id ?? null : null, snapshot?.id ?? null)
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

    const previewValid = !preview.isFetching && !preview.isError &&
        preview.data?.snapshot_id === snapshot.id && preview.data.snapshot_valid === true &&
        preview.data.integrity_status === "verified" && preview.data.mutation_performed === false &&
        !!preview.data.configuration_diff?.review_fingerprint && !preview.data.configuration_diff.truncated
    const diff = preview.data?.configuration_diff

    const handleConfirm = async () => {
        if (!executor.id || !confirmed || submitting || !previewValid) {
            return
        }
        setSubmitting(true)
        const toastId = toast.loading(t("executors.rollback.toasts.submitting"))
        try {
            const result = await rollbackMutation.mutateAsync({
                executorId: executor.id,
                snapshotId: snapshot.id,
                reviewFingerprint: diff?.review_fingerprint ?? undefined,
            })
            if ("task_id" in result) {
                toast.info(t("tasks.submitted", { name: executor.name, operation: t("executors.snapshots.actions.rollback") }), { id: toastId })
                handleOpenChange(false)
                onSuccess?.()
                return
            }
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
                setConfirmText("")
                void preview.refetch()
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
            <AlertDialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
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

                    {diff && (
                        <section aria-label={t("executors.rollback.dialog.diffTitle")} className="flex min-w-0 flex-col gap-2">
                            <h3 className="text-sm font-medium">{t("executors.rollback.dialog.diffTitle")}</h3>
                            <p className="text-muted-foreground">{t("executors.rollback.dialog.diffLegend")}</p>
                            <p>{t(`executors.rollback.dialog.scopes.${diff.scope}`)}</p>
                            {diff.current_missing && <p className="text-warning">{t("executors.rollback.dialog.targetMissing")}</p>}
                            {diff.lines.length ? (
                                <ol className="max-h-80 overflow-y-auto rounded-md border border-border text-xs font-mono" data-testid="recovery-configuration-diff">
                                    {diff.lines.map((line, index) => (
                                        <li key={`${line.path}:${line.operation}:${index}`} className={cn("flex gap-2 px-3 py-1.5", line.operation === "-" ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success")}>
                                            <span className="shrink-0" aria-label={t(line.operation === "-" ? "executors.rollback.dialog.removed" : "executors.rollback.dialog.added")}>{line.operation}</span>
                                            <span className="min-w-0 whitespace-pre-wrap break-all">{line.path}: {line.redacted ? t("executors.rollback.dialog.hiddenValue") : line.value}</span>
                                        </li>
                                    ))}
                                </ol>
                            ) : <p>{t("executors.rollback.dialog.noChanges")}</p>}
                            {diff.lines.some(line => line.redacted) && <p className="text-muted-foreground">{t("executors.rollback.dialog.secretNote")}</p>}
                            {diff.truncated && <p role="alert" className="text-destructive">{t("executors.rollback.dialog.diffTooLarge")}</p>}
                        </section>
                    )}

                    <div className="space-y-1.5" aria-live="polite">
                        <p className="text-muted-foreground">{t("executors.rollback.dialog.configurationPolicy")}</p>
                        {preview.isFetching ? (
                            <p>{t("executors.rollback.dialog.checking")}</p>
                        ) : preview.isError ? (
                            <p role="alert" className="text-destructive">{getApiErrorDetail(preview.error) ?? t("executors.rollback.dialog.checkFailed")}</p>
                        ) : previewValid ? (
                            <p>{t("executors.rollback.dialog.checkPassed")}</p>
                        ) : (
                            <p role="alert" className="break-words text-destructive">
                                {t("executors.rollback.dialog.checkInvalid")}
                                {preview.data?.validation_error && ` ${preview.data.validation_error}`}
                            </p>
                        )}
                    </div>
                    <div>
                        <Button size="sm" variant="outline" disabled={preview.isFetching || submitting} onClick={() => void preview.refetch()}>
                            {t("executors.rollback.dialog.recheck")}
                        </Button>
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
                        onClick={event => { event.preventDefault(); void handleConfirm() }}
                        disabled={!confirmed || submitting || !previewValid}
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
