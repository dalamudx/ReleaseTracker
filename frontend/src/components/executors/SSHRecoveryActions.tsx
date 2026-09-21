import { useState } from "react"
import { useTranslation } from "react-i18next"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { getApiErrorDetailMessage } from "./executorSheetHelpers"

type Action = "restore_files" | "verify_and_unlock"
export function SSHRecoveryActions({executorId, executorName, snapshotId, onSuccess}: {executorId: number; executorName: string; snapshotId: number; onSuccess: () => void}) {
    const {t} = useTranslation()
    const [action, setAction] = useState<Action | null>(null)
    const [busy, setBusy] = useState(false)
    const [message, setMessage] = useState("")
    const run = async () => {
        if (!action) return
        setBusy(true)
        setMessage("")
        try {
            const result = await api.recoverSSHCompose(executorId, snapshotId, action)
            if ("task_id" in result) {
                setMessage(t("tasks.submitted", { name: executorName, operation: t(action === "restore_files" ? "sshExecutor.restoreFiles" : "sshExecutor.verifyUnlock") }))
                setAction(null)
                return
            }
            setMessage(t(action === "restore_files" ? "sshExecutor.restored" : "sshExecutor.verified"))
            setAction(null)
            onSuccess()
        } catch (e) {
            setMessage(getApiErrorDetailMessage(e) || t("sshExecutor.recoveryFailed"))
        } finally { setBusy(false) }
    }
    return <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setAction("restore_files")}>{t("sshExecutor.restoreFiles")}</Button>
            <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setAction("verify_and_unlock")}>{t("sshExecutor.verifyUnlock")}</Button>
        </div>
        {message && <p role="status" className="max-w-sm break-words text-sm">{message}</p>}
        <AlertDialog open={action !== null} onOpenChange={open => {if (!open && !busy) setAction(null)}}>
            <AlertDialogContent>
                <AlertDialogHeader><AlertDialogTitle>{t(action === "restore_files" ? "sshExecutor.restoreFiles" : "sshExecutor.verifyUnlock")}</AlertDialogTitle><AlertDialogDescription>{t("sshExecutor.recoveryWarning")}</AlertDialogDescription></AlertDialogHeader>
                {message && <p role="alert" className="break-words text-sm text-destructive">{message}</p>}
                <AlertDialogFooter><AlertDialogCancel disabled={busy}>{t("common.cancel")}</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={e => {e.preventDefault(); void run()}}>{t("sshExecutor.confirmStopped")}</AlertDialogAction></AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    </div>
}
